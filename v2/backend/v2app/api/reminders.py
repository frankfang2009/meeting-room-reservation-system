from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify

from ..common import clean_text, local_now, parse_json_object
from ..db import get_db, transaction
from ..errors import ApiError
from ..security import current_user, locked_actor, login_required
from ..services.reservations import serialize_reservation


bp = Blueprint("reminder_api", __name__, url_prefix="/api/v1/reminders")

# 变更通知只保留最近 45 天的事件；确认回执保留 90 天后由确认动作顺带清理。
CHANGE_NOTICE_MAX_AGE_DAYS = 45
RECEIPT_RETENTION_DAYS = 90


def _utc_minute_key(moment: datetime) -> str:
    """事件时间戳是 UTC RFC3339（毫秒）；按前 16 位做分钟粒度的字符串比较。"""

    return moment.strftime("%Y-%m-%dT%H:%M")


def _upcoming_window_end(now: datetime, preference) -> datetime:
    return now + timedelta(minutes=int(preference["reminder_lead_minutes"]))


def _change_diffs(before_json, after_json) -> list[dict]:
    """从事件快照计算字段级对比；任一侧缺失或不可解析时返回空列表。"""

    if not before_json or not after_json:
        return []
    try:
        before = json.loads(before_json)
        after = json.loads(after_json)
    except (TypeError, ValueError):
        return []
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    return [
        {"field": key, "from": before[key], "to": after[key]}
        for key in after
        if key in before and before[key] != after[key]
    ]


@bp.get("/due")
@login_required
def due_reminders():
    db = get_db()
    actor = current_user()
    preference = db.execute(
        """
        SELECT booking_change_notifications, booking_reminder, reminder_lead_minutes
        FROM user_preferences WHERE user_id = ?
        """,
        (actor["id"],),
    ).fetchone()
    if preference is None:
        return jsonify({"items": []})
    items: list[dict] = []

    if preference["booking_change_notifications"]:
        cutoff = _utc_minute_key(
            datetime.now(timezone.utc) - timedelta(days=CHANGE_NOTICE_MAX_AGE_DAYS)
        )
        changed_rows = db.execute(
            """
            SELECT r.*, u.display_name AS owner_display_name,
                   e.id AS event_id, e.event_type AS change_type,
                   e.occurred_at AS change_occurred_at,
                   e.before_json, e.after_json,
                   a.display_name AS actor_display_name
            FROM reservation_events e
            JOIN reservations r ON r.id = e.reservation_id
            JOIN users u ON u.id = r.owner_user_id
            JOIN users a ON a.id = e.actor_user_id
            WHERE r.owner_user_id = ?
              AND e.actor_user_id != r.owner_user_id
              AND e.event_type IN ('updated', 'cancelled')
              AND substr(e.occurred_at, 1, 16) >= ?
              AND NOT EXISTS (
                  SELECT 1 FROM notice_receipts nr
                  WHERE nr.event_id = e.id AND nr.user_id = r.owner_user_id
              )
            ORDER BY e.occurred_at, e.rowid
            """,
            (actor["id"], cutoff),
        ).fetchall()
        for row in changed_rows:
            item = serialize_reservation(row, actor)
            item["kind"] = "change"
            item["changeType"] = row["change_type"]
            item["eventId"] = row["event_id"]
            item["occurredAt"] = row["change_occurred_at"]
            item["actorName"] = row["actor_display_name"]
            item["diffs"] = _change_diffs(row["before_json"], row["after_json"])
            items.append(item)

    if not preference["booking_reminder"]:
        return jsonify({"items": items})
    now = local_now().replace(tzinfo=None)
    limit = _upcoming_window_end(now, preference)
    rows = db.execute(
        """
        SELECT r.*, u.display_name AS owner_display_name
        FROM reservations r JOIN users u ON u.id = r.owner_user_id
        WHERE r.owner_user_id = ? AND r.status = 'active'
          AND r.booking_date BETWEEN ? AND ?
        ORDER BY r.booking_date, r.start_time
        """,
        (actor["id"], now.date().isoformat(), limit.date().isoformat()),
    ).fetchall()
    for row in rows:
        starts_at = datetime.strptime(
            f"{row['booking_date']} {row['start_time']}", "%Y-%m-%d %H:%M"
        )
        if not now < starts_at <= limit:
            continue
        item = serialize_reservation(row, actor)
        item["kind"] = "upcoming"
        items.append(item)
    return jsonify({"items": items})


@bp.post("/ack")
@login_required
def acknowledge_change_notice():
    payload = parse_json_object()
    event_id = clean_text(
        payload.get("eventId"), field="eventId", label="变更事件", maximum=64
    )
    db = get_db()
    with transaction(db):
        actor = locked_actor(db)
        row = db.execute(
            """
            SELECT e.event_type, e.actor_user_id, r.owner_user_id
            FROM reservation_events e
            JOIN reservations r ON r.id = e.reservation_id
            WHERE e.id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            raise ApiError(404, "NOT_FOUND", "变更事件不存在")
        if row["owner_user_id"] != actor["id"]:
            raise ApiError(403, "FORBIDDEN", "无权确认他人的预约变更通知")
        if (
            row["event_type"] not in ("updated", "cancelled")
            or row["actor_user_id"] == row["owner_user_id"]
        ):
            raise ApiError(422, "VALIDATION_ERROR", "该事件不是他人的预约变更")
        preference = db.execute(
            """
            SELECT booking_change_notifications
            FROM user_preferences WHERE user_id = ?
            """,
            (actor["id"],),
        ).fetchone()
        if preference is None or not preference["booking_change_notifications"]:
            raise ApiError(409, "NOTIFICATION_NOT_DUE", "该变更通知当前不可确认")
        db.execute(
            """
            INSERT INTO notice_receipts (event_id, user_id, acknowledged_at)
            VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(event_id, user_id)
            DO UPDATE SET acknowledged_at = excluded.acknowledged_at
            """,
            (event_id, actor["id"]),
        )
        db.execute(
            "DELETE FROM notice_receipts WHERE substr(acknowledged_at, 1, 16) < ?",
            (
                _utc_minute_key(
                    datetime.now(timezone.utc)
                    - timedelta(days=RECEIPT_RETENTION_DAYS)
                ),
            ),
        )
    return jsonify({"acknowledged": True, "eventId": event_id})

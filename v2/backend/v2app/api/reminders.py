from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify

from ..common import clean_text, local_now, parse_json_object
from ..db import get_db, transaction
from ..errors import ApiError
from ..security import current_user, locked_actor, login_required
from ..services.reservations import (
    reservation_details_allowed,
    serialize_reservation,
)


bp = Blueprint("reminder_api", __name__, url_prefix="/api/v1/reminders")

# 变更通知只保留最近 45 天的事件；确认回执保留 90 天后由确认动作顺带清理。
CHANGE_NOTICE_MAX_AGE_DAYS = 45
RECEIPT_RETENTION_DAYS = 90
NOTICE_IDENTITY_FIELDS = ("partyName", "purpose", "date", "start", "end", "roomName")


def _utc_minute_key(moment: datetime) -> str:
    """事件时间戳是 UTC RFC3339（毫秒）；按前 16 位做分钟粒度的字符串比较。"""

    return moment.strftime("%Y-%m-%dT%H:%M")


def _upcoming_window_end(now: datetime, preference) -> datetime:
    return now + timedelta(minutes=int(preference["reminder_lead_minutes"]))


def _change_snapshot(snapshot_json) -> dict:
    """只接受预约事件写入的 JSON 对象；损坏快照不进入通知投影。"""

    if not snapshot_json:
        return {}
    try:
        snapshot = json.loads(snapshot_json)
    except (TypeError, ValueError):
        return {}
    return snapshot if isinstance(snapshot, dict) else {}


def _change_diffs(before_json, after_json) -> list[dict]:
    """从事件快照计算字段级对比；任一侧缺失或不可解析时返回空列表。"""

    before = _change_snapshot(before_json)
    after = _change_snapshot(after_json)
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    return [
        {"field": key, "from": before[key], "to": after[key]}
        for key in after
        if key in before and before[key] != after[key]
    ]


def _change_notice_identity(before_json, after_json) -> dict:
    """投影变更发生时的原预约，避免本人后续编辑让旧通知指错预约。"""

    before = _change_snapshot(before_json)
    after = _change_snapshot(after_json)
    source = before or after
    return {
        field: source[field]
        for field in NOTICE_IDENTITY_FIELDS
        if field in source and source[field] is not None
    }


def _snapshot_owner(snapshot_json) -> tuple[str, str]:
    snapshot = _change_snapshot(snapshot_json)
    return str(snapshot.get("ownerId") or ""), str(snapshot.get("ownerName") or "")


def _event_snapshot_reservation(row) -> dict:
    snapshot = _change_snapshot(row["after_json"]) or _change_snapshot(
        row["before_json"]
    )
    owner_id = str(snapshot.get("ownerId") or "")
    owner_name = str(snapshot.get("ownerName") or "")
    return {
        "id": row["id"],
        "date": snapshot.get("date"),
        "roomId": snapshot.get("roomId"),
        "roomName": snapshot.get("roomName"),
        "start": snapshot.get("start"),
        "end": snapshot.get("end"),
        "partyName": snapshot.get("partyName"),
        "caseNumber": snapshot.get("caseNumber"),
        "purpose": snapshot.get("purpose"),
        "notes": snapshot.get("notes"),
        "tagId": snapshot.get("tagId"),
        "tagLabel": snapshot.get("tagLabel"),
        "ownerId": owner_id,
        "owner": {"id": owner_id, "name": owner_name},
        "status": snapshot.get("status"),
        "revision": snapshot.get("revision"),
        "canEdit": False,
        "canCancel": False,
        "handoverState": None,
    }


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
            WHERE CASE
                    WHEN json_valid(e.before_json)
                    THEN json_extract(e.before_json, '$.ownerId')
                    WHEN json_valid(e.after_json)
                    THEN json_extract(e.after_json, '$.ownerId')
                  END = ?
              AND e.actor_user_id != ?
              AND e.event_type IN ('updated', 'cancelled')
              AND substr(e.occurred_at, 1, 16) >= ?
              AND NOT EXISTS (
                  SELECT 1 FROM notice_receipts nr
                  WHERE nr.event_id = e.id AND nr.user_id = ?
              )
            ORDER BY e.occurred_at, e.rowid
            """,
            (actor["id"], actor["id"], cutoff, actor["id"]),
        ).fetchall()
        for row in changed_rows:
            item = (
                serialize_reservation(row, actor)
                if reservation_details_allowed(row, actor)
                else _event_snapshot_reservation(row)
            )
            item["kind"] = "change"
            item["changeType"] = row["change_type"]
            item["eventId"] = row["event_id"]
            item["occurredAt"] = row["change_occurred_at"]
            item["actorName"] = row["actor_display_name"]
            item["diffs"] = _change_diffs(row["before_json"], row["after_json"])
            item["noticeIdentity"] = _change_notice_identity(
                row["before_json"], row["after_json"]
            )
            items.append(item)

    # 待我处理的交接请求：与变更通知共用同一轮询；按读时条件过滤，
    # 预约已开始/取消的请求自然消失（状态由写路径收敛）。
    # 不受两项通知偏好约束——交接是需要处理的请求，不是可关的通知。
    now = local_now().replace(tzinfo=None)
    handover_rows = db.execute(
        """
        SELECT hr.id AS handover_request_id,
               f.display_name AS from_display_name,
               r.*, u.display_name AS owner_display_name
        FROM handover_requests hr
        JOIN reservations r ON r.id = hr.reservation_id
        JOIN users u ON u.id = r.owner_user_id
        JOIN users f ON f.id = hr.from_user_id
        WHERE hr.to_user_id = ? AND hr.status = 'pending'
          AND r.status = 'active'
          AND r.revision = hr.expected_revision
          AND r.booking_date || ' ' || r.start_time > ?
        ORDER BY hr.created_at, hr.rowid
        """,
        (actor["id"], now.strftime("%Y-%m-%d %H:%M")),
    ).fetchall()
    for row in handover_rows:
        item = serialize_reservation(row, actor)
        item["kind"] = "handover"
        item["handoverRequestId"] = row["handover_request_id"]
        item["fromName"] = row["from_display_name"]
        items.append(item)

    # 管理员指派已经即时生效，不生成可接受/拒绝的 pending request；接收人仍必须
    # 知晓所有权变化。复用 handover 事件和 notice_receipts，避免为结果通知升级 schema。
    # 每场预约只投影最新一次 handover，且只在接收人仍是当前预约者时显示，防止连续
    # 重指派后旧通知误称“预约已转入你名下”。该通知不受个人提醒开关影响。
    assignment_cutoff = _utc_minute_key(
        datetime.now(timezone.utc) - timedelta(days=CHANGE_NOTICE_MAX_AGE_DAYS)
    )
    assignment_rows = db.execute(
        """
        SELECT r.*, u.display_name AS owner_display_name,
               e.id AS assignment_event_id,
               e.occurred_at AS assignment_occurred_at,
               e.before_json, e.after_json,
               a.display_name AS assignment_actor_name
        FROM reservation_events e
        JOIN reservations r ON r.id = e.reservation_id
        JOIN users u ON u.id = r.owner_user_id
        JOIN users a ON a.id = e.actor_user_id
        WHERE e.event_type = 'handover'
          AND r.owner_user_id = ?
          AND e.actor_user_id != ?
          AND substr(e.occurred_at, 1, 16) >= ?
          AND NOT EXISTS (
              SELECT 1 FROM notice_receipts nr
              WHERE nr.event_id = e.id AND nr.user_id = ?
          )
          AND NOT EXISTS (
              SELECT 1 FROM reservation_events later
              WHERE later.reservation_id = e.reservation_id
                AND later.event_type = 'handover'
                AND (
                    later.occurred_at > e.occurred_at
                    OR (later.occurred_at = e.occurred_at AND later.rowid > e.rowid)
                )
          )
        ORDER BY e.occurred_at, e.rowid
        """,
        (actor["id"], actor["id"], assignment_cutoff, actor["id"]),
    ).fetchall()
    for row in assignment_rows:
        assigned_to_id, _ = _snapshot_owner(row["after_json"])
        if assigned_to_id != actor["id"]:
            continue
        _, from_name = _snapshot_owner(row["before_json"])
        item = serialize_reservation(row, actor)
        item["kind"] = "assignment"
        item["eventId"] = row["assignment_event_id"]
        item["occurredAt"] = row["assignment_occurred_at"]
        item["assignedByName"] = row["assignment_actor_name"]
        item["fromName"] = from_name or "原预约者"
        items.append(item)

    if not preference["booking_reminder"]:
        return jsonify({"items": items})
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
def acknowledge_notice():
    payload = parse_json_object()
    event_id = clean_text(
        payload.get("eventId"), field="eventId", label="变更事件", maximum=64
    )
    db = get_db()
    with transaction(db):
        actor = locked_actor(db)
        row = db.execute(
            """
            SELECT e.event_type, e.actor_user_id, e.before_json, e.after_json
            FROM reservation_events e
            JOIN reservations r ON r.id = e.reservation_id
            WHERE e.id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            raise ApiError(404, "NOT_FOUND", "变更事件不存在")
        if row["event_type"] == "handover":
            assigned_to_id, _ = _snapshot_owner(row["after_json"])
            if assigned_to_id != actor["id"]:
                raise ApiError(403, "FORBIDDEN", "无权确认他人的管理员指派通知")
            if row["actor_user_id"] == actor["id"]:
                raise ApiError(422, "VALIDATION_ERROR", "本人操作不产生指派通知")
        else:
            event_owner_id, _ = _snapshot_owner(row["before_json"])
            if not event_owner_id:
                event_owner_id, _ = _snapshot_owner(row["after_json"])
            if event_owner_id != actor["id"]:
                raise ApiError(403, "FORBIDDEN", "无权确认他人的预约变更通知")
            if (
                row["event_type"] not in ("updated", "cancelled")
                or row["actor_user_id"] == event_owner_id
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

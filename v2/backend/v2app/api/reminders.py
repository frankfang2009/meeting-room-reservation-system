from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, jsonify

from ..common import local_now, parse_int, parse_json_object
from ..db import get_db, transaction
from ..errors import ApiError
from ..security import current_user, locked_actor, login_required
from ..services.reservations import serialize_reservation


bp = Blueprint("reminder_api", __name__, url_prefix="/api/v1/reminders")


def _was_acknowledged(db, row, actor_id: str, kind: str) -> bool:
    receipt = db.execute(
        """
        SELECT acknowledged_at FROM reminder_receipts
        WHERE reservation_id = ? AND user_id = ?
          AND reservation_revision = ? AND kind = ?
        """,
        (row["id"], actor_id, row["revision"], kind),
    ).fetchone()
    return bool(receipt and receipt[0])


@bp.get("/due")
@login_required
def due_reminders():
    db = get_db()
    actor = current_user()
    preference = db.execute(
        """
        SELECT booking_change_notifications, booking_reminder
        FROM user_preferences WHERE user_id = ?
        """,
        (actor["id"],),
    ).fetchone()
    if preference is None:
        return jsonify({"items": []})
    due = []

    if preference["booking_change_notifications"]:
        changed_rows = db.execute(
            """
            SELECT r.*, u.display_name AS owner_display_name,
                   e.event_type AS change_type
            FROM reservations r
            JOIN users u ON u.id = r.owner_user_id
            JOIN reservation_events e
              ON e.reservation_id = r.id AND e.revision = r.revision
            WHERE r.owner_user_id = ?
              AND e.actor_user_id != r.owner_user_id
              AND e.event_type IN ('updated', 'cancelled')
            ORDER BY e.occurred_at, e.rowid
            """,
            (actor["id"],),
        ).fetchall()
        for row in changed_rows:
            if _was_acknowledged(db, row, actor["id"], "change"):
                continue
            item = serialize_reservation(row, actor)
            item["kind"] = "change"
            item["changeType"] = row["change_type"]
            due.append(item)

    if not preference["booking_reminder"]:
        return jsonify({"items": due})
    now = local_now().replace(tzinfo=None)
    limit = now + timedelta(minutes=30)
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
        if _was_acknowledged(db, row, actor["id"], "upcoming"):
            continue
        item = serialize_reservation(row, actor)
        item["kind"] = "upcoming"
        due.append(item)
    return jsonify({"items": due})


@bp.post("/<reservation_id>/ack")
@login_required
def acknowledge_reminder(reservation_id: str):
    payload = parse_json_object()
    db = get_db()
    with transaction(db):
        actor = locked_actor(db)
        row = db.execute(
            "SELECT * FROM reservations WHERE id = ?", (reservation_id,)
        ).fetchone()
        if row is None:
            raise ApiError(404, "NOT_FOUND", "预约不存在")
        if row["owner_user_id"] != actor["id"]:
            raise ApiError(403, "FORBIDDEN", "无权确认他人的预约提醒")
        kind = payload.get("kind")
        if kind not in ("change", "upcoming"):
            raise ApiError(422, "VALIDATION_ERROR", "通知类型必须是 change 或 upcoming")
        revision = parse_int(payload.get("revision"), field="revision")
        if revision != row["revision"]:
            raise ApiError(409, "REVISION_CONFLICT", "预约内容已发生变化")
        preference = db.execute(
            """
            SELECT booking_change_notifications, booking_reminder
            FROM user_preferences WHERE user_id = ?
            """,
            (actor["id"],),
        ).fetchone()
        if kind == "change":
            event = db.execute(
                """
                SELECT 1 FROM reservation_events
                WHERE reservation_id = ? AND revision = ?
                  AND actor_user_id != ?
                  AND event_type IN ('updated', 'cancelled')
                """,
                (reservation_id, revision, actor["id"]),
            ).fetchone()
            if not preference["booking_change_notifications"] or event is None:
                raise ApiError(409, "NOTIFICATION_NOT_DUE", "该变更通知当前不可确认")
        else:
            now = local_now().replace(tzinfo=None)
            starts_at = datetime.strptime(
                f"{row['booking_date']} {row['start_time']}", "%Y-%m-%d %H:%M"
            )
            if (
                not preference["booking_reminder"]
                or row["status"] != "active"
                or not now < starts_at <= now + timedelta(minutes=30)
            ):
                raise ApiError(409, "REMINDER_NOT_DUE", "该临近提醒当前不可确认")
        db.execute(
            """
            INSERT INTO reminder_receipts (
                reservation_id, user_id, reservation_revision, kind, acknowledged_at
            ) VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(reservation_id, user_id, reservation_revision, kind)
            DO UPDATE SET acknowledged_at = excluded.acknowledged_at
            """,
            (reservation_id, actor["id"], revision, kind),
        )
    return jsonify(
        {
            "acknowledged": True,
            "reservationId": reservation_id,
            "revision": revision,
            "kind": kind,
        }
    )

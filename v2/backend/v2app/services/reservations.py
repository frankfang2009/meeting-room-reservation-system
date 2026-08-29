from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime
from typing import Any, Optional

from flask import current_app

from ..common import (
    canonical_json,
    clean_text,
    decode_cursor,
    escape_like,
    encode_cursor,
    local_now,
    minutes_to_time,
    new_id,
    parse_date,
    parse_int,
    parse_page_size,
    time_to_minutes,
    validate_inclusive_date_range,
)
from ..db import get_db, transaction
from ..errors import ApiError
from ..invariants import ApplicationInvariantError
from ..security import current_user, locked_actor


def _failpoint(name: str) -> None:
    hook = current_app.config.get("TRANSACTION_FAILPOINT")
    if callable(hook):
        hook(name)
    elif hook == name:
        raise RuntimeError(f"transaction failpoint: {name}")


def _tag_slot(value: Any) -> int:
    text = str(value or "")
    match = re.fullmatch(r"(?:tag-)?([1-4])", text)
    if not match:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={"tagId": "请选择有效标签"},
        )
    return int(match.group(1))


def _tag_label(db: sqlite3.Connection, owner_id: str, slot: int) -> str:
    if slot in (1, 2):
        row = db.execute("SELECT label FROM global_tags WHERE slot = ?", (slot,)).fetchone()
        if row is None:
            raise ApplicationInvariantError("单位标签记录缺失")
        return row[0]
    row = db.execute(
        """
        SELECT personal_tag_3_label, personal_tag_4_label
        FROM user_preferences WHERE user_id = ?
        """,
        (owner_id,),
    ).fetchone()
    if row is None:
        raise ApplicationInvariantError("预约所有者的个人偏好记录缺失")
    return row[0] if slot == 3 else row[1]


def _settings(db: sqlite3.Connection) -> dict[str, Any]:
    row = db.execute("SELECT * FROM system_settings WHERE id = 1").fetchone()
    if row is None:
        raise RuntimeError("系统预约设置缺失")
    return dict(row)


def _slot_times(start: str, end: str, step: int = 30) -> list[str]:
    start_minutes = time_to_minutes(start, field="start")
    end_minutes = time_to_minutes(end, field="end")
    return [minutes_to_time(cursor) for cursor in range(start_minutes, end_minutes, step)]


def _validate_booking_payload(
    db: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    owner_id: str,
) -> dict[str, Any]:
    booking_date = parse_date(payload.get("date"))
    room_id = clean_text(
        payload.get("roomId"), field="roomId", label="笔录室", maximum=64
    )
    start = clean_text(payload.get("start"), field="start", label="开始时间", maximum=5)
    start_minutes = time_to_minutes(start, field="start")
    duration = parse_int(payload.get("duration"), field="duration")
    settings = _settings(db)
    if (
        duration < settings["slot_minutes"]
        or duration > settings["max_duration_minutes"]
        or duration % settings["slot_minutes"] != 0
    ):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={"duration": "预约时长必须是 30–180 分钟，且按 30 分钟递增"},
        )
    work_start = time_to_minutes(settings["work_start"], field="workStart")
    work_end = time_to_minutes(settings["work_end"], field="workEnd")
    end_minutes = start_minutes + duration
    if start_minutes % settings["slot_minutes"] or start_minutes < work_start or end_minutes > work_end:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={"start": "预约时间必须位于当前工作时段内"},
        )
    end = minutes_to_time(end_minutes)
    now = local_now().replace(tzinfo=None)
    starts_at = datetime.strptime(f"{booking_date} {start}", "%Y-%m-%d %H:%M")
    if starts_at <= now:
        raise ApiError(422, "BOOKING_STARTED", "不能创建或修改已经开始的预约")

    room = db.execute(
        "SELECT * FROM rooms WHERE id = ? AND is_active = 1", (room_id,)
    ).fetchone()
    if room is None:
        raise ApiError(
            422,
            "ROOM_UNAVAILABLE",
            "所选笔录室当前不可用",
            fields={"roomId": "请重新选择笔录室"},
        )

    party_name = clean_text(
        payload.get("partyName"), field="partyName", label="当事人姓名", maximum=120
    )
    case_number = clean_text(
        payload.get("caseNumber"), field="caseNumber", label="案号", maximum=120
    )
    purpose = clean_text(
        payload.get("purpose"),
        field="purpose",
        label="事项",
        maximum=120,
    )
    notes = clean_text(
        payload.get("notes", ""),
        field="notes",
        label="备注",
        maximum=500,
        required=False,
    )
    tag_slot = _tag_slot(payload.get("tagId", "tag-1"))
    return {
        "room_id": room_id,
        "room_name": room["name"],
        "booking_date": booking_date,
        "start": start,
        "end": end,
        "duration": duration,
        "party_name": party_name,
        "case_number": case_number,
        "purpose": purpose,
        "notes": notes,
        "tag_slot": tag_slot,
        "tag_label": _tag_label(db, owner_id, tag_slot),
        "slots": _slot_times(start, end, settings["slot_minutes"]),
    }


def _row_for_id(db: sqlite3.Connection, reservation_id: str):
    return db.execute(
        """
        SELECT r.*, u.display_name AS owner_display_name
        FROM reservations r
        JOIN users u ON u.id = r.owner_user_id
        WHERE r.id = ?
        """,
        (reservation_id,),
    ).fetchone()


def serialize_reservation(row: Any, actor: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    item = dict(row)
    owner_name = item.get("owner_name_snapshot")
    can_manage = False
    if actor is not None:
        can_manage = actor["role"] == "admin" or actor["id"] == item["owner_user_id"]
    start_at = datetime.strptime(
        f"{item['booking_date']} {item['start_time']}", "%Y-%m-%d %H:%M"
    )
    end_at = datetime.strptime(
        f"{item['booking_date']} {item['end_time']}", "%Y-%m-%d %H:%M"
    )
    now = local_now().replace(tzinfo=None)
    can_edit = bool(can_manage and item["status"] == "active" and start_at > now)
    can_cancel = bool(can_manage and item["status"] == "active" and end_at > now)
    return {
        "id": item["id"],
        "date": item["booking_date"],
        "roomId": item["room_id"],
        "roomName": item["room_name_snapshot"],
        "start": item["start_time"],
        "end": item["end_time"],
        "partyName": item["party_name"],
        "caseNumber": item["case_number"],
        "purpose": item["purpose"],
        "notes": item["notes"],
        "tagId": f"tag-{item['tag_slot']}",
        "tagLabel": item["tag_label_snapshot"],
        "ownerId": item["owner_user_id"],
        "owner": {"id": item["owner_user_id"], "name": owner_name},
        "status": item["status"],
        "revision": item["revision"],
        "createdAt": item["created_at"],
        "updatedAt": item["updated_at"],
        "canEdit": can_edit,
        "canCancel": can_cancel,
        "handoverState": item.get("handover_state"),
    }


def reservation_details_allowed(row: Any, actor: dict[str, Any]) -> bool:
    return bool(
        row["status"] != "cancelled"
        or actor["role"] == "admin"
        or row["owner_user_id"] == actor["id"]
    )


def _snapshot(row: Any) -> dict[str, Any]:
    item = dict(row)
    return {
        "ownerId": item["owner_user_id"],
        "ownerName": item["owner_name_snapshot"],
        "roomId": item["room_id"],
        "roomName": item["room_name_snapshot"],
        "date": item["booking_date"],
        "start": item["start_time"],
        "end": item["end_time"],
        "partyName": item["party_name"],
        "caseNumber": item["case_number"],
        "purpose": item["purpose"],
        "notes": item["notes"],
        "tagId": f"tag-{item['tag_slot']}",
        "tagLabel": item["tag_label_snapshot"],
        "status": item["status"],
        "revision": item["revision"],
    }


def _conflicts(
    db: sqlite3.Connection,
    *,
    room_id: str,
    booking_date: str,
    slots: list[str],
    excluding_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in slots)
    parameters: list[Any] = [room_id, booking_date, *slots]
    exclusion = ""
    if excluding_id:
        exclusion = " AND r.id != ?"
        parameters.append(excluding_id)
    rows = db.execute(
        f"""
        SELECT DISTINCT r.id, r.room_id, r.booking_date, r.start_time, r.end_time
        FROM reservation_slots s
        JOIN reservations r ON r.id = s.reservation_id
        WHERE s.room_id = ? AND s.booking_date = ?
          AND s.slot_start IN ({placeholders})
          AND r.status = 'active'{exclusion}
        ORDER BY r.start_time
        """,
        parameters,
    ).fetchall()
    return [
        {
            "id": row["id"],
            "roomId": row["room_id"],
            "date": row["booking_date"],
            "start": row["start_time"],
            "end": row["end_time"],
        }
        for row in rows
    ]


def _insert_slots(
    db: sqlite3.Connection,
    *,
    reservation_id: str,
    room_id: str,
    booking_date: str,
    slots: list[str],
) -> None:
    try:
        db.executemany(
            """
            INSERT INTO reservation_slots
                (reservation_id, room_id, booking_date, slot_start)
            VALUES (?, ?, ?, ?)
            """,
            [
                (reservation_id, room_id, booking_date, slot)
                for slot in slots
            ],
        )
    except sqlite3.IntegrityError as error:
        constraint = (
            "reservation_slots.room_id, reservation_slots.booking_date, "
            "reservation_slots.slot_start"
        )
        if constraint not in str(error):
            raise
        conflicts = _conflicts(
            db,
            room_id=room_id,
            booking_date=booking_date,
            slots=slots,
            excluding_id=reservation_id,
        )
        raise ApiError(
            409,
            "SLOT_CONFLICT",
            "所选时段已被占用",
            conflicts=conflicts,
        ) from error


def _insert_event(
    db: sqlite3.Connection,
    *,
    reservation_id: str,
    actor_id: str,
    event_type: str,
    revision: int,
    before: Optional[dict[str, Any]],
    after: Optional[dict[str, Any]],
) -> None:
    db.execute(
        """
        INSERT INTO reservation_events (
            id, reservation_id, actor_user_id, event_type, revision,
            before_json, after_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(),
            reservation_id,
            actor_id,
            event_type,
            revision,
            canonical_json(before) if before is not None else None,
            canonical_json(after) if after is not None else None,
        ),
    )


def create_reservation(payload: dict[str, Any]) -> dict[str, Any]:
    db = get_db()
    reservation_id = new_id()
    with transaction(db):
        actor = locked_actor(db)
        values = _validate_booking_payload(db, payload, owner_id=actor["id"])
        conflicts = _conflicts(
            db,
            room_id=values["room_id"],
            booking_date=values["booking_date"],
            slots=values["slots"],
        )
        if conflicts:
            raise ApiError(
                409,
                "SLOT_CONFLICT",
                "所选时段已被占用",
                conflicts=conflicts,
            )
        db.execute(
            """
            INSERT INTO reservations (
                id, room_id, room_name_snapshot, booking_date, start_time,
                end_time, owner_user_id, owner_name_snapshot, party_name,
                case_number, purpose, notes, tag_slot, tag_label_snapshot
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reservation_id,
                values["room_id"],
                values["room_name"],
                values["booking_date"],
                values["start"],
                values["end"],
                actor["id"],
                actor["display_name"],
                values["party_name"],
                values["case_number"],
                values["purpose"],
                values["notes"],
                values["tag_slot"],
                values["tag_label"],
            ),
        )
        _failpoint("create_after_reservation")
        _insert_slots(
            db,
            reservation_id=reservation_id,
            room_id=values["room_id"],
            booking_date=values["booking_date"],
            slots=values["slots"],
        )
        _failpoint("create_after_slots")
        row = _row_for_id(db, reservation_id)
        _insert_event(
            db,
            reservation_id=reservation_id,
            actor_id=actor["id"],
            event_type="created",
            revision=1,
            before=None,
            after=_snapshot(row),
        )
        _failpoint("create_after_event")
    row = _row_for_id(db, reservation_id)
    return serialize_reservation(row, actor)


def _load_mutable_reservation(
    db: sqlite3.Connection,
    reservation_id: str,
    actor: dict[str, Any],
    *,
    allow_started_cancel: bool = False,
) -> Any:
    row = _row_for_id(db, reservation_id)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "预约不存在")
    if actor["role"] != "admin" and row["owner_user_id"] != actor["id"]:
        raise ApiError(403, "FORBIDDEN", "无权修改他人的预约")
    if row["status"] != "active":
        raise ApiError(409, "BOOKING_NOT_ACTIVE", "预约已经取消")
    starts_at = datetime.strptime(
        f"{row['booking_date']} {row['start_time']}", "%Y-%m-%d %H:%M"
    )
    now = local_now().replace(tzinfo=None)
    if allow_started_cancel:
        ends_at = datetime.strptime(
            f"{row['booking_date']} {row['end_time']}", "%Y-%m-%d %H:%M"
        )
        if ends_at <= now:
            raise ApiError(409, "BOOKING_ENDED", "已结束的预约不能取消")
    elif starts_at <= now:
        raise ApiError(409, "BOOKING_STARTED", "已经开始的预约不能修改")
    return row


def update_reservation(reservation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    expected = parse_int(payload.get("expectedRevision"), field="expectedRevision")
    db = get_db()
    with transaction(db):
        actor = locked_actor(db)
        existing = _load_mutable_reservation(db, reservation_id, actor)
        if existing["revision"] != expected:
            raise ApiError(
                409,
                "REVISION_CONFLICT",
                "预约内容已发生变化",
                current=serialize_reservation(existing, actor),
            )
        values = _validate_booking_payload(
            db, payload, owner_id=existing["owner_user_id"]
        )
        conflicts = _conflicts(
            db,
            room_id=values["room_id"],
            booking_date=values["booking_date"],
            slots=values["slots"],
            excluding_id=reservation_id,
        )
        if conflicts:
            raise ApiError(
                409,
                "SLOT_CONFLICT",
                "所选时段已被占用",
                conflicts=conflicts,
            )
        before = _snapshot(existing)
        db.execute("DELETE FROM reservation_slots WHERE reservation_id = ?", (reservation_id,))
        _failpoint("update_after_old_slots_deleted")
        next_revision = expected + 1
        cursor = db.execute(
            """
            UPDATE reservations
            SET room_id = ?, room_name_snapshot = ?, booking_date = ?,
                start_time = ?, end_time = ?, party_name = ?, case_number = ?,
                purpose = ?, notes = ?, tag_slot = ?, tag_label_snapshot = ?,
                revision = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ? AND revision = ?
            """,
            (
                values["room_id"], values["room_name"], values["booking_date"],
                values["start"], values["end"], values["party_name"],
                values["case_number"], values["purpose"], values["notes"],
                values["tag_slot"], values["tag_label"], next_revision,
                reservation_id, expected,
            ),
        )
        if cursor.rowcount != 1:
            raise ApiError(409, "REVISION_CONFLICT", "预约内容已发生变化")
        _insert_slots(
            db,
            reservation_id=reservation_id,
            room_id=values["room_id"],
            booking_date=values["booking_date"],
            slots=values["slots"],
        )
        _failpoint("update_after_slots")
        updated = _row_for_id(db, reservation_id)
        _insert_event(
            db,
            reservation_id=reservation_id,
            actor_id=actor["id"],
            event_type="updated",
            revision=next_revision,
            before=before,
            after=_snapshot(updated),
        )
        _failpoint("update_after_event")
    return serialize_reservation(_row_for_id(db, reservation_id), actor)


def cancel_reservation(reservation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    expected = parse_int(payload.get("expectedRevision"), field="expectedRevision")
    db = get_db()
    with transaction(db):
        actor = locked_actor(db)
        existing = _load_mutable_reservation(
            db, reservation_id, actor, allow_started_cancel=True
        )
        if existing["revision"] != expected:
            raise ApiError(
                409,
                "REVISION_CONFLICT",
                "预约内容已发生变化",
                current=serialize_reservation(existing, actor),
            )
        before = _snapshot(existing)
        next_revision = expected + 1
        db.execute(
            """
            UPDATE reservations
            SET status = 'cancelled', revision = ?, cancelled_by_user_id = ?,
                cancelled_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ? AND revision = ?
            """,
            (next_revision, actor["id"], reservation_id, expected),
        )
        db.execute("DELETE FROM reservation_slots WHERE reservation_id = ?", (reservation_id,))
        _failpoint("cancel_after_slots")
        cancelled = _row_for_id(db, reservation_id)
        _insert_event(
            db,
            reservation_id=reservation_id,
            actor_id=actor["id"],
            event_type="cancelled",
            revision=next_revision,
            before=before,
            after=_snapshot(cancelled),
        )
        _failpoint("cancel_after_event")
    return serialize_reservation(_row_for_id(db, reservation_id), actor)


def list_reservations(
    date_from: str,
    date_to: str,
    args: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    start = date.fromisoformat(parse_date(date_from, field="dateFrom"))
    end = date.fromisoformat(parse_date(date_to, field="dateTo"))
    validate_inclusive_date_range(start, end)
    actor = current_user()
    args = args or {}
    page_size = parse_page_size(args.get("pageSize"))
    cursor_context = canonical_json(
        {"kind": "reservations", "dateFrom": start.isoformat(), "dateTo": end.isoformat()}
    )
    clauses = ["r.booking_date BETWEEN ? AND ?", "r.status = 'active'"]
    params: list[Any] = [start.isoformat(), end.isoformat()]
    total = get_db().execute(
        f"SELECT COUNT(*) FROM reservations r WHERE {' AND '.join(clauses)}",
        params,
    ).fetchone()[0]
    if args.get("cursor"):
        cursor = decode_cursor(args["cursor"], length=4, context=cursor_context)
        clauses.append(
            "(r.booking_date, r.start_time, r.room_name_snapshot, r.id) > (?, ?, ?, ?)"
        )
        params.extend(cursor)
    params.append(page_size + 1)
    rows = get_db().execute(
        f"""
        SELECT r.*, u.display_name AS owner_display_name
        FROM reservations r JOIN users u ON u.id = r.owner_user_id
        WHERE {' AND '.join(clauses)}
        ORDER BY r.booking_date, r.start_time, r.room_name_snapshot, r.id
        LIMIT ?
        """,
        params,
    ).fetchall()
    has_more = len(rows) > page_size
    page = rows[:page_size]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor(
            [
                last["booking_date"],
                last["start_time"],
                last["room_name_snapshot"],
                last["id"],
            ],
            context=cursor_context,
        )
    return {
        "items": [serialize_reservation(row, actor) for row in page],
        "nextCursor": next_cursor,
        "pageSize": page_size,
        "total": total,
    }


def list_upcoming() -> list[dict[str, Any]]:
    actor = current_user()
    now = local_now().replace(tzinfo=None)
    rows = get_db().execute(
        """
        SELECT r.*, u.display_name AS owner_display_name
        FROM reservations r JOIN users u ON u.id = r.owner_user_id
        WHERE r.owner_user_id = ? AND r.status = 'active'
          AND (r.booking_date > ? OR (r.booking_date = ? AND r.end_time > ?))
        ORDER BY r.booking_date, r.start_time
        """,
        (actor["id"], now.date().isoformat(), now.date().isoformat(), now.strftime("%H:%M")),
    ).fetchall()
    return [serialize_reservation(row, actor) for row in rows]


def _next_month(month_start: date) -> date:
    if month_start.year == date.max.year and month_start.month == 12:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "月份超出系统可安全查询的范围",
            fields={"month": "最大可查询月份为 9999-11"},
        )
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def list_history(args: dict[str, Any]) -> dict[str, Any]:
    now = local_now().replace(tzinfo=None)
    month = str(args.get("month") or now.strftime("%Y-%m"))
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ApiError(422, "VALIDATION_ERROR", "月份格式应为 YYYY-MM")
    try:
        month_start = date.fromisoformat(month + "-01")
    except ValueError:
        raise ApiError(422, "VALIDATION_ERROR", "月份无效")
    actor = current_user()
    page_size = parse_page_size(args.get("pageSize"))
    owner_id = str(args.get("ownerId") or "").strip() or None
    if actor["role"] == "employee":
        if owner_id and owner_id != actor["id"]:
            raise ApiError(403, "FORBIDDEN", "员工只能查看本人的预约记录")
        owner_id = actor["id"]
    tag_value = args.get("tagId")
    tag_slot = _tag_slot(tag_value) if tag_value else None
    if tag_slot in (3, 4) and not owner_id:
        raise ApiError(422, "PERSONAL_TAG_OWNER_REQUIRED", "筛选个人标签前必须选择用户")

    clauses = ["r.booking_date >= ?", "r.booking_date < ?"]
    params: list[Any] = [month_start.isoformat(), _next_month(month_start).isoformat()]
    if owner_id:
        clauses.append("r.owner_user_id = ?")
        params.append(owner_id)
    room_id = str(args.get("roomId") or "").strip() or None
    if room_id:
        clauses.append("r.room_id = ?")
        params.append(room_id)
    status_value = str(args.get("status") or "").strip() or None
    if status_value and status_value not in {"active", "cancelled"}:
        raise ApiError(422, "VALIDATION_ERROR", "预约状态无效")
    if status_value:
        clauses.append("r.status = ?")
        params.append(status_value)
    if tag_slot:
        clauses.append("r.tag_slot = ?")
        params.append(tag_slot)
    query = str(args.get("query") or "").strip()
    if query:
        if len(query) > 120:
            raise ApiError(422, "VALIDATION_ERROR", "搜索内容过长")
        clauses.append(
            "(r.party_name LIKE ? ESCAPE '\\' "
            "OR r.case_number LIKE ? ESCAPE '\\' "
            "OR r.purpose LIKE ? ESCAPE '\\' "
            "OR r.notes LIKE ? ESCAPE '\\' "
            "OR r.room_name_snapshot LIKE ? ESCAPE '\\' "
            "OR r.tag_label_snapshot LIKE ? ESCAPE '\\')"
        )
        pattern = f"%{escape_like(query)}%"
        params.extend([pattern] * 6)
    cursor_context = canonical_json(
        {
            "kind": "history",
            "month": month,
            "ownerId": owner_id,
            "roomId": room_id,
            "status": status_value,
            "tagSlot": tag_slot,
            "query": query,
        }
    )
    total = get_db().execute(
        f"SELECT COUNT(*) FROM reservations r WHERE {' AND '.join(clauses)}",
        params,
    ).fetchone()[0]
    if args.get("cursor"):
        cursor = decode_cursor(args["cursor"], length=3, context=cursor_context)
        clauses.append("(r.booking_date, r.start_time, r.id) < (?, ?, ?)")
        params.extend(cursor)
    params.append(page_size + 1)
    rows = get_db().execute(
        f"""
        SELECT r.*, u.display_name AS owner_display_name,
               CASE
                 WHEN r.status = 'active'
                   AND (r.booking_date > ? OR (r.booking_date = ? AND r.start_time > ?))
                   AND EXISTS (
                   SELECT 1 FROM handover_requests hr
                   WHERE hr.reservation_id = r.id AND hr.status = 'pending'
                     AND hr.expected_revision = r.revision
                 ) THEN 'pending'
                 WHEN r.status = 'active' AND EXISTS (
                   SELECT 1 FROM reservation_events re
                   WHERE re.reservation_id = r.id AND re.event_type = 'handover'
                 ) THEN 'completed'
                 ELSE NULL
               END AS handover_state
        FROM reservations r JOIN users u ON u.id = r.owner_user_id
        WHERE {' AND '.join(clauses)}
        ORDER BY r.booking_date DESC, r.start_time DESC, r.id DESC
        LIMIT ?
        """,
        [now.date().isoformat(), now.date().isoformat(), now.strftime("%H:%M"), *params],
    ).fetchall()
    has_more = len(rows) > page_size
    page = rows[:page_size]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor(
            [last["booking_date"], last["start_time"], last["id"]],
            context=cursor_context,
        )
    return {
        "items": [serialize_reservation(row, actor) for row in page],
        "nextCursor": next_cursor,
        "pageSize": page_size,
        "total": total,
    }


def get_reservation(reservation_id: str) -> dict[str, Any]:
    row = _row_for_id(get_db(), reservation_id)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "预约不存在")
    actor = current_user()
    if not reservation_details_allowed(row, actor):
        raise ApiError(403, "FORBIDDEN", "无权查看他人已取消的预约")
    return serialize_reservation(row, actor)


def list_events(reservation_id: str) -> list[dict[str, Any]]:
    db = get_db()
    reservation = _row_for_id(db, reservation_id)
    if reservation is None:
        raise ApiError(404, "NOT_FOUND", "预约不存在")
    actor = current_user()
    if actor["role"] != "admin" and reservation["owner_user_id"] != actor["id"]:
        raise ApiError(403, "FORBIDDEN", "无权查看该预约的变更记录")
    rows = db.execute(
        """
        SELECT e.*, u.display_name AS actor_name
        FROM reservation_events e JOIN users u ON u.id = e.actor_user_id
        WHERE e.reservation_id = ? ORDER BY e.occurred_at, e.rowid
        """,
        (reservation_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "type": row["event_type"],
            "revision": row["revision"],
            "actor": {"id": row["actor_user_id"], "name": row["actor_name"]},
            "before": json.loads(row["before_json"]) if row["before_json"] else None,
            "after": json.loads(row["after_json"]) if row["after_json"] else None,
            "occurredAt": row["occurred_at"],
        }
        for row in rows
    ]

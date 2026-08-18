from __future__ import annotations

from flask import Blueprint, jsonify

from ..common import clean_text, parse_bool, parse_int, parse_json_object
from ..db import DEFAULT_REMINDER_TEMPLATE, get_db, transaction
from ..errors import ApiError
from ..security import current_user, locked_actor, login_required, serialize_user
from ..services.audit import write_security_audit
from .core import _serialize_preferences


bp = Blueprint("preference_api", __name__, url_prefix="/api/v1/preferences")


@bp.get("")
@login_required
def read_preferences():
    db = get_db()
    user = current_user()
    return jsonify(
        {"profile": serialize_user(user), **_serialize_preferences(db, user["id"])}
    )


@bp.put("")
@login_required
def update_preferences():
    payload = parse_json_object()
    db = get_db()
    with transaction(db):
        actor = locked_actor(db)
        existing = db.execute(
            "SELECT * FROM user_preferences WHERE user_id = ?", (actor["id"],)
        ).fetchone()
        name = clean_text(
            payload.get("name", actor["display_name"]),
            field="name",
            label="姓名",
            maximum=80,
        )
        department = clean_text(
            payload.get("department", actor["department"]),
            field="department",
            label="部门",
            maximum=120,
            required=False,
        )
        duration = parse_int(
            payload.get("defaultDuration", existing["default_duration"]),
            field="defaultDuration",
        )
        if duration < 30 or duration > 180 or duration % 30:
            raise ApiError(422, "VALIDATION_ERROR", "默认预约时长必须是 30–180 分钟")
        room_id = payload.get("defaultRoomId", existing["default_room_id"])
        if room_id == "":
            room_id = None
        if room_id is not None:
            room_id = clean_text(
                room_id,
                field="defaultRoomId",
                label="默认笔录室",
                maximum=64,
            )
            if not db.execute(
                "SELECT 1 FROM rooms WHERE id = ? AND is_active = 1", (room_id,)
            ).fetchone():
                raise ApiError(422, "ROOM_UNAVAILABLE", "默认笔录室当前不可用")
        default_tag_slot = existing["default_tag_slot"]
        if "defaultTagSlot" in payload:
            if payload["defaultTagSlot"] is None:
                default_tag_slot = None
            else:
                default_tag_slot = parse_int(
                    payload["defaultTagSlot"], field="defaultTagSlot"
                )
                if default_tag_slot not in (1, 2, 3, 4):
                    raise ApiError(
                        422,
                        "VALIDATION_ERROR",
                        "请检查输入内容",
                        fields={"defaultTagSlot": "默认标签必须是槽位 1–4 或不指定"},
                    )
        change_notifications = (
            parse_bool(
                payload["bookingChangeNotifications"],
                field="bookingChangeNotifications",
            )
            if "bookingChangeNotifications" in payload
            else bool(existing["booking_change_notifications"])
        )
        reminder = (
            parse_bool(payload["bookingReminder"], field="bookingReminder")
            if "bookingReminder" in payload
            else bool(existing["booking_reminder"])
        )
        reminder_sound = (
            parse_bool(payload["reminderSound"], field="reminderSound")
            if "reminderSound" in payload
            else bool(existing["reminder_sound"])
        )
        reminder_lead_minutes = (
            parse_int(payload["reminderLeadMinutes"], field="reminderLeadMinutes")
            if "reminderLeadMinutes" in payload
            else int(existing["reminder_lead_minutes"])
        )
        if reminder_lead_minutes not in (15, 30, 60):
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "请检查输入内容",
                fields={"reminderLeadMinutes": "提醒提前量必须是 15、30 或 60 分钟"},
            )
        reminder_template = existing["reminder_template"]
        if "reminderTemplate" in payload:
            reminder_template = clean_text(
                payload["reminderTemplate"],
                field="reminderTemplate",
                label="对外提醒模板",
                maximum=200,
                required=False,
            ) or DEFAULT_REMINDER_TEMPLATE
        personal = {3: existing["personal_tag_3_label"], 4: existing["personal_tag_4_label"]}
        if "personalTags" in payload:
            if not isinstance(payload["personalTags"], list):
                raise ApiError(422, "VALIDATION_ERROR", "personalTags 必须是数组")
            for tag in payload["personalTags"]:
                if not isinstance(tag, dict):
                    raise ApiError(422, "VALIDATION_ERROR", "个人标签格式无效")
                slot = parse_int(tag.get("slot"), field="slot")
                if slot not in (3, 4):
                    raise ApiError(422, "VALIDATION_ERROR", "个人标签只能使用槽位 3、4")
                personal[slot] = clean_text(
                    tag.get("label"),
                    field=f"tag{slot}",
                    label=f"标签 {slot} 名称",
                    maximum=40,
                )
        db.execute(
            """
            UPDATE users SET display_name = ?, department = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ?
            """,
            (name, department, actor["id"]),
        )
        db.execute(
            """
            UPDATE user_preferences
            SET default_duration = ?, default_room_id = ?, default_tag_slot = ?,
                booking_change_notifications = ?, booking_reminder = ?,
                reminder_sound = ?, reminder_lead_minutes = ?,
                reminder_template = ?,
                personal_tag_3_label = ?, personal_tag_4_label = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE user_id = ?
            """,
            (
                duration, room_id, default_tag_slot,
                int(change_notifications), int(reminder),
                int(reminder_sound), reminder_lead_minutes, reminder_template,
                personal[3], personal[4], actor["id"],
            ),
        )
        write_security_audit(
            db,
            actor_user_id=actor["id"],
            action="preferences.updated",
            target_type="user",
            target_id=actor["id"],
            details={
                "defaultDuration": duration,
                "defaultRoomId": room_id,
                "defaultTagSlot": default_tag_slot,
                "reminderLeadMinutes": reminder_lead_minutes,
                "reminderSound": reminder_sound,
                "reminderTemplateUpdated": (
                    reminder_template != existing["reminder_template"]
                ),
                "reminderTemplateLength": len(reminder_template),
            },
        )
    g_user = db.execute("SELECT * FROM users WHERE id = ?", (actor["id"],)).fetchone()
    return jsonify(
        {"profile": serialize_user(dict(g_user)), **_serialize_preferences(db, actor["id"])}
    )

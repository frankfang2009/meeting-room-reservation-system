from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, g, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from ..common import (
    clean_text,
    local_now,
    new_id,
    parse_json_object,
    remote_is_loopback,
    time_to_minutes,
)
from ..db import PRODUCT_GENERATION, SCHEMA_VERSION, get_db, is_setup_complete, transaction
from ..errors import ApiError
from ..security import (
    assert_login_allowed,
    clear_login_failures,
    csrf_token,
    current_user,
    login_rate_key,
    login_required,
    record_login_failure,
    serialize_user,
)
from ..services.audit import write_security_audit
from ..runtime.install_state import sync_install_json


bp = Blueprint("core_api", __name__, url_prefix="/api/v1")


def serialize_room(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "isActive": bool(row["is_active"]),
        "sortOrder": row["sort_order"],
    }


def serialize_room_with_metrics(db, row: Any) -> dict[str, Any]:
    result = serialize_room(row)
    now = local_now().replace(tzinfo=None)
    today = now.date().isoformat()
    current_time = now.strftime("%H:%M")
    counts = db.execute(
        """
        SELECT
            SUM(CASE WHEN booking_date = ? THEN 1 ELSE 0 END) AS today_count,
            SUM(CASE WHEN booking_date > ? OR
                          (booking_date = ? AND start_time > ?)
                     THEN 1 ELSE 0 END) AS future_count
        FROM reservations
        WHERE room_id = ? AND status = 'active'
        """,
        (today, today, today, current_time, row["id"]),
    ).fetchone()
    next_row = db.execute(
        """
        SELECT booking_date, start_time
        FROM reservations
        WHERE room_id = ? AND status = 'active'
          AND (booking_date > ? OR (booking_date = ? AND start_time > ?))
        ORDER BY booking_date, start_time
        LIMIT 1
        """,
        (row["id"], today, today, current_time),
    ).fetchone()
    result["todayCount"] = int(counts["today_count"] or 0)
    result["futureCount"] = int(counts["future_count"] or 0)
    if next_row is None:
        result["nextBooking"] = None
    elif next_row["booking_date"] == today:
        result["nextBooking"] = f"今天 {next_row['start_time']}"
    else:
        month, day = next_row["booking_date"].split("-")[1:]
        result["nextBooking"] = f"{int(month)}月{int(day)}日 {next_row['start_time']}"
    return result


def _serialize_preferences(db, user_id: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM user_preferences WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        raise RuntimeError("用户偏好缺失")
    return {
        "defaultDuration": row["default_duration"],
        "defaultRoomId": row["default_room_id"],
        "bookingChangeNotifications": bool(row["booking_change_notifications"]),
        "bookingReminder": bool(row["booking_reminder"]),
        "personalTags": [
            {"id": "tag-3", "slot": 3, "label": row["personal_tag_3_label"]},
            {"id": "tag-4", "slot": 4, "label": row["personal_tag_4_label"]},
        ],
    }


def _serialize_personal_tags(row: Any) -> list[dict[str, Any]]:
    return [
        {"id": "tag-3", "slot": 3, "label": row["personal_tag_3_label"]},
        {"id": "tag-4", "slot": 4, "label": row["personal_tag_4_label"]},
    ]


def _serialize_settings(db) -> dict[str, Any]:
    row = db.execute("SELECT * FROM system_settings WHERE id = 1").fetchone()
    return {
        "workStart": row["work_start"],
        "workEnd": row["work_end"],
        "slotMinutes": row["slot_minutes"],
        "maxDurationMinutes": row["max_duration_minutes"],
    }


@bp.get("/setup")
def setup_state():
    complete = is_setup_complete()
    return jsonify(
        {
            "setupRequired": not complete,
            "setupComplete": complete,
            "csrfToken": csrf_token(),
        }
    )


@bp.get("/session")
def read_session():
    complete = is_setup_complete()
    user = current_user() if complete else None
    return jsonify(
        {
            "productVersion": current_app.config["PRODUCT_VERSION"],
            "setupComplete": complete,
            "authenticated": user is not None,
            "csrfToken": csrf_token(),
            "currentUser": serialize_user(user) if user else None,
        }
    )


def _validate_username(value: Any) -> str:
    username = clean_text(value, field="username", label="用户名", maximum=80)
    if len(username) < 3 or any(character.isspace() for character in username):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={"username": "用户名至少 3 个字符且不能包含空格"},
        )
    return username


def _validate_password(value: Any, *, field: str = "password") -> str:
    password = str(value or "")
    if len(password) < 8 or len(password) > 256:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={field: "密码长度必须为 8–256 个字符"},
        )
    return password


@bp.post("/setup/complete")
def complete_setup():
    if not remote_is_loopback():
        raise ApiError(403, "SETUP_LOOPBACK_ONLY", "首次设置只能在服务器本机完成")
    payload = parse_json_object()
    admin_payload = payload.get("admin")
    rooms_payload = payload.get("rooms")
    if not isinstance(admin_payload, dict):
        raise ApiError(422, "VALIDATION_ERROR", "请填写首名管理员信息")
    if not isinstance(rooms_payload, list) or not rooms_payload:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={"rooms": "至少创建一个笔录室"},
        )
    username = _validate_username(admin_payload.get("username"))
    password = _validate_password(admin_payload.get("password"))
    display_name = clean_text(
        admin_payload.get("name"), field="name", label="姓名", maximum=80
    )
    department = clean_text(
        admin_payload.get("department", ""),
        field="department",
        label="部门",
        maximum=120,
        required=False,
    )
    work_start = clean_text(
        payload.get("workStart", "08:30"),
        field="workStart",
        label="工作开始时间",
        maximum=5,
    )
    work_end = clean_text(
        payload.get("workEnd", "17:30"),
        field="workEnd",
        label="工作结束时间",
        maximum=5,
    )
    start_minutes = time_to_minutes(work_start, field="workStart")
    end_minutes = time_to_minutes(work_end, field="workEnd")
    if start_minutes % 30 or end_minutes % 30 or end_minutes <= start_minutes:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={"workEnd": "工作时间必须按 30 分钟对齐，且结束晚于开始"},
        )
    room_names: list[str] = []
    for index, value in enumerate(rooms_payload):
        if not isinstance(value, dict):
            raise ApiError(422, "VALIDATION_ERROR", "笔录室格式无效")
        room_names.append(
            clean_text(
                value.get("name"),
                field=f"rooms.{index}.name",
                label="笔录室名称",
                maximum=80,
            )
        )
    if len(set(room_names)) != len(room_names):
        raise ApiError(422, "VALIDATION_ERROR", "笔录室名称不能重复")

    db = get_db()
    admin_id = new_id()
    with transaction(db):
        if is_setup_complete(db):
            raise ApiError(409, "SETUP_ALREADY_COMPLETE", "首次设置已经完成")
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise ApiError(409, "SETUP_STATE_INVALID", "首次设置数据状态异常")
        db.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, department, role)
            VALUES (?, ?, ?, ?, ?, 'admin')
            """,
            (admin_id, username, generate_password_hash(password), display_name, department),
        )
        room_ids = []
        for order, name in enumerate(room_names, 1):
            room_id = new_id()
            room_ids.append(room_id)
            db.execute(
                "INSERT INTO rooms (id, name, sort_order) VALUES (?, ?, ?)",
                (room_id, name, order),
            )
        db.execute(
            """
            INSERT INTO user_preferences (user_id, default_room_id)
            VALUES (?, ?)
            """,
            (admin_id, room_ids[0]),
        )
        db.execute(
            """
            UPDATE system_settings SET work_start = ?, work_end = ? WHERE id = 1
            """,
            (work_start, work_end),
        )
        db.executemany(
            """
            INSERT INTO app_meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (
                ("product_generation", str(PRODUCT_GENERATION)),
                ("schema_version", str(SCHEMA_VERSION)),
                ("setup_complete", "1"),
            ),
        )
        write_security_audit(
            db,
            action="setup.completed",
            target_type="system",
            target_id=str(PRODUCT_GENERATION),
            details={"roomCount": len(room_ids)},
        )
        hook = current_app.config.get("SETUP_FAILPOINT")
        if hook:
            if callable(hook):
                hook()
            else:
                raise RuntimeError("setup failpoint")
    synchronized = False
    try:
        synchronized = sync_install_json(
            Path(current_app.config["DATA_DIR"]) / "install.json",
            install_id=current_app.config["INSTALL_ID"],
            setup_complete=True,
        )
    except (OSError, RuntimeError):
        current_app.logger.exception("首次设置已提交，但 install.json 同步失败；下次启动将自愈")
    completed_event = current_app.config.get("SETUP_COMPLETED_EVENT")
    if completed_event is not None:
        completed_event.set()
    return jsonify(
        {
            "setupComplete": True,
            "restartRequired": True,
            "installStateSynchronized": synchronized,
        }
    ), 201


@bp.post("/session")
def login():
    if not is_setup_complete():
        raise ApiError(409, "SETUP_REQUIRED", "请先完成首次设置")
    payload = parse_json_object()
    username = clean_text(
        payload.get("username"), field="username", label="用户名", maximum=80
    )
    password = str(payload.get("password") or "")
    if len(password) > 256:
        raise ApiError(401, "INVALID_CREDENTIALS", "用户名或密码错误")
    key = login_rate_key(username)
    assert_login_allowed(key)
    row = get_db().execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()
    valid_password = bool(row and check_password_hash(row["password_hash"], password))
    if not valid_password:
        record_login_failure(key)
        raise ApiError(401, "INVALID_CREDENTIALS", "用户名或密码错误")
    if not row["is_active"]:
        record_login_failure(key)
        raise ApiError(403, "ACCOUNT_DISABLED", "账号已停用，请联系管理员")
    clear_login_failures(key)
    session.clear()
    session["user_id"] = row["id"]
    session["session_version"] = row["session_version"]
    token = csrf_token()
    g.current_user = dict(row)
    return jsonify(
        {
            "authenticated": True,
            "csrfToken": token,
            "currentUser": serialize_user(dict(row)),
        }
    )


@bp.delete("/session")
def logout():
    session.clear()
    g.pop("current_user", None)
    return jsonify({"authenticated": False, "csrfToken": csrf_token()})


@bp.get("/bootstrap")
@login_required
def bootstrap():
    db = get_db()
    user = current_user()
    room_query = "SELECT * FROM rooms"
    if user["role"] != "admin":
        room_query += " WHERE is_active = 1"
    room_query += " ORDER BY sort_order, name"
    room_rows = db.execute(room_query).fetchall()
    rooms = [
        serialize_room_with_metrics(db, row)
        if user["role"] == "admin"
        else serialize_room(row)
        for row in room_rows
    ]
    users = []
    if user["role"] == "admin":
        user_rows = db.execute(
            """
            SELECT users.*, user_preferences.personal_tag_3_label,
                   user_preferences.personal_tag_4_label
            FROM users
            JOIN user_preferences ON user_preferences.user_id = users.id
            ORDER BY users.display_name, users.username
            """
        ).fetchall()
        for row in user_rows:
            item = serialize_user(dict(row))
            item["personalTags"] = _serialize_personal_tags(row)
            users.append(item)
    global_tags = [
        {"id": f"tag-{row['slot']}", "slot": row["slot"], "label": row["label"]}
        for row in db.execute("SELECT * FROM global_tags ORDER BY slot").fetchall()
    ]
    return jsonify(
        {
            "productVersion": current_app.config["PRODUCT_VERSION"],
            "currentUser": serialize_user(user),
            "rooms": rooms,
            "users": users,
            "globalTags": global_tags,
            "personalTags": _serialize_preferences(db, user["id"])["personalTags"],
            "preferences": _serialize_preferences(db, user["id"]),
            "settings": _serialize_settings(db),
            "permissions": {
                "manageRooms": user["role"] == "admin",
                "manageUsers": user["role"] == "admin",
                "manageSystem": user["role"] == "admin",
            },
        }
    )


__all__ = [
    "_serialize_preferences",
    "_serialize_settings",
    "bp",
    "serialize_room",
    "serialize_room_with_metrics",
]

from __future__ import annotations

import sqlite3
from typing import Any

from flask import Blueprint, jsonify, session
from werkzeug.security import generate_password_hash

from ..common import (
    clean_text,
    local_now,
    new_id,
    parse_bool,
    parse_int,
    parse_json_object,
    time_to_minutes,
)
from ..db import get_db, transaction
from ..errors import ApiError
from ..security import admin_required, current_user, locked_actor, serialize_user
from ..services.audit import write_security_audit
from ..services.reservations import serialize_reservation
from .core import _serialize_settings, serialize_room_with_metrics, serialize_rooms_with_metrics


bp = Blueprint("admin_api", __name__, url_prefix="/api/v1")


@bp.put("/admin/settings")
@admin_required
def update_system_settings():
    payload = parse_json_object()
    db = get_db()
    with transaction(db):
        actor = locked_actor(db, admin=True)
        current = db.execute("SELECT * FROM system_settings WHERE id = 1").fetchone()
        if current is None:
            raise RuntimeError("系统设置缺失")
        work_start = clean_text(
            payload.get("workStart"), field="workStart", label="工作开始时间", maximum=5
        )
        work_end = clean_text(
            payload.get("workEnd"), field="workEnd", label="工作结束时间", maximum=5
        )
        start_minutes = time_to_minutes(work_start, field="workStart")
        end_minutes = time_to_minutes(work_end, field="workEnd")
        slot_minutes = int(current["slot_minutes"])
        fields = {}
        if start_minutes % slot_minutes:
            fields["workStart"] = f"开始时间必须按 {slot_minutes} 分钟对齐"
        if end_minutes % slot_minutes:
            fields["workEnd"] = f"结束时间必须按 {slot_minutes} 分钟对齐"
        if end_minutes <= start_minutes:
            fields["workEnd"] = "结束时间必须晚于开始时间"
        if fields:
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "请检查输入内容",
                fields=fields,
            )
        before = {
            "workStart": current["work_start"],
            "workEnd": current["work_end"],
        }
        after = {"workStart": work_start, "workEnd": work_end}
        db.execute(
            "UPDATE system_settings SET work_start = ?, work_end = ? WHERE id = 1",
            (work_start, work_end),
        )
        write_security_audit(
            db,
            actor_user_id=actor["id"],
            action="settings.updated",
            target_type="system",
            target_id="work-hours",
            details={"before": before, "after": after},
        )
    return jsonify(_serialize_settings(db))


def _validate_role(value: Any) -> str:
    if value not in ("admin", "employee"):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={"role": "角色只能是 admin 或 employee"},
        )
    return str(value)


def _validate_password(value: Any, *, field: str = "password") -> str:
    password = str(value or "")
    if not 8 <= len(password) <= 256:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={field: "密码长度必须为 8–256 个字符"},
        )
    return password


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


@bp.get("/rooms")
@admin_required
def list_rooms():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM rooms ORDER BY sort_order, name"
    ).fetchall()
    return jsonify({"items": serialize_rooms_with_metrics(db, rows)})


def _room_deletion_impact(db, room_id: str, actor: dict[str, Any]):
    room = db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    if room is None:
        raise ApiError(404, "NOT_FOUND", "笔录室不存在")
    now = local_now().replace(tzinfo=None)
    boundary = (room_id, now.date().isoformat(), now.date().isoformat(), now.strftime("%H:%M"))
    total = db.execute(
        """
        SELECT COUNT(*) FROM reservations
        WHERE room_id = ? AND status = 'active'
          AND (booking_date > ? OR (booking_date = ? AND end_time > ?))
        """,
        boundary,
    ).fetchone()[0]
    rows = []
    if total:
        rows = db.execute(
            """
            SELECT r.* FROM reservations r
            WHERE r.room_id = ? AND r.status = 'active'
              AND (r.booking_date > ? OR (r.booking_date = ? AND r.end_time > ?))
            ORDER BY r.booking_date, r.start_time, r.id
            LIMIT 50
            """,
            boundary,
        ).fetchall()
    return room, {
        "room": {"id": room_id, "name": room["name"]},
        "total": total,
        "items": [serialize_reservation(row, actor) for row in rows],
    }


@bp.get("/rooms/<room_id>/deletion-impact")
@admin_required
def room_deletion_impact(room_id: str):
    actor = current_user()
    _, impact = _room_deletion_impact(get_db(), room_id, actor)
    return jsonify(impact)


@bp.post("/rooms")
@admin_required
def create_room():
    payload = parse_json_object()
    name = clean_text(payload.get("name"), field="name", label="笔录室名称", maximum=80)
    sort_order = parse_int(payload.get("sortOrder", 1), field="sortOrder")
    if not 1 <= sort_order <= 10000:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={"sortOrder": "排序值必须在 1–10000 之间"},
        )
    is_active = (
        parse_bool(payload["isActive"], field="isActive")
        if "isActive" in payload
        else True
    )
    show_on_display = (
        parse_bool(payload["showOnDisplay"], field="showOnDisplay")
        if "showOnDisplay" in payload
        else True
    )
    room_id = new_id()
    db = get_db()
    try:
        with transaction(db):
            actor = locked_actor(db, admin=True)
            db.execute(
                """
                INSERT INTO rooms (
                    id, name, sort_order, is_active, show_on_display
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (room_id, name, sort_order, int(is_active), int(show_on_display)),
            )
            write_security_audit(
                db,
                actor_user_id=actor["id"],
                action="room.created",
                target_type="room",
                target_id=room_id,
                details={
                    "name": name,
                    "isActive": is_active,
                    "showOnDisplay": show_on_display,
                },
            )
    except sqlite3.IntegrityError as error:
        raise ApiError(409, "ROOM_NAME_EXISTS", "笔录室名称已存在") from error
    row = db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    return jsonify(serialize_room_with_metrics(db, row)), 201


@bp.patch("/rooms/<room_id>")
@admin_required
def update_room(room_id: str):
    payload = parse_json_object()
    db = get_db()
    try:
        with transaction(db):
            actor = locked_actor(db, admin=True)
            room = db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
            if room is None:
                raise ApiError(404, "NOT_FOUND", "笔录室不存在")
            name = clean_text(
                payload.get("name", room["name"]),
                field="name",
                label="笔录室名称",
                maximum=80,
            )
            enabled = (
                parse_bool(payload["isActive"], field="isActive")
                if "isActive" in payload
                else bool(room["is_active"])
            )
            show_on_display = (
                parse_bool(payload["showOnDisplay"], field="showOnDisplay")
                if "showOnDisplay" in payload
                else bool(room["show_on_display"])
            )
            sort_order = (
                parse_int(payload["sortOrder"], field="sortOrder")
                if "sortOrder" in payload
                else room["sort_order"]
            )
            if not 1 <= sort_order <= 10000:
                raise ApiError(
                    422,
                    "VALIDATION_ERROR",
                    "请检查输入内容",
                    fields={"sortOrder": "排序值必须在 1–10000 之间"},
                )
            db.execute(
                """
                UPDATE rooms SET name = ?, is_active = ?, sort_order = ?,
                    show_on_display = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id = ?
                """,
                (name, int(enabled), sort_order, int(show_on_display), room_id),
            )
            write_security_audit(
                db,
                actor_user_id=actor["id"],
                action="room.updated",
                target_type="room",
                target_id=room_id,
                details={
                    "name": name,
                    "enabled": enabled,
                    "sortOrder": sort_order,
                    "showOnDisplay": show_on_display,
                },
            )
    except sqlite3.IntegrityError as error:
        raise ApiError(409, "ROOM_NAME_EXISTS", "笔录室名称已存在") from error
    return jsonify(
        serialize_room_with_metrics(
            db,
            db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone(),
        )
    )


@bp.delete("/rooms/<room_id>")
@admin_required
def delete_room(room_id: str):
    db = get_db()
    with transaction(db):
        actor = locked_actor(db, admin=True)
        room, impact = _room_deletion_impact(db, room_id, actor)
        if impact["total"]:
            raise ApiError(
                409,
                "ROOM_HAS_FUTURE_BOOKINGS",
                "该笔录室仍有未结束预约，不能删除",
                conflicts=impact["items"],
                current={
                    "roomId": room_id,
                    "roomName": room["name"],
                    "total": impact["total"],
                },
            )
        db.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        write_security_audit(
            db,
            actor_user_id=actor["id"],
            action="room.deleted",
            target_type="room",
            target_id=room_id,
            details={"name": room["name"]},
        )
    return jsonify({"deleted": True})


@bp.get("/users")
@admin_required
def list_users():
    rows = get_db().execute(
        "SELECT * FROM users ORDER BY display_name, username"
    ).fetchall()
    return jsonify({"items": [serialize_user(dict(row)) for row in rows]})


@bp.post("/users")
@admin_required
def create_user():
    payload = parse_json_object()
    username = _validate_username(payload.get("username"))
    password = _validate_password(payload.get("password"))
    display_name = clean_text(payload.get("name"), field="name", label="姓名", maximum=80)
    department = clean_text(
        payload.get("department", ""),
        field="department",
        label="部门",
        maximum=120,
        required=False,
    )
    role = _validate_role(payload.get("role", "employee"))
    enabled = (
        parse_bool(payload["enabled"], field="enabled")
        if "enabled" in payload
        else True
    )
    user_id = new_id()
    db = get_db()
    try:
        with transaction(db):
            actor = locked_actor(db, admin=True)
            db.execute(
                """
                INSERT INTO users
                    (id, username, password_hash, display_name, department, role, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    generate_password_hash(password),
                    display_name,
                    department,
                    role,
                    int(enabled),
                ),
            )
            db.execute("INSERT INTO user_preferences (user_id) VALUES (?)", (user_id,))
            write_security_audit(
                db,
                actor_user_id=actor["id"],
                action="user.created",
                target_type="user",
                target_id=user_id,
                details={"role": role, "enabled": enabled},
            )
    except sqlite3.IntegrityError as error:
        raise ApiError(409, "USERNAME_EXISTS", "用户名已存在") from error
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return jsonify(serialize_user(dict(row))), 201


def _assert_admin_survives(db, target: Any, next_role: str, next_enabled: bool) -> None:
    if target["role"] != "admin" or not target["is_active"]:
        return
    if next_role == "admin" and next_enabled:
        return
    count = db.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
    ).fetchone()[0]
    if count <= 1:
        raise ApiError(
            409,
            "LAST_ADMIN_REQUIRED",
            "必须至少保留一名启用的管理员",
        )


@bp.patch("/users/<user_id>")
@admin_required
def update_user(user_id: str):
    payload = parse_json_object()
    db = get_db()
    reauthenticate = False
    try:
        with transaction(db):
            actor = locked_actor(db, admin=True)
            target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if target is None:
                raise ApiError(404, "NOT_FOUND", "用户不存在")
            submitted_username = payload.get("username", target["username"])
            username = _validate_username(submitted_username)
            if username != target["username"]:
                raise ApiError(
                    422,
                    "USERNAME_IMMUTABLE",
                    "用户名创建后不可修改",
                    fields={"username": "用户名创建后不可修改"},
                )
            display_name = clean_text(
                payload.get("name", target["display_name"]),
                field="name",
                label="姓名",
                maximum=80,
            )
            department = clean_text(
                payload.get("department", target["department"]),
                field="department",
                label="部门",
                maximum=120,
                required=False,
            )
            role = _validate_role(payload.get("role", target["role"]))
            enabled = (
                parse_bool(payload["enabled"], field="enabled")
                if "enabled" in payload
                else bool(target["is_active"])
            )
            if user_id == actor["id"] and not enabled:
                raise ApiError(409, "CANNOT_DISABLE_SELF", "不能停用当前登录账号")
            _assert_admin_survives(db, target, role, enabled)
            revoke = role != target["role"] or int(enabled) != target["is_active"]
            db.execute(
                """
                UPDATE users
                SET username = ?, display_name = ?, department = ?, role = ?,
                    is_active = ?, session_version = session_version + ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id = ?
                """,
                (username, display_name, department, role, int(enabled), int(revoke), user_id),
            )
            write_security_audit(
                db,
                actor_user_id=actor["id"],
                action="user.updated",
                target_type="user",
                target_id=user_id,
                details={"role": role, "enabled": enabled},
            )
            reauthenticate = bool(revoke and user_id == actor["id"])
    except sqlite3.IntegrityError as error:
        raise ApiError(409, "USERNAME_EXISTS", "用户名已存在") from error
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    result = serialize_user(dict(row))
    result["reauthenticate"] = reauthenticate
    if reauthenticate:
        session.clear()
    return jsonify(result)


@bp.post("/users/<user_id>/reset-password")
@admin_required
def reset_password(user_id: str):
    payload = parse_json_object()
    password = _validate_password(payload.get("password"), field="password")
    db = get_db()
    reauthenticate = False
    with transaction(db):
        actor = locked_actor(db, admin=True)
        target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if target is None:
            raise ApiError(404, "NOT_FOUND", "用户不存在")
        db.execute(
            """
            UPDATE users
            SET password_hash = ?, session_version = session_version + 1,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ?
            """,
            (generate_password_hash(password), user_id),
        )
        write_security_audit(
            db,
            actor_user_id=actor["id"],
            action="user.password_reset",
            target_type="user",
            target_id=user_id,
        )
        reauthenticate = user_id == actor["id"]
    if reauthenticate:
        session.clear()
    return jsonify({"reset": True, "reauthenticate": reauthenticate})


@bp.put("/tags/global")
@admin_required
def update_global_tags():
    payload = parse_json_object()
    tags = payload.get("tags")
    if not isinstance(tags, list):
        raise ApiError(422, "VALIDATION_ERROR", "tags 必须是数组")
    labels: dict[int, str] = {}
    for item in tags:
        if not isinstance(item, dict):
            raise ApiError(422, "VALIDATION_ERROR", "标签格式无效")
        slot = parse_int(item.get("slot"), field="slot")
        if slot not in (1, 2):
            raise ApiError(422, "VALIDATION_ERROR", "全局标签只能使用槽位 1、2")
        labels[slot] = clean_text(
            item.get("label"), field=f"tag{slot}", label=f"标签 {slot} 名称", maximum=40
        )
    if set(labels) != {1, 2}:
        raise ApiError(422, "VALIDATION_ERROR", "必须同时提交标签槽位 1、2")
    db = get_db()
    with transaction(db):
        actor = locked_actor(db, admin=True)
        db.executemany(
            """
            UPDATE global_tags SET label = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE slot = ?
            """,
            [(labels[slot], slot) for slot in (1, 2)],
        )
        write_security_audit(
            db,
            actor_user_id=actor["id"],
            action="tags.global_updated",
            target_type="tag",
            details={"slots": [1, 2]},
        )
    return jsonify(
        {
            "items": [
                {"id": f"tag-{slot}", "slot": slot, "label": labels[slot]}
                for slot in (1, 2)
            ]
        }
    )

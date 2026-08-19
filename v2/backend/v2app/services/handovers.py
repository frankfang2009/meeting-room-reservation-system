from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from ..common import clean_text, local_now, new_id
from ..db import get_db, transaction
from ..errors import ApiError
from ..security import locked_actor
from .audit import write_security_audit
from .reservations import _insert_event, _load_mutable_reservation, _snapshot, serialize_reservation


def _reservation_id_of(row: Any) -> str:
    keys = row.keys() if hasattr(row, "keys") else []
    return row["reservation_id"] if "reservation_id" in keys else row["id"]


def _request_row(db, request_id: str) -> Any:
    row = db.execute(
        """
        SELECT hr.id, hr.reservation_id, hr.from_user_id, hr.to_user_id,
               hr.expected_revision, hr.status, hr.created_at, hr.decided_at,
               r.*, u.display_name AS owner_display_name,
               f.display_name AS from_display_name,
               t.display_name AS to_display_name,
               r.status AS reservation_status
        FROM handover_requests hr
        JOIN reservations r ON r.id = hr.reservation_id
        JOIN users u ON u.id = r.owner_user_id
        JOIN users f ON f.id = hr.from_user_id
        JOIN users t ON t.id = hr.to_user_id
        WHERE hr.id = ?
        """,
        (request_id,),
    ).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "交接请求不存在")
    return row


def _target_user(db, to_user_id: str) -> Any:
    to_user_id = clean_text(
        to_user_id, field="toUserId", label="交接对象", maximum=64
    )
    row = db.execute(
        "SELECT id, display_name, role, is_active FROM users WHERE id = ?",
        (to_user_id,),
    ).fetchone()
    if row is None or not row["is_active"]:
        raise ApiError(422, "USER_UNAVAILABLE", "交接对象不存在或已停用")
    return row


def _started(row: Any) -> bool:
    starts_at = datetime.strptime(
        f"{row['booking_date']} {row['start_time']}", "%Y-%m-%d %H:%M"
    )
    return starts_at <= local_now().replace(tzinfo=None)


def load_handover_reservation(reservation_id: str, actor: dict[str, Any]) -> Any:
    """为人员选择器加载可交接的预约，复用写入路径的权限和状态边界。"""

    return _load_mutable_reservation(get_db(), reservation_id, actor)


def serialize_handover_request(row: Any) -> dict[str, Any]:
    reservation = serialize_reservation(row, None)
    # Joined handover rows expose both hr.id and r.id as ``id``. sqlite3.Row
    # keeps the first duplicate column, so the generic reservation serializer
    # otherwise leaks the handover-request id as the reservation id.
    reservation["id"] = _reservation_id_of(row)
    return {
        "id": row["id"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "decidedAt": row["decided_at"],
        "expectedRevision": row["expected_revision"],
        "fromUser": {"id": row["from_user_id"], "name": row["from_display_name"]},
        "toUser": {"id": row["to_user_id"], "name": row["to_display_name"]},
        "reservation": reservation,
    }


def _apply_handover(
    db: sqlite3.Connection,
    row: Any,
    to_user: Any,
    *,
    actor: dict[str, Any],
    request_id: str | None,
    action: str,
) -> dict[str, Any]:
    """在当前事务内翻转预约归属：乐观锁更新 + handover 事件 + 审计。"""

    before = _snapshot(row)
    updated = db.execute(
        """
        UPDATE reservations
        SET owner_user_id = ?, owner_name_snapshot = ?,
            revision = revision + 1,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
        WHERE id = ? AND revision = ?
        """,
        (to_user["id"], to_user["display_name"], _reservation_id_of(row), row["revision"]),
    )
    if updated.rowcount != 1:
        raise ApiError(
            409,
            "REVISION_CONFLICT",
            "预约内容已发生变化，交接未完成",
        )
    reservation_id = _reservation_id_of(row)
    new_row = db.execute(
        "SELECT * FROM reservations WHERE id = ?", (reservation_id,)
    ).fetchone()
    _insert_event(
        db,
        reservation_id=reservation_id,
        actor_id=actor["id"],
        event_type="handover",
        revision=new_row["revision"],
        before=before,
        after=_snapshot(new_row),
    )
    write_security_audit(
        db,
        actor_user_id=actor["id"],
        action=f"handover.{action}",
        target_type="reservation",
        target_id=reservation_id,
        details={
            "fromUserId": row["owner_user_id"],
            "toUserId": to_user["id"],
            "requestId": request_id,
            "revision": new_row["revision"],
        },
    )
    return serialize_reservation(new_row, actor)


def request_handover(reservation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    db = get_db()
    with transaction(db):
        actor = locked_actor(db)
        row = _load_mutable_reservation(db, reservation_id, actor)
        to_user = _target_user(db, payload.get("toUserId"))
        if to_user["id"] == row["owner_user_id"]:
            raise ApiError(422, "VALIDATION_ERROR", "不能交接给自己")

        # 管理员路径：直接指派、立即生效（兜底手段，不产生待确认请求）。
        if actor["role"] == "admin" and row["owner_user_id"] != actor["id"]:
            reservation = _apply_handover(
                db, row, to_user, actor=actor, request_id=None, action="assigned"
            )
            return {"assigned": True, "reservation": reservation}

        if actor["role"] != "admin" and row["owner_user_id"] != actor["id"]:
            raise ApiError(403, "FORBIDDEN", "无权交接他人的预约")
        existing = db.execute(
            """
            SELECT id FROM handover_requests
            WHERE reservation_id = ? AND status = 'pending'
            """,
            (reservation_id,),
        ).fetchone()
        if existing is not None:
            raise ApiError(409, "HANDOVER_REQUEST_EXISTS", "该预约已有待处理的交接请求")
        request_id = new_id()
        db.execute(
            """
            INSERT INTO handover_requests (
                id, reservation_id, from_user_id, to_user_id, expected_revision
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (request_id, reservation_id, actor["id"], to_user["id"], row["revision"]),
        )
        write_security_audit(
            db,
            actor_user_id=actor["id"],
            action="handover.requested",
            target_type="reservation",
            target_id=reservation_id,
            details={
                "fromUserId": actor["id"],
                "toUserId": to_user["id"],
                "requestId": request_id,
                "expectedRevision": row["revision"],
            },
        )
        row = _request_row(db, request_id)
        return {"assigned": False, "request": serialize_handover_request(row)}


def _expire_request(request_id: str) -> None:
    """在独立事务中把请求标记为过期；随后的 409 不会回滚这条状态。"""

    db = get_db()
    with transaction(db):
        locked_actor(db)
        db.execute(
            """
            UPDATE handover_requests
            SET status = 'expired',
                decided_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ? AND status = 'pending'
            """,
            (request_id,),
        )


def decide_handover(request_id: str, decision: str) -> dict[str, Any]:
    db = get_db()
    with transaction(db):
        actor = locked_actor(db)
        row = _request_row(db, request_id)
        if row["status"] != "pending":
            raise ApiError(409, "HANDOVER_REQUEST_CLOSED", "该交接请求已处理")
        if row["to_user_id"] != actor["id"]:
            raise ApiError(403, "FORBIDDEN", "只有被指定的接手人可以处理该交接请求")

        if decision == "decline":
            db.execute(
                """
                UPDATE handover_requests
                SET status = 'declined',
                    decided_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id = ?
                """,
                (request_id,),
            )
            write_security_audit(
                db,
                actor_user_id=actor["id"],
                action="handover.declined",
                target_type="reservation",
                target_id=row["reservation_id"],
                details={"fromUserId": row["from_user_id"], "requestId": request_id},
            )
            row = _request_row(db, request_id)
            return {"request": serialize_handover_request(row)}

        if row["reservation_status"] != "active" or _started(row):
            pass  # 预约已取消或已开始：请求过期作废，预约保持原归属。
        elif row["revision"] != row["expected_revision"]:
            pass  # 发起后预约被编辑过：请求作废，需要重新发起（乐观锁语义一致）。
        else:
            to_user = db.execute(
                "SELECT id, display_name FROM users WHERE id = ?",
                (row["to_user_id"],),
            ).fetchone()
            reservation = _apply_handover(
                db, row, to_user, actor=actor, request_id=request_id, action="accepted"
            )
            db.execute(
                """
                UPDATE handover_requests
                SET status = 'accepted',
                    decided_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id = ?
                """,
                (request_id,),
            )
            db.execute(
                """
                UPDATE handover_requests
                SET status = 'withdrawn',
                    decided_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE reservation_id = ? AND status = 'pending' AND id != ?
                """,
                (row["reservation_id"], request_id),
            )
            return {"reservation": reservation}

    # 到达这里说明预约不可交接：先把请求状态落为过期（独立事务），
    # 再按原因返回冲突；翻转归属的事务已经整体回滚，预约保持原归属。
    _expire_request(request_id)
    if row["revision"] != row["expected_revision"]:
        raise ApiError(
            409,
            "REVISION_CONFLICT",
            "预约内容已变化，该交接请求已作废，请让发起人重新发起",
        )
    raise ApiError(409, "HANDOVER_EXPIRED", "预约已开始或已取消，交接请求作废")


def withdraw_handover(request_id: str) -> dict[str, Any]:
    db = get_db()
    with transaction(db):
        actor = locked_actor(db)
        row = _request_row(db, request_id)
        if row["status"] != "pending":
            raise ApiError(409, "HANDOVER_REQUEST_CLOSED", "该交接请求已处理")
        if row["from_user_id"] != actor["id"] and actor["role"] != "admin":
            raise ApiError(403, "FORBIDDEN", "只有发起人或管理员可以撤回该交接请求")
        db.execute(
            """
            UPDATE handover_requests
            SET status = 'withdrawn',
                decided_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ?
            """,
            (request_id,),
        )
        write_security_audit(
            db,
            actor_user_id=actor["id"],
            action="handover.withdrawn",
            target_type="reservation",
            target_id=row["reservation_id"],
            details={"requestId": request_id},
        )
        row = _request_row(db, request_id)
        return {"request": serialize_handover_request(row)}


def _pending_scope_clause() -> str:
    # 只按读时条件过滤（active 且未开始）；不做 GET 内写回，避免被动轮询推进数据序号。
    return "hr.status = 'pending' AND r.status = 'active' AND r.booking_date || ' ' || r.start_time > :now"


def list_my_handovers() -> dict[str, Any]:
    db = get_db()
    actor = locked_actor(db)
    now = local_now().replace(tzinfo=None).strftime("%Y-%m-%d %H:%M")
    query = """
        SELECT hr.*, r.*, u.display_name AS owner_display_name,
               f.display_name AS from_display_name,
               t.display_name AS to_display_name
        FROM handover_requests hr
        JOIN reservations r ON r.id = hr.reservation_id
        JOIN users u ON u.id = r.owner_user_id
        JOIN users f ON f.id = hr.from_user_id
        JOIN users t ON t.id = hr.to_user_id
        WHERE {scope}
        ORDER BY hr.created_at, hr.rowid
    """
    incoming = db.execute(
        query.format(scope="hr.to_user_id = :user AND " + _pending_scope_clause()),
        {"user": actor["id"], "now": now},
    ).fetchall()
    outgoing = db.execute(
        query.format(scope="hr.from_user_id = :user AND " + _pending_scope_clause()),
        {"user": actor["id"], "now": now},
    ).fetchall()
    return {
        "incoming": [serialize_handover_request(row) for row in incoming],
        "outgoing": [serialize_handover_request(row) for row in outgoing],
    }

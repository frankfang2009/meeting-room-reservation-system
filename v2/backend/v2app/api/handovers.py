from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..common import parse_json_object
from ..db import get_db
from ..security import current_user, login_required
from ..services import handovers


bp = Blueprint("handover_api", __name__, url_prefix="/api/v1")


@bp.post("/reservations/<reservation_id>/handover")
@login_required
def create_handover(reservation_id: str):
    return jsonify(handovers.request_handover(reservation_id, parse_json_object()))


@bp.post("/handover-requests/<request_id>/accept")
@login_required
def accept_handover(request_id: str):
    return jsonify(handovers.decide_handover(request_id, "accept"))


@bp.post("/handover-requests/<request_id>/decline")
@login_required
def decline_handover(request_id: str):
    return jsonify(handovers.decide_handover(request_id, "decline"))


@bp.delete("/handover-requests/<request_id>")
@login_required
def withdraw_handover(request_id: str):
    return jsonify(handovers.withdraw_handover(request_id))


@bp.get("/handover-requests")
@login_required
def list_handovers():
    return jsonify(handovers.list_my_handovers())


@bp.get("/users/directory")
@login_required
def user_directory():
    """交接选择器的最小员工投影：任何已登录用户可读，不含用户名等账号信息。"""

    db = get_db()
    actor = current_user()
    reservation_id = request.args.get("reservationId", "").strip()
    excluded_user_id = actor["id"]
    if reservation_id:
        reservation = handovers.load_handover_reservation(reservation_id, actor)
        # 候选人的语义是“新的预约者”：排除当前预约者，而不是排除操作人。
        # 因此管理员处理他人的预约时，管理员自己是合法候选人。
        excluded_user_id = reservation["owner_user_id"]
    rows = db.execute(
        """
        SELECT id, display_name, department
        FROM users
        WHERE is_active = 1
        ORDER BY display_name, id
        """
    ).fetchall()
    return jsonify(
        {
            "users": [
                {
                    "id": row["id"],
                    "name": row["display_name"],
                    "department": row["department"],
                }
                for row in rows
                if row["id"] != excluded_user_id
            ]
        }
    )

"""一句话预约的会话入口。列表、重命名和删除不在本增量。"""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..common import parse_json_object
from ..security import login_required
from ..services import quick_booking


bp = Blueprint("conversation_api", __name__, url_prefix="/api/v1/conversations")


@bp.post("/<conversation_id>/messages")
@login_required
def post_message(conversation_id: str):
    body = parse_json_object()
    return jsonify(quick_booking.send_message(conversation_id, body.get("text")))

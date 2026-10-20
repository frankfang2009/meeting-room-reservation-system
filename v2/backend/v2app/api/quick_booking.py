"""快速预约的准备与确认。准备不调用模型；确认复用创建预约事务。"""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..common import parse_json_object
from ..security import login_required
from ..services import quick_booking


bp = Blueprint("quick_booking_api", __name__, url_prefix="/api/v1/quick-booking")


@bp.post("/prepare")
@login_required
def prepare():
    return jsonify(quick_booking.prepare_booking(parse_json_object()))


@bp.post("/commit")
@login_required
def commit():
    return jsonify(quick_booking.commit_booking(parse_json_object())), 201

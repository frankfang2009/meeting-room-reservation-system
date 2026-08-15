from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..common import parse_json_object
from ..security import login_required
from ..services import reservations as service


bp = Blueprint("reservation_api", __name__, url_prefix="/api/v1/reservations")


@bp.get("")
@login_required
def list_bookings():
    date_from = request.args.get("dateFrom") or request.args.get("date")
    date_to = request.args.get("dateTo") or date_from
    if not date_from:
        from ..common import local_now

        date_from = date_to = local_now().date().isoformat()
    return jsonify(
        service.list_reservations(date_from, date_to, request.args.to_dict())
    )


@bp.post("")
@login_required
def create_booking():
    return jsonify(service.create_reservation(parse_json_object())), 201


@bp.get("/upcoming")
@login_required
def upcoming_bookings():
    return jsonify({"items": service.list_upcoming()})


@bp.get("/history")
@login_required
def booking_history():
    return jsonify(service.list_history(request.args.to_dict()))


@bp.get("/<reservation_id>")
@login_required
def read_booking(reservation_id: str):
    return jsonify(service.get_reservation(reservation_id))


@bp.patch("/<reservation_id>")
@login_required
def update_booking(reservation_id: str):
    return jsonify(service.update_reservation(reservation_id, parse_json_object()))


@bp.post("/<reservation_id>/cancel")
@login_required
def cancel_booking(reservation_id: str):
    return jsonify(service.cancel_reservation(reservation_id, parse_json_object()))


@bp.get("/<reservation_id>/events")
@login_required
def booking_events(reservation_id: str):
    return jsonify({"items": service.list_events(reservation_id)})

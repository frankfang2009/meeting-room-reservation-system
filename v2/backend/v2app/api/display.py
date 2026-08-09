from __future__ import annotations

from flask import Blueprint, jsonify

from ..common import local_now, mask_party_name, remote_is_private_or_loopback
from ..db import get_db, is_setup_complete
from ..errors import ApiError


bp = Blueprint("display_api", __name__, url_prefix="/api/v1/display")


def _public_period(row):
    if row is None:
        return None
    return {
        "maskedPartyName": mask_party_name(row["party_name"]),
        "start": row["start_time"],
        "end": row["end_time"],
    }


@bp.get("/today")
def today_display():
    if not remote_is_private_or_loopback():
        raise ApiError(403, "LAN_ONLY", "公开大屏仅允许局域网访问")
    if not is_setup_complete():
        raise ApiError(503, "SETUP_REQUIRED", "系统尚未完成首次设置")
    db = get_db()
    now = local_now()
    today = now.date().isoformat()
    current_time = now.strftime("%H:%M")
    rooms = []
    for room in db.execute(
        "SELECT id, name FROM rooms WHERE is_active = 1 ORDER BY sort_order, name"
    ).fetchall():
        rows = db.execute(
            """
            SELECT party_name, start_time, end_time
            FROM reservations
            WHERE room_id = ? AND booking_date = ? AND status = 'active'
            ORDER BY start_time
            """,
            (room["id"], today),
        ).fetchall()
        current = next(
            (row for row in rows if row["start_time"] <= current_time < row["end_time"]),
            None,
        )
        following = next((row for row in rows if row["start_time"] > current_time), None)
        rooms.append(
            {
                "id": room["id"],
                "name": room["name"],
                "current": _public_period(current),
                "next": _public_period(following),
            }
        )
    payload = {
        "serverDate": today,
        "serverTime": current_time,
        "lastUpdatedAt": now.isoformat(timespec="seconds"),
        "status": "online",
        "rooms": rooms,
    }
    return jsonify(payload)

from __future__ import annotations

from datetime import date

from flask import Blueprint, jsonify

from ..common import local_now, parse_date
from ..db import get_db
from ..security import current_user, login_required
from ..services.reservations import serialize_reservation


bp = Blueprint("activity_api", __name__, url_prefix="/api/v1/activity")


def _month_start(value: date, offset: int = 0) -> date:
    month_index = value.year * 12 + value.month - 1 + offset
    year, zero_based_month = divmod(month_index, 12)
    return date(year, zero_based_month + 1, 1)


def _completed_clause() -> str:
    return """
        owner_user_id = ? AND status = 'active'
        AND (booking_date < ? OR (booking_date = ? AND end_time <= ?))
    """


def _duration_sql() -> str:
    return """
        (
            CAST(substr(end_time, 1, 2) AS INTEGER) * 60
            + CAST(substr(end_time, 4, 2) AS INTEGER)
            - CAST(substr(start_time, 1, 2) AS INTEGER) * 60
            - CAST(substr(start_time, 4, 2) AS INTEGER)
        )
    """


@bp.get("/days/<day_value>")
@login_required
def read_activity_day(day_value: str):
    actor = current_user()
    db = get_db()
    selected_day = parse_date(day_value)
    now = local_now()
    today_key = now.date().isoformat()
    current_time = now.strftime("%H:%M")
    rows = db.execute(
        f"""
        SELECT *
        FROM reservations
        WHERE {_completed_clause()} AND booking_date = ?
        ORDER BY start_time, end_time, id
        """,
        (actor["id"], today_key, today_key, current_time, selected_day),
    ).fetchall()
    return jsonify(
        {
            "date": selected_day,
            "items": [serialize_reservation(row, actor) for row in rows],
        }
    )


@bp.get("")
@login_required
def read_activity():
    actor = current_user()
    db = get_db()
    now = local_now()
    today = now.date()
    today_key = today.isoformat()
    current_time = now.strftime("%H:%M")
    range_start = _month_start(today, -11)
    current_month_start = _month_start(today)
    completed_parameters = (actor["id"], today_key, today_key, current_time)

    summary = db.execute(
        f"""
        SELECT COUNT(*) AS total_completed,
               COALESCE(SUM({_duration_sql()}), 0) AS total_duration_minutes,
               COUNT(DISTINCT booking_date) AS active_days,
               SUM(CASE WHEN booking_date >= ? THEN 1 ELSE 0 END)
                   AS current_month_completed
        FROM reservations
        WHERE {_completed_clause()}
        """,
        (current_month_start.isoformat(), *completed_parameters),
    ).fetchone()

    favorite_room = db.execute(
        f"""
        SELECT room_name_snapshot AS label, COUNT(*) AS use_count
        FROM reservations
        WHERE {_completed_clause()}
        GROUP BY room_name_snapshot
        ORDER BY use_count DESC,
                 MAX(booking_date || 'T' || end_time) DESC,
                 room_name_snapshot
        LIMIT 1
        """,
        completed_parameters,
    ).fetchone()
    favorite_tag = db.execute(
        f"""
        SELECT tag_label_snapshot AS label, COUNT(*) AS use_count
        FROM reservations
        WHERE {_completed_clause()}
        GROUP BY tag_label_snapshot
        ORDER BY use_count DESC,
                 MAX(booking_date || 'T' || end_time) DESC,
                 tag_label_snapshot
        LIMIT 1
        """,
        completed_parameters,
    ).fetchone()
    daily_rows = db.execute(
        f"""
        SELECT booking_date, COUNT(*) AS completed
        FROM reservations
        WHERE {_completed_clause()} AND booking_date >= ?
        GROUP BY booking_date
        ORDER BY booking_date
        """,
        (*completed_parameters, range_start.isoformat()),
    ).fetchall()

    total_completed = int(summary["total_completed"] or 0)
    total_duration = int(summary["total_duration_minutes"] or 0)
    average_duration = round(total_duration / total_completed) if total_completed else 0
    return jsonify(
        {
            "range": {
                "start": range_start.isoformat(),
                "end": today_key,
            },
            "summary": {
                "currentMonthCompleted": int(summary["current_month_completed"] or 0),
                "totalCompleted": total_completed,
                "totalDurationMinutes": total_duration,
                "activeDays": int(summary["active_days"] or 0),
            },
            "overview": {
                "averageDurationMinutes": average_duration,
                "favoriteRoom": favorite_room["label"] if favorite_room else None,
                "favoriteTag": favorite_tag["label"] if favorite_tag else None,
            },
            "days": [
                {"date": row["booking_date"], "completed": int(row["completed"])}
                for row in daily_rows
            ],
        }
    )

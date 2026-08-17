from __future__ import annotations

from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request

from ..common import local_now
from ..db import get_db, transaction
from ..errors import ApiError
from ..security import current_user, login_required
from ..services.audit import write_security_audit
from ..services.reporting import (
    CSV_FIELD_VERSION,
    csv_rows,
    get_report_overview,
    parse_export_status,
    parse_report_filters,
    render_csv,
    resolve_report_scope,
)


bp = Blueprint("report_api", __name__, url_prefix="/api/v1/reports")


def _request_context():
    args = request.args.to_dict()
    scope = resolve_report_scope(args.get("scope"), args.get("ownerId"))
    filters = parse_report_filters(args, scope)
    return args, scope, filters


def _audit_details(scope, filters, *, status, result, row_count, error_code=None):
    return {
        "result": result,
        "scope": scope.kind,
        "ownerId": scope.owner_id,
        "dateFrom": filters.date_from.isoformat(),
        "dateTo": filters.date_to.isoformat(),
        "roomId": filters.room_id,
        "tagSlot": filters.tag_slot,
        "queryApplied": bool(filters.query),
        "status": status,
        "fieldVersion": CSV_FIELD_VERSION,
        "rowCount": row_count,
        "errorCode": error_code,
    }


def _write_export_audit(scope, filters, *, status, result, row_count, error_code=None):
    actor = current_user()
    with transaction(get_db(), track_change=False):
        write_security_audit(
            get_db(),
            actor_user_id=actor["id"],
            action="report.csv_exported",
            target_type="report",
            target_id=scope.owner_id or "overall",
            details=_audit_details(
                scope,
                filters,
                status=status,
                result=result,
                row_count=row_count,
                error_code=error_code,
            ),
        )


@bp.get("/overview")
@login_required
def report_overview():
    _args, scope, filters = _request_context()
    return jsonify(get_report_overview(scope, filters))


@bp.get("/reservations.csv")
@login_required
def export_reservations_csv():
    args, scope, filters = _request_context()
    status = parse_export_status(args.get("status"))
    try:
        rows = csv_rows(scope, filters, status)
    except ApiError as error:
        if error.code == "EXPORT_TOO_LARGE":
            _write_export_audit(
                scope,
                filters,
                status=status,
                result="failed",
                row_count=None,
                error_code=error.code,
            )
        raise
    content = render_csv(rows)
    _write_export_audit(
        scope,
        filters,
        status=status,
        result="succeeded",
        row_count=len(rows),
    )
    now = local_now()
    chinese_name = (
        f"办件明细_{filters.date_from:%Y%m%d}-{filters.date_to:%Y%m%d}_"
        f"{now:%Y%m%d-%H%M%S}.csv"
    )
    response = Response(content, mimetype="text/csv")
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        "attachment; filename=reservation-details.csv; "
        f"filename*=UTF-8''{quote(chinese_name)}"
    )
    response.headers["X-Report-Field-Version"] = CSV_FIELD_VERSION
    response.headers["X-Report-Row-Count"] = str(len(rows))
    return response


__all__ = ["bp"]

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from ..common import local_now, parse_date, time_to_minutes
from ..db import get_db
from ..errors import ApiError
from ..security import current_user


PERSONAL_METRIC_VERSION = "personal-service-v1"
OVERALL_METRIC_VERSION = "overall-service-v1"
CSV_FIELD_VERSION = "reservation-detail-v1"
CSV_EXPORT_LIMIT = 20_000
WEEKDAY_LABELS = {
    1: "周一",
    2: "周二",
    3: "周三",
    4: "周四",
    5: "周五",
    6: "周六",
    7: "周日",
}


@dataclass(frozen=True)
class ReportScope:
    kind: str
    owner_id: Optional[str]
    owner_name: Optional[str]
    owner_department: Optional[str]
    owner_enabled: Optional[bool]

    @property
    def personal(self) -> bool:
        return self.kind in {"self", "person"}

    def serialize(self) -> dict[str, Any]:
        owner = None
        if self.owner_id:
            owner = {
                "id": self.owner_id,
                "name": self.owner_name,
                "department": self.owner_department,
                "enabled": self.owner_enabled,
            }
        return {"kind": self.kind, "owner": owner}


@dataclass(frozen=True)
class ReportFilters:
    date_from: date
    date_to: date
    room_id: Optional[str]
    room_name: Optional[str]
    tag_slot: Optional[int]
    query: str

    def serialize(self) -> dict[str, Any]:
        return {
            "dateFrom": self.date_from.isoformat(),
            "dateTo": self.date_to.isoformat(),
            "roomId": self.room_id,
            "roomName": self.room_name,
            "tagId": f"tag-{self.tag_slot}" if self.tag_slot else None,
            "query": self.query,
        }


def _user_scope(user: Mapping[str, Any], kind: str) -> ReportScope:
    return ReportScope(
        kind=kind,
        owner_id=str(user["id"]),
        owner_name=str(user["display_name"]),
        owner_department=str(user["department"]),
        owner_enabled=bool(user["is_active"]),
    )


def resolve_report_scope(
    requested_scope: Any = None,
    owner_id: Any = None,
) -> ReportScope:
    actor = current_user()
    role = actor["role"]
    requested = str(requested_scope or ("overall" if role == "admin" else "self")).strip()
    requested_owner = str(owner_id or "").strip()

    if role == "employee":
        if requested != "self" or requested_owner:
            raise ApiError(403, "FORBIDDEN", "无权查看其他人员的数据")
        return _user_scope(actor, "self")

    if requested == "overall":
        if requested_owner:
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "全单位视角不能指定人员",
                fields={"ownerId": "请先切换到人员视角"},
            )
        return ReportScope("overall", None, None, None, None)
    if requested == "self":
        if requested_owner:
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "本人视角不能指定其他人员",
                fields={"ownerId": "本人视角不需要人员参数"},
            )
        return _user_scope(actor, "self")
    if requested != "person":
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "数据视角无效",
            fields={"scope": "请选择全单位、本人或人员视角"},
        )
    if not requested_owner or len(requested_owner) > 64:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请选择人员",
            fields={"ownerId": "请选择要查看的人员"},
        )
    owner = get_db().execute(
        "SELECT * FROM users WHERE id = ?",
        (requested_owner,),
    ).fetchone()
    if owner is None:
        raise ApiError(404, "NOT_FOUND", "人员不存在")
    return _user_scope(owner, "person")


def _tag_slot(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    match = re.fullmatch(r"(?:tag-)?([1-4])", str(value).strip())
    if not match:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "标签无效",
            fields={"tagId": "请选择有效标签"},
        )
    return int(match.group(1))


def parse_report_filters(args: Mapping[str, Any], scope: ReportScope) -> ReportFilters:
    now = local_now()
    default_from = now.date().replace(day=1).isoformat()
    start = date.fromisoformat(parse_date(args.get("dateFrom") or default_from, field="dateFrom"))
    end = date.fromisoformat(parse_date(args.get("dateTo") or now.date().isoformat(), field="dateTo"))
    if end < start or (end - start).days > 365:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "日期范围无效或超过 366 天",
            fields={"dateTo": "结束日期应不早于开始日期，且跨度不超过 366 天"},
        )
    if end >= date(9999, 12, 1):
        # 与预约历史的月份上界同一稳定线：月桶/周桶的下一界计算不得越过 date.max。
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "日期超出系统可安全查询的范围",
            fields={"dateTo": "最大可查询日期为 9999-11-30"},
        )

    room_id = str(args.get("roomId") or "").strip() or None
    room_name = None
    if room_id:
        if len(room_id) > 64:
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "笔录室无效",
                fields={"roomId": "请选择有效笔录室"},
            )
        room = get_db().execute("SELECT name FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if room is None:
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "笔录室无效",
                fields={"roomId": "请选择有效笔录室"},
            )
        room_name = str(room["name"])

    tag_slot = _tag_slot(args.get("tagId"))
    if scope.kind == "overall" and tag_slot in {3, 4}:
        raise ApiError(
            422,
            "PERSONAL_TAG_OWNER_REQUIRED",
            "全单位视角不能汇总个人标签",
            fields={"tagId": "请选择单位标签，或先切换到人员视角"},
        )

    query = str(args.get("query") or "").strip()
    if len(query) > 120:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "搜索内容过长",
            fields={"query": "搜索内容不能超过 120 个字符"},
        )
    return ReportFilters(start, end, room_id, room_name, tag_slot, query)


def parse_export_status(value: Any) -> Optional[str]:
    status = str(value or "").strip() or None
    if status and status not in {"active", "cancelled"}:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "预约状态无效",
            fields={"status": "请选择有效或已取消"},
        )
    return status


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_reservation_base_query(
    scope: ReportScope,
    filters: ReportFilters,
    *,
    status: Optional[str] = None,
) -> tuple[str, list[Any]]:
    clauses = ["r.booking_date BETWEEN ? AND ?"]
    params: list[Any] = [filters.date_from.isoformat(), filters.date_to.isoformat()]
    if scope.owner_id:
        clauses.append("r.owner_user_id = ?")
        params.append(scope.owner_id)
    if filters.room_id:
        clauses.append("r.room_id = ?")
        params.append(filters.room_id)
    if filters.tag_slot:
        clauses.append("r.tag_slot = ?")
        params.append(filters.tag_slot)
    if filters.query:
        pattern = f"%{_escape_like(filters.query)}%"
        clauses.append(
            "(r.party_name LIKE ? ESCAPE '\\' "
            "OR r.case_number LIKE ? ESCAPE '\\' "
            "OR r.purpose LIKE ? ESCAPE '\\' "
            "OR r.notes LIKE ? ESCAPE '\\')"
        )
        params.extend([pattern, pattern, pattern, pattern])
    if status:
        clauses.append("r.status = ?")
        params.append(status)
    return " AND ".join(clauses), params


def _duration_minutes(row: Mapping[str, Any]) -> int:
    return time_to_minutes(row["end_time"], field="end") - time_to_minutes(
        row["start_time"], field="start"
    )


def _rows(
    scope: ReportScope,
    filters: ReportFilters,
    *,
    status: Optional[str] = None,
    limit: Optional[int] = None,
):
    where, params = build_reservation_base_query(scope, filters, status=status)
    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT ?"
        params.append(limit)
    return get_db().execute(
        f"""
        SELECT r.*, u.display_name AS current_owner_name,
               u.department AS current_owner_department,
               u.is_active AS current_owner_enabled
        FROM reservations r
        JOIN users u ON u.id = r.owner_user_id
        WHERE {where}
        ORDER BY r.booking_date, r.start_time, r.room_name_snapshot,
                 r.created_at, r.id
        {limit_clause}
        """,
        params,
    ).fetchall()


def _week_start(value: date) -> date:
    return value.fromordinal(value.toordinal() - value.weekday())


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _next_month(value: date) -> date:
    return date(
        value.year + (1 if value.month == 12 else 0),
        1 if value.month == 12 else value.month + 1,
        1,
    )


def _serialize_weekly(
    rows: Iterable[Mapping[str, Any]],
    filters: ReportFilters,
) -> list[dict[str, Any]]:
    first_week = _week_start(filters.date_from)
    last_week = _week_start(filters.date_to)
    buckets: dict[str, dict[str, Any]] = {}
    cursor = first_week
    while cursor <= last_week:
        key = cursor.isoformat()
        buckets[key] = {
            "weekStart": key,
            "activeCount": 0,
            "activeDurationMinutes": 0,
        }
        cursor = date.fromordinal(cursor.toordinal() + 7)
    for row in rows:
        if row["status"] != "active":
            continue
        start = _week_start(date.fromisoformat(row["booking_date"]))
        key = start.isoformat()
        bucket = buckets.setdefault(
            key,
            {"weekStart": key, "activeCount": 0, "activeDurationMinutes": 0},
        )
        bucket["activeCount"] += 1
        bucket["activeDurationMinutes"] += _duration_minutes(row)
    return [buckets[key] for key in sorted(buckets)]


def _serialize_monthly(
    rows: Iterable[Mapping[str, Any]],
    filters: ReportFilters,
) -> list[dict[str, Any]]:
    first_month = _month_start(filters.date_from)
    last_month = _month_start(filters.date_to)
    buckets: dict[str, dict[str, Any]] = {}
    cursor = first_month
    while cursor <= last_month:
        key = cursor.isoformat()
        buckets[key] = {
            "monthStart": key,
            "activeCount": 0,
            "activeDurationMinutes": 0,
        }
        cursor = _next_month(cursor)
    for row in rows:
        if row["status"] != "active":
            continue
        start = _month_start(date.fromisoformat(row["booking_date"]))
        key = start.isoformat()
        bucket = buckets.setdefault(
            key,
            {"monthStart": key, "activeCount": 0, "activeDurationMinutes": 0},
        )
        bucket["activeCount"] += 1
        bucket["activeDurationMinutes"] += _duration_minutes(row)
    return [buckets[key] for key in sorted(buckets)]


def _serialize_time_distribution(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[int, str], int] = {}
    for row in rows:
        if row["status"] != "active":
            continue
        weekday = date.fromisoformat(row["booking_date"]).isoweekday()
        start = time_to_minutes(row["start_time"], field="start")
        end = time_to_minutes(row["end_time"], field="end")
        for minute in range(start, end, 30):
            slot = f"{minute // 60:02d}:{minute % 60:02d}"
            buckets[(weekday, slot)] = buckets.get((weekday, slot), 0) + 1
    return [
        {
            "weekday": weekday,
            "weekdayLabel": WEEKDAY_LABELS[weekday],
            "slot": slot,
            "count": buckets[(weekday, slot)],
        }
        for weekday, slot in sorted(buckets)
    ]


def _serialize_personal_tags(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        if row["status"] != "active":
            continue
        slot = int(row["tag_slot"])
        label = str(row["tag_label_snapshot"] or f"标签 {slot}")
        key = (slot, label)
        bucket = buckets.setdefault(
            key,
            {"tagId": f"tag-{slot}", "label": label, "activeCount": 0, "activeDurationMinutes": 0},
        )
        bucket["activeCount"] += 1
        bucket["activeDurationMinutes"] += _duration_minutes(row)
    return sorted(
        buckets.values(),
        key=lambda item: (-item["activeCount"], item["tagId"], item["label"]),
    )


def _serialize_person_workload(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    db = get_db()
    buckets: dict[str, dict[str, Any]] = {
        str(user["id"]): {
            "ownerId": str(user["id"]),
            "name": str(user["display_name"]),
            "department": str(user["department"]),
            "enabled": bool(user["is_active"]),
            "activeCount": 0,
            "activeDurationMinutes": 0,
        }
        for user in db.execute(
            "SELECT id, display_name, department, is_active FROM users ORDER BY display_name, username"
        ).fetchall()
        if bool(user["is_active"])
    }
    for row in rows:
        if row["status"] != "active":
            continue
        owner_id = str(row["owner_user_id"])
        bucket = buckets.setdefault(
            owner_id,
            {
                "ownerId": owner_id,
                "name": str(row["current_owner_name"]),
                "department": str(row["current_owner_department"]),
                "enabled": bool(row["current_owner_enabled"]),
                "activeCount": 0,
                "activeDurationMinutes": 0,
            },
        )
        bucket["activeCount"] += 1
        bucket["activeDurationMinutes"] += _duration_minutes(row)
    return sorted(buckets.values(), key=lambda item: (item["name"], item["ownerId"]))


def _serialize_room_workload(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row["status"] != "active":
            continue
        key = (str(row["room_id"]), str(row["room_name_snapshot"]))
        bucket = buckets.setdefault(
            key,
            {
                "roomId": key[0],
                "label": key[1],
                "activeCount": 0,
                "activeDurationMinutes": 0,
            },
        )
        bucket["activeCount"] += 1
        bucket["activeDurationMinutes"] += _duration_minutes(row)
    return sorted(
        buckets.values(),
        key=lambda item: (-item["activeDurationMinutes"], item["label"], item["roomId"]),
    )


def _serialize_global_tags(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[Optional[int], str], dict[str, Any]] = {}
    for row in rows:
        if row["status"] != "active":
            continue
        slot = int(row["tag_slot"])
        if slot in {1, 2}:
            label = str(row["tag_label_snapshot"] or f"标签 {slot}")
            key: tuple[Optional[int], str] = (slot, label)
            tag_id = f"tag-{slot}"
        else:
            key = (None, "未使用单位标签")
            label = key[1]
            tag_id = None
        bucket = buckets.setdefault(
            key,
            {"tagId": tag_id, "label": label, "activeCount": 0, "activeDurationMinutes": 0},
        )
        bucket["activeCount"] += 1
        bucket["activeDurationMinutes"] += _duration_minutes(row)
    return sorted(
        buckets.values(),
        key=lambda item: (-item["activeCount"], item["tagId"] or "tag-z", item["label"]),
    )


def get_report_overview(scope: ReportScope, filters: ReportFilters) -> dict[str, Any]:
    rows = list(_rows(scope, filters))
    now = local_now().replace(tzinfo=None)
    active_rows = [row for row in rows if row["status"] == "active"]
    cancelled_count = sum(row["status"] == "cancelled" for row in rows)
    active_count = len(active_rows)
    total_count = active_count + cancelled_count
    ended_count = sum(
        datetime.strptime(
            f"{row['booking_date']} {row['end_time']}", "%Y-%m-%d %H:%M"
        )
        <= now
        for row in active_rows
    )
    duration = sum(_duration_minutes(row) for row in active_rows)
    payload: dict[str, Any] = {
        "resolvedScope": scope.serialize(),
        "filters": filters.serialize(),
        "metricVersion": PERSONAL_METRIC_VERSION if scope.personal else OVERALL_METRIC_VERSION,
        "generatedAtUtc": local_now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "summary": {
            "activeCount": active_count,
            "endedCount": ended_count,
            "activeDurationMinutes": duration,
            "cancelledCount": cancelled_count,
            "cancellationRate": round(cancelled_count / total_count, 4) if total_count else None,
        },
        "weeklyTrend": _serialize_weekly(rows, filters),
        "monthlyTrend": _serialize_monthly(rows, filters),
        "weekdayTimeDistribution": _serialize_time_distribution(rows),
        "exportRowCount": total_count,
    }
    if scope.personal:
        payload["tagDistribution"] = _serialize_personal_tags(rows)
    else:
        payload.update(
            {
                "personWorkload": _serialize_person_workload(rows),
                "roomWorkload": _serialize_room_workload(rows),
                "globalTagDistribution": _serialize_global_tags(rows),
            }
        )
    return payload


CSV_HEADERS = (
    "预约日期",
    "开始时间",
    "结束时间",
    "预约时长（分钟）",
    "笔录室",
    "预约人",
    "当事人",
    "案号",
    "事项",
    "标签",
    "备注",
    "状态",
    "创建时间",
    "最后修改时间",
    "取消时间",
)


def csv_safe_cell(value: Any) -> Any:
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    if re.match(r"^\s*[=+\-@]", value):
        return "'" + value
    return value


def csv_rows(scope: ReportScope, filters: ReportFilters, status: Optional[str]):
    # 先以 LIMIT+1 在数据库侧封顶；超限时不会把全量行拉进内存。
    rows = list(_rows(scope, filters, status=status, limit=CSV_EXPORT_LIMIT + 1))
    if len(rows) > CSV_EXPORT_LIMIT:
        raise ApiError(
            422,
            "EXPORT_TOO_LARGE",
            "导出记录超过 20,000 行，请缩小日期范围或增加筛选条件",
        )
    return rows


def render_csv(rows: Iterable[Mapping[str, Any]]) -> bytes:
    target = io.StringIO(newline="")
    writer = csv.writer(target, lineterminator="\r\n")
    writer.writerow(CSV_HEADERS)
    for row in rows:
        values = (
            row["booking_date"],
            row["start_time"],
            row["end_time"],
            _duration_minutes(row),
            row["room_name_snapshot"],
            row["owner_name_snapshot"],
            row["party_name"],
            row["case_number"],
            row["purpose"],
            row["tag_label_snapshot"],
            row["notes"],
            "有效" if row["status"] == "active" else "已取消",
            row["created_at"],
            row["updated_at"],
            row["cancelled_at"],
        )
        writer.writerow([csv_safe_cell(value) for value in values])
    return b"\xef\xbb\xbf" + target.getvalue().encode("utf-8")


__all__ = [
    "CSV_EXPORT_LIMIT",
    "CSV_FIELD_VERSION",
    "OVERALL_METRIC_VERSION",
    "PERSONAL_METRIC_VERSION",
    "ReportFilters",
    "ReportScope",
    "build_reservation_base_query",
    "csv_rows",
    "csv_safe_cell",
    "get_report_overview",
    "parse_export_status",
    "parse_report_filters",
    "render_csv",
    "resolve_report_scope",
]

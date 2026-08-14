from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, current_app, jsonify, request

from ..backup import (
    create_backup,
    latest_backup_record,
    maintenance_lock,
    reserve_backup_sequence,
)
from ..common import (
    clean_text,
    decode_cursor,
    encode_cursor,
    local_now,
    new_id,
    parse_json_object,
    parse_page_size,
)
from ..db import (
    PRODUCT_GENERATION,
    SCHEMA_VERSION,
    database_health,
    get_db,
    is_setup_complete,
    transaction,
)
from ..errors import ApiError
from ..security import admin_required, current_user, locked_actor
from ..services.audit import write_security_audit


bp = Blueprint("system_api", __name__, url_prefix="/api/v1")

TOKEN_SCOPES = {"rooms:read", "availability:read", "health:read"}


def _parse_zoned_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("timestamp must contain date and time")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed


def _utc_timestamp(value: Any) -> str:
    parsed = _parse_zoned_timestamp(value).astimezone(timezone.utc)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _backup_dir() -> Path:
    return Path(current_app.config["BACKUP_DIR"])


def _token_item(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "prefix": row["token_prefix"],
        "scopes": json.loads(row["scopes"]),
        "createdAt": row["created_at"],
        "expiresAt": row["expires_at"],
        "revokedAt": row["revoked_at"],
        "lastUsedAt": row["last_used_at"],
    }


@bp.get("/admin/system")
@admin_required
def system_status():
    db = get_db()
    health = database_health(db)
    data_sequence_row = db.execute(
        "SELECT value FROM app_meta WHERE key = 'data_sequence'"
    ).fetchone()
    data_sequence = int(data_sequence_row[0]) if data_sequence_row else 0
    install_id = current_app.config["INSTALL_ID"]
    backup = latest_backup_record(
        _backup_dir(), expected_install_id=install_id, verify_hash=True
    )
    caught_up = bool(
        backup and backup[1]["sourceDataSequence"] == data_sequence
    )
    checked_at = local_now().isoformat(timespec="seconds")
    healthy = health["quickCheckOk"] and health["foreignKeysOk"]
    status = "normal" if healthy and caught_up else "warning" if healthy else "critical"
    label = (
        "系统运行正常"
        if status == "normal"
        else "系统可用，但备份待处理"
        if status == "warning"
        else "数据库健康检查失败"
    )
    return jsonify(
        {
            "status": status,
            "label": label,
            "lastCheckedAt": checked_at,
            "productVersion": current_app.config["PRODUCT_VERSION"],
            "productGeneration": PRODUCT_GENERATION,
            "databaseVersion": SCHEMA_VERSION,
            "setupComplete": is_setup_complete(),
            "lanAddress": current_app.config.get("LAN_ADDRESS"),
            "lastBackupAt": (
                backup[1]["createdAtUtc"]
                if backup else None
            ),
            "backupCaughtUp": caught_up,
            "backupSequence": backup[1]["sequence"] if backup else None,
            "dataSequence": data_sequence,
            "servicePort": current_app.config["SERVICE_PORT"],
            "bindMode": "lan" if is_setup_complete(db) else "loopback",
            "apiStatus": "online",
            "displayStatus": "online",
            "health": (
                "healthy"
                if status == "normal"
                else "warning"
                if status == "warning"
                else "unhealthy"
            ),
            "services": [
                {"id": "api", "label": "预约 API", "status": "normal", "value": "正常"},
                {"id": "display", "label": "局域网大屏", "status": "normal", "value": "正常"},
                {
                    "id": "database",
                    "label": "数据库健康",
                    "status": "normal" if healthy else "critical",
                    "value": "正常" if healthy else "异常",
                },
                {
                    "id": "backup",
                    "label": "备份追平",
                    "status": "normal" if caught_up else "warning",
                    "value": "已追平" if caught_up else "待备份",
                },
            ],
        }
    )


@bp.post("/admin/backups")
@admin_required
def run_backup():
    db = get_db()
    actor_snapshot = current_user()
    sequence = None
    try:
        with maintenance_lock(
            Path(current_app.config["DATA_DIR"]) / "maintenance.lock",
            operation="backup",
            install_id=current_app.config["INSTALL_ID"],
        ):
            with transaction(db, track_change=False):
                actor = locked_actor(db, admin=True)
                sequence, data_sequence = reserve_backup_sequence(
                    db,
                    _backup_dir(),
                    install_id=current_app.config["INSTALL_ID"],
                )
                write_security_audit(
                    db,
                    actor_user_id=actor["id"],
                    action="backup.requested",
                    target_type="system",
                    details={"sequence": sequence, "result": "requested"},
                )
            target, sidecar = create_backup(
                Path(current_app.config["DATABASE"]),
                _backup_dir(),
                install_id=current_app.config["INSTALL_ID"],
                sequence=sequence,
                source_data_sequence=data_sequence,
            )
    except ApiError:
        # Preserve product-defined authorization and validation responses.  The
        # broad fallback below is only for an actual backup pipeline failure.
        raise
    except Exception:
        current_app.logger.exception("backup failed sequence=%s", sequence)
        try:
            with transaction(db, track_change=False):
                write_security_audit(
                    db,
                    actor_user_id=actor_snapshot["id"],
                    action="backup.failed",
                    target_type="system",
                    details={"sequence": sequence, "result": "failed"},
                )
        except Exception:
            current_app.logger.exception(
                "failed to record backup failure audit sequence=%s", sequence
            )
        raise ApiError(500, "BACKUP_FAILED", "备份未能完成，请查看诊断日志")
    with transaction(db, track_change=False):
        write_security_audit(
            db,
            actor_user_id=actor_snapshot["id"],
            action="backup.succeeded",
            target_type="system",
            target_id=target.name,
            details={"sequence": sequence, "result": "succeeded"},
        )
    return jsonify(
        {
            "created": True,
            "fileName": target.name,
            "createdAtUtc": sidecar["createdAtUtc"],
            "sequence": sequence,
            "sourceDataSequence": sidecar["sourceDataSequence"],
        }
    ), 201


@bp.get("/admin/diagnostics")
@admin_required
def diagnostics():
    db = get_db()
    health = database_health(db)
    backup = latest_backup_record(
        _backup_dir(),
        expected_install_id=current_app.config["INSTALL_ID"],
        verify_hash=True,
    )
    return jsonify(
        {
            "productVersion": current_app.config["PRODUCT_VERSION"],
            "productGeneration": PRODUCT_GENERATION,
            "schemaVersion": SCHEMA_VERSION,
            "setupComplete": is_setup_complete(db),
            "databaseIntegrity": health["quickCheck"],
            "foreignKeyErrors": health["foreignKeyErrors"],
            "databaseBytes": Path(current_app.config["DATABASE"]).stat().st_size,
            "latestBackupFile": backup[0].name if backup else None,
            "latestBackupSequence": backup[1]["sequence"] if backup else None,
            "servicePort": current_app.config["SERVICE_PORT"],
            "bindMode": "lan" if is_setup_complete(db) else "loopback",
            "lanAddress": current_app.config.get("LAN_ADDRESS"),
            "generatedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
    )


@bp.get("/admin/audit")
@admin_required
def read_audit_log():
    db = get_db()
    page_size = parse_page_size(request.args.get("pageSize"), default=50, maximum=200)
    clauses = ["1 = 1"]
    params: list[Any] = []
    cursor_filters: dict[str, str] = {}
    for query_name, column, maximum in (
        ("action", "a.action", 80),
        ("actorId", "a.actor_user_id", 64),
        ("targetType", "a.target_type", 80),
        ("targetId", "a.target_id", 128),
    ):
        raw = request.args.get(query_name)
        if raw:
            value = clean_text(
                raw,
                field=query_name,
                label=query_name,
                maximum=maximum,
            )
            clauses.append(f"{column} = ?")
            params.append(value)
            cursor_filters[query_name] = value
    outcome = request.args.get("outcome")
    if outcome:
        outcome_value = clean_text(
            outcome, field="outcome", label="outcome", maximum=40
        )
        clauses.append(
            "COALESCE(json_extract(a.details_json, '$.result'), "
            "json_extract(a.details_json, '$.reason')) = ?"
        )
        params.append(outcome_value)
        cursor_filters["outcome"] = outcome_value
    date_filters: dict[str, datetime] = {}
    for query_name, operator in (("dateFrom", ">="), ("dateTo", "<=")):
        raw = request.args.get(query_name)
        if raw:
            try:
                normalized = _utc_timestamp(raw)
            except (OverflowError, TypeError, ValueError):
                raise ApiError(
                    422,
                    "VALIDATION_ERROR",
                    f"{query_name} 必须是带时区的 ISO 8601 日期时间",
                )
            clauses.append(f"julianday(a.occurred_at) {operator} julianday(?)")
            params.append(normalized)
            cursor_filters[query_name] = normalized
            date_filters[query_name] = _parse_zoned_timestamp(normalized)
    if (
        "dateFrom" in date_filters
        and "dateTo" in date_filters
        and date_filters["dateFrom"] > date_filters["dateTo"]
    ):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "dateFrom 不能晚于 dateTo",
        )
    cursor_context = json.dumps(
        cursor_filters,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    total = db.execute(
        f"SELECT COUNT(*) FROM security_audit_log a WHERE {' AND '.join(clauses)}",
        params,
    ).fetchone()[0]
    if request.args.get("cursor"):
        cursor = decode_cursor(
            request.args["cursor"],
            length=2,
            context=cursor_context,
        )
        clauses.append("(a.occurred_at, a.id) < (?, ?)")
        params.extend(cursor)
    params.append(page_size + 1)
    rows = db.execute(
        f"""
        SELECT a.*, u.display_name AS actor_name
        FROM security_audit_log a
        LEFT JOIN users u ON u.id = a.actor_user_id
        WHERE {' AND '.join(clauses)}
        ORDER BY a.occurred_at DESC, a.id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    has_more = len(rows) > page_size
    page = rows[:page_size]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor(
            [last["occurred_at"], last["id"]],
            context=cursor_context,
        )
    return jsonify(
        {
            "items": [
                {
                    "id": row["id"],
                    "action": row["action"],
                    "actor": (
                        {"id": row["actor_user_id"], "name": row["actor_name"]}
                        if row["actor_user_id"]
                        else None
                    ),
                    "targetType": row["target_type"],
                    "targetId": row["target_id"],
                    "details": json.loads(row["details_json"]),
                    "occurredAtUtc": row["occurred_at"],
                }
                for row in page
            ],
            "nextCursor": next_cursor,
            "pageSize": page_size,
            "total": total,
        }
    )


@bp.get("/admin/tokens")
@admin_required
def list_tokens():
    rows = get_db().execute("SELECT * FROM api_tokens ORDER BY created_at DESC").fetchall()
    return jsonify({"items": [_token_item(row) for row in rows]})


@bp.post("/admin/tokens")
@admin_required
def create_token():
    payload = parse_json_object()
    name = clean_text(payload.get("name"), field="name", label="令牌名称", maximum=80)
    scopes = payload.get("scopes")
    if (
        not isinstance(scopes, list)
        or not scopes
        or not all(
            isinstance(scope, str) and scope in TOKEN_SCOPES for scope in scopes
        )
    ):
        raise ApiError(422, "VALIDATION_ERROR", "令牌范围无效")
    scopes = sorted(set(scopes))
    expires_at = payload.get("expiresAt")
    if expires_at is not None:
        try:
            expires_at = _utc_timestamp(expires_at)
        except (TypeError, ValueError):
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "expiresAt 必须是带时区的 ISO 8601 日期时间",
            )
    raw = "mr2_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    token_id = new_id()
    db = get_db()
    with transaction(db):
        actor = locked_actor(db, admin=True)
        db.execute(
            """
            INSERT INTO api_tokens
                (id, name, token_prefix, token_hash, scopes, created_by_user_id, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (token_id, name, raw[:12], digest, json.dumps(scopes), actor["id"], expires_at),
        )
        write_security_audit(
            db,
            actor_user_id=actor["id"],
            action="token.created",
            target_type="api_token",
            target_id=token_id,
            details={"scopes": scopes},
        )
    row = db.execute("SELECT * FROM api_tokens WHERE id = ?", (token_id,)).fetchone()
    result = _token_item(row)
    result["token"] = raw
    return jsonify(result), 201


@bp.delete("/admin/tokens/<token_id>")
@admin_required
def revoke_token(token_id: str):
    db = get_db()
    with transaction(db):
        actor = locked_actor(db, admin=True)
        cursor = db.execute(
            """
            UPDATE api_tokens
            SET revoked_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ? AND revoked_at IS NULL
            """,
            (token_id,),
        )
        if cursor.rowcount != 1:
            raise ApiError(404, "NOT_FOUND", "令牌不存在或已经撤销")
        write_security_audit(
            db,
            actor_user_id=actor["id"],
            action="token.revoked",
            target_type="api_token",
            target_id=token_id,
        )
    return jsonify({"revoked": True})


def require_token(scope: str) -> Callable:
    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped(*args, **kwargs):
            authorization = request.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                raise ApiError(401, "TOKEN_REQUIRED", "需要只读 API 令牌")
            raw = authorization[7:]
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            row = get_db().execute(
                "SELECT * FROM api_tokens WHERE token_hash = ?", (digest,)
            ).fetchone()
            if row is None or row["revoked_at"]:
                raise ApiError(401, "TOKEN_INVALID", "API 令牌无效")
            if row["expires_at"]:
                try:
                    expiry = _parse_zoned_timestamp(row["expires_at"])
                except (TypeError, ValueError):
                    raise ApiError(401, "TOKEN_INVALID", "API 令牌时效信息无效")
                if expiry <= local_now().astimezone(timezone.utc):
                    raise ApiError(401, "TOKEN_EXPIRED", "API 令牌已过期")
            if scope not in json.loads(row["scopes"]):
                raise ApiError(403, "TOKEN_SCOPE_FORBIDDEN", "API 令牌范围不足")
            get_db().execute(
                "UPDATE api_tokens SET last_used_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
                (row["id"],),
            )
            return view(*args, **kwargs)

        return wrapped

    return decorator


@bp.get("/integration/rooms")
@require_token("rooms:read")
def integration_rooms():
    rows = get_db().execute(
        "SELECT id, name, sort_order FROM rooms WHERE is_active = 1 ORDER BY sort_order, name"
    ).fetchall()
    return jsonify({"items": [{"id": row["id"], "name": row["name"]} for row in rows]})


@bp.get("/integration/availability")
@require_token("availability:read")
def integration_availability():
    from ..common import minutes_to_time, parse_date, time_to_minutes

    booking_date = parse_date(request.args.get("date"))
    db = get_db()
    settings = db.execute("SELECT * FROM system_settings WHERE id = 1").fetchone()
    start = time_to_minutes(settings["work_start"], field="workStart")
    end = time_to_minutes(settings["work_end"], field="workEnd")
    rooms = db.execute(
        "SELECT id, name FROM rooms WHERE is_active = 1 ORDER BY sort_order, name"
    ).fetchall()
    occupied_by_room = {}
    for row in db.execute(
        """
        SELECT s.room_id, s.slot_start
        FROM reservation_slots s
        JOIN rooms r ON r.id = s.room_id
        WHERE s.booking_date = ? AND r.is_active = 1
        """,
        (booking_date,),
    ).fetchall():
        occupied_by_room.setdefault(row["room_id"], set()).add(row["slot_start"])
    result = []
    for room in rooms:
        occupied = occupied_by_room.get(room["id"], set())
        slots = [
            {"start": minutes_to_time(cursor), "available": minutes_to_time(cursor) not in occupied}
            for cursor in range(start, end, settings["slot_minutes"])
        ]
        result.append({"roomId": room["id"], "roomName": room["name"], "slots": slots})
    return jsonify({"date": booking_date, "rooms": result})


@bp.get("/integration/health")
@require_token("health:read")
def integration_health():
    return jsonify({"ok": True, "productVersion": current_app.config["PRODUCT_VERSION"]})

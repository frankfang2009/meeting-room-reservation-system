from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, current_app, jsonify, request

from ..backup import create_backup, latest_backup
from ..common import clean_text, local_now, new_id, parse_json_object
from ..db import PRODUCT_GENERATION, SCHEMA_VERSION, get_db, is_setup_complete, transaction
from ..errors import ApiError
from ..security import admin_required, locked_actor
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
    backup = latest_backup(_backup_dir())
    checked_at = local_now().isoformat(timespec="seconds")
    return jsonify(
        {
            "status": "normal",
            "label": "系统运行正常",
            "lastCheckedAt": checked_at,
            "productVersion": current_app.config["PRODUCT_VERSION"],
            "productGeneration": PRODUCT_GENERATION,
            "databaseVersion": SCHEMA_VERSION,
            "setupComplete": is_setup_complete(),
            "lanAddress": current_app.config.get("LAN_ADDRESS"),
            "lastBackupAt": (
                datetime.fromtimestamp(backup.stat().st_mtime).astimezone().isoformat(timespec="seconds")
                if backup else None
            ),
            "apiStatus": "online",
            "displayStatus": "online",
            "health": "healthy",
            "services": [
                {"id": "api", "label": "预约 API", "status": "normal", "value": "正常"},
                {"id": "display", "label": "局域网大屏", "status": "normal", "value": "正常"},
                {"id": "database", "label": "数据库健康", "status": "normal", "value": "正常"},
            ],
        }
    )


@bp.post("/admin/backups")
@admin_required
def run_backup():
    db = get_db()
    with transaction(db):
        actor = locked_actor(db, admin=True)
        write_security_audit(
            db,
            actor_user_id=actor["id"],
            action="backup.requested",
            target_type="system",
        )
    target = create_backup(Path(current_app.config["DATABASE"]), _backup_dir())
    return jsonify(
        {
            "created": True,
            "fileName": target.name,
            "createdAt": datetime.fromtimestamp(target.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
        }
    ), 201


@bp.get("/admin/diagnostics")
@admin_required
def diagnostics():
    db = get_db()
    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_key_errors = len(db.execute("PRAGMA foreign_key_check").fetchall())
    backup = latest_backup(_backup_dir())
    return jsonify(
        {
            "productVersion": current_app.config["PRODUCT_VERSION"],
            "productGeneration": PRODUCT_GENERATION,
            "schemaVersion": SCHEMA_VERSION,
            "setupComplete": is_setup_complete(db),
            "databaseIntegrity": integrity,
            "foreignKeyErrors": foreign_key_errors,
            "databaseBytes": Path(current_app.config["DATABASE"]).stat().st_size,
            "latestBackupFile": backup.name if backup else None,
            "generatedAt": local_now().isoformat(timespec="seconds"),
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
    if not isinstance(scopes, list) or not scopes or not all(scope in TOKEN_SCOPES for scope in scopes):
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

        wrapped.__name__ = view.__name__
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
    result = []
    for room in db.execute(
        "SELECT id, name FROM rooms WHERE is_active = 1 ORDER BY sort_order, name"
    ).fetchall():
        occupied = {
            row[0]
            for row in db.execute(
                "SELECT slot_start FROM reservation_slots WHERE room_id = ? AND booking_date = ?",
                (room["id"], booking_date),
            ).fetchall()
        }
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

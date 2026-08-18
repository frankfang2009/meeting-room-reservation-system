from __future__ import annotations

import os
import hmac
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, request, send_from_directory

from .common import remote_is_loopback
from .db import (
    DatabaseStartupState,
    PRODUCT_GENERATION,
    is_setup_complete,
    prepare_database,
    register_db,
)
from .errors import ApiError, register_error_handlers
from .runtime.identity import (
    load_existing_install_id,
    load_existing_secret,
    load_or_create_install_id,
    load_or_create_secret,
)
from .runtime.install_state import load_install_json, sync_install_json
from .security import register_security
from .services import update_check


PRODUCT_VERSION = "V2.2.3"
PACKAGE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PACKAGE_DIR.parent
DEFAULT_DATA_DIR = BACKEND_DIR / "data"
DEFAULT_STATIC_DIR = BACKEND_DIR.parent / "frontend" / "dist" / "client"


def create_app(test_config: Optional[dict[str, Any]] = None) -> Flask:
    supplied = dict(test_config or {})
    data_dir = Path(
        supplied.get("DATA_DIR")
        or os.environ.get("MEETING_ROOM_V2_DATA_DIR", DEFAULT_DATA_DIR)
    )
    database = Path(supplied.get("DATABASE") or data_dir / "reservation.db")
    install_json_path = data_dir / "install.json"
    install_id_path = data_dir / "install_id"
    secret_path = data_dir / ".secret_key"

    metadata = None
    startup: DatabaseStartupState
    try:
        metadata = load_install_json(install_json_path)
        existing_install_id = supplied.get("INSTALL_ID")
        if existing_install_id is None and install_id_path.exists():
            existing_install_id = load_existing_install_id(install_id_path)
        if (
            metadata is not None
            and existing_install_id is None
        ):
            startup = DatabaseStartupState(
                ready=False,
                setup_complete=False,
                code="INSTALL_IDENTITY_MISSING",
                message="安装标识文件缺失，已进入恢复模式",
            )
        elif (
            metadata is not None
            and metadata["install_id"] != existing_install_id
        ):
            startup = DatabaseStartupState(
                ready=False,
                setup_complete=False,
                code="INSTALL_IDENTITY_MISMATCH",
                message="安装身份文件不一致，已进入恢复模式",
            )
        else:
            startup = prepare_database(
                database,
                mirror_setup_complete=(
                    metadata["setup_complete"] if metadata is not None else None
                ),
            )
    except RuntimeError:
        startup = DatabaseStartupState(
            ready=False,
            setup_complete=False,
            code="INSTALL_STATE_INVALID",
            message="安装状态文件无效，已进入恢复模式",
        )

    if startup.ready:
        secret_key = supplied.get("SECRET_KEY") or load_or_create_secret(secret_path)
        install_id = supplied.get("INSTALL_ID") or load_or_create_install_id(
            install_id_path
        )
        if metadata is not None and metadata["install_id"] != install_id:
            startup = DatabaseStartupState(
                ready=False,
                setup_complete=False,
                code="INSTALL_IDENTITY_MISMATCH",
                message="安装身份文件不一致，已进入恢复模式",
            )
        elif startup.setup_complete and metadata is not None and not metadata["setup_complete"]:
            # SQLite is the sole setup authority. Only upward repair is legal.
            sync_install_json(
                install_json_path,
                install_id=install_id,
                setup_complete=True,
            )
    else:
        try:
            secret_key = supplied.get("SECRET_KEY") or load_existing_secret(secret_path)
        except RuntimeError:
            # Recovery health/static pages need a process-local Flask key, but
            # must never create or replace installation identity files.
            secret_key = secrets.token_hex(32)
        install_id = supplied.get("INSTALL_ID")
        if not install_id and metadata is not None:
            install_id = metadata.get("install_id")
        if not install_id:
            try:
                install_id = load_existing_install_id(install_id_path)
            except RuntimeError:
                install_id = "unavailable"

    app = Flask(__name__, static_folder=None)
    app.config.from_mapping(
        PRODUCT_VERSION=PRODUCT_VERSION,
        DATA_DIR=str(data_dir),
        DATABASE=str(database),
        BACKUP_DIR=str(supplied.get("BACKUP_DIR") or data_dir.parent / "backups"),
        STATIC_DIR=str(supplied.get("STATIC_DIR") or DEFAULT_STATIC_DIR),
        SECRET_KEY=secret_key,
        INSTALL_ID=install_id,
        SESSION_COOKIE_NAME="meeting_room_v2_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=False,
        SESSION_IDLE_SECONDS=30 * 60,
        SESSION_ABSOLUTE_SECONDS=12 * 60 * 60,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        SESSION_REFRESH_EACH_REQUEST=False,
        MAX_CONTENT_LENGTH=256 * 1024,
        JSON_AS_ASCII=False,
        LAN_ADDRESS=None,
        SERVICE_PORT=8080,
        ACTIVE_BIND_MODE=None,
        UPDATE_CHECK_ENABLED=False,
        UPDATE_CHECK_URL=update_check.DEFAULT_MANIFEST_URL,
        SYSTEM_READY=startup.ready,
        DATABASE_SETUP_COMPLETE=startup.setup_complete,
        RECOVERY_STATE=(
            None
            if startup.ready
            else {"code": startup.code, "message": startup.message}
        ),
    )
    app.config.update(supplied)

    register_db(app)
    register_security(app)
    register_error_handlers(app)

    @app.before_request
    def fail_closed_recovery_gate():
        if app.config["SYSTEM_READY"]:
            return None
        if request.path.startswith("/api/v1"):
            recovery = app.config["RECOVERY_STATE"]
            raise ApiError(
                503,
                "SYSTEM_RECOVERY_REQUIRED",
                recovery["message"],
                fields={"recoveryCode": recovery["code"]},
            )
        return None

    from .api.admin import bp as admin_bp
    from .api.activity import bp as activity_bp
    from .api.core import bp as core_bp
    from .api.display import bp as display_bp
    from .api.preferences import bp as preferences_bp
    from .api.reports import bp as reports_bp
    from .api.reminders import bp as reminders_bp
    from .api.reservations import bp as reservations_bp
    from .api.system import bp as system_bp

    for blueprint in (
        core_bp,
        reservations_bp,
        admin_bp,
        activity_bp,
        reports_bp,
        preferences_bp,
        reminders_bp,
        display_bp,
        system_bp,
    ):
        app.register_blueprint(blueprint)

    @app.get("/healthz", strict_slashes=False)
    def healthz():
        ready = bool(app.config["SYSTEM_READY"])
        complete = is_setup_complete() if ready else bool(
            app.config["DATABASE_SETUP_COMPLETE"]
        )
        bind_mode = app.config.get("ACTIVE_BIND_MODE")
        if bind_mode not in {"loopback", "lan"}:
            bind_mode = "lan" if ready and complete else "loopback"
        payload: dict[str, Any] = {
            "ok": ready,
            "product_generation": PRODUCT_GENERATION,
            "setup_complete": complete,
            "bind_mode": bind_mode,
            "port": app.config["SERVICE_PORT"],
            "status": "ready" if ready else "recovery",
        }
        if remote_is_loopback():
            payload["install_id"] = app.config["INSTALL_ID"]
            if not ready:
                payload["recovery_code"] = app.config["RECOVERY_STATE"]["code"]
        return jsonify(payload)

    service_control = app.config.get("SERVICE_CONTROL")
    service_stop_event = app.config.get("SERVICE_STOP_EVENT")
    if isinstance(service_control, dict) and service_stop_event is not None:

        @app.post("/_service/stop")
        def stop_service():
            # This endpoint is intentionally outside the public API. It is
            # reachable only through loopback and requires the per-process
            # random token held in data/service.pid.
            if not remote_is_loopback():
                return "Not found", 404
            supplied_token = request.headers.get("X-Meeting-Room-Service-Token", "")
            expected_token = str(service_control.get("token") or "")
            if not supplied_token or not hmac.compare_digest(
                supplied_token, expected_token
            ):
                return "Forbidden", 403
            payload = request.get_json(silent=True)
            expected = {
                key: service_control[key]
                for key in ("pid", "executable", "servicePath", "installId")
            }
            if not isinstance(payload, dict) or payload != expected:
                return "Forbidden", 403
            service_stop_event.set()
            return jsonify({"stopping": True, "pid": service_control["pid"]})

    @app.get("/assets/<path:filename>")
    def static_asset(filename: str):
        assets = Path(app.config["STATIC_DIR"]) / "assets"
        if not assets.is_dir():
            return "Frontend assets are not built", 503
        return send_from_directory(assets, filename)

    @app.get("/")
    @app.get("/<path:frontend_path>")
    def spa(frontend_path: str = ""):
        if frontend_path.startswith("api/") or frontend_path == "healthz":
            raise ApiError(404, "NOT_FOUND", "资源不存在")
        static_dir = Path(app.config["STATIC_DIR"])
        candidate = static_dir / frontend_path
        if frontend_path and candidate.is_file():
            return send_from_directory(static_dir, frontend_path)
        index = static_dir / "index.html"
        if not index.is_file():
            return "React frontend has not been built", 503
        return send_from_directory(static_dir, "index.html")

    return app


__all__ = ["PRODUCT_VERSION", "create_app"]

from __future__ import annotations

import os
import hmac
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, request, send_from_directory

from .common import remote_is_loopback
from .db import (
    PRODUCT_GENERATION,
    database_setup_complete,
    is_setup_complete,
    prepare_database,
    register_db,
)
from .errors import register_error_handlers
from .runtime.identity import load_or_create_install_id, load_or_create_secret
from .runtime.install_state import sync_install_json
from .security import register_security


PRODUCT_VERSION = "V2.0.0"
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

    # Database generation classification must happen before this process creates
    # or replaces any installation identity beside an existing database.
    prepare_database(database)

    secret_key = supplied.get("SECRET_KEY")
    if not secret_key:
        secret_key = load_or_create_secret(data_dir / ".secret_key")
    install_id = supplied.get("INSTALL_ID")
    if not install_id:
        install_id = load_or_create_install_id(data_dir / "install_id")
    # SQLite is the authority. If setup committed but the process died before
    # mirroring installer metadata, the next start repairs install.json here.
    sync_install_json(
        data_dir / "install.json",
        install_id=install_id,
        setup_complete=database_setup_complete(database),
    )

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
        MAX_CONTENT_LENGTH=256 * 1024,
        JSON_AS_ASCII=False,
        LAN_ADDRESS=None,
        SERVICE_PORT=8080,
    )
    app.config.update(supplied)

    register_db(app)
    register_security(app)
    register_error_handlers(app)

    from .api.admin import bp as admin_bp
    from .api.core import bp as core_bp
    from .api.display import bp as display_bp
    from .api.preferences import bp as preferences_bp
    from .api.reminders import bp as reminders_bp
    from .api.reservations import bp as reservations_bp
    from .api.system import bp as system_bp

    for blueprint in (
        core_bp,
        reservations_bp,
        admin_bp,
        preferences_bp,
        reminders_bp,
        display_bp,
        system_bp,
    ):
        app.register_blueprint(blueprint)

    @app.get("/healthz")
    def healthz():
        complete = is_setup_complete()
        bind_mode = "lan" if complete else "loopback"
        payload: dict[str, Any] = {
            "ok": True,
            "product_generation": PRODUCT_GENERATION,
            "setup_complete": complete,
            "bind_mode": bind_mode,
            "port": app.config["SERVICE_PORT"],
        }
        if remote_is_loopback():
            payload["install_id"] = app.config["INSTALL_ID"]
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
            return jsonify({"error": {"code": "NOT_FOUND", "message": "资源不存在"}}), 404
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

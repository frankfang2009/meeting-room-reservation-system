from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from functools import wraps
from typing import Any, Callable, Optional

from flask import Flask, current_app, g, request, session

from .common import parse_json_object
from .db import get_db
from .errors import ApiError


_LOGIN_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)
_LOGIN_LOCK = threading.RLock()


def csrf_token() -> str:
    value = session.get("_csrf_token")
    if not isinstance(value, str) or len(value) < 32:
        value = secrets.token_urlsafe(32)
        session["_csrf_token"] = value
    return value


def current_user() -> Optional[dict[str, Any]]:
    if "current_user" in g:
        return g.current_user
    user_id = session.get("user_id")
    session_version = session.get("session_version")
    if not user_id or not session_version:
        g.current_user = None
        return None
    row = get_db().execute(
        "SELECT * FROM users WHERE id = ? AND is_active = 1",
        (user_id,),
    ).fetchone()
    if row is None or row["session_version"] != session_version:
        session.clear()
        g.current_user = None
        g.session_expired = True
        return None
    g.current_user = dict(row)
    return g.current_user


def serialize_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "username": user["username"],
        "name": user["display_name"],
        "department": user["department"],
        "role": user["role"],
        "enabled": bool(user["is_active"]),
    }


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        user = current_user()
        if user is None:
            code = "SESSION_EXPIRED" if getattr(g, "session_expired", False) else "SESSION_REQUIRED"
            raise ApiError(401, code, "请重新登录" if code == "SESSION_EXPIRED" else "请先登录")
        return view(*args, **kwargs)

    return wrapped


def admin_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    @login_required
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if current_user()["role"] != "admin":
            raise ApiError(403, "FORBIDDEN", "需要管理员权限")
        return view(*args, **kwargs)

    return wrapped


def locked_actor(db, *, admin: bool = False) -> dict[str, Any]:
    snapshot = current_user()
    if snapshot is None:
        raise ApiError(401, "SESSION_REQUIRED", "请先登录")
    row = db.execute(
        """
        SELECT * FROM users
        WHERE id = ? AND is_active = 1 AND session_version = ?
        """,
        (snapshot["id"], snapshot["session_version"]),
    ).fetchone()
    if row is None:
        session.clear()
        g.current_user = None
        raise ApiError(401, "SESSION_EXPIRED", "账号状态已变化，请重新登录")
    actor = dict(row)
    if admin and actor["role"] != "admin":
        raise ApiError(403, "FORBIDDEN", "需要管理员权限")
    return actor


def _csrf_protect() -> None:
    if not request.path.startswith("/api/v1"):
        return
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    expected = session.get("_csrf_token")
    supplied = request.headers.get("X-CSRF-Token")
    if not isinstance(expected, str) or not isinstance(supplied, str):
        raise ApiError(403, "CSRF_INVALID", "页面状态已过期，请刷新后重试")
    if not hmac.compare_digest(expected, supplied):
        raise ApiError(403, "CSRF_INVALID", "页面状态已过期，请刷新后重试")


def login_rate_key(username: str) -> str:
    return f"{request.remote_addr or 'unknown'}:{username.casefold()}"


def assert_login_allowed(key: str) -> None:
    window = float(current_app.config.get("LOGIN_RATE_WINDOW_SECONDS", 300))
    maximum = int(current_app.config.get("LOGIN_RATE_MAX_ATTEMPTS", 8))
    now = time.monotonic()
    with _LOGIN_LOCK:
        attempts = _LOGIN_ATTEMPTS[key]
        while attempts and attempts[0] <= now - window:
            attempts.popleft()
        if len(attempts) >= maximum:
            raise ApiError(429, "LOGIN_RATE_LIMITED", "登录尝试过多，请稍后再试")


def record_login_failure(key: str) -> None:
    with _LOGIN_LOCK:
        _LOGIN_ATTEMPTS[key].append(time.monotonic())


def clear_login_failures(key: str) -> None:
    with _LOGIN_LOCK:
        _LOGIN_ATTEMPTS.pop(key, None)


def register_security(app: Flask) -> None:
    app.before_request(_csrf_protect)

    @app.after_request
    def add_security_headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-Meeting-Room-System"] = "2"
        if request.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-store"
        return response


__all__ = [
    "admin_required",
    "assert_login_allowed",
    "clear_login_failures",
    "csrf_token",
    "current_user",
    "locked_actor",
    "login_rate_key",
    "login_required",
    "parse_json_object",
    "record_login_failure",
    "register_security",
    "serialize_user",
]

from __future__ import annotations

import hmac
import hashlib
import secrets
import threading
import time
from collections import OrderedDict, deque
from functools import wraps
from typing import Any, Callable, Optional

from flask import Flask, current_app, g, request, session

from .common import parse_json_object
from .db import get_db, transaction
from .errors import ApiError


_LOGIN_ATTEMPTS: "OrderedDict[str, deque[float]]" = OrderedDict()
_LOGIN_LOCK = threading.RLock()
_LOGIN_BUCKET_LIMIT = 4096
_PASSIVE_SESSION_PATHS = {
    "/api/v1/reminders/due",
    "/api/v1/admin/system",
    "/api/v1/admin/audit",
    "/api/v1/admin/tokens",
}


def _session_clock() -> float:
    provider = current_app.config.get("SESSION_TIME_PROVIDER")
    return float(provider() if provider else time.time())


def audit_fingerprint(label: str, value: str) -> str:
    raw = value.encode("utf-8", "replace")
    secret = str(current_app.config["SECRET_KEY"]).encode("utf-8")
    prefix = ("audit-" + label + "\0").encode("ascii")
    return hmac.new(secret, prefix + raw, hashlib.sha256).hexdigest()[:24]


def request_ip_fingerprint() -> str:
    return audit_fingerprint("ip", request.remote_addr or "unknown")


def _audit_session_expiry(user_id: str, reason: str) -> None:
    from .services.audit import write_security_audit

    db = get_db()
    try:
        with transaction(db, track_change=False):
            write_security_audit(
                db,
                action="auth.session_expired",
                target_type="session",
                target_id=user_id,
                details={
                    "reason": reason,
                    "result": "expired",
                    "ipFingerprint": request_ip_fingerprint(),
                },
            )
    except Exception:
        current_app.logger.exception("failed to record session expiry audit")


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
    issued_at = session.get("_issued_at")
    last_active_at = session.get("_last_active_at")
    now = _session_clock()
    if (
        isinstance(issued_at, bool)
        or not isinstance(issued_at, (int, float))
        or isinstance(last_active_at, bool)
        or not isinstance(last_active_at, (int, float))
    ):
        _audit_session_expiry(str(user_id), "timestamps_missing")
        session.clear()
        g.current_user = None
        g.session_expired = True
        return None
    idle_seconds = int(current_app.config.get("SESSION_IDLE_SECONDS", 30 * 60))
    absolute_seconds = int(
        current_app.config.get("SESSION_ABSOLUTE_SECONDS", 12 * 60 * 60)
    )
    if now - float(issued_at) > absolute_seconds:
        _audit_session_expiry(str(user_id), "absolute_timeout")
        session.clear()
        g.current_user = None
        g.session_expired = True
        return None
    if now - float(last_active_at) > idle_seconds:
        _audit_session_expiry(str(user_id), "idle_timeout")
        session.clear()
        g.current_user = None
        g.session_expired = True
        return None
    row = get_db().execute(
        "SELECT * FROM users WHERE id = ? AND is_active = 1",
        (user_id,),
    ).fetchone()
    if row is None or row["session_version"] != session_version:
        _audit_session_expiry(str(user_id), "account_state_changed")
        session.clear()
        g.current_user = None
        g.session_expired = True
        return None
    if request.method != "GET" or request.path not in _PASSIVE_SESSION_PATHS:
        session["_last_active_at"] = now
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
    if not current_app.config.get("SYSTEM_READY", False):
        # The recovery gate must be the only externally observable API state;
        # a missing CSRF cookie must not downgrade recovery responses to 403.
        return
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


def login_rate_keys(username: str) -> tuple[str, str]:
    address = request.remote_addr or "unknown"
    return (f"ip:{address}", f"account:{username.casefold()}")


def _prune_login_buckets(now: float, window: float) -> None:
    for key in list(_LOGIN_ATTEMPTS):
        attempts = _LOGIN_ATTEMPTS[key]
        while attempts and attempts[0] <= now - window:
            attempts.popleft()
        if not attempts:
            _LOGIN_ATTEMPTS.pop(key, None)
    while len(_LOGIN_ATTEMPTS) > _LOGIN_BUCKET_LIMIT:
        _LOGIN_ATTEMPTS.popitem(last=False)


def assert_login_allowed(keys: tuple[str, str]) -> None:
    window = float(current_app.config.get("LOGIN_RATE_WINDOW_SECONDS", 300))
    account_maximum = int(current_app.config.get("LOGIN_RATE_MAX_ATTEMPTS", 8))
    ip_maximum = int(current_app.config.get("LOGIN_RATE_IP_MAX_ATTEMPTS", 32))
    now = time.monotonic()
    with _LOGIN_LOCK:
        _prune_login_buckets(now, window)
        for key, maximum in zip(keys, (ip_maximum, account_maximum)):
            attempts = _LOGIN_ATTEMPTS.get(key, ())
            if len(attempts) >= maximum:
                raise ApiError(429, "LOGIN_RATE_LIMITED", "登录尝试过多，请稍后再试")


def record_login_failure(keys: tuple[str, str]) -> None:
    with _LOGIN_LOCK:
        now = time.monotonic()
        window = float(current_app.config.get("LOGIN_RATE_WINDOW_SECONDS", 300))
        _prune_login_buckets(now, window)
        for key in keys:
            attempts = _LOGIN_ATTEMPTS.setdefault(key, deque())
            attempts.append(now)
            _LOGIN_ATTEMPTS.move_to_end(key)
        _prune_login_buckets(now, window)


def clear_login_failures(keys: tuple[str, str]) -> None:
    with _LOGIN_LOCK:
        # Keep the per-IP bucket so rotating usernames cannot bypass the
        # limiter; a successful account only clears its credential bucket.
        _LOGIN_ATTEMPTS.pop(keys[1], None)


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
    "audit_fingerprint",
    "assert_login_allowed",
    "clear_login_failures",
    "csrf_token",
    "current_user",
    "locked_actor",
    "login_rate_keys",
    "login_required",
    "parse_json_object",
    "record_login_failure",
    "request_ip_fingerprint",
    "register_security",
    "serialize_user",
]

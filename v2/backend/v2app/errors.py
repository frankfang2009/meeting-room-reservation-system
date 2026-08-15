from __future__ import annotations

import sqlite3
import uuid
from typing import Any, Mapping, Optional

from flask import Flask, current_app, g, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        fields: Optional[Mapping[str, str]] = None,
        conflicts: Optional[list[dict[str, Any]]] = None,
        current: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.fields = dict(fields or {})
        self.conflicts = conflicts
        self.current = current

    def payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.fields:
            error["fields"] = self.fields
        if self.conflicts is not None:
            error["conflicts"] = self.conflicts
        if self.current is not None:
            error["current"] = self.current
        return {"error": error}


def register_error_handlers(app: Flask) -> None:
    def request_id() -> str:
        value = getattr(g, "request_id", None)
        if not isinstance(value, str):
            value = uuid.uuid4().hex
            g.request_id = value
        return value

    def api_payload(error: dict[str, Any]) -> dict[str, Any]:
        identifier = request_id()
        normalized = dict(error)
        normalized["requestId"] = identifier
        return {"error": normalized, "requestId": identifier}

    @app.before_request
    def assign_request_id() -> None:
        request_id()

    @app.after_request
    def expose_request_id(response):
        response.headers["X-Request-Id"] = request_id()
        return response

    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return jsonify(api_payload(error.payload()["error"])), error.status

    @app.errorhandler(400)
    def handle_bad_request(error: Any):
        if request.path.startswith("/api/"):
            message = getattr(error, "description", "请求内容无效")
            return jsonify(
                api_payload({"code": "BAD_REQUEST", "message": message})
            ), 400
        return "Bad request", 400

    @app.errorhandler(RequestEntityTooLarge)
    def handle_too_large(_error: Any):
        if request.path.startswith("/api/"):
            return jsonify(
                api_payload(
                    {"code": "PAYLOAD_TOO_LARGE", "message": "请求内容超过允许大小"}
                )
            ), 413
        return "Payload too large", 413

    @app.errorhandler(404)
    def handle_not_found(_error: Any):
        if request.path.startswith("/api/"):
            return jsonify(api_payload({"code": "NOT_FOUND", "message": "资源不存在"})), 404
        return "Not found", 404

    @app.errorhandler(405)
    def handle_method_not_allowed(_error: Any):
        if request.path.startswith("/api/"):
            return jsonify(
                api_payload({"code": "METHOD_NOT_ALLOWED", "message": "请求方法不允许"})
            ), 405
        return "Method not allowed", 405

    @app.errorhandler(sqlite3.Error)
    def handle_sqlite_error(error: sqlite3.Error):
        current_app.logger.exception(
            "database request failed request_id=%s", request_id()
        )
        if request.path.startswith("/api/"):
            return jsonify(
                api_payload(
                    {
                        "code": "DATABASE_UNAVAILABLE",
                        "message": "数据库暂时不可用，请联系管理员",
                    }
                )
            ), 503
        return "Service unavailable", 503

    @app.errorhandler(Exception)
    def handle_unexpected(error: Exception):
        current_app.logger.exception(
            "unhandled request failure request_id=%s", request_id()
        )
        if request.path.startswith("/api/"):
            return jsonify(
                api_payload(
                    {"code": "INTERNAL_ERROR", "message": "系统无法完成请求"}
                )
            ), 500
        return "Internal server error", 500

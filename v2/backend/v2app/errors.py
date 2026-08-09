from __future__ import annotations

from typing import Any, Mapping, Optional

from flask import Flask, jsonify, request


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
    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return jsonify(error.payload()), error.status

    @app.errorhandler(400)
    def handle_bad_request(error: Any):
        if request.path.startswith("/api/"):
            message = getattr(error, "description", "请求内容无效")
            return jsonify({"error": {"code": "BAD_REQUEST", "message": message}}), 400
        return "Bad request", 400

    @app.errorhandler(404)
    def handle_not_found(_error: Any):
        if request.path.startswith("/api/"):
            return jsonify(
                {"error": {"code": "NOT_FOUND", "message": "资源不存在"}}
            ), 404
        return "Not found", 404

    @app.errorhandler(405)
    def handle_method_not_allowed(_error: Any):
        if request.path.startswith("/api/"):
            return jsonify(
                {"error": {"code": "METHOD_NOT_ALLOWED", "message": "请求方法不允许"}}
            ), 405
        return "Method not allowed", 405

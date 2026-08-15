from __future__ import annotations

import ipaddress
import base64
import binascii
import hashlib
import hmac
import json
import re
import uuid
from datetime import date, datetime
from typing import Any, Optional

from flask import current_app, request

from .errors import ApiError


DOUBLE_CHARACTER_SURNAMES = (
    "欧阳", "司马", "上官", "诸葛", "夏侯", "东方", "皇甫", "尉迟",
    "公孙", "长孙", "宇文", "慕容", "令狐", "司徒", "司空",
)


def new_id() -> str:
    return str(uuid.uuid4())


def local_now() -> datetime:
    provider = current_app.config.get("NOW_PROVIDER")
    value = provider() if provider else datetime.now().astimezone()
    if not isinstance(value, datetime):
        raise RuntimeError("NOW_PROVIDER 必须返回 datetime")
    if value.tzinfo is None:
        return value.astimezone()
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_json_object() -> dict[str, Any]:
    if not request.is_json:
        raise ApiError(400, "JSON_REQUIRED", "请求必须使用 JSON")
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ApiError(400, "JSON_REQUIRED", "请求 JSON 必须是对象")
    return value


def clean_text(
    value: Any,
    *,
    field: str,
    label: str,
    maximum: int,
    required: bool = True,
) -> str:
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    text = value.strip()
    if required and not text:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={field: f"请输入{label}"},
        )
    if len(text) > maximum:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={field: f"{label}不能超过 {maximum} 个字符"},
        )
    if any(ord(character) < 32 and character not in "\n\t" for character in text):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={field: f"{label}包含无效字符"},
        )
    return text


def parse_date(value: Any, *, field: str = "date") -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={field: "日期格式应为 YYYY-MM-DD"},
        )


def time_to_minutes(value: Any, *, field: str) -> int:
    text = str(value or "")
    if not re.fullmatch(r"\d{2}:\d{2}", text):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={field: "时间格式应为 HH:MM"},
        )
    hours, minutes = (int(part) for part in text.split(":"))
    if hours > 23 or minutes > 59:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={field: "时间无效"},
        )
    return hours * 60 + minutes


def minutes_to_time(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def parse_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ApiError(
        422,
        "VALIDATION_ERROR",
        "请检查输入内容",
        fields={field: "必须是布尔值"},
    )


def parse_int(value: Any, *, field: str) -> int:
    if type(value) is not int:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={field: "必须是整数"},
        )
    return value


def parse_page_size(value: Any, *, default: int = 100, maximum: int = 200) -> int:
    if value in (None, ""):
        return default
    text = str(value)
    if not re.fullmatch(r"[1-9][0-9]{0,3}", text):
        raise ApiError(422, "VALIDATION_ERROR", "pageSize 必须是正整数")
    parsed = int(text)
    if parsed > maximum:
        raise ApiError(422, "VALIDATION_ERROR", f"pageSize 不能超过 {maximum}")
    return parsed


def _cursor_context_digest(context: str) -> str:
    return hashlib.sha256(context.encode("utf-8")).hexdigest()


def encode_cursor(parts: list[str], *, context: Optional[str] = None) -> str:
    value: Any = parts
    if context is not None:
        value = {"context": _cursor_context_digest(context), "parts": parts}
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    secret = str(current_app.config["SECRET_KEY"]).encode("utf-8")
    signature = hmac.new(
        secret, b"api-cursor\0" + body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return body + "." + signature


def decode_cursor(
    value: Any,
    *,
    length: int,
    context: Optional[str] = None,
) -> list[str]:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ApiError(422, "INVALID_CURSOR", "分页游标无效")
    try:
        body, supplied_signature = value.rsplit(".", 1)
        secret = str(current_app.config["SECRET_KEY"]).encode("utf-8")
        expected_signature = hmac.new(
            secret, b"api-cursor\0" + body.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("cursor signature mismatch")
        padded = body + "=" * (-len(body) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        decoded = json.loads(raw.decode("ascii"))
    except (binascii.Error, UnicodeError, ValueError):
        raise ApiError(422, "INVALID_CURSOR", "分页游标无效")
    if context is None:
        parts = decoded
    elif (
        not isinstance(decoded, dict)
        or set(decoded) != {"context", "parts"}
        or decoded.get("context") != _cursor_context_digest(context)
    ):
        raise ApiError(422, "INVALID_CURSOR", "分页游标与当前筛选条件不匹配")
    else:
        parts = decoded["parts"]
    if (
        not isinstance(parts, list)
        or len(parts) != length
        or not all(isinstance(part, str) and len(part) <= 256 for part in parts)
    ):
        raise ApiError(422, "INVALID_CURSOR", "分页游标无效")
    return parts


def mask_party_name(value: Any) -> str:
    name = str(value or "").strip()
    characters = list(name)
    if len(characters) <= 1:
        return "访客"
    if not all("\u3400" <= character <= "\u9fff" for character in characters):
        return characters[0] + "***"
    for surname in DOUBLE_CHARACTER_SURNAMES:
        if name.startswith(surname) and len(characters) >= 3:
            return surname + "*"
    if len(characters) == 2:
        return characters[0] + "*"
    return characters[0] + "*" + characters[-1]


def remote_is_private_or_loopback(value: Optional[str] = None) -> bool:
    raw = value if value is not None else request.remote_addr
    try:
        address = ipaddress.ip_address(raw or "")
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_private)


def remote_is_loopback(value: Optional[str] = None) -> bool:
    raw = value if value is not None else request.remote_addr
    try:
        return ipaddress.ip_address(raw or "").is_loopback
    except ValueError:
        return False

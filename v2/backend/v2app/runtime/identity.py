from __future__ import annotations

import os
import re
import secrets
import time
import uuid
from pathlib import Path


_RACE_RETRIES = 20
_RACE_DELAY_SECONDS = 0.025


def _write_exclusive(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _wait_for_value(path: Path) -> str:
    for _attempt in range(_RACE_RETRIES):
        try:
            value = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            value = ""
        if value:
            return value
        time.sleep(_RACE_DELAY_SECONDS)
    raise RuntimeError(f"等待并发创建身份文件超时：{path.name}")


def load_or_create_secret(path: Path) -> str:
    try:
        if path.stat().st_size > 128:
            raise RuntimeError("系统会话密钥体积异常")
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        value = ""
    except RuntimeError:
        raise
    except (OSError, UnicodeError) as error:
        raise RuntimeError("无法读取系统会话密钥") from error
    if value:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise RuntimeError("系统会话密钥已损坏，拒绝启动")
        return value
    if path.exists():
        raise RuntimeError("系统会话密钥为空，拒绝启动")

    candidate = secrets.token_hex(32)
    try:
        _write_exclusive(path, candidate + "\n")
        value = candidate
    except FileExistsError:
        value = _wait_for_value(path)
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise RuntimeError("系统会话密钥创建后校验失败")
    return value


def load_existing_secret(path: Path) -> str:
    try:
        if path.stat().st_size > 128:
            raise RuntimeError("系统会话密钥体积异常")
        value = path.read_text(encoding="utf-8").strip()
    except RuntimeError:
        raise
    except (FileNotFoundError, OSError, UnicodeError) as error:
        raise RuntimeError("缺少或无法读取系统会话密钥") from error
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise RuntimeError("系统会话密钥已损坏")
    return value


def load_or_create_install_id(path: Path) -> str:
    try:
        if path.stat().st_size > 128:
            raise RuntimeError("安装标识文件过大")
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        value = ""
    except (OSError, UnicodeError) as error:
        raise RuntimeError("无法读取安装标识") from error
    if value:
        try:
            parsed = uuid.UUID(value)
        except ValueError as error:
            raise RuntimeError("安装标识已损坏，拒绝启动") from error
        if str(parsed) != value or parsed.version != 4 or parsed.variant != uuid.RFC_4122:
            raise RuntimeError("安装标识格式无效，拒绝启动")
        return value
    if path.exists():
        raise RuntimeError("安装标识为空，拒绝启动")

    candidate = str(uuid.uuid4())
    try:
        _write_exclusive(path, candidate + "\n")
        value = candidate
    except FileExistsError:
        value = _wait_for_value(path)
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise RuntimeError("安装标识创建后校验失败") from error
    if str(parsed) != value or parsed.version != 4:
        raise RuntimeError("安装标识创建后校验失败")
    return value


def load_existing_install_id(path: Path) -> str:
    try:
        if path.stat().st_size > 128:
            raise RuntimeError("安装标识文件过大")
        value = path.read_text(encoding="utf-8").strip()
    except RuntimeError:
        raise
    except (FileNotFoundError, OSError, UnicodeError) as error:
        raise RuntimeError("缺少或无法读取安装标识") from error
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise RuntimeError("安装标识已损坏") from error
    if str(parsed) != value or parsed.version != 4 or parsed.variant != uuid.RFC_4122:
        raise RuntimeError("安装标识格式无效")
    return value

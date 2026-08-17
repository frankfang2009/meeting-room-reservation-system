"""受控的 macOS 版本检查：只读取固定清单，绝不下载或执行任何更新内容。

本模块只在 macOS 自托管版（app/EDITION 标记）启用；Windows 版与开发态保持
关闭，以维持产品契约对“在线或自动升级”的排除。检查结果只服务管理员系统
状态页的展示；任何网络或格式失败都安静降级，不影响业务功能。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Optional
from urllib.request import ProxyHandler, build_opener

PRODUCT_SLUG = "meeting-room-reservation-system-v2"
MACOS_CHANNEL = "macos-selfhost"
RELEASES_INDEX_URL = (
    "https://github.com/frankfang2009/meeting-room-reservation-system/releases"
)
DEFAULT_MANIFEST_URL = (
    RELEASES_INDEX_URL + "/latest/download/latest-macos.json"
)
MANIFEST_TIMEOUT_SECONDS = 10.0
MANIFEST_MAX_BYTES = 64 * 1024
PERIODIC_INTERVAL_SECONDS = 24 * 60 * 60
MANUAL_THROTTLE_SECONDS = 60
SIDECAR_NAME = "update-check.json"

_VERSION_PATTERN = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")

# 单例锁串行化“读状态-请求-写状态”，避免并发检查互相覆盖。
_state_lock = threading.RLock()


class UpdateCheckError(RuntimeError):
    """版本检查的内部失败；对外一律表现为“暂时无法检查更新”。"""


def normalize_version(value: Any) -> Optional[tuple[int, int, int]]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith(("V", "v")):
        text = text[1:]
    match = _VERSION_PATTERN.fullmatch(text)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def release_url(tag: Any) -> Optional[str]:
    if not isinstance(tag, str):
        return None
    version = normalize_version(tag[1:] if tag.startswith(("V", "v")) else None)
    if not tag.startswith(("v", "V")) or version is None:
        return None
    return f"{RELEASES_INDEX_URL}/tag/v{version[0]}.{version[1]}.{version[2]}"


def parse_manifest(raw: bytes) -> dict[str, str]:
    """校验清单：只接受严格 schema，且 tag 必须与 version 严格对应。"""

    if not raw or len(raw) > MANIFEST_MAX_BYTES:
        raise UpdateCheckError("清单为空或体积异常")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise UpdateCheckError("清单不是有效 JSON") from error
    if not isinstance(value, dict):
        raise UpdateCheckError("清单不是 JSON 对象")
    if value.get("product") != PRODUCT_SLUG:
        raise UpdateCheckError("清单产品标识不匹配")
    if value.get("channel") != MACOS_CHANNEL:
        raise UpdateCheckError("清单渠道不匹配")
    version = normalize_version(value.get("version"))
    if version is None:
        raise UpdateCheckError("清单版本号无效")
    tag = value.get("tag")
    if not isinstance(tag, str) or release_url(tag) is None:
        raise UpdateCheckError("清单发布标签无效")
    if tag.lstrip("Vv") != value["version"].strip().lstrip("Vv"):
        raise UpdateCheckError("清单标签与版本不一致")
    return {
        "version": f"{version[0]}.{version[1]}.{version[2]}",
        "tag": f"v{version[0]}.{version[1]}.{version[2]}",
    }


def _utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _parse_utc(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return dt.datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def sidecar_path(data_dir: Path) -> Path:
    return Path(data_dir) / SIDECAR_NAME


def _empty_state() -> dict[str, Any]:
    return {
        "schema": 1,
        "lastAttemptAtUtc": None,
        "lastSuccessAtUtc": None,
        "lastErrorAtUtc": None,
        "latestVersion": None,
        "latestTag": None,
    }


def load_state(data_dir: Path) -> dict[str, Any]:
    """读取检查状态；损坏或缺失时返回全新状态，绝不抛出。"""

    try:
        raw = sidecar_path(data_dir).read_bytes()
    except OSError:
        return _empty_state()
    if len(raw) > 16 * 1024:
        return _empty_state()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        return _empty_state()
    if not isinstance(value, dict) or value.get("schema") != 1:
        return _empty_state()
    state = _empty_state()
    for key in state:
        if key in value:
            state[key] = value[key]
    if state["latestVersion"] is not None and (
        normalize_version(state["latestVersion"]) is None
        or state["latestTag"] is None
        or release_url(state["latestTag"]) is None
    ):
        state["latestVersion"] = None
        state["latestTag"] = None
    return state


def _save_state(data_dir: Path, state: dict[str, Any]) -> None:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = sidecar_path(data_dir)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    encoded = (
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _fetch_manifest(url: str, timeout: float) -> bytes:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme == "https":
        pass
    elif parts.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}:
        # 回环 HTTP 只服务测试注入；生产清单地址固定为 HTTPS。
        pass
    else:
        raise UpdateCheckError("清单地址必须是 HTTPS")
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            raw = response.read(MANIFEST_MAX_BYTES + 1)
    except Exception as error:
        raise UpdateCheckError("清单请求失败") from error
    if len(raw) > MANIFEST_MAX_BYTES:
        raise UpdateCheckError("清单体积超限")
    return raw


def view(
    *,
    enabled: bool,
    data_dir: Path,
    current_version: str,
) -> dict[str, Any]:
    """为管理员系统状态页汇总当前检查状态；禁用时只返回开关。"""

    if not enabled:
        return {"enabled": False}
    state = load_state(data_dir)
    current = normalize_version(current_version)
    latest = normalize_version(state.get("latestVersion"))
    status = "unknown"
    latest_version: Optional[str] = None
    release: Optional[str] = None
    if current is not None and latest is not None:
        latest_version = state.get("latestVersion")
        release = release_url(state.get("latestTag"))
        status = "available" if latest > current else "current"
    return {
        "enabled": True,
        "status": status,
        "currentVersion": (
            f"{current[0]}.{current[1]}.{current[2]}" if current else None
        ),
        "latestVersion": latest_version,
        "releaseUrl": release,
        "lastCheckedAtUtc": (
            state.get("lastSuccessAtUtc") or state.get("lastAttemptAtUtc")
        ),
    }


def perform_check(
    *,
    data_dir: Path,
    current_version: str,
    url: str = DEFAULT_MANIFEST_URL,
    timeout: float = MANIFEST_TIMEOUT_SECONDS,
    throttle_seconds: int = MANUAL_THROTTLE_SECONDS,
    force: bool = False,
) -> tuple[bool, dict[str, Any]]:
    """执行一次检查并返回 (是否实际发起, 汇总视图)。

    任何网络、格式或状态文件失败都不会抛出到调用方业务路径；除
    `UpdateCheckError` 以外的异常也不允许逃逸（记为一次失败尝试）。
    """

    with _state_lock:
        state = load_state(data_dir)
        last_attempt = _parse_utc(state.get("lastAttemptAtUtc"))
        now = _utc_now()
        if (
            not force
            and last_attempt is not None
            and _parse_utc(now) - last_attempt < throttle_seconds
        ):
            return False, view(
                enabled=True, data_dir=data_dir, current_version=current_version
            )
        state["lastAttemptAtUtc"] = now
        try:
            manifest = parse_manifest(_fetch_manifest(url, timeout))
        except UpdateCheckError:
            state["lastErrorAtUtc"] = now
            _save_state(data_dir, state)
            return True, view(
                enabled=True, data_dir=data_dir, current_version=current_version
            )
        except Exception:
            state["lastErrorAtUtc"] = now
            _save_state(data_dir, state)
            return True, view(
                enabled=True, data_dir=data_dir, current_version=current_version
            )
        state["lastSuccessAtUtc"] = now
        state["lastErrorAtUtc"] = None
        state["latestVersion"] = manifest["version"]
        state["latestTag"] = manifest["tag"]
        _save_state(data_dir, state)
        return True, view(
            enabled=True, data_dir=data_dir, current_version=current_version
        )


def maybe_periodic_check(
    *,
    data_dir: Path,
    current_version: str,
    url: str = DEFAULT_MANIFEST_URL,
    interval_seconds: int = PERIODIC_INTERVAL_SECONDS,
) -> tuple[bool, dict[str, Any]]:
    """服务启动时调用的限频检查：距上次尝试不足间隔时直接跳过。"""

    with _state_lock:
        state = load_state(data_dir)
        last_attempt = _parse_utc(state.get("lastAttemptAtUtc"))
        if last_attempt is not None:
            import time as _time

            if _time.time() - last_attempt < interval_seconds:
                return False, view(
                    enabled=True,
                    data_dir=data_dir,
                    current_version=current_version,
                )
    return perform_check(
        data_dir=data_dir,
        current_version=current_version,
        url=url,
        throttle_seconds=MANUAL_THROTTLE_SECONDS,
    )

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Any


INSTALL_FIELDS = {
    "schema",
    "product_generation",
    "install_id",
    "installed_version",
    "installed_at_utc",
    "port",
    "setup_bind",
    "lan_bind",
    "setup_complete",
}


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
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


def sync_install_json(
    path: Path,
    *,
    install_id: str,
    setup_complete: bool,
) -> bool:
    """Mirror DB setup state into installer metadata without making it truth.

    Development and tests may have no install.json; that is a valid no-op. A
    present production file is validated before an atomic, field-preserving
    update. The caller must always derive setup_complete from SQLite.
    """

    value = load_install_json(path)
    if value is None:
        return False
    if value.get("install_id") != install_id:
        raise RuntimeError("install.json 与 install_id 不一致")
    if value["setup_complete"] == setup_complete:
        return True
    if value["setup_complete"] and not setup_complete:
        raise RuntimeError("拒绝将 install.json setup_complete 从 true 降级为 false")
    value["setup_complete"] = setup_complete
    _atomic_json(path, value)
    return True


def load_install_json(path: Path) -> Any:
    try:
        if path.stat().st_size > 64 * 1024:
            raise RuntimeError("install.json 体积异常")
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as error:
        raise RuntimeError("无法读取 install.json") from error
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError) as error:
        raise RuntimeError("install.json 无法解析") from error
    if not isinstance(value, dict) or set(value) != INSTALL_FIELDS:
        raise RuntimeError("install.json 字段不符合 V2 约定")
    if value.get("schema") != 1 or value.get("product_generation") != 2:
        raise RuntimeError("install.json 不属于 V2 安装")
    if not isinstance(value.get("install_id"), str) or not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        value["install_id"],
    ):
        raise RuntimeError("install.json 安装标识无效")
    if not isinstance(value.get("installed_version"), str) or not value[
        "installed_version"
    ]:
        raise RuntimeError("install.json 安装版本无效")
    if not isinstance(value.get("installed_at_utc"), str) or not value[
        "installed_at_utc"
    ]:
        raise RuntimeError("install.json 安装时间无效")
    if value.get("port") != 8080:
        raise RuntimeError("install.json 端口不是 8080")
    if value.get("setup_bind") != "127.0.0.1" or value.get("lan_bind") != "0.0.0.0":
        raise RuntimeError("install.json 绑定模式无效")
    if not isinstance(value.get("setup_complete"), bool):
        raise RuntimeError("install.json setup_complete 类型无效")
    return value

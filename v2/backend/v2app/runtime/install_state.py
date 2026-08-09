from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any


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

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError) as error:
        raise RuntimeError("install.json 无法解析") from error
    if not isinstance(value, dict):
        raise RuntimeError("install.json 格式无效")
    if value.get("schema") != 1 or value.get("product_generation") != 2:
        raise RuntimeError("install.json 不属于 V2 安装")
    if value.get("install_id") != install_id:
        raise RuntimeError("install.json 与 install_id 不一致")
    if value.get("port") != 8080:
        raise RuntimeError("install.json 端口不是 8080")
    if not isinstance(value.get("setup_complete"), bool):
        raise RuntimeError("install.json setup_complete 类型无效")
    if value["setup_complete"] == setup_complete:
        return True
    value["setup_complete"] = setup_complete
    _atomic_json(path, value)
    return True

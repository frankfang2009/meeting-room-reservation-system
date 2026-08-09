#!/usr/bin/env python3
"""后续 V2 累计更新的身份与数据保护基线。

V2.0.0 暂不交付更新包；本模块先锁定未来更新器必须遵守的边界：只读取明确
登记或明确传入的 V2 根目录、拒绝 V1、保护全部现场可变目录，并在写程序前
生成可核验的数据快照。它有意不包含任何 V1 兼容桥或磁盘扫描。
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from .installer_core import (
        GENERATION_FILE,
        INSTALLED_MANIFEST,
        INSTALL_ID_FILE,
        INSTALL_INFO,
        PRODUCT_GENERATION,
        RECEIPT_FILE,
        SERVICE_PORT,
        TRANSACTION_FILE,
        VERSION,
        VERSION_FILE,
        InstallerError,
        assert_plain_file,
        assert_plain_tree,
        atomic_write,
        canonical_uuid4,
        is_reparse_or_link,
        json_bytes,
        records_for_tree,
        safe_relative_path,
        tree_digest,
        version_tuple,
    )
except ImportError:
    from installer_core import (  # type: ignore
        GENERATION_FILE,
        INSTALLED_MANIFEST,
        INSTALL_ID_FILE,
        INSTALL_INFO,
        PRODUCT_GENERATION,
        RECEIPT_FILE,
        SERVICE_PORT,
        TRANSACTION_FILE,
        VERSION,
        VERSION_FILE,
        InstallerError,
        assert_plain_file,
        assert_plain_tree,
        atomic_write,
        canonical_uuid4,
        is_reparse_or_link,
        json_bytes,
        records_for_tree,
        safe_relative_path,
        tree_digest,
        version_tuple,
    )


REGISTRY_SUBKEY = r"Software\MeetingRoomReservationV2"
DATABASE_SCHEMA_VERSION = 1
EXPECTED_V2_TABLES = frozenset(
    {
        "app_meta",
        "system_settings",
        "users",
        "user_preferences",
        "global_tags",
        "rooms",
        "reservations",
        "reservation_slots",
        "reservation_events",
        "reminder_receipts",
        "api_tokens",
        "security_audit_log",
    }
)
PROTECTED_PREFIXES = (
    "_程序文件/data/",
    "_程序文件/backups/",
    "_程序文件/logs/",
)
PROTECTED_EXACT = frozenset(
    {
        "_程序文件/data",
        "_程序文件/backups",
        "_程序文件/logs",
        INSTALL_INFO,
        INSTALL_ID_FILE,
        VERSION_FILE,
        GENERATION_FILE,
        INSTALLED_MANIFEST,
        TRANSACTION_FILE,
        RECEIPT_FILE,
    }
)


class UpdatePolicyError(InstallerError):
    """未来 V2 更新包不符合代际、版本或数据保护约定。"""


@dataclass(frozen=True)
class V2InstallIdentity:
    root: Path
    install_id: str
    version: str
    setup_complete: bool
    database: Path
    install_info: Mapping[str, Any]


@dataclass(frozen=True)
class DataSnapshot:
    root: Path
    data_root: Path
    manifest_path: Path
    tree_sha256: str
    file_count: int


def resolve_install_root(explicit: Optional[Path] = None) -> Path:
    """解析唯一明确来源；绝不枚举磁盘、桌面、下载目录或相邻目录。"""

    if explicit is not None:
        return Path(explicit)
    environment = os.environ.get("MEETING_ROOM_V2_INSTALL_ROOT")
    if environment:
        return Path(environment)
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REGISTRY_SUBKEY) as key:
                value, kind = winreg.QueryValueEx(key, "InstallRoot")
            if kind != winreg.REG_SZ or not isinstance(value, str) or not value:
                raise UpdatePolicyError("V2 安装根注册表记录无效")
            return Path(value)
        except FileNotFoundError as error:
            raise UpdatePolicyError(
                "没有 V2 安装根登记；必须由用户明确提供目录，更新器不会扫描磁盘"
            ) from error
        except OSError as error:
            raise UpdatePolicyError("无法读取 V2 安装根登记") from error
    raise UpdatePolicyError("必须明确提供 V2 安装目录；更新器不会扫描磁盘")


def _read_json(path: Path, description: str) -> Mapping[str, Any]:
    assert_plain_file(path, description)
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf") or len(raw) > 1024 * 1024:
        raise UpdatePolicyError(f"{description}带 BOM 或体积异常")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise UpdatePolicyError(f"{description}不是有效 UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise UpdatePolicyError(f"{description}必须是 JSON 对象")
    return value


def _read_text(path: Path, description: str) -> str:
    assert_plain_file(path, description)
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf") or len(raw) > 4096:
        raise UpdatePolicyError(f"{description}带 BOM 或体积异常")
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise UpdatePolicyError(f"{description}不是 UTF-8") from error


def _database_setup_state(database: Path) -> bool:
    assert_plain_file(database, "V2 数据库")
    try:
        uri = database.resolve(strict=True).as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=10)
    except sqlite3.Error as error:
        raise UpdatePolicyError("无法只读打开 V2 数据库") from error
    try:
        metadata = dict(
            connection.execute(
                "SELECT key, value FROM app_meta "
                "WHERE key IN ('product_generation', 'schema_version', 'setup_complete')"
            ).fetchall()
        )
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        users = int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        rooms = int(connection.execute("SELECT COUNT(*) FROM rooms").fetchone()[0])
        reservations = int(
            connection.execute("SELECT COUNT(*) FROM reservations").fetchone()[0]
        )
    except sqlite3.Error as error:
        raise UpdatePolicyError(
            "数据库缺少 V2 代际元数据；拒绝把 V1 或未知数据库当作 V2"
        ) from error
    finally:
        connection.close()
    if str(metadata.get("product_generation", "")).strip() != str(PRODUCT_GENERATION):
        raise UpdatePolicyError("数据库 product_generation 不是 2；拒绝更新")
    if str(metadata.get("schema_version", "")).strip() != str(DATABASE_SCHEMA_VERSION):
        raise UpdatePolicyError("V2 数据库 schema_version 不是受支持的 1")
    missing = EXPECTED_V2_TABLES - tables
    if missing:
        raise UpdatePolicyError("V2 数据库结构不完整：" + ", ".join(sorted(missing)))
    if integrity != [("ok",)] or foreign_keys:
        raise UpdatePolicyError("V2 数据库完整性或外键检查失败")
    setup_value = str(metadata.get("setup_complete", "")).strip()
    if setup_value not in {"0", "1"}:
        raise UpdatePolicyError("V2 数据库 setup_complete 元数据非法")
    setup_complete = setup_value == "1"
    if setup_complete and (users < 1 or rooms < 1):
        raise UpdatePolicyError("V2 数据库声称已设置，但缺少首名用户或笔录室")
    if not setup_complete and any((users, rooms, reservations)):
        raise UpdatePolicyError("V2 数据库尚未设置却已包含业务数据")
    return setup_complete


def load_v2_identity(root: Path) -> V2InstallIdentity:
    """只读取显式根目录内的固定身份文件。"""

    requested = Path(root).expanduser()
    if not requested.is_absolute():
        raise UpdatePolicyError("V2 安装目录必须是绝对路径")
    try:
        resolved = requested.resolve(strict=True)
    except OSError as error:
        raise UpdatePolicyError("V2 安装目录不存在或无法读取") from error
    if is_reparse_or_link(resolved) or not resolved.is_dir():
        raise UpdatePolicyError("V2 安装目录不能是链接、重解析点或特殊文件")
    info = _read_json(resolved / INSTALL_INFO, "V2 安装身份")
    expected_fields = {
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
    if set(info) != expected_fields or info.get("schema") != 1:
        raise UpdatePolicyError("V2 安装身份字段不符合约定")
    if info.get("product_generation") != PRODUCT_GENERATION:
        raise UpdatePolicyError("安装身份 product_generation 不是 2；拒绝 V1/未知安装")
    install_id = canonical_uuid4(info.get("install_id"))
    if install_id is None:
        raise UpdatePolicyError("V2 install_id 无效")
    if _read_text(resolved / INSTALL_ID_FILE, "V2 install_id 文件") != install_id:
        raise UpdatePolicyError("V2 install_id 文件与安装身份不一致")
    generation = _read_text(resolved / GENERATION_FILE, "V2 产品代际文件")
    if generation != str(PRODUCT_GENERATION):
        raise UpdatePolicyError("V2 产品代际文件不是 2")
    version = _read_text(resolved / VERSION_FILE, "V2 版本文件")
    if version_tuple(version) < version_tuple(VERSION):
        raise UpdatePolicyError("安装版本早于 V2.0.0，不属于可更新 V2 基线")
    if info.get("installed_version") != version:
        raise UpdatePolicyError("V2 安装身份版本与版本文件不一致")
    if info.get("port") != SERVICE_PORT:
        raise UpdatePolicyError("V2 安装身份端口不是固定 8080")
    setup_mirror = info.get("setup_complete")
    if not isinstance(setup_mirror, bool):
        raise UpdatePolicyError("V2 setup_complete 类型非法")
    installed_manifest = _read_json(resolved / INSTALLED_MANIFEST, "已安装发布清单")
    if (
        installed_manifest.get("product_generation") != PRODUCT_GENERATION
        or installed_manifest.get("kind") != "fresh-install"
    ):
        raise UpdatePolicyError("已安装发布清单不属于 V2 全新基线")
    database = resolved / "_程序文件" / "data" / "reservation.db"
    if database.exists():
        setup_complete = _database_setup_state(database)
        if setup_mirror is not setup_complete:
            raise UpdatePolicyError(
                "install.json 与 V2 数据库 setup_complete 不一致；"
                "必须先由服务启动自愈，更新器不会猜测或改写身份"
            )
    elif setup_mirror:
        raise UpdatePolicyError("首次设置已完成但 V2 数据库不存在")
    else:
        setup_complete = False
    return V2InstallIdentity(
        root=resolved,
        install_id=install_id,
        version=version,
        setup_complete=setup_complete,
        database=database,
        install_info=info,
    )


def assert_update_payload_safe(records: Sequence[Mapping[str, Any]]) -> None:
    """未来更新负载不得携带或覆盖任何现场可变数据。"""

    if not records:
        raise UpdatePolicyError("V2 更新负载文件清单为空")
    folded: dict[str, str] = {}
    for record in records:
        if not isinstance(record, Mapping) or "path" not in record:
            raise UpdatePolicyError("V2 更新负载记录缺少 path")
        relative = str(record["path"])
        safe_relative_path(relative)
        lower = relative.casefold()
        if lower in {path.casefold() for path in PROTECTED_EXACT} or any(
            lower.startswith(prefix.casefold()) for prefix in PROTECTED_PREFIXES
        ):
            raise UpdatePolicyError(f"V2 更新负载试图覆盖受保护现场文件：{relative}")
        if lower in folded:
            raise UpdatePolicyError(
                f"V2 更新负载包含 Windows 大小写冲突：{folded[lower]} / {relative}"
            )
        folded[lower] = relative


def snapshot_protected_data(identity: V2InstallIdentity, snapshot_root: Path) -> DataSnapshot:
    """在写程序前复制并哈希整个 data 树；不遗漏未知客户文件。"""

    destination = Path(snapshot_root)
    if destination.exists() or is_reparse_or_link(destination):
        raise UpdatePolicyError("V2 更新数据快照目标必须不存在")
    data_root = identity.root / "_程序文件" / "data"
    assert_plain_tree(data_root, "V2 data")
    destination.mkdir(parents=True)
    copied = destination / "data"
    try:
        shutil.copytree(data_root, copied)
        source_records = records_for_tree(data_root)
        copied_records = records_for_tree(copied)
        if source_records != copied_records:
            raise UpdatePolicyError("V2 data 快照复制后文件集合或哈希不一致")
        digest = tree_digest(copied_records)
        manifest = {
            "schema": 1,
            "kind": "v2-protected-data-snapshot",
            "product_generation": PRODUCT_GENERATION,
            "install_root": str(identity.root),
            "install_id": identity.install_id,
            "source_version": identity.version,
            "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "tree_sha256": digest,
            "files": list(copied_records),
        }
        manifest_path = destination / "snapshot-manifest.json"
        atomic_write(manifest_path, json_bytes(manifest))
        return DataSnapshot(
            root=destination,
            data_root=copied,
            manifest_path=manifest_path,
            tree_sha256=digest,
            file_count=len(copied_records),
        )
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def build_update_state(
    identity: V2InstallIdentity,
    target_version: str,
    target_payload_sha256: str,
) -> Mapping[str, Any]:
    if version_tuple(target_version) <= version_tuple(identity.version):
        raise UpdatePolicyError("V2 更新目标版本必须高于当前版本")
    if not isinstance(target_payload_sha256, str) or len(target_payload_sha256) != 64:
        raise UpdatePolicyError("V2 更新目标 payload SHA-256 格式非法")
    try:
        int(target_payload_sha256, 16)
    except ValueError as error:
        raise UpdatePolicyError("V2 更新目标 payload SHA-256 格式非法") from error
    return {
        "schema": 1,
        "kind": "v2-cumulative-update",
        "product_generation": PRODUCT_GENERATION,
        "transaction_id": uuid.uuid4().hex,
        "install_root": str(identity.root),
        "install_id": identity.install_id,
        "source_version": identity.version,
        "target_version": target_version,
        "target_payload_sha256": target_payload_sha256,
        "stage": "preflight",
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


class V2UpdatePreflight:
    """未来累计更新器在停服务和写文件前必须调用的共同入口。"""

    def __init__(
        self,
        install_root: Path,
        target_version: str,
        target_payload_sha256: str,
        payload_records: Sequence[Mapping[str, Any]],
    ) -> None:
        self.identity = load_v2_identity(install_root)
        assert_update_payload_safe(payload_records)
        self.state = build_update_state(
            self.identity,
            target_version,
            target_payload_sha256,
        )

    def snapshot(self, rollback_root: Path) -> DataSnapshot:
        return snapshot_protected_data(self.identity, rollback_root)

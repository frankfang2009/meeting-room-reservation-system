#!/usr/bin/env python3
"""V2 离线累计更新的身份、数据保护与事务核心。

本模块只接受明确登记或明确传入的 V2 根目录，不扫描磁盘，不读取、
迁移或删除 V1 数据。所有程序替换均位于可回滚事务内；现场 data、backups、
logs、install_id 与 secret 不属于更新负载。
"""

from __future__ import annotations

import datetime as dt
import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
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
        ExclusiveLock,
        EventLog,
        WindowsSystemController,
        assert_record_sets_equal,
        assert_plain_file,
        assert_plain_tree,
        atomic_write,
        canonical_uuid4,
        is_reparse_or_link,
        json_bytes,
        production_install_root,
        records_for_tree,
        safe_relative_path,
        sha256_bytes,
        sha256_file,
        tree_digest,
        validate_records,
        version_tuple,
        windows_filesystem_acl_policy_script,
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
        ExclusiveLock,
        EventLog,
        WindowsSystemController,
        assert_record_sets_equal,
        assert_plain_file,
        assert_plain_tree,
        atomic_write,
        canonical_uuid4,
        is_reparse_or_link,
        json_bytes,
        production_install_root,
        records_for_tree,
        safe_relative_path,
        sha256_bytes,
        sha256_file,
        tree_digest,
        validate_records,
        version_tuple,
        windows_filesystem_acl_policy_script,
    )


PRODUCTION_UPDATE_SUPPORTED = True
REGISTRY_SUBKEY = r"Software\MeetingRoomReservationV2"
MIN_UPDATABLE_VERSION = "2.0.0"
SUPPORTED_SOURCE_VERSIONS = frozenset({"2.1.0"})
# 升级器在替换程序文件并启动新服务后仍会终验数据库：届时服务已把
# schema 迁移到当前版本（v4，回执表重建为 notice_receipts、新增
# handover_requests），因此接受 1/2/3/4 四种版本，回执表按版本取
# reminder_receipts 或 notice_receipts 之一，交接表只对 v4 校验存在性。
DATABASE_SCHEMA_VERSION = 4
SUPPORTED_DATABASE_SCHEMA_VERSIONS = frozenset({1, 2, 3, DATABASE_SCHEMA_VERSION})
HANDOVER_TABLE_MIN_VERSION = 4
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
        "api_tokens",
        "security_audit_log",
    }
)
RECEIPTS_TABLES = frozenset({"reminder_receipts", "notice_receipts"})
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
        "_程序文件/update-transaction.json",
        "_程序文件/update-receipt.json",
    }
)

UPDATE_TRANSACTION_FILE = "_程序文件/update-transaction.json"
UPDATE_RECEIPT_FILE = "_程序文件/update-receipt.json"
UPDATE_LOCK_FILE = "_程序文件/data/maintenance.lock"
UPDATE_TOOL_MANIFEST = "manifest.json"
UPDATE_PAYLOAD_NAME = "payload-update.zip"


def re_fullmatch_hex32(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 32:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.casefold()


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
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REGISTRY_SUBKEY) as key:
                value, kind = winreg.QueryValueEx(key, "InstallRoot")
            if kind != winreg.REG_SZ or not isinstance(value, str) or not value:
                raise UpdatePolicyError("V2 安装根注册表记录无效")
        except FileNotFoundError as error:
            raise UpdatePolicyError(
                "没有 V2 安装根登记；必须由用户明确提供目录，更新器不会扫描磁盘"
            ) from error
        except OSError as error:
            raise UpdatePolicyError("无法读取 V2 安装根登记") from error
        registered = Path(value).expanduser()
        if not registered.is_absolute():
            raise UpdatePolicyError("V2 安装根注册表记录无效")
        try:
            resolved = registered.resolve(strict=True)
            expected = production_install_root().resolve(strict=True)
        except OSError as error:
            raise UpdatePolicyError("V2 安装根注册表记录无效") from error
        if os.path.normcase(os.path.abspath(str(resolved))) != os.path.normcase(
            os.path.abspath(str(expected))
        ):
            raise UpdatePolicyError("V2 安装根登记与固定生产目录不一致")
        return resolved
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
    schema_version = str(metadata.get("schema_version", "")).strip()
    if schema_version not in {
        str(version) for version in SUPPORTED_DATABASE_SCHEMA_VERSIONS
    }:
        raise UpdatePolicyError(
            "V2 数据库 schema_version 不是可迁移的 1、2、3 或当前 "
            f"{DATABASE_SCHEMA_VERSION}"
        )
    missing = EXPECTED_V2_TABLES - tables
    if missing:
        raise UpdatePolicyError("V2 数据库结构不完整：" + ", ".join(sorted(missing)))
    if not (RECEIPTS_TABLES & tables):
        raise UpdatePolicyError("V2 数据库结构不完整：缺少提醒回执表")
    if (
        schema_version.isdigit()
        and int(schema_version) >= HANDOVER_TABLE_MIN_VERSION
        and "handover_requests" not in tables
    ):
        raise UpdatePolicyError("V2 数据库结构不完整：缺少交接请求表")
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
    if version_tuple(version) < version_tuple(MIN_UPDATABLE_VERSION):
        raise UpdatePolicyError("安装版本早于 V2.0.0，不属于可更新 V2 基线")
    if info.get("installed_version") != version:
        raise UpdatePolicyError("V2 安装身份版本与版本文件不一致")
    if info.get("port") != SERVICE_PORT:
        raise UpdatePolicyError("V2 安装身份端口不是固定 8080")
    setup_mirror = info.get("setup_complete")
    if not isinstance(setup_mirror, bool):
        raise UpdatePolicyError("V2 setup_complete 类型非法")
    installed_manifest = _read_json(resolved / INSTALLED_MANIFEST, "已安装发布清单")
    if installed_manifest.get("product_generation") != PRODUCT_GENERATION or (
        installed_manifest.get("kind") not in {"fresh-install", "v2-cumulative-update"}
    ):
        raise UpdatePolicyError("已安装发布清单不属于 V2 安装或累计更新")
    manifest_version = installed_manifest.get("version")
    if manifest_version != version:
        raise UpdatePolicyError("已安装发布清单与版本文件不一致")
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


def read_installed_version(root: Path) -> str:
    """提权确认只读取对普通用户开放的版本文件；data/ 内身份仅管理员可读。"""

    version = _read_text(Path(root) / VERSION_FILE, "V2 版本文件")
    if version_tuple(version) < version_tuple(MIN_UPDATABLE_VERSION):
        raise UpdatePolicyError("安装版本早于 V2.0.0，不属于可更新 V2 基线")
    return version


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


@dataclass(frozen=True)
class UpdatePayload:
    zip_path: Path
    zip_sha256: str
    tree_sha256: str
    records: tuple[Mapping[str, Any], ...]
    files: Mapping[str, bytes]


@dataclass(frozen=True)
class UpdateBundle:
    """已经反向校验的离线累计更新包。"""

    tool_root: Path
    manifest: Mapping[str, Any]
    manifest_sha256: str
    payload: UpdatePayload
    supported_source_versions: frozenset[str]

    @classmethod
    def load(cls, tool_root: Path) -> "UpdateBundle":
        root = Path(tool_root).resolve(strict=True)
        assert_plain_tree(root, "V2 更新工具目录")
        manifest_path = root / UPDATE_TOOL_MANIFEST
        assert_plain_file(manifest_path, "V2 更新清单")
        raw = manifest_path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf") or len(raw) > 8 * 1024 * 1024:
            raise UpdatePolicyError("V2 更新清单带 BOM 或体积异常")
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise UpdatePolicyError("V2 更新清单不是有效 UTF-8 JSON") from error
        expected = {
            "schema",
            "kind",
            "product_generation",
            "release",
            "version",
            "supported_source_versions",
            "payload",
            "runtime",
            "tool",
            "supply_chain",
            "acceptance",
        }
        if not isinstance(manifest, dict) or set(manifest) != expected:
            raise UpdatePolicyError("V2 更新清单顶层字段不符合约定")
        if (
            manifest["schema"] != 1
            or manifest["kind"] != "v2-cumulative-update"
            or manifest["product_generation"] != PRODUCT_GENERATION
            or manifest["version"] != VERSION
            or manifest["release"] != f"V{VERSION}"
        ):
            raise UpdatePolicyError("更新包不是当前受支持的 V2 累计更新")
        sources = manifest["supported_source_versions"]
        if (
            not isinstance(sources, list)
            or not sources
            or any(not isinstance(item, str) for item in sources)
            or len(set(sources)) != len(sources)
        ):
            raise UpdatePolicyError("更新包支持版本矩阵非法")
        for source in sources:
            if version_tuple(source) >= version_tuple(VERSION):
                raise UpdatePolicyError("更新包支持版本必须早于目标版本")
        acceptance = manifest["acceptance"]
        if not isinstance(acceptance, dict) or acceptance != {
            "status": "candidate",
            "formal_external_release_allowed": False,
        }:
            raise UpdatePolicyError("未经实机与签名验收的更新包必须保持候选状态")
        cls._validate_evidence(manifest["supply_chain"])
        cls._validate_tool_files(root, manifest["tool"])
        cls._validate_runtime(root, manifest["runtime"])
        payload = cls._load_payload(root, manifest["payload"])
        return cls(
            tool_root=root,
            manifest=manifest,
            manifest_sha256=sha256_bytes(raw),
            payload=payload,
            supported_source_versions=frozenset(sources),
        )

    @staticmethod
    def _validate_evidence(value: Any) -> None:
        if not isinstance(value, dict) or set(value) != {
            "frontend_components_sha256",
            "runtime_provenance_sha256",
            "sbom_sha256",
            "third_party_notices_sha256",
        }:
            raise UpdatePolicyError("更新包供应链证据字段不完整")
        for digest in value.values():
            if not isinstance(digest, str) or len(digest) != 64:
                raise UpdatePolicyError("更新包供应链 SHA-256 非法")
            try:
                int(digest, 16)
            except ValueError as error:
                raise UpdatePolicyError("更新包供应链 SHA-256 非法") from error

    @staticmethod
    def _validate_tool_files(root: Path, value: Any) -> None:
        if not isinstance(value, dict) or set(value) != {"tree_sha256", "files"}:
            raise UpdatePolicyError("更新工具清单非法")
        records = validate_records(value["files"], "V2 更新工具")
        actual = []
        for record in records:
            path = root.joinpath(*str(record["path"]).split("/"))
            assert_plain_file(path, "V2 更新工具文件")
            actual.append(
                {"path": record["path"], "size": path.stat().st_size, "sha256": sha256_file(path)}
            )
        assert_record_sets_equal(records, tuple(actual), "V2 更新工具")
        if tree_digest(records) != value["tree_sha256"]:
            raise UpdatePolicyError("更新工具文件树摘要不一致")

    @staticmethod
    def _validate_runtime(root: Path, value: Any) -> None:
        if not isinstance(value, dict) or set(value) != {"directory", "tree_sha256", "files"}:
            raise UpdatePolicyError("更新 runtime 清单非法")
        if value["directory"] != "runtime":
            raise UpdatePolicyError("更新 runtime 目录不符合固定约定")
        records = validate_records(value["files"], "V2 更新 runtime")
        runtime = root / "runtime"
        actual = records_for_tree(runtime)
        assert_record_sets_equal(records, actual, "V2 更新 runtime")
        if tree_digest(actual) != value["tree_sha256"]:
            raise UpdatePolicyError("更新 runtime 文件树摘要不一致")

    @staticmethod
    def _load_payload(root: Path, value: Any) -> UpdatePayload:
        if not isinstance(value, dict) or set(value) != {
            "file", "size", "sha256", "tree_sha256", "files"
        }:
            raise UpdatePolicyError("更新 payload 清单非法")
        if value["file"] != UPDATE_PAYLOAD_NAME:
            raise UpdatePolicyError("更新 payload 文件名不符合约定")
        path = root / UPDATE_PAYLOAD_NAME
        assert_plain_file(path, "V2 更新 payload")
        if path.stat().st_size != value["size"] or sha256_file(path) != value["sha256"]:
            raise UpdatePolicyError("更新 payload 体积或 SHA-256 不一致")
        records = validate_records(value["files"], "V2 更新 payload")
        assert_update_payload_safe(records)
        if tree_digest(records) != value["tree_sha256"]:
            raise UpdatePolicyError("更新 payload 文件树摘要不一致")
        expected = {str(record["path"]): record for record in records}
        files: dict[str, bytes] = {}
        try:
            archive = zipfile.ZipFile(path, "r")
        except (OSError, zipfile.BadZipFile) as error:
            raise UpdatePolicyError("更新 payload 不是有效 ZIP") from error
        with archive:
            for info in archive.infolist():
                relative = info.filename
                safe_relative_path(relative)
                if info.is_dir() or relative.endswith("/") or relative in files:
                    raise UpdatePolicyError(f"更新 payload 含非法或重复条目：{relative}")
                mode = (info.external_attr >> 16) & 0xFFFF
                if mode and (mode & 0o170000) not in {0, 0o100000}:
                    raise UpdatePolicyError(f"更新 payload 含非普通文件：{relative}")
                content = archive.read(info)
                record = expected.get(relative)
                if record is None or len(content) != record["size"] or sha256_bytes(content) != record["sha256"]:
                    raise UpdatePolicyError(f"更新 payload 文件校验失败：{relative}")
                files[relative] = content
        if set(files) != set(expected):
            raise UpdatePolicyError("更新 payload 实际文件集与清单不一致")
        required = {
            "_程序文件/app/service.py",
            "_程序文件/app/static/index.html",
            "_程序文件/app/static/help/index.html",
            "_程序文件/runtime/python.exe",
            "_程序文件/runtime/pythonw.exe",
        }
        if not required.issubset(files):
            raise UpdatePolicyError("更新 payload 缺少服务或前端入口")
        return UpdatePayload(
            zip_path=path,
            zip_sha256=str(value["sha256"]),
            tree_sha256=str(value["tree_sha256"]),
            records=records,
            files=files,
        )


class UpdateSystemController:
    """只管理已核验属于当前 install_id 的 V2 资源。"""

    def capture_and_stop(self, identity: V2InstallIdentity) -> Mapping[str, Any]:
        raise NotImplementedError

    def capture_and_stop_fail_closed(
        self, identity: V2InstallIdentity
    ) -> Mapping[str, Any]:
        raise NotImplementedError

    def start_for_health(self, identity: V2InstallIdentity) -> None:
        raise NotImplementedError

    def restore(self, identity: V2InstallIdentity, state: Mapping[str, Any]) -> None:
        raise NotImplementedError

    def apply_security(self, identity: V2InstallIdentity) -> None:
        raise NotImplementedError

    def verify(self, identity: V2InstallIdentity) -> None:
        raise NotImplementedError


class PassiveUpdateSystemController(UpdateSystemController):
    def __init__(self, *, running: bool = True) -> None:
        self.running = running
        self.startup_enabled = running
        self.backup_enabled = running
        self.backup_running = False
        self.manual_firewall_enabled = running
        self.background_firewall_enabled = running
        self.verifications = 0
        self.security_applications = 0

    def capture_and_stop(self, identity: V2InstallIdentity) -> Mapping[str, Any]:
        del identity
        state = {
            "service_running": self.running,
            "service_task_enabled": self.startup_enabled,
            "backup_task_enabled": self.backup_enabled,
            "backup_task_running": self.backup_running,
            "manual_firewall_enabled": self.manual_firewall_enabled,
            "background_firewall_enabled": self.background_firewall_enabled,
        }
        self.running = False
        self.backup_running = False
        return state

    def capture_and_stop_fail_closed(
        self, identity: V2InstallIdentity
    ) -> Mapping[str, Any]:
        return self.capture_and_stop(identity)

    def start_for_health(self, identity: V2InstallIdentity) -> None:
        del identity
        self.running = True

    def restore(self, identity: V2InstallIdentity, state: Mapping[str, Any]) -> None:
        del identity
        self.running = bool(state["service_running"])
        self.startup_enabled = bool(state["service_task_enabled"])
        self.backup_enabled = bool(state["backup_task_enabled"])
        self.backup_running = bool(state["backup_task_running"])
        self.manual_firewall_enabled = bool(state["manual_firewall_enabled"])
        self.background_firewall_enabled = bool(state["background_firewall_enabled"])

    def apply_security(self, identity: V2InstallIdentity) -> None:
        del identity
        self.security_applications += 1

    def verify(self, identity: V2InstallIdentity) -> None:
        del identity
        self.verifications += 1


class WindowsUpdateSystemController(UpdateSystemController):
    """Windows 实现复用现有 install_id、任务、防火墙和 ACL 强校验。"""

    def __init__(self) -> None:
        self.base = WindowsSystemController()

    def capture_and_stop(self, identity: V2InstallIdentity) -> Mapping[str, Any]:
        return self._capture_and_stop(identity, restore_on_error=True)

    def capture_and_stop_fail_closed(
        self, identity: V2InstallIdentity
    ) -> Mapping[str, Any]:
        return self._capture_and_stop(identity, restore_on_error=False)

    def _capture_and_stop(
        self, identity: V2InstallIdentity, *, restore_on_error: bool
    ) -> Mapping[str, Any]:
        script = r"""
$ErrorActionPreference='Stop'
$identity='MeetingRoomReservationV2:'+$env:MRV2_INSTALL_ID
$registered=Get-ItemProperty -LiteralPath $env:MRV2_REGISTRY_KEY -ErrorAction Stop
if ([string]$registered.InstallRoot -ne $env:MRV2_ROOT -or [string]$registered.InstallId -ne $env:MRV2_INSTALL_ID -or [string]$registered.SecurityInstallId -ne $env:MRV2_INSTALL_ID) { throw 'V2 登记身份不一致。' }
$main=Get-ScheduledTask -TaskPath $env:MRV2_TASK_PATH -TaskName $env:MRV2_TASK_NAME -ErrorAction Stop
$backup=Get-ScheduledTask -TaskPath $env:MRV2_TASK_PATH -TaskName $env:MRV2_BACKUP_TASK_NAME -ErrorAction Stop
foreach ($task in @($main,$backup)) { if ([string]$task.Description -ne $identity -or [string]$task.Principal.UserId -ne 'SYSTEM') { throw 'V2 任务身份不一致。' } }
$manual=Get-NetFirewallRule -DisplayName $env:MRV2_FW_MANUAL -ErrorAction Stop
$background=Get-NetFirewallRule -DisplayName $env:MRV2_FW_BACKGROUND -ErrorAction Stop
foreach ($rule in @($manual,$background)) { if ([string]$rule.Description -ne $identity) { throw 'V2 防火墙身份不一致。' } }
$state=[ordered]@{service_running=([string]$main.State -eq 'Running');service_task_enabled=([string]$main.State -ne 'Disabled');backup_task_enabled=([string]$backup.State -ne 'Disabled');backup_task_running=([string]$backup.State -eq 'Running');manual_firewall_enabled=([string]$manual.Enabled -eq 'True');background_firewall_enabled=([string]$background.Enabled -eq 'True')}
"""
        if restore_on_error:
            script += r"""
try {
  foreach ($task in @($main,$backup)) { Stop-ScheduledTask -InputObject $task -ErrorAction SilentlyContinue; Disable-ScheduledTask -InputObject $task | Out-Null }
  foreach ($rule in @($manual,$background)) { Disable-NetFirewallRule -InputObject $rule }
} catch {
  if ($state.service_task_enabled) { Enable-ScheduledTask -InputObject $main | Out-Null }
  if ($state.backup_task_enabled) { Enable-ScheduledTask -InputObject $backup | Out-Null }
  if ($state.manual_firewall_enabled) { Enable-NetFirewallRule -InputObject $manual }
  if ($state.background_firewall_enabled) { Enable-NetFirewallRule -InputObject $background }
  if ($state.service_running) { Start-ScheduledTask -InputObject $main }
  if ($state.backup_task_running) { Start-ScheduledTask -InputObject $backup }
  throw
}
"""
        else:
            script += r"""
foreach ($task in @($main,$backup)) { Stop-ScheduledTask -InputObject $task -ErrorAction Stop }
foreach ($task in @($main,$backup)) { Disable-ScheduledTask -InputObject $task -ErrorAction Stop | Out-Null }
foreach ($rule in @($manual,$background)) { Disable-NetFirewallRule -InputObject $rule -ErrorAction Stop }
$stoppedMain=Get-ScheduledTask -TaskPath $env:MRV2_TASK_PATH -TaskName $env:MRV2_TASK_NAME -ErrorAction Stop
$stoppedBackup=Get-ScheduledTask -TaskPath $env:MRV2_TASK_PATH -TaskName $env:MRV2_BACKUP_TASK_NAME -ErrorAction Stop
if ([string]$stoppedMain.State -eq 'Running' -or [string]$stoppedBackup.State -eq 'Running') { throw 'V2 任务停止状态无法验证。' }
"""
        script += r"""
$state | ConvertTo-Json -Compress
"""
        output = self.base._run_powershell(
            script, self.base._environment(identity.root, identity.install_id)
        )
        try:
            state = json.loads(output.strip().splitlines()[-1])
        except (IndexError, ValueError) as error:
            raise UpdatePolicyError("无法读取 V2 更新前运行状态") from error
        expected = {
            "service_running", "service_task_enabled", "backup_task_enabled", "backup_task_running",
            "manual_firewall_enabled", "background_firewall_enabled",
        }
        if not isinstance(state, dict) or set(state) != expected or not all(
            isinstance(value, bool) for value in state.values()
        ):
            raise UpdatePolicyError("V2 更新前运行状态非法")
        return state

    def start_for_health(self, identity: V2InstallIdentity) -> None:
        self.base.activate(identity.root, identity.install_id)

    def restore(self, identity: V2InstallIdentity, state: Mapping[str, Any]) -> None:
        self.base.activate(identity.root, identity.install_id)
        environment = dict(self.base._environment(identity.root, identity.install_id))
        environment.update({f"MRV2_STATE_{key.upper()}": "1" if value else "0" for key, value in state.items()})
        script = r"""
$ErrorActionPreference='Stop'
$main=Get-ScheduledTask -TaskPath $env:MRV2_TASK_PATH -TaskName $env:MRV2_TASK_NAME -ErrorAction Stop
$backup=Get-ScheduledTask -TaskPath $env:MRV2_TASK_PATH -TaskName $env:MRV2_BACKUP_TASK_NAME -ErrorAction Stop
if ($env:MRV2_STATE_SERVICE_RUNNING -ne '1') { Stop-ScheduledTask -InputObject $main -ErrorAction SilentlyContinue }
if ($env:MRV2_STATE_SERVICE_TASK_ENABLED -ne '1') { Disable-ScheduledTask -InputObject $main | Out-Null }
if ($env:MRV2_STATE_BACKUP_TASK_ENABLED -ne '1') { Disable-ScheduledTask -InputObject $backup | Out-Null }
if ($env:MRV2_STATE_BACKUP_TASK_RUNNING -eq '1') { Start-ScheduledTask -InputObject $backup }
if ($env:MRV2_STATE_MANUAL_FIREWALL_ENABLED -ne '1') { Disable-NetFirewallRule -DisplayName $env:MRV2_FW_MANUAL }
if ($env:MRV2_STATE_BACKGROUND_FIREWALL_ENABLED -ne '1') { Disable-NetFirewallRule -DisplayName $env:MRV2_FW_BACKGROUND }
"""
        self.base._run_powershell(script, environment)

    def apply_security(self, identity: V2InstallIdentity) -> None:
        # 更新和回滚都可能换入新目录；始终复用全新安装的
        # 同一份文件系统策略，不只修补 app/runtime 两个程序根。
        script = r"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($env:MRV2_ROOT).TrimEnd('\')
$program = Join-Path $root '_程序文件'
""" + windows_filesystem_acl_policy_script()
        self.base._run_powershell(
            script, self.base._environment(identity.root, identity.install_id)
        )

    def verify(self, identity: V2InstallIdentity) -> None:
        self.base.verify_security(identity.root, identity.install_id)


BACKUP_NAME_PREFIX = "reservation-v2-backup-"


def _backup_sequence_filename_floor(backup_dir: Path) -> int:
    """取备份目录文件名中的最大序列，含当前安装无法解析的 sidecar。

    健康检查失败的更新尝试会由换入的新版本运行时在 backups/ 留下带更高
    databaseSchemaVersion 的备份；旧版本代码扫描 sidecar 时会跳过无法
    解析的文件，但备份文件名里的序列已被占用，序列分配必须避开。
    """

    floor = 0
    for suffix, stem_width in ((".db", 3), (".json", 5)):
        for path in backup_dir.glob(f"{BACKUP_NAME_PREFIX}*{suffix}"):
            stem = path.name[len(BACKUP_NAME_PREFIX) : -stem_width]
            if len(stem) == 8 and stem.isdigit():
                floor = max(floor, int(stem))
    return floor


def reconcile_backup_sequence_floor(identity: V2InstallIdentity) -> None:
    """V241-B1：重试更新前把旧数据库的备份序列水位抬到文件名下限。

    回滚会把 data 树恢复到在线备份之前的快照，backup_sequence 水位随
    之回落，但被占用的备份文件名仍在。更新前在线备份由当前安装的旧
    版本代码执行，其序列预留只认可解析的 sidecar，会把新序列瞄准已
    存在的文件并以"拒绝覆盖"失败。这里仅当水位低于文件名下限时做
    单行原子上调；水位是产品自身维护的单调键，上调不会复用或覆盖
    任何既有备份。
    """

    database = identity.root / "_程序文件" / "data" / "reservation.db"
    backup_dir = identity.root / "_程序文件" / "backups"
    if not database.is_file() or not backup_dir.is_dir():
        return
    floor = _backup_sequence_filename_floor(backup_dir)
    if floor <= 0:
        return
    db = sqlite3.connect(database, timeout=10)
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT value FROM app_meta WHERE key = 'backup_sequence'"
        ).fetchone()
        current = int(row[0]) if row is not None and str(row[0]).isdigit() else 0
        if floor > current:
            db.execute(
                """
                INSERT INTO app_meta (key, value) VALUES ('backup_sequence', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(floor),),
            )
            db.execute("COMMIT")
    finally:
        db.close()


def default_online_backup(identity: V2InstallIdentity) -> None:
    if not identity.setup_complete:
        return
    reconcile_backup_sequence_floor(identity)
    python = identity.root / "_程序文件" / "runtime" / "python.exe"
    backup = identity.root / "_程序文件" / "app" / "backup.py"
    assert_plain_file(python, "V2 冻结 Python")
    assert_plain_file(backup, "V2 在线备份入口")
    process = subprocess.run(
        [str(python), str(backup), "--expected-install-id", identity.install_id],
        cwd=str(backup.parent),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if process.returncode != 0:
        raise UpdatePolicyError("更新前在线备份失败：" + (process.stderr or process.stdout).strip())


def default_update_health_probe(identity: V2InstallIdentity, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last_error = "尚未响应"
    while time.monotonic() < deadline:
        try:
            with opener.open(f"http://127.0.0.1:{SERVICE_PORT}/healthz", timeout=1) as response:
                raw = response.read(16 * 1024 + 1)
            payload = json.loads(raw.decode("utf-8"))
            if len(raw) > 16 * 1024 or not isinstance(payload, dict):
                raise UpdatePolicyError("更新后健康响应异常")
            expected = {
                "ok": True,
                "product_generation": PRODUCT_GENERATION,
                "install_id": identity.install_id,
                "setup_complete": identity.setup_complete,
                "port": SERVICE_PORT,
            }
            if any(payload.get(key) != value for key, value in expected.items()):
                raise UpdatePolicyError(f"更新后健康身份不一致：{payload}")
            return
        except (OSError, UnicodeError, ValueError, urllib.error.URLError, UpdatePolicyError) as error:
            last_error = str(error)
            time.sleep(0.25)
    raise UpdatePolicyError(f"更新后服务未通过回环健康检查：{last_error}")


@dataclass(frozen=True)
class UpdateResult:
    install_root: Path
    source_version: str
    target_version: str
    receipt_path: Path


class UpdateRollbackError(UpdatePolicyError):
    """更新失败且自动回滚无法证明完成。"""


class V2UpdateTransaction:
    """V2.1.0 起源的第一代离线累计更新事务（目标版本随发布演进）。"""

    def __init__(
        self,
        bundle: UpdateBundle,
        install_root: Path,
        controller: UpdateSystemController,
        *,
        log: Optional[EventLog] = None,
        online_backup: Optional[Any] = default_online_backup,
        health_probe: Optional[Any] = default_update_health_probe,
        fault_hook: Optional[Any] = None,
    ) -> None:
        self.bundle = bundle
        self.install_root = Path(install_root)
        self.controller = controller
        self.log = log or EventLog()
        self.online_backup = online_backup
        self.health_probe = health_probe
        self.fault_hook = fault_hook

    def _stage(self, state: dict[str, Any], stage: str, state_path: Path) -> None:
        state["stage"] = stage
        atomic_write(state_path, json_bytes(state))
        self.log.write(f"V2 更新事务阶段：{stage}")
        if self.fault_hook is not None:
            self.fault_hook(stage)

    @staticmethod
    def _payload_root_files(payload: UpdatePayload) -> tuple[str, ...]:
        return tuple(
            relative for relative in payload.files
            if not relative.startswith(("_程序文件/app/", "_程序文件/runtime/"))
        )

    def _extract_staging(self, staging: Path) -> None:
        staging.mkdir()
        for relative, content in self.bundle.payload.files.items():
            destination = staging.joinpath(*relative.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                handle.write(content)
        actual = records_for_tree(staging)
        assert_record_sets_equal(self.bundle.payload.records, actual, "V2 更新 staging")
        if tree_digest(actual) != self.bundle.payload.tree_sha256:
            raise UpdatePolicyError("V2 更新 staging 文件树摘要不一致")

    def _snapshot_program(self, identity: V2InstallIdentity, rollback: Path) -> Mapping[str, Any]:
        program_snapshot = rollback / "program"
        program_snapshot.mkdir(parents=True)
        for name in ("app", "runtime"):
            source = identity.root / "_程序文件" / name
            assert_plain_tree(source, f"V2 已安装 {name}")
            shutil.copytree(source, program_snapshot / name)
        root_records = []
        for relative in self._payload_root_files(self.bundle.payload):
            path = identity.root.joinpath(*relative.split("/"))
            present = path.exists()
            if present:
                assert_plain_file(path, "V2 客户入口")
                destination = program_snapshot / "root" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
            root_records.append({"path": relative, "present": present})
        for relative in (VERSION_FILE, INSTALLED_MANIFEST, RECEIPT_FILE):
            path = identity.root.joinpath(*relative.split("/"))
            if path.exists():
                destination = program_snapshot / "protected" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
        value = {
            "schema": 1,
            "app_tree_sha256": tree_digest(records_for_tree(program_snapshot / "app")),
            "runtime_tree_sha256": tree_digest(records_for_tree(program_snapshot / "runtime")),
            "root_files": root_records,
        }
        atomic_write(program_snapshot / "manifest.json", json_bytes(value))
        return value

    @staticmethod
    def _cleanup_displaced_dirs(root: Path) -> None:
        """清除断电中断遗留的旧程序临时目录。

        调用点必须已经证明现行 app/runtime 完整（快照哈希或提交点身份），
        此时 displaced 只能是替换窗口中断的残渣；现行目录缺失时把旧目录
        放回原位是唯一保守选择。
        """

        program = root / "_程序文件"
        for name in ("app", "runtime"):
            displaced = program / f".update-displaced-{name}"
            if not displaced.exists():
                continue
            current = program / name
            if current.exists():
                shutil.rmtree(displaced)
            else:
                os.replace(displaced, current)

    def _restore(
        self,
        identity: V2InstallIdentity,
        rollback: Path,
        run_state: Mapping[str, Any],
        *,
        restore_controller: bool,
    ) -> None:
        program_snapshot = rollback / "program"
        manifest = _read_json(program_snapshot / "manifest.json", "V2 程序回滚清单")
        for name in ("app", "runtime"):
            current = identity.root / "_程序文件" / name
            if current.exists():
                shutil.rmtree(current)
            shutil.copytree(program_snapshot / name, current)
            if tree_digest(records_for_tree(current)) != manifest[f"{name}_tree_sha256"]:
                raise UpdateRollbackError(f"V2 旧 {name} 恢复后哈希不一致")
        self._cleanup_displaced_dirs(identity.root)
        for record in manifest["root_files"]:
            relative = str(record["path"])
            destination = identity.root.joinpath(*relative.split("/"))
            source = program_snapshot / "root" / relative
            if record["present"]:
                atomic_write(destination, source.read_bytes())
            else:
                with contextlib.suppress(FileNotFoundError):
                    destination.unlink()
        for relative in (VERSION_FILE, INSTALLED_MANIFEST, RECEIPT_FILE):
            destination = identity.root.joinpath(*relative.split("/"))
            source = program_snapshot / "protected" / relative
            if source.exists():
                atomic_write(destination, source.read_bytes())
            else:
                with contextlib.suppress(FileNotFoundError):
                    destination.unlink()
        data_snapshot = rollback / "protected-data" / "data"
        snapshot_manifest = _read_json(
            rollback / "protected-data" / "snapshot-manifest.json",
            "V2 数据回滚清单",
        )
        if (
            snapshot_manifest.get("install_id") != identity.install_id
            or snapshot_manifest.get("source_version") != identity.version
            or tree_digest(records_for_tree(data_snapshot))
            != snapshot_manifest.get("tree_sha256")
        ):
            raise UpdateRollbackError("V2 数据回滚快照身份或哈希不一致")

        # 先在 backups 下的私有回滚树中构建并校验完整恢复目录，
        # 完整换入 data 策略根后再统一固化 ACL。
        restored_data = rollback / "data-restore-candidate"
        displaced_data = rollback / "data-before-restore"
        data = identity.root / "_程序文件" / "data"
        expected_data_sha256 = snapshot_manifest["tree_sha256"]

        if restored_data.exists():
            if not restored_data.is_dir() or is_reparse_or_link(restored_data):
                raise UpdateRollbackError("V2 data 恢复候选目录非法")
            if tree_digest(records_for_tree(restored_data)) != expected_data_sha256:
                # 候选目录是精确事务 rollback 下的普通文件树，且
                # 绑定快照已验证。不完整候选可丢弃并从快照重建。
                shutil.rmtree(restored_data)
        if displaced_data.exists() and (
            not displaced_data.is_dir() or is_reparse_or_link(displaced_data)
        ):
            raise UpdateRollbackError("V2 data 恢复旧目录非法")

        if displaced_data.exists():
            if data.exists():
                if (
                    not data.is_dir()
                    or is_reparse_or_link(data)
                    or tree_digest(records_for_tree(data)) != expected_data_sha256
                ):
                    raise UpdateRollbackError("V2 data 恢复中断现场存在冲突")
                if restored_data.exists():
                    shutil.rmtree(restored_data)
            else:
                if not restored_data.exists():
                    raise UpdateRollbackError("V2 data 恢复中断现场不完整")
                os.replace(restored_data, data)
                if tree_digest(records_for_tree(data)) != expected_data_sha256:
                    raise UpdateRollbackError("V2 data 恢复后哈希不一致")
            shutil.rmtree(displaced_data)
        else:
            if data.is_dir() and tree_digest(records_for_tree(data)) == expected_data_sha256:
                if restored_data.exists():
                    shutil.rmtree(restored_data)
            else:
                if not data.is_dir() or is_reparse_or_link(data):
                    raise UpdateRollbackError("V2 现行 data 目录缺失或非法")
                if not restored_data.exists():
                    shutil.copytree(data_snapshot, restored_data)
                    if tree_digest(records_for_tree(restored_data)) != expected_data_sha256:
                        raise UpdateRollbackError("V2 data 恢复候选目录哈希不一致")
                os.replace(data, displaced_data)
                try:
                    os.replace(restored_data, data)
                except BaseException:
                    os.replace(displaced_data, data)
                    raise
                if tree_digest(records_for_tree(data)) != expected_data_sha256:
                    os.replace(data, restored_data)
                    os.replace(displaced_data, data)
                    raise UpdateRollbackError("V2 data 恢复后哈希不一致")
                shutil.rmtree(displaced_data)

        # 所有 program/root/data 替换完成后才重固化统一策略并完整验证。
        # 安全或身份验证失败时不得恢复原运行状态。
        self.controller.apply_security(identity)
        self.controller.verify(identity)
        load_v2_identity(identity.root)
        if restore_controller:
            self.controller.restore(identity, run_state)

    def _replace_program(self, identity: V2InstallIdentity, staging: Path) -> None:
        replaced: list[tuple[Path, Path]] = []
        try:
            for name in ("app", "runtime"):
                staged = staging / "_程序文件" / name
                current = identity.root / "_程序文件" / name
                displaced = identity.root / "_程序文件" / f".update-displaced-{name}"
                if displaced.exists():
                    raise UpdatePolicyError(f"V2 更新旧 {name} 临时目录已存在")
                os.replace(current, displaced)
                try:
                    os.replace(staged, current)
                except BaseException:
                    os.replace(displaced, current)
                    raise
                replaced.append((current, displaced))
            for _, displaced in replaced:
                shutil.rmtree(displaced)
        except BaseException:
            for current, displaced in reversed(replaced):
                if current.exists():
                    shutil.rmtree(current)
                if displaced.exists():
                    os.replace(displaced, current)
            raise
        for relative in self._payload_root_files(self.bundle.payload):
            content = self.bundle.payload.files[relative]
            atomic_write(identity.root.joinpath(*relative.split("/")), content)

    def _verify_installed_payload(self, identity: V2InstallIdentity) -> None:
        for record in self.bundle.payload.records:
            relative = str(record["path"])
            path = identity.root.joinpath(*relative.split("/"))
            assert_plain_file(path, "V2 更新后程序")
            if path.stat().st_size != record["size"] or sha256_file(path) != record["sha256"]:
                raise UpdatePolicyError(f"V2 更新后程序校验失败：{relative}")

    def _commit_identity(
        self,
        identity: V2InstallIdentity,
        state: dict[str, Any],
        state_path: Path,
    ) -> None:
        info = dict(identity.install_info)
        info["installed_version"] = VERSION
        atomic_write(identity.root / INSTALL_INFO, json_bytes(info))
        if self.fault_hook is not None:
            self.fault_hook("commit_install_info_written")
        atomic_write(identity.root / INSTALLED_MANIFEST, json_bytes(self.bundle.manifest))
        if self.fault_hook is not None:
            self.fault_hook("commit_manifest_written")
        atomic_write(identity.root / VERSION_FILE, f"{VERSION}\n".encode("ascii"))
        if self.fault_hook is not None:
            self.fault_hook("commit_version_written")
        state["stage"] = "version_committed"
        state["committed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        atomic_write(state_path, json_bytes(state))

    @staticmethod
    def _finalize_receipt(
        identity: V2InstallIdentity,
        state: dict[str, Any],
        state_path: Path,
    ) -> Path:
        receipt_path = identity.root / UPDATE_RECEIPT_FILE
        receipt = dict(state)
        receipt["stage"] = "complete"
        receipt["completed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        atomic_write(receipt_path, json_bytes(receipt))
        with contextlib.suppress(FileNotFoundError):
            state_path.unlink()
        return receipt_path

    def _load_identity_for_recovery(
        self,
        root: Path,
        state_path: Path,
    ) -> V2InstallIdentity:
        """允许同一更新包从三份版本身份的部分提交中恢复。"""

        state = _read_json(state_path, "V2 未完成更新事务")
        basic = {
            "schema",
            "kind",
            "product_generation",
            "transaction_id",
            "install_root",
            "install_id",
            "source_version",
            "target_version",
            "target_payload_sha256",
            "stage",
        }
        if not basic.issubset(state) or (
            state.get("schema") != 1
            or state.get("kind") != "v2-cumulative-update"
            or state.get("product_generation") != PRODUCT_GENERATION
            or state.get("install_root") != str(root)
            or state.get("target_version") != VERSION
            or state.get("target_payload_sha256") != self.bundle.payload.zip_sha256
        ):
            raise UpdatePolicyError("未完成更新事务与当前安装或更新包身份不一致")
        source_version = str(state.get("source_version"))
        if source_version not in self.bundle.supported_source_versions:
            raise UpdatePolicyError("未完成更新事务的来源版本不受当前更新包支持")
        install_id = canonical_uuid4(state.get("install_id"))
        txid = str(state.get("transaction_id"))
        if not re_fullmatch_hex32(txid):
            raise UpdatePolicyError("未完成更新事务 ID 非法")
        rollback = root / "_程序文件" / "backups" / "updates" / txid
        if (
            state.get("rollback_root") != str(rollback)
            or not rollback.is_dir()
            or is_reparse_or_link(rollback)
        ):
            raise UpdatePolicyError("未完成更新事务缺少可验证回滚材料")

        live_data = root / "_程序文件" / "data"
        identity_data = live_data
        if not live_data.exists():
            # data 目录的原子换入窗口中断时，只从已绑定事务的
            # 私有快照读取原身份，并同时验证待换入候选树。
            displaced_data = rollback / "data-before-restore"
            restored_data = rollback / "data-restore-candidate"
            identity_data = rollback / "protected-data" / "data"
            expected_data_sha256 = state.get("data_snapshot_sha256")
            if (
                not displaced_data.is_dir()
                or is_reparse_or_link(displaced_data)
                or not identity_data.is_dir()
                or is_reparse_or_link(identity_data)
                or not restored_data.is_dir()
                or is_reparse_or_link(restored_data)
                or not isinstance(expected_data_sha256, str)
                or tree_digest(records_for_tree(identity_data))
                != expected_data_sha256
                or tree_digest(records_for_tree(restored_data))
                != expected_data_sha256
            ):
                raise UpdatePolicyError("V2 data 恢复中断现场无法验证")
        if install_id is None or _read_text(identity_data / "install_id", "V2 install_id 文件") != install_id:
            raise UpdatePolicyError("未完成更新事务的 install_id 不一致")
        if _read_text(root / GENERATION_FILE, "V2 产品代际文件") != str(PRODUCT_GENERATION):
            raise UpdatePolicyError("未完成更新事务的产品代际不是 2")

        current_info = _read_json(identity_data / "install.json", "V2 当前安装身份")
        current_manifest = _read_json(root / INSTALLED_MANIFEST, "V2 当前发布清单")
        expected_info_fields = {
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
        if (
            set(current_info) != expected_info_fields
            or current_info.get("schema") != 1
            or current_info.get("product_generation") != PRODUCT_GENERATION
            or current_info.get("port") != SERVICE_PORT
            or not isinstance(current_info.get("setup_complete"), bool)
        ):
            raise UpdatePolicyError("未完成更新事务的当前安装身份字段非法")
        if (
            current_manifest.get("product_generation") != PRODUCT_GENERATION
            or current_manifest.get("kind")
            not in {"fresh-install", "v2-cumulative-update"}
        ):
            raise UpdatePolicyError("未完成更新事务的当前发布清单不属于 V2")
        current_versions = {
            str(current_info.get("installed_version")),
            str(current_manifest.get("version")),
            _read_text(root / VERSION_FILE, "V2 当前版本文件"),
        }
        if not current_versions.issubset({source_version, VERSION}):
            raise UpdatePolicyError("未完成更新事务出现未知或回退的版本身份")
        if current_info.get("install_id") != install_id:
            raise UpdatePolicyError("未完成更新事务的安装身份文件已变化")
        if current_versions == {VERSION} and live_data.exists():
            return load_v2_identity(root)
        if current_versions == {source_version} and live_data.exists():
            return load_v2_identity(root)

        snapshot_info = _read_json(
            rollback / "protected-data" / "data" / "install.json",
            "V2 更新前安装身份快照",
        )
        if (
            snapshot_info.get("install_id") != install_id
            or snapshot_info.get("product_generation") != PRODUCT_GENERATION
            or snapshot_info.get("installed_version") != source_version
        ):
            raise UpdatePolicyError("V2 更新前身份快照与未完成事务不一致")
        identity_database = identity_data / "reservation.db"
        if identity_database.exists():
            setup_complete = _database_setup_state(identity_database)
        else:
            setup_complete = False
        if snapshot_info.get("setup_complete") is not setup_complete:
            raise UpdatePolicyError("V2 更新恢复期间数据库与原安装身份不一致")
        return V2InstallIdentity(
            root=root,
            install_id=install_id,
            version=source_version,
            setup_complete=setup_complete,
            database=live_data / "reservation.db",
            install_info=snapshot_info,
        )

    def _recover_existing_transaction(
        self,
        identity: V2InstallIdentity,
        state_path: Path,
    ) -> V2InstallIdentity:
        state = _read_json(state_path, "V2 未完成更新事务")
        required = {
            "schema",
            "kind",
            "product_generation",
            "transaction_id",
            "install_root",
            "install_id",
            "source_version",
            "target_version",
            "target_payload_sha256",
            "stage",
            "created_at_utc",
            "rollback_root",
            "data_snapshot_sha256",
            "run_state",
        }
        if not required.issubset(state) or (
            state.get("schema") != 1
            or state.get("kind") != "v2-cumulative-update"
            or state.get("product_generation") != PRODUCT_GENERATION
            or state.get("install_root") != str(identity.root)
            or state.get("install_id") != identity.install_id
            or state.get("target_version") != VERSION
            or state.get("target_payload_sha256") != self.bundle.payload.zip_sha256
        ):
            raise UpdatePolicyError("未完成更新事务与当前安装或更新包身份不一致")
        txid = str(state["transaction_id"])
        if not re_fullmatch_hex32(txid):
            raise UpdatePolicyError("未完成更新事务 ID 非法")
        expected_rollback = identity.root / "_程序文件" / "backups" / "updates" / txid
        rollback = Path(str(state["rollback_root"]))
        if (
            rollback != expected_rollback
            or not rollback.is_dir()
            or is_reparse_or_link(rollback)
        ):
            raise UpdatePolicyError("未完成更新事务缺少可验证回滚材料")
        staging = identity.root / "_程序文件" / f".update-staging-{txid}"

        run_state = state.get("run_state")
        expected_state = {
            "service_running",
            "service_task_enabled",
            "backup_task_enabled",
            "backup_task_running",
            "manual_firewall_enabled",
            "background_firewall_enabled",
        }
        if not isinstance(run_state, dict) or set(run_state) != expected_state or not all(
            isinstance(value, bool) for value in run_state.values()
        ):
            raise UpdatePolicyError("未完成更新事务的原运行状态非法")

        # 版本文件是提交点。若三份身份已经全部指向目标版本，
        # 中断只可恢复原运行状态、补写回执和清理，不得回滚可能已产生的新数据。
        if identity.version == VERSION:
            self.controller.verify(identity)
            self.controller.capture_and_stop(identity)
            self.controller.verify(identity)
            self.controller.restore(identity, run_state)
            self._cleanup_displaced_dirs(identity.root)
            self._finalize_receipt(identity, dict(state), state_path)
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(rollback, ignore_errors=True)
            return load_v2_identity(identity.root)

        if state.get("source_version") != identity.version:
            raise UpdatePolicyError("未完成更新事务的源版本已变化")
        stage = str(state["stage"])
        if stage == "service_stopped_pre_snapshot":
            self.controller.verify(identity)
            self.controller.capture_and_stop(identity)
            self.controller.verify(identity)
            self.controller.restore(identity, run_state)
        else:
            snapshot_manifest = _read_json(
                rollback / "protected-data" / "snapshot-manifest.json",
                "V2 数据回滚清单",
            )
            if state.get("data_snapshot_sha256") != snapshot_manifest.get(
                "tree_sha256"
            ):
                raise UpdatePolicyError("未完成更新事务的数据快照摘要不一致")
            self.controller.capture_and_stop_fail_closed(identity)
            self._restore(
                identity,
                rollback,
                run_state,
                restore_controller=True,
            )
        with contextlib.suppress(FileNotFoundError):
            state_path.unlink()
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(rollback, ignore_errors=True)
        return load_v2_identity(identity.root)

    def run(self) -> UpdateResult:
        requested_root = Path(self.install_root).expanduser()
        if not requested_root.is_absolute():
            raise UpdatePolicyError("V2 安装目录必须是绝对路径")
        try:
            resolved_root = requested_root.resolve(strict=True)
        except OSError as error:
            raise UpdatePolicyError("V2 安装目录不存在或无法读取") from error
        if is_reparse_or_link(resolved_root) or not resolved_root.is_dir():
            raise UpdatePolicyError("V2 安装目录不能是链接、重解析点或特殊文件")
        state_path = resolved_root / UPDATE_TRANSACTION_FILE
        if state_path.exists():
            identity = self._load_identity_for_recovery(resolved_root, state_path)
        else:
            identity = load_v2_identity(resolved_root)
        if state_path.exists():
            identity = self._recover_existing_transaction(identity, state_path)
        if identity.version == VERSION:
            receipt = identity.root / UPDATE_RECEIPT_FILE
            if receipt.is_file():
                return UpdateResult(identity.root, VERSION, VERSION, receipt)
            raise UpdatePolicyError("当前已是目标版本，但缺少可验证更新回执")
        if identity.version not in self.bundle.supported_source_versions:
            raise UpdatePolicyError(
                f"当前 V{identity.version} 不在本更新包支持矩阵中"
            )
        preflight = V2UpdatePreflight(
            identity.root,
            VERSION,
            self.bundle.payload.zip_sha256,
            self.bundle.payload.records,
        )
        required = sum(int(record["size"]) for record in self.bundle.payload.records)
        data_size = sum(int(record["size"]) for record in records_for_tree(identity.root / "_程序文件" / "data"))
        if shutil.disk_usage(identity.root).free < (required * 3 + data_size * 2 + 64 * 1024 * 1024):
            raise UpdatePolicyError("V2 更新的 staging、数据快照与回滚空间不足")
        lock_key = hashlib.sha256(
            f"{identity.root}|{identity.install_id}".casefold().encode("utf-8")
        ).hexdigest()[:24]
        lock_path = Path(tempfile.gettempdir()) / f"meetingroom-v2-update-{lock_key}.lock"
        state = dict(preflight.state)
        txid = str(state["transaction_id"])
        rollback = identity.root / "_程序文件" / "backups" / "updates" / txid
        staging = identity.root / "_程序文件" / f".update-staging-{txid}"
        run_state: Mapping[str, Any] = {
            "service_running": False,
            "service_task_enabled": False,
            "backup_task_enabled": False,
            "backup_task_running": False,
            "manual_firewall_enabled": False,
            "background_firewall_enabled": False,
        }
        prepared = False
        resources_stopped = False
        health_runtime_started = False
        committed = False
        self.log.add_path(
            identity.root / "_程序文件" / "logs" /
            f"update-{dt.datetime.now():%Y%m%d_%H%M%S_%f}.log"
        )
        with ExclusiveLock(lock_path):
            try:
                self.controller.verify(identity)
                if self.online_backup is not None:
                    self.online_backup(identity)
                rollback.mkdir(parents=True)
                run_state = self.controller.capture_and_stop(identity)
                resources_stopped = True
                state.update(
                    {
                        "rollback_root": str(rollback),
                        "data_snapshot_sha256": None,
                        "run_state": dict(run_state),
                    }
                )
                self._stage(state, "service_stopped_pre_snapshot", state_path)
                snapshot = preflight.snapshot(rollback / "protected-data")
                self._snapshot_program(identity, rollback)
                state["data_snapshot_sha256"] = snapshot.tree_sha256
                prepared = True
                self._stage(state, "prepared", state_path)
                self._extract_staging(staging)
                self._stage(state, "staging_verified", state_path)
                self._replace_program(identity, staging)
                self.controller.apply_security(identity)
                self._verify_installed_payload(identity)
                self._stage(state, "program_replaced", state_path)
                self.controller.verify(identity)
                self._stage(state, "security_verified", state_path)
                self.controller.start_for_health(identity)
                health_runtime_started = True
                if self.health_probe is not None:
                    self.health_probe(identity)
                self.controller.capture_and_stop_fail_closed(identity)
                health_runtime_started = False
                self._stage(state, "healthcheck_passed_and_stopped", state_path)
                self._commit_identity(identity, state, state_path)
                committed = True
                final_identity = load_v2_identity(identity.root)
                if final_identity.install_id != identity.install_id or final_identity.version != VERSION:
                    raise UpdateRollbackError("V2 更新提交后安装身份不一致")
                self.controller.verify(final_identity)
                self.controller.restore(final_identity, run_state)
                self._stage(state, "run_state_restored", state_path)
                receipt = self._finalize_receipt(final_identity, state, state_path)
                shutil.rmtree(rollback, ignore_errors=True)
                shutil.rmtree(staging, ignore_errors=True)
                self.log.write(f"V2 累计更新完成：{identity.version} -> {VERSION}")
                return UpdateResult(identity.root, identity.version, VERSION, receipt)
            except BaseException as error:
                self.log.write(f"V2 累计更新失败：{error}", "ERROR")
                if committed:
                    raise UpdateRollbackError(
                        "V2 目标版本已提交，不得自动回滚或覆盖可能的新数据"
                    ) from error
                rollback_errors: list[str] = []
                if prepared:
                    if health_runtime_started:
                        try:
                            # This snapshot belongs only to the transient health
                            # runtime. Preserve the original pre-update run_state.
                            self.controller.capture_and_stop_fail_closed(identity)
                            health_runtime_started = False
                        except BaseException as rollback_error:
                            rollback_errors.append(str(rollback_error))
                    if not rollback_errors:
                        try:
                            self._restore(
                                identity,
                                rollback,
                                run_state,
                                restore_controller=resources_stopped,
                            )
                        except BaseException as rollback_error:
                            rollback_errors.append(str(rollback_error))
                elif resources_stopped:
                    try:
                        self.controller.verify(identity)
                        self.controller.restore(identity, run_state)
                    except BaseException as rollback_error:
                        rollback_errors.append(str(rollback_error))
                if rollback_errors:
                    raise UpdateRollbackError(
                        "V2 更新失败且自动回滚未能验证：" + "；".join(rollback_errors)
                    ) from error
                with contextlib.suppress(FileNotFoundError):
                    state_path.unlink()
                shutil.rmtree(staging, ignore_errors=True)
                shutil.rmtree(rollback, ignore_errors=True)
                raise

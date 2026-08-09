#!/usr/bin/env python3
"""V2.0.0 全新安装事务核心。

本模块只依赖 Python 标准库，既供 Windows 交付包执行，也供 macOS/Linux
运行单元测试。它只接受调用方明确给出的目标目录；没有任何 V1 搜索、读取、
迁移或删除逻辑。
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import ctypes
import datetime as dt
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence


PRODUCT_GENERATION = 2
VERSION = "2.0.0"
RELEASE = "V2.0.0"
MANIFEST_SCHEMA = 1
SERVICE_PORT = 8080
SETUP_BIND = "127.0.0.1"
LAN_BIND = "0.0.0.0"
HEALTH_PATH = "/healthz"
TASK_PATH = "\\"
TASK_NAME = "会议室预约系统 V2"
SERVICE_ENTRYPOINT = "_程序文件/service.py"
TOOL_MANIFEST = "manifest.json"
INSTALL_INFO = "_程序文件/data/install.json"
INSTALL_ID_FILE = "_程序文件/data/install_id"
SECRET_FILE = "_程序文件/data/.secret_key"
VERSION_FILE = "_程序文件/版本.txt"
GENERATION_FILE = "_程序文件/产品代际.txt"
INSTALLED_MANIFEST = "_程序文件/release-manifest.json"
TRANSACTION_FILE = "_程序文件/install-transaction.json"
RECEIPT_FILE = "_程序文件/install-receipt.json"
LOG_DIR = "_程序文件/logs"
BACKUP_DIR = "_程序文件/backups"
RUNTIME_DIR = "_程序文件/runtime"

VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
UUID4_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
WINDOWS_RESERVED_BASENAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        "conin$",
        "conout$",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
        "com¹",
        "com²",
        "com³",
        "lpt¹",
        "lpt²",
        "lpt³",
    }
)


class InstallerError(RuntimeError):
    """安装输入或事务状态不安全。"""


class InstallCancelled(InstallerError):
    """用户取消安装或 UAC。"""


class InstallBusy(InstallerError):
    """同一目标已有安装事务。"""


class InstallCommittedError(InstallerError):
    """版本已经提交；必须保留现场，不得自动删除新数据。"""


class RollbackError(InstallerError):
    """安装器无法证明目标仍属于当前事务，因而拒绝删除。"""


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def version_tuple(value: str) -> tuple[int, int, int]:
    if not VERSION_RE.fullmatch(value):
        raise InstallerError(f"版本号格式非法：{value}")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def canonical_uuid4(value: object) -> Optional[str]:
    if not isinstance(value, str) or not UUID4_RE.fullmatch(value):
        return None
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return None
    return value if str(parsed) == value and parsed.version == 4 else None


def safe_relative_path(relative: str) -> tuple[str, ...]:
    """校验交付清单和 ZIP 的 Windows 相对路径。"""

    if not isinstance(relative, str) or not relative:
        raise InstallerError("交付路径不能为空")
    if relative != unicodedata.normalize("NFC", relative):
        raise InstallerError(f"交付路径必须使用 NFC Unicode：{relative!r}")
    if "\\" in relative or "\x00" in relative:
        raise InstallerError(f"交付路径包含非法字符：{relative!r}")
    if len(relative.encode("utf-8")) > 1024:
        raise InstallerError(f"交付路径过长：{relative!r}")
    path = PurePosixPath(relative)
    if path.is_absolute() or relative.startswith("/"):
        raise InstallerError(f"交付路径不能是绝对路径：{relative}")
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise InstallerError(f"交付路径包含穿越或空组件：{relative}")
    for component in parts:
        if component.endswith((" ", ".")):
            raise InstallerError(f"Windows 路径组件不能以空格或点结尾：{relative}")
        if any(ord(character) < 32 for character in component):
            raise InstallerError(f"交付路径包含控制字符：{relative}")
        if any(character in component for character in '<>:"|?*'):
            raise InstallerError(f"交付路径包含 Windows 非法字符：{relative}")
        if len(component.encode("utf-16-le")) // 2 > 120:
            raise InstallerError(f"交付路径组件过长：{relative}")
        basename = component.split(".", 1)[0].casefold()
        if basename in WINDOWS_RESERVED_BASENAMES:
            raise InstallerError(f"交付路径使用 Windows 保留名：{relative}")
    return tuple(parts)


def is_reparse_or_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = int(getattr(os.lstat(path), "st_file_attributes", 0))
    except (FileNotFoundError, OSError):
        return False
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def assert_plain_file(path: Path, description: str) -> None:
    if is_reparse_or_link(path) or not path.is_file():
        raise InstallerError(f"{description}缺失、不是普通文件或是链接：{path}")


def assert_plain_tree(root: Path, description: str) -> None:
    if is_reparse_or_link(root) or not root.is_dir():
        raise InstallerError(f"{description}缺失、不是普通目录或是链接：{root}")
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in list(directories) + list(files):
            path = current_path / name
            if is_reparse_or_link(path):
                raise InstallerError(f"{description}包含链接或重解析点：{path}")
        for name in files:
            if not (current_path / name).is_file():
                raise InstallerError(f"{description}包含特殊文件：{current_path / name}")


def records_for_tree(root: Path) -> tuple[Mapping[str, Any], ...]:
    assert_plain_tree(root, "文件树")
    records: list[Mapping[str, Any]] = []
    folded: dict[str, str] = {}
    # Manifest validation compares complete POSIX relative paths.  Sorting
    # Path objects compares components instead, which orders ``flask/`` before
    # ``flask-3.x.dist-info/`` and makes a real site-packages tree fail its own
    # reverse verification.  Sort by the exact serialized key we deliver.
    for path in sorted(
        root.rglob("*"),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        if path.is_dir():
            continue
        assert_plain_file(path, "文件树条目")
        relative = path.relative_to(root).as_posix()
        safe_relative_path(relative)
        key = relative.casefold()
        if key in folded:
            raise InstallerError(
                f"文件树包含 Windows 大小写冲突：{folded[key]} / {relative}"
            )
        folded[key] = relative
        records.append(
            {"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)}
        )
    return tuple(records)


def tree_digest(records: Iterable[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda item: str(item["path"])):
        digest.update(str(record["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record["size"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(record["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def validate_records(value: Any, description: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list) or not value:
        raise InstallerError(f"{description}文件清单为空或类型错误")
    records: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    folded: dict[str, str] = {}
    previous = ""
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {"path", "size", "sha256"}:
            raise InstallerError(f"{description}文件记录字段非法")
        relative = str(raw["path"])
        safe_relative_path(relative)
        if relative <= previous:
            raise InstallerError(f"{description}文件清单必须按路径严格排序")
        previous = relative
        if relative in seen:
            raise InstallerError(f"{description}文件清单路径重复：{relative}")
        key = relative.casefold()
        if key in folded:
            raise InstallerError(
                f"{description}文件清单大小写冲突：{folded[key]} / {relative}"
            )
        size = raw["size"]
        digest = str(raw["sha256"])
        if not isinstance(size, int) or size < 0 or not SHA256_RE.fullmatch(digest):
            raise InstallerError(f"{description}文件记录大小或哈希非法：{relative}")
        record = {"path": relative, "size": size, "sha256": digest}
        records.append(record)
        seen.add(relative)
        folded[key] = relative
    return tuple(records)


def assert_record_sets_equal(
    expected: Sequence[Mapping[str, Any]],
    actual: Sequence[Mapping[str, Any]],
    description: str,
) -> None:
    expected_map = {str(record["path"]): record for record in expected}
    actual_map = {str(record["path"]): record for record in actual}
    if set(expected_map) != set(actual_map):
        missing = sorted(set(expected_map) - set(actual_map))
        extra = sorted(set(actual_map) - set(expected_map))
        raise InstallerError(f"{description}文件集合不一致；缺少={missing}，多出={extra}")
    for relative, record in expected_map.items():
        if actual_map[relative] != record:
            raise InstallerError(f"{description}文件大小或哈希不一致：{relative}")


@dataclass(frozen=True)
class Payload:
    zip_path: Path
    zip_sha256: str
    records: tuple[Mapping[str, Any], ...]
    tree_sha256: str
    files: Mapping[str, bytes]


@dataclass(frozen=True)
class Bundle:
    tool_root: Path
    manifest: Mapping[str, Any]
    manifest_sha256: str
    payload: Payload
    runtime_records: tuple[Mapping[str, Any], ...]
    runtime_tree_sha256: str
    tool_records: tuple[Mapping[str, Any], ...]
    tool_tree_sha256: str

    @classmethod
    def load(cls, tool_root: Path) -> "Bundle":
        tool_root = Path(tool_root).resolve(strict=True)
        assert_plain_tree(tool_root, "V2 安装工具目录")
        manifest_path = tool_root / TOOL_MANIFEST
        assert_plain_file(manifest_path, "V2 安装清单")
        raw_manifest = manifest_path.read_bytes()
        if raw_manifest.startswith(b"\xef\xbb\xbf") or len(raw_manifest) > 8 * 1024 * 1024:
            raise InstallerError("V2 安装清单带 BOM 或体积异常")
        try:
            manifest = json.loads(raw_manifest.decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise InstallerError("V2 安装清单不是有效 UTF-8 JSON") from error
        expected_top = {
            "schema",
            "kind",
            "product_generation",
            "release",
            "version",
            "service",
            "payload",
            "runtime",
            "tool",
            "acceptance",
        }
        if not isinstance(manifest, dict) or set(manifest) != expected_top:
            raise InstallerError("V2 安装清单顶层字段不符合约定")
        if (
            manifest["schema"] != MANIFEST_SCHEMA
            or manifest["kind"] != "fresh-install"
            or manifest["product_generation"] != PRODUCT_GENERATION
            or manifest["release"] != RELEASE
            or manifest["version"] != VERSION
        ):
            raise InstallerError("安装包不是受支持的 V2.0.0 全新安装包")
        version_tuple(str(manifest["version"]))
        cls._validate_service(manifest["service"])
        cls._validate_acceptance(manifest["acceptance"])
        payload = cls._load_payload(tool_root, manifest["payload"])
        runtime_records, runtime_digest = cls._load_runtime(tool_root, manifest["runtime"])
        tool_records, tool_digest = cls._load_tool(tool_root, manifest["tool"])
        return cls(
            tool_root=tool_root,
            manifest=manifest,
            manifest_sha256=sha256_bytes(raw_manifest),
            payload=payload,
            runtime_records=runtime_records,
            runtime_tree_sha256=runtime_digest,
            tool_records=tool_records,
            tool_tree_sha256=tool_digest,
        )

    @staticmethod
    def _validate_service(value: Any) -> None:
        expected = {
            "port": SERVICE_PORT,
            "setup_bind": SETUP_BIND,
            "lan_bind": LAN_BIND,
            "health_path": HEALTH_PATH,
            "task_path": TASK_PATH,
            "task_name": TASK_NAME,
            "entrypoint": SERVICE_ENTRYPOINT,
        }
        if not isinstance(value, dict) or value != expected:
            raise InstallerError("安装包服务管理契约不符合 V2.0.0 固定约定")

    @staticmethod
    def _validate_acceptance(value: Any) -> None:
        if not isinstance(value, dict) or set(value) != {
            "status",
            "formal_external_release_allowed",
        }:
            raise InstallerError("安装包验收状态字段不符合约定")
        if value["status"] != "candidate" or value["formal_external_release_allowed"] is not False:
            raise InstallerError("未经实机验收的安装包必须保持候选状态")

    @staticmethod
    def _load_payload(tool_root: Path, value: Any) -> Payload:
        if not isinstance(value, dict) or set(value) != {
            "file",
            "size",
            "sha256",
            "tree_sha256",
            "files",
        }:
            raise InstallerError("V2 payload 清单字段不符合约定")
        filename = str(value["file"])
        if PurePosixPath(filename).name != filename or not filename.endswith(".zip"):
            raise InstallerError("V2 payload 文件名非法")
        records = validate_records(value["files"], "V2 payload")
        expected_digest = str(value["tree_sha256"])
        if not SHA256_RE.fullmatch(expected_digest) or tree_digest(records) != expected_digest:
            raise InstallerError("V2 payload 树哈希不一致")
        path = tool_root / filename
        assert_plain_file(path, "V2 payload ZIP")
        content = path.read_bytes()
        if len(content) != value["size"] or sha256_bytes(content) != value["sha256"]:
            raise InstallerError("V2 payload ZIP 大小或 SHA-256 不一致")
        files: dict[str, bytes] = {}
        try:
            archive = zipfile.ZipFile(io.BytesIO(content), "r")
        except (OSError, zipfile.BadZipFile) as error:
            raise InstallerError("V2 payload 不是有效 ZIP") from error
        with archive:
            for info in archive.infolist():
                relative = info.filename
                safe_relative_path(relative)
                if info.is_dir() or relative.endswith("/"):
                    raise InstallerError(f"V2 payload 不允许目录条目：{relative}")
                if relative in files:
                    raise InstallerError(f"V2 payload 路径重复：{relative}")
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_IFMT(mode) == stat.S_IFLNK or info.flag_bits & 0x1:
                    raise InstallerError(f"V2 payload 含链接或加密文件：{relative}")
                files[relative] = archive.read(info)
            if archive.testzip() is not None:
                raise InstallerError("V2 payload CRC 校验失败")
        actual_records = tuple(
            {
                "path": relative,
                "size": len(files[relative]),
                "sha256": sha256_bytes(files[relative]),
            }
            for relative in sorted(files)
        )
        assert_record_sets_equal(records, actual_records, "V2 payload")
        if SERVICE_ENTRYPOINT not in files:
            raise InstallerError(f"V2 payload 缺少服务入口：{SERVICE_ENTRYPOINT}")
        return Payload(path, str(value["sha256"]), records, expected_digest, files)

    @staticmethod
    def _load_runtime(
        tool_root: Path, value: Any
    ) -> tuple[tuple[Mapping[str, Any], ...], str]:
        if not isinstance(value, dict) or set(value) != {
            "directory",
            "tree_sha256",
            "files",
        }:
            raise InstallerError("V2 runtime 清单字段不符合约定")
        if value["directory"] != "runtime":
            raise InstallerError("V2 runtime 目录名非法")
        records = validate_records(value["files"], "V2 runtime")
        digest = str(value["tree_sha256"])
        if not SHA256_RE.fullmatch(digest) or tree_digest(records) != digest:
            raise InstallerError("V2 runtime 树哈希不一致")
        runtime_root = tool_root / "runtime"
        actual = records_for_tree(runtime_root)
        assert_record_sets_equal(records, actual, "V2 runtime")
        if tree_digest(actual) != digest:
            raise InstallerError("V2 runtime 实际树哈希不一致")
        required = {"python.exe", "pythonw.exe"}
        actual_paths = {str(record["path"]) for record in records}
        if not required.issubset(actual_paths):
            raise InstallerError(f"V2 runtime 缺少：{sorted(required - actual_paths)}")
        return records, digest

    @staticmethod
    def _load_tool(
        tool_root: Path, value: Any
    ) -> tuple[tuple[Mapping[str, Any], ...], str]:
        if not isinstance(value, dict) or set(value) != {"tree_sha256", "files"}:
            raise InstallerError("V2 安装程序清单字段不符合约定")
        records = validate_records(value["files"], "V2 安装程序")
        expected_paths = {"install.py", "installer_core.py"}
        record_paths = {str(record["path"]) for record in records}
        if record_paths != expected_paths:
            raise InstallerError("V2 安装程序清单必须且只能包含固定入口和事务核心")
        digest = str(value["tree_sha256"])
        if not SHA256_RE.fullmatch(digest) or tree_digest(records) != digest:
            raise InstallerError("V2 安装程序树哈希不一致")
        actual_records: list[Mapping[str, Any]] = []
        for relative in sorted(expected_paths):
            path = tool_root / relative
            assert_plain_file(path, "V2 安装程序")
            actual_records.append(
                {
                    "path": relative,
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        actual = tuple(actual_records)
        assert_record_sets_equal(records, actual, "V2 安装程序")
        if tree_digest(actual) != digest:
            raise InstallerError("V2 安装程序实际树哈希不一致")
        return records, digest

    def assert_fits_target(self, target: Path) -> None:
        relatives = [str(record["path"]) for record in self.payload.records]
        relatives.extend(f"{RUNTIME_DIR}/{record['path']}" for record in self.runtime_records)
        relatives.extend(
            [INSTALL_INFO, INSTALL_ID_FILE, SECRET_FILE, VERSION_FILE, INSTALLED_MANIFEST]
        )
        longest = max(len(str(target.joinpath(*relative.split("/")))) for relative in relatives)
        if longest > 240:
            raise InstallerError(
                f"安装目录过深，最长最终路径为 {longest} 个字符；必须不超过 240"
            )


def validate_target(path: Path) -> tuple[Path, bool]:
    """只验证调用方明确给出的目标；不会枚举相邻目录或磁盘。"""

    if not isinstance(path, Path):
        path = Path(path)
    raw = path.expanduser()
    if not raw.is_absolute():
        raise InstallerError("安装目录必须是绝对路径")
    if is_reparse_or_link(raw):
        raise InstallerError("安装目录不能是链接或重解析点")
    parent = raw.parent
    try:
        parent = parent.resolve(strict=True)
    except OSError as error:
        raise InstallerError(f"安装目录的父目录不存在或无法读取：{parent}") from error
    if is_reparse_or_link(parent) or not parent.is_dir():
        raise InstallerError("安装目录父目录不能是链接、重解析点或特殊文件")
    target = parent / raw.name
    preexisting_empty = False
    if target.exists():
        if is_reparse_or_link(target) or not target.is_dir():
            raise InstallerError("目标必须不存在或是普通空目录")
        try:
            preexisting_empty = next(target.iterdir(), None) is None
        except OSError as error:
            raise InstallerError("无法确认目标目录是否为空") from error
        if not preexisting_empty:
            raise InstallerError("目标目录不是空目录；安装器不会覆盖或删除其中内容")
    if os.name == "nt":
        if str(target).startswith("\\\\") or not target.drive:
            raise InstallerError("安装目录必须位于本机固定磁盘，不能使用网络共享")
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(f"{target.drive}\\")
        if drive_type != 3:  # DRIVE_FIXED
            raise InstallerError("安装目录必须位于本机固定磁盘")
    return target, preexisting_empty


class ExclusiveLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Optional[Any] = None

    def __enter__(self) -> "ExclusiveLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if is_reparse_or_link(self.path):
            raise InstallerError(f"安装锁不能是链接或重解析点：{self.path}")
        handle = self.path.open("a+b")
        try:
            handle.seek(0)
            if handle.read(1) == b"":
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            handle.close()
            raise InstallBusy("另一套 V2 安装事务正在处理同一目标") from error
        self.handle = handle
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        if self.handle is None:
            return
        with contextlib.suppress(OSError):
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


class EventLog:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def add_path(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path not in self.paths:
            self.paths.append(path)

    def write(self, message: str, level: str = "INFO") -> None:
        line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S.%f} [{level}] {message}\n"
        for path in tuple(self.paths):
            try:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
                    handle.flush()
            except OSError:
                continue


class SystemController:
    """Windows 外部资源接口；测试使用 PassiveSystemController。"""

    def configure_disabled(self, install_root: Path, install_id: str) -> None:
        raise NotImplementedError

    def activate(self, install_root: Path, install_id: str) -> None:
        raise NotImplementedError

    def rollback_resources(self, install_root: Path, install_id: str) -> None:
        raise NotImplementedError


class PassiveSystemController(SystemController):
    def __init__(self) -> None:
        self.configured = False
        self.activated = False
        self.rolled_back = False

    def configure_disabled(self, install_root: Path, install_id: str) -> None:
        del install_root, install_id
        self.configured = True

    def activate(self, install_root: Path, install_id: str) -> None:
        del install_root, install_id
        if not self.configured:
            raise InstallerError("测试控制器尚未配置")
        self.activated = True

    def rollback_resources(self, install_root: Path, install_id: str) -> None:
        del install_root, install_id
        self.rolled_back = True
        self.configured = False


class WindowsSystemController(SystemController):
    FIREWALL_MANUAL = "会议室预约系统V2-手动"
    FIREWALL_BACKGROUND = "会议室预约系统V2-后台"
    REGISTRY_KEY = r"HKLM:\Software\MeetingRoomReservationV2"

    @staticmethod
    def _run_powershell(script: str, environment: Mapping[str, str]) -> str:
        merged = os.environ.copy()
        merged.update(environment)
        process = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=merged,
        )
        if process.returncode != 0:
            details = (process.stderr or process.stdout).strip()
            raise InstallerError(f"Windows 服务管理失败（{process.returncode}）：{details}")
        return process.stdout

    def configure_disabled(self, install_root: Path, install_id: str) -> None:
        script = r"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($env:MRV2_ROOT).TrimEnd('\')
$program = Join-Path $root '_程序文件'
$pythonw = Join-Path $program 'runtime\pythonw.exe'
$service = Join-Path $program 'service.py'
$working = $program
$taskPath = $env:MRV2_TASK_PATH
$taskName = $env:MRV2_TASK_NAME
$identity = 'MeetingRoomReservationV2:' + $env:MRV2_INSTALL_ID
if (Test-Path -LiteralPath $env:MRV2_REGISTRY_KEY) {
    $registered = Get-ItemProperty -LiteralPath $env:MRV2_REGISTRY_KEY
    throw ('V2 专属安装登记已经存在，拒绝覆盖。InstallRoot={0}; InstallId={1}' -f @(
        [string]$registered.InstallRoot,
        [string]$registered.InstallId
    ))
}
$existing = @(Get-ScheduledTask -TaskPath $taskPath -TaskName $taskName -ErrorAction SilentlyContinue)
if ($existing.Count -ne 0) { throw 'V2 专属计划任务已经存在，拒绝覆盖。' }
foreach ($name in @($env:MRV2_FW_MANUAL, $env:MRV2_FW_BACKGROUND)) {
    if (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue) {
        throw "V2 专属防火墙规则已经存在，拒绝覆盖：$name"
    }
}
$action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"' + $service + '"') -WorkingDirectory $working
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 0)
Register-ScheduledTask -TaskPath $taskPath -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $identity | Out-Null
Disable-ScheduledTask -TaskPath $taskPath -TaskName $taskName | Out-Null
New-NetFirewallRule -DisplayName $env:MRV2_FW_MANUAL -Description $identity -Direction Inbound -Action Allow -Program (Join-Path $program 'runtime\python.exe') -Protocol TCP -LocalPort 8080 -Profile Domain,Private -RemoteAddress LocalSubnet -Enabled False | Out-Null
New-NetFirewallRule -DisplayName $env:MRV2_FW_BACKGROUND -Description $identity -Direction Inbound -Action Allow -Program $pythonw -Protocol TCP -LocalPort 8080 -Profile Domain,Private -RemoteAddress LocalSubnet -Enabled False | Out-Null
New-Item -Path $env:MRV2_REGISTRY_KEY | Out-Null
New-ItemProperty -Path $env:MRV2_REGISTRY_KEY -Name InstallRoot -Value $root -PropertyType String -Force | Out-Null
New-ItemProperty -Path $env:MRV2_REGISTRY_KEY -Name InstallId -Value $env:MRV2_INSTALL_ID -PropertyType String -Force | Out-Null
New-ItemProperty -Path $env:MRV2_REGISTRY_KEY -Name ProductGeneration -Value 2 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $env:MRV2_REGISTRY_KEY -Name Port -Value 8080 -PropertyType DWord -Force | Out-Null
"""
        environment = self._environment(install_root, install_id)
        self._run_powershell(script, environment)

    def activate(self, install_root: Path, install_id: str) -> None:
        script = r"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($env:MRV2_ROOT).TrimEnd('\')
$identity = 'MeetingRoomReservationV2:' + $env:MRV2_INSTALL_ID
$registered = Get-ItemProperty -LiteralPath $env:MRV2_REGISTRY_KEY -ErrorAction Stop
if ([string]$registered.InstallId -ne $env:MRV2_INSTALL_ID -or [string]$registered.InstallRoot -ne $root) {
    throw 'V2 安装登记身份不一致。'
}
$task = Get-ScheduledTask -TaskPath $env:MRV2_TASK_PATH -TaskName $env:MRV2_TASK_NAME -ErrorAction Stop
if ([string]$task.Description -ne $identity) {
    throw 'V2 计划任务身份不一致。'
}
foreach ($name in @($env:MRV2_FW_MANUAL, $env:MRV2_FW_BACKGROUND)) {
    $rules = @(Get-NetFirewallRule -DisplayName $name -ErrorAction Stop)
    if ($rules.Count -ne 1 -or [string]$rules[0].Description -ne $identity) {
        throw "V2 防火墙规则身份不一致：$name"
    }
}
foreach ($name in @($env:MRV2_FW_MANUAL, $env:MRV2_FW_BACKGROUND)) {
    Enable-NetFirewallRule -DisplayName $name
}
Enable-ScheduledTask -TaskPath $env:MRV2_TASK_PATH -TaskName $env:MRV2_TASK_NAME | Out-Null
Start-ScheduledTask -TaskPath $env:MRV2_TASK_PATH -TaskName $env:MRV2_TASK_NAME
"""
        self._run_powershell(script, self._environment(install_root, install_id))

    def rollback_resources(self, install_root: Path, install_id: str) -> None:
        script = r"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$identity = 'MeetingRoomReservationV2:' + $env:MRV2_INSTALL_ID
$task = Get-ScheduledTask -TaskPath $env:MRV2_TASK_PATH -TaskName $env:MRV2_TASK_NAME -ErrorAction SilentlyContinue
if ($null -ne $task -and [string]$task.Description -eq $identity) {
    Stop-ScheduledTask -TaskPath $env:MRV2_TASK_PATH -TaskName $env:MRV2_TASK_NAME -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskPath $env:MRV2_TASK_PATH -TaskName $env:MRV2_TASK_NAME -Confirm:$false
}
foreach ($name in @($env:MRV2_FW_MANUAL, $env:MRV2_FW_BACKGROUND)) {
    Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue |
        Where-Object { [string]$_.Description -eq $identity } |
        Remove-NetFirewallRule
}
if (Test-Path -LiteralPath $env:MRV2_REGISTRY_KEY) {
    $item = Get-ItemProperty -LiteralPath $env:MRV2_REGISTRY_KEY
    if ([string]$item.InstallId -eq $env:MRV2_INSTALL_ID -and [string]$item.InstallRoot -eq [IO.Path]::GetFullPath($env:MRV2_ROOT).TrimEnd('\')) {
        Remove-Item -LiteralPath $env:MRV2_REGISTRY_KEY -Recurse -Force
    }
}
"""
        self._run_powershell(script, self._environment(install_root, install_id))

    def _environment(self, install_root: Path, install_id: str) -> Mapping[str, str]:
        return {
            "MRV2_ROOT": str(install_root),
            "MRV2_INSTALL_ID": install_id,
            "MRV2_TASK_PATH": TASK_PATH,
            "MRV2_TASK_NAME": TASK_NAME,
            "MRV2_FW_MANUAL": self.FIREWALL_MANUAL,
            "MRV2_FW_BACKGROUND": self.FIREWALL_BACKGROUND,
            "MRV2_REGISTRY_KEY": self.REGISTRY_KEY,
        }


def default_health_probe(install_root: Path, install_id: str, timeout: float = 30.0) -> None:
    del install_root
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{SERVICE_PORT}{HEALTH_PATH}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last_error = "尚未响应"
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=1) as response:
                raw = response.read(16 * 1024 + 1)
            if len(raw) > 16 * 1024:
                raise InstallerError("V2 健康响应体积异常")
            payload = json.loads(raw.decode("utf-8"))
            expected = {
                "ok": True,
                "product_generation": PRODUCT_GENERATION,
                "install_id": install_id,
                "setup_complete": False,
                "bind_mode": "loopback",
                "port": SERVICE_PORT,
            }
            if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in expected.items()):
                raise InstallerError(f"V2 首次启动健康身份不一致：{payload}")
            return
        except (OSError, UnicodeError, ValueError, urllib.error.URLError, InstallerError) as error:
            last_error = str(error)
            time.sleep(0.25)
    raise InstallerError(f"V2 首次启动未在限定时间通过回环健康检查：{last_error}")


@dataclass(frozen=True)
class InstallResult:
    install_root: Path
    install_id: str
    setup_url: str
    receipt_path: Path


class InstallTransaction:
    def __init__(
        self,
        bundle: Bundle,
        target: Path,
        controller: SystemController,
        *,
        log: Optional[EventLog] = None,
        health_probe: Optional[Callable[[Path, str], None]] = default_health_probe,
        fault_hook: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.bundle = bundle
        self.target, self.preexisting_empty = validate_target(Path(target))
        self.bundle.assert_fits_target(self.target)
        self.controller = controller
        self.log = log or EventLog()
        self.health_probe = health_probe
        self.fault_hook = fault_hook

    def _stage(self, state: dict[str, Any], name: str, state_path: Path) -> None:
        state["stage"] = name
        atomic_write(state_path, json_bytes(state))
        self.log.write(f"安装事务阶段：{name}")
        if self.fault_hook is not None:
            self.fault_hook(name)

    def _extract_payload(self, staging: Path) -> None:
        for relative, content in self.bundle.payload.files.items():
            destination = staging.joinpath(*relative.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                handle.write(content)
        actual = tuple(
            {
                "path": str(record["path"]),
                "size": staging.joinpath(*str(record["path"]).split("/")).stat().st_size,
                "sha256": sha256_file(
                    staging.joinpath(*str(record["path"]).split("/"))
                ),
            }
            for record in self.bundle.payload.records
        )
        assert_record_sets_equal(self.bundle.payload.records, actual, "安装后的 V2 payload")

    def _copy_runtime(self, staging: Path) -> None:
        source = self.bundle.tool_root / "runtime"
        destination = staging / RUNTIME_DIR
        shutil.copytree(source, destination)
        actual = records_for_tree(destination)
        assert_record_sets_equal(self.bundle.runtime_records, actual, "安装后的 V2 runtime")
        if tree_digest(actual) != self.bundle.runtime_tree_sha256:
            raise InstallerError("安装后的 V2 runtime 树哈希不一致")

    def _create_local_identity(self, staging: Path, install_id: str) -> None:
        data = staging / "_程序文件" / "data"
        data.mkdir(parents=True)
        (staging / BACKUP_DIR).mkdir(parents=True, exist_ok=True)
        (staging / LOG_DIR).mkdir(parents=True, exist_ok=True)
        installed_at = dt.datetime.now(dt.timezone.utc).isoformat()
        info = {
            "schema": 1,
            "product_generation": PRODUCT_GENERATION,
            "install_id": install_id,
            "installed_version": VERSION,
            "installed_at_utc": installed_at,
            "port": SERVICE_PORT,
            "setup_bind": SETUP_BIND,
            "lan_bind": LAN_BIND,
            "setup_complete": False,
        }
        atomic_write(staging / INSTALL_INFO, json_bytes(info))
        atomic_write(staging / INSTALL_ID_FILE, (install_id + "\n").encode("ascii"))
        # 后端只接受 32 字节随机数的 64 位小写十六进制编码。
        secret = secrets.token_hex(32)
        atomic_write(staging / SECRET_FILE, (secret + "\n").encode("ascii"))
        with contextlib.suppress(OSError):
            os.chmod(staging / SECRET_FILE, stat.S_IRUSR | stat.S_IWUSR)
        atomic_write(
            staging / INSTALLED_MANIFEST,
            json_bytes(self.bundle.manifest),
        )

    @staticmethod
    def _transaction_matches(root: Path, txid: str) -> bool:
        path = root / TRANSACTION_FILE
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return False
        return isinstance(value, dict) and value.get("transaction_id") == txid

    def run(self) -> InstallResult:
        target_key = sha256_bytes(str(self.target).casefold().encode("utf-8"))[:24]
        lock = Path(tempfile.gettempdir()) / f"meetingroom-v2-install-{target_key}.lock"
        txid = uuid.uuid4().hex
        install_id = str(uuid.uuid4())
        staging = self.target.parent / f".{self.target.name}.installing-{txid}"
        empty_backup = self.target.parent / f".{self.target.name}.empty-{txid}"
        target_committed = False
        version_committed = False
        resources_configured = False
        state: dict[str, Any] = {
            "schema": 1,
            "kind": "fresh-install",
            "transaction_id": txid,
            "install_root": str(self.target),
            "install_id": install_id,
            "product_generation": PRODUCT_GENERATION,
            "version": VERSION,
            "manifest_sha256": self.bundle.manifest_sha256,
            "payload_sha256": self.bundle.payload.zip_sha256,
            "runtime_tree_sha256": self.bundle.runtime_tree_sha256,
            "stage": "preflight",
            "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        with ExclusiveLock(lock):
            # 锁取得后再次确认，避免两个窗口同时看见空目录。
            validated, current_empty = validate_target(self.target)
            if validated != self.target or current_empty != self.preexisting_empty:
                raise InstallerError("安装目录在预检后发生变化")
            if staging.exists() or empty_backup.exists():
                raise InstallerError("本事务的临时目标异常地已经存在")
            staging.mkdir()
            state_path = staging / TRANSACTION_FILE
            self.log.add_path(
                staging
                / LOG_DIR
                / f"install-{dt.datetime.now():%Y%m%d_%H%M%S_%f}.log"
            )
            try:
                self._stage(state, "staging_created", state_path)
                self._extract_payload(staging)
                self._copy_runtime(staging)
                self._create_local_identity(staging, install_id)
                if is_reparse_or_link(staging / SERVICE_ENTRYPOINT):
                    raise InstallerError("V2 服务入口不能是链接或重解析点")
                self._stage(state, "files_verified", state_path)

                if self.preexisting_empty:
                    os.replace(self.target, empty_backup)
                os.replace(staging, self.target)
                target_committed = True
                state_path = self.target / TRANSACTION_FILE
                self.log.paths = [
                    self.target / path.relative_to(staging)
                    if staging in path.parents
                    else path
                    for path in self.log.paths
                ]
                self._stage(state, "target_placed", state_path)

                resources_configured = True
                self.controller.configure_disabled(self.target, install_id)
                self._stage(state, "resources_configured", state_path)

                atomic_write(
                    self.target / GENERATION_FILE,
                    f"{PRODUCT_GENERATION}\n".encode("ascii"),
                )
                # 版本文件是全新安装事务提交点，必须最后写入。
                atomic_write(self.target / VERSION_FILE, f"{VERSION}\n".encode("ascii"))
                version_committed = True
                self._stage(state, "version_committed", state_path)

                self.controller.activate(self.target, install_id)
                self._stage(state, "service_started", state_path)
                if self.health_probe is not None:
                    self.health_probe(self.target, install_id)
                self._stage(state, "healthcheck_passed", state_path)

                receipt = dict(state)
                receipt["stage"] = "complete"
                receipt["completed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
                atomic_write(self.target / RECEIPT_FILE, json_bytes(receipt))
                with contextlib.suppress(FileNotFoundError):
                    state_path.unlink()
                with contextlib.suppress(OSError):
                    if empty_backup.exists():
                        empty_backup.rmdir()
                self.log.write("V2.0.0 全新安装完成；首次设置前服务保持回环访问")
                return InstallResult(
                    install_root=self.target,
                    install_id=install_id,
                    setup_url=f"http://127.0.0.1:{SERVICE_PORT}/setup",
                    receipt_path=self.target / RECEIPT_FILE,
                )
            except BaseException as error:
                self.log.write(f"V2.0.0 安装失败：{error}", "ERROR")
                if version_committed:
                    raise InstallCommittedError(
                        "V2 文件已经提交，启动或健康检查失败；为保护可能产生的新数据，"
                        "安装器保留现场且不会自动删除目标目录"
                    ) from error
                rollback_errors: list[str] = []
                if resources_configured:
                    try:
                        self.controller.rollback_resources(self.target, install_id)
                    except BaseException as rollback_error:
                        rollback_errors.append(f"外部资源恢复失败：{rollback_error}")
                if rollback_errors:
                    raise RollbackError(
                        "；".join(rollback_errors)
                        + "；为避免遗留任务或防火墙指向已删除路径，V2 目标现场已保留"
                    ) from error
                try:
                    if target_committed and self.target.exists():
                        if not self._transaction_matches(self.target, txid):
                            raise RollbackError(
                                "目标事务身份已变化，拒绝自动删除目标目录"
                            )
                        shutil.rmtree(self.target)
                    elif staging.exists():
                        if not self._transaction_matches(staging, txid):
                            raise RollbackError(
                                "临时目录事务身份已变化，拒绝自动删除"
                            )
                        shutil.rmtree(staging)
                    if empty_backup.exists():
                        os.replace(empty_backup, self.target)
                except BaseException as rollback_error:
                    rollback_errors.append(str(rollback_error))
                raise


def is_admin() -> bool:
    if os.name != "nt":
        return os.geteuid() == 0 if hasattr(os, "geteuid") else True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def encode_elevation_context(target: Path, manifest_sha256: str) -> str:
    payload = json_bytes(
        {
            "schema": 1,
            "install_root": str(target),
            "manifest_sha256": manifest_sha256,
            "port": SERVICE_PORT,
            "nonce": uuid.uuid4().hex,
        }
    )
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_elevation_context(value: str, expected_manifest_sha256: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,16384}", value or ""):
        raise InstallerError("管理员安装上下文格式非法")
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(value + padding)
        context = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeError, ValueError) as error:
        raise InstallerError("管理员安装上下文无法解码") from error
    if not isinstance(context, dict) or set(context) != {
        "schema",
        "install_root",
        "manifest_sha256",
        "port",
        "nonce",
    }:
        raise InstallerError("管理员安装上下文字段非法")
    if (
        context["schema"] != 1
        or context["port"] != SERVICE_PORT
        or context["manifest_sha256"] != expected_manifest_sha256
        or not re.fullmatch(r"[0-9a-f]{32}", str(context["nonce"]))
    ):
        raise InstallerError("管理员安装上下文身份不一致")
    target, _ = validate_target(Path(str(context["install_root"])))
    return target


def run_elevated(tool_root: Path, context_value: str) -> int:
    if os.name != "nt":
        raise InstallerError("管理员提权入口只能在 Windows 上使用")
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    python = tool_root / "runtime" / "python.exe"
    script = tool_root / "install.py"
    parameters = subprocess.list2cmdline([str(script), "--elevated-context", context_value])
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000100  # NOCLOSEPROCESS | NOASYNC
    info.lpVerb = "runas"
    info.lpFile = str(python)
    info.lpParameters = parameters
    info.lpDirectory = str(tool_root)
    info.nShow = 1
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell_execute = shell32.ShellExecuteExW
    shell_execute.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell_execute.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    if not shell_execute(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == 1223:
            raise InstallCancelled("用户取消 Windows 管理员授权")
        raise InstallerError(f"无法启动管理员安装进程，Win32={error}")
    if not info.hProcess:
        raise InstallerError("管理员安装进程没有返回有效句柄")
    try:
        if kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF) == 0xFFFFFFFF:
            raise InstallerError("等待管理员安装进程失败")
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise InstallerError("无法取得管理员安装进程退出码")
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)

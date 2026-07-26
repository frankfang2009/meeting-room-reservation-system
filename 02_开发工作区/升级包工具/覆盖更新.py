#!/usr/bin/env python3
"""V1.0.2-r1 全量修复更新器。

该文件随修复 ZIP 交付并由独立冻结 runtime 执行。更新器不会运行客户安装
目录中可能损坏的 Python，也不会通过 cmd/RunAs 给 BAT 传递参数。

安全模型：

* 当前客户 data 是唯一权威数据源，整个更新过程不写、不替换真实 data；
* 先把受管程序精确规范化为冻结 V1.0.1，再覆盖 V1.0.2；
* V1.0.2 的数据库和服务检查只使用 data 的事务副本；
* 目标提交前失败时收敛到已知良好的 V1.0.1 程序；
* 目标版本文件提交后绝不再自动降级；
* data、既有 backups/logs 和旧 _升级回滚均不做镜像或删除。
"""

from __future__ import annotations

import argparse
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
import shutil
import socket
import sqlite3
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
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence


TARGET_VERSION = "1.0.2"
BASELINE_VERSION = "1.0.1"
REPAIR_RELEASE = "V1.0.2-r1"
BROKEN_V102_PACKAGE_SHA256 = (
    "2e7e78a61de9a403f3facd37b47c1580c35bce38b91465f4919326fa72d77730"
)
TASK_NAME = "会议室预约系统"
SERVICE_PORT = 8080
STATE_SCHEMA = 1

TOOL_MANIFEST = "manifest.json"
STATE_NAME = "_V102覆盖更新状态.json"
LOCK_NAME = "_V102覆盖更新锁"
ROLLBACK_NAME = "_V102覆盖更新回滚"
LEGACY_STATE_NAME = "_升级状态.json"
LEGACY_LOCK_NAME = "_升级锁"
LEGACY_ROLLBACK_NAME = "_升级回滚"

TOP_LEVEL_FILES = (
    "① 启动系统.bat",
    "② 立即备份.bat",
    "③ 设置开机自动启动.bat",
    "④ 停止本次后台系统.bat",
    "⑤ 取消开机自动启动.bat",
    "使用说明.txt",
)
PROGRAM_FILES = (
    "_程序文件/app.py",
    "_程序文件/server.py",
    "_程序文件/backup.py",
    "_程序文件/migrate_check.py",
    "_程序文件/requirements.txt",
    "_程序文件/版本.txt",
)
MANAGED_TREES = ("_程序文件/static", "_程序文件/templates")
REQUIRED_FILES = frozenset(TOP_LEVEL_FILES + PROGRAM_FILES)
MANAGED_PREFIXES = tuple(path + "/" for path in MANAGED_TREES)
PROTECTED_NAMES = frozenset(
    {
        "data",
        "backups",
        "logs",
        "runtime",
        LEGACY_ROLLBACK_NAME,
        LEGACY_STATE_NAME,
        LEGACY_LOCK_NAME,
        ROLLBACK_NAME,
        STATE_NAME,
        LOCK_NAME,
    }
)
KNOWN_LEGACY_STAGES = frozenset(
    {
        "preparing",
        "service_stopped",
        "backup_ready",
        "snapshot_ready",
        "program_replaced",
        "migration_complete",
        "healthcheck_passed",
        "version_committed",
        "service_restored",
        "rollback_restored",
    }
)
NEW_STAGES = frozenset(
    {
        "preflight",
        "stopped",
        "snapshot_ready",
        "runtime_ready",
        "baseline_applying",
        "baseline_verified",
        "target_applying",
        "target_verified",
        "healthcheck_passed",
        "target_committed",
        "baseline_rollback_complete",
        "complete",
    }
)
VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
TXID_RE = re.compile(r"[0-9a-f]{32}")
RESERVED_WINDOWS_BASENAMES = frozenset(
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


class UpdateError(RuntimeError):
    """预检、事务或恢复无法安全继续。"""


class UpdateCancelled(UpdateError):
    """用户取消 UAC 或目录选择。"""


class UpdateBusy(UpdateError):
    """另一更新进程持有独占锁。"""


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_digest(records: Iterable[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda item: str(item["path"])):
        digest.update(str(record["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value or "")
    if match is None:
        raise UpdateError(f"版本号格式非法：{value!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _is_reparse_or_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    if bool(getattr(path, "is_junction", lambda: False)()):
        return True
    attributes = int(getattr(info, "st_file_attributes", 0))
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def _assert_plain_path(path: Path, description: str, *, directory: bool) -> None:
    if _is_reparse_or_link(path):
        raise UpdateError(f"{description}不能是符号链接或 Windows 重解析点：{path}")
    if directory and not path.is_dir():
        raise UpdateError(f"{description}不是目录：{path}")
    if not directory and not path.is_file():
        raise UpdateError(f"{description}不是普通文件：{path}")


def _assert_descendants_plain(root: Path, description: str) -> None:
    _assert_plain_path(root, description, directory=True)
    for current, directories, files in os.walk(str(root), followlinks=False):
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            if _is_reparse_or_link(path):
                raise UpdateError(f"{description}包含链接或重解析点：{path}")
        for name in files:
            path = current_path / name
            if _is_reparse_or_link(path) or not path.is_file():
                raise UpdateError(f"{description}包含特殊文件：{path}")


def _safe_relative_path(value: str) -> tuple[str, ...]:
    if (
        not value
        or value.startswith(("/", "\\"))
        or "\\" in value
        or ":" in value
        or "\x00" in value
    ):
        raise UpdateError(f"更新负载路径非法：{value!r}")
    parts = tuple(value.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise UpdateError(f"更新负载路径包含空段、. 或 ..：{value}")
    for part in parts:
        if any(ord(character) < 32 for character in part):
            raise UpdateError(f"更新负载路径包含控制字符：{value}")
        if any(character in '<>"|?*' for character in part):
            raise UpdateError(f"更新负载路径包含 Windows 非法字符：{value}")
        if part.endswith((" ", ".")):
            raise UpdateError(f"更新负载路径以空格或句点结尾：{value}")
        if len(part.encode("utf-16-le")) // 2 > 255:
            raise UpdateError(f"更新负载单个路径名称过长：{value}")
        if part.split(".", 1)[0].casefold() in RESERVED_WINDOWS_BASENAMES:
            raise UpdateError(f"更新负载使用 Windows 保留名称：{value}")
    if any(part.casefold() in {name.casefold() for name in PROTECTED_NAMES} for part in parts):
        raise UpdateError(f"更新负载错误包含受保护目录：{value}")
    if value not in REQUIRED_FILES and not value.startswith(MANAGED_PREFIXES):
        raise UpdateError(f"更新负载出现白名单外文件：{value}")
    return parts


def _records_for_tree(
    root: Path,
    *,
    skip: Optional[Callable[[str], bool]] = None,
) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    _assert_descendants_plain(root, "待校验目录")
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if skip is not None and skip(relative):
            continue
        records.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return records


def _records_map(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    folded: dict[str, str] = {}
    for raw in records:
        names = set(raw)
        if names != {"path", "size", "sha256"}:
            raise UpdateError("更新清单文件记录字段不符合约定")
        path = str(raw["path"])
        if path in result:
            raise UpdateError(f"更新清单包含重复路径：{path}")
        key = unicodedata.normalize("NFC", path).casefold()
        if key in folded:
            raise UpdateError(
                f"更新清单包含 Windows 大小写冲突：{folded[key]} / {path}"
            )
        size = raw["size"]
        sha256 = str(raw["sha256"])
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        ):
            raise UpdateError(f"更新清单文件记录非法：{path}")
        result[path] = {"path": path, "size": size, "sha256": sha256}
        folded[key] = path
    return result


def _assert_record_sets_equal(
    expected: Sequence[Mapping[str, Any]],
    actual: Sequence[Mapping[str, Any]],
    description: str,
) -> None:
    expected_map = _records_map(expected)
    actual_map = _records_map(actual)
    if expected_map != actual_map:
        missing = sorted(set(expected_map) - set(actual_map))
        extra = sorted(set(actual_map) - set(expected_map))
        changed = sorted(
            path
            for path in set(expected_map) & set(actual_map)
            if expected_map[path] != actual_map[path]
        )
        raise UpdateError(
            f"{description}不一致；缺少={missing}，多出={extra}，变化={changed}"
        )


def _copy_tree_verified(source: Path, destination: Path) -> list[dict[str, Any]]:
    _assert_descendants_plain(source, "复制源目录")
    if destination.exists():
        raise UpdateError(f"安全副本目标已经存在：{destination}")
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    source_records = _records_for_tree(source)
    destination_records = _records_for_tree(destination)
    _assert_record_sets_equal(source_records, destination_records, "目录安全副本")
    return destination_records


def _tree_size(root: Path, description: str) -> int:
    _assert_descendants_plain(root, description)
    total = 0
    for current, _directories, files in os.walk(str(root), followlinks=False):
        current_path = Path(current)
        for name in files:
            total += (current_path / name).stat().st_size
    return total


def _assert_sufficient_space(
    bundle: "Bundle", install_root: Path, data_root: Path
) -> None:
    data_size = _tree_size(data_root, "客户 data")
    database_size = (data_root / "reservation.db").stat().st_size
    runtime_size = sum(int(record["size"]) for record in bundle.runtime_records)
    program_size = max(
        sum(int(record["size"]) for record in bundle.baseline.records),
        sum(int(record["size"]) for record in bundle.target.records),
    )
    current_program_size = 0
    for relative in list(TOP_LEVEL_FILES) + list(PROGRAM_FILES):
        path = install_root.joinpath(*relative.split("/"))
        if path.is_file() and not _is_reparse_or_link(path):
            current_program_size += path.stat().st_size
    for tree in MANAGED_TREES:
        path = install_root.joinpath(*tree.split("/"))
        if path.exists():
            current_program_size += _tree_size(
                path, f"当前受管程序 {tree}"
            )
    required = (
        data_size * 2
        + database_size
        + runtime_size
        + current_program_size
        + program_size * 2
        + 128 * 1024 * 1024
    )
    free = shutil.disk_usage(install_root).free
    if free < required:
        raise UpdateError(
            "安装磁盘剩余空间不足："
            f"至少需要约 {required / (1024 * 1024):.1f} MB，"
            f"当前约 {free / (1024 * 1024):.1f} MB"
        )


@dataclass(frozen=True)
class Payload:
    version: str
    zip_name: str
    zip_sha256: str
    files: Mapping[str, bytes]
    records: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class Bundle:
    tool_root: Path
    release: str
    baseline: Payload
    target: Payload
    runtime_records: tuple[Mapping[str, Any], ...]
    runtime_tree_sha256: str

    @classmethod
    def load(cls, tool_root: Path) -> "Bundle":
        tool_root = tool_root.resolve()
        manifest_path = tool_root / TOOL_MANIFEST
        _assert_plain_path(manifest_path, "修复更新清单", directory=False)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise UpdateError("修复更新清单无法读取或 JSON 已损坏") from error
        if set(manifest) != {
            "schema",
            "release",
            "baseline",
            "target",
            "runtime",
        }:
            raise UpdateError("修复更新清单顶层字段不符合约定")
        if manifest["schema"] != 1 or manifest["release"] != REPAIR_RELEASE:
            raise UpdateError("修复更新清单版本不受支持")

        baseline = cls._load_payload(tool_root, manifest["baseline"], BASELINE_VERSION)
        target = cls._load_payload(tool_root, manifest["target"], TARGET_VERSION)
        runtime = manifest["runtime"]
        if set(runtime) != {"tree_sha256", "files"}:
            raise UpdateError("runtime 清单字段不符合约定")
        runtime_records_map = _records_map(runtime["files"])
        runtime_records = tuple(
            runtime_records_map[path] for path in sorted(runtime_records_map)
        )
        runtime_digest = str(runtime["tree_sha256"])
        if _tree_digest(runtime_records) != runtime_digest:
            raise UpdateError("runtime 清单自身的树哈希不一致")
        runtime_root = tool_root / "runtime"
        _assert_descendants_plain(runtime_root, "修复工具 runtime")
        actual_runtime = _records_for_tree(runtime_root)
        _assert_record_sets_equal(
            runtime_records, actual_runtime, "修复工具 runtime"
        )
        if _tree_digest(actual_runtime) != runtime_digest:
            raise UpdateError("修复工具 runtime 树哈希不一致")
        for required in ("python.exe", "pythonw.exe"):
            if required not in runtime_records_map:
                raise UpdateError(f"修复工具 runtime 缺少 {required}")
        return cls(
            tool_root=tool_root,
            release=REPAIR_RELEASE,
            baseline=baseline,
            target=target,
            runtime_records=runtime_records,
            runtime_tree_sha256=runtime_digest,
        )

    @staticmethod
    def _load_payload(tool_root: Path, section: Any, expected_version: str) -> Payload:
        if not isinstance(section, dict) or set(section) != {
            "version",
            "file",
            "sha256",
            "size",
            "files",
        }:
            raise UpdateError("更新负载清单字段不符合约定")
        version = str(section["version"])
        if version != expected_version:
            raise UpdateError(
                f"更新负载版本错误：期望 {expected_version}，实际 {version}"
            )
        zip_name = str(section["file"])
        if Path(zip_name).name != zip_name or not zip_name.endswith(".zip"):
            raise UpdateError(f"更新负载文件名非法：{zip_name}")
        zip_path = tool_root / zip_name
        _assert_plain_path(zip_path, "更新负载 ZIP", directory=False)
        zip_bytes = zip_path.read_bytes()
        if len(zip_bytes) != section["size"]:
            raise UpdateError(f"更新负载 ZIP 大小不一致：{zip_name}")
        zip_sha256 = str(section["sha256"])
        if _sha256_bytes(zip_bytes) != zip_sha256:
            raise UpdateError(f"更新负载 ZIP SHA-256 不一致：{zip_name}")

        records_map = _records_map(section["files"])
        if not REQUIRED_FILES.issubset(records_map):
            raise UpdateError(
                f"更新负载缺少固定文件：{sorted(REQUIRED_FILES - set(records_map))}"
            )
        for tree in MANAGED_TREES:
            prefix = tree + "/"
            if not any(path.startswith(prefix) for path in records_map):
                raise UpdateError(f"更新负载目录为空：{tree}")
        for path in records_map:
            _safe_relative_path(path)

        files: Dict[str, bytes] = {}
        try:
            archive = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
        except (OSError, zipfile.BadZipFile) as error:
            raise UpdateError(f"更新负载不是有效 ZIP：{zip_name}") from error
        with archive:
            seen_folded: dict[str, str] = {}
            for info in archive.infolist():
                path = info.filename
                _safe_relative_path(path)
                if info.is_dir() or path.endswith("/"):
                    raise UpdateError(f"更新负载 ZIP 不允许目录条目：{path}")
                if path in files:
                    raise UpdateError(f"更新负载 ZIP 路径重复：{path}")
                folded = path.casefold()
                if folded in seen_folded:
                    raise UpdateError(
                        f"更新负载 ZIP 大小写冲突：{seen_folded[folded]} / {path}"
                    )
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_IFMT(mode) == stat.S_IFLNK or info.flag_bits & 0x1:
                    raise UpdateError(f"更新负载 ZIP 含链接或加密文件：{path}")
                content = archive.read(info)
                files[path] = content
                seen_folded[folded] = path
            bad = archive.testzip()
            if bad is not None:
                raise UpdateError(f"更新负载 ZIP CRC 校验失败：{bad}")

        if set(files) != set(records_map):
            raise UpdateError(f"更新负载 ZIP 文件集合与清单不一致：{zip_name}")
        for path, content in files.items():
            record = records_map[path]
            if len(content) != record["size"] or _sha256_bytes(content) != record["sha256"]:
                raise UpdateError(f"更新负载文件哈希不一致：{path}")
        version_bytes = files["_程序文件/版本.txt"]
        try:
            version_text = version_bytes.decode("utf-8").strip()
        except UnicodeDecodeError as error:
            raise UpdateError("更新负载版本文件不是 UTF-8") from error
        if version_text != expected_version:
            raise UpdateError(
                f"更新负载版本文件错误：期望 {expected_version}，实际 {version_text}"
            )
        records = tuple(records_map[path] for path in sorted(records_map))
        return Payload(
            version=version,
            zip_name=zip_name,
            zip_sha256=zip_sha256,
            files=files,
            records=records,
        )


class EventLog:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def add_path(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if _is_reparse_or_link(path.parent):
            raise UpdateError(f"日志目录不能是链接或重解析点：{path.parent}")
        if _is_reparse_or_link(path):
            raise UpdateError(f"日志文件不能是链接或重解析点：{path}")
        if path not in self.paths:
            self.paths.append(path)

    def write(self, message: str, level: str = "INFO") -> None:
        line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S.%f}"[:-3]
        line += f" [{level}] {message}"
        print(message)
        for path in tuple(self.paths):
            try:
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line + "\n")
            except OSError:
                continue


class ExclusiveLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Optional[Any] = None
        self.original_bytes: Optional[bytes] = None

    def __enter__(self) -> "ExclusiveLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if _is_reparse_or_link(self.path):
            raise UpdateError(f"升级锁不能是链接或重解析点：{self.path}")
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            original_size = handle.tell()
            if original_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.seek(0)
            self.original_bytes = handle.read(original_size)
        except (OSError, BlockingIOError) as error:
            handle.close()
            self.original_bytes = None
            raise UpdateBusy("另一个修复更新正在运行") from error
        self.handle = handle
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                with contextlib.suppress(OSError):
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                with contextlib.suppress(OSError):
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


@dataclass(frozen=True)
class TaskState:
    exists: bool
    enabled: bool
    was_running: bool
    system_was_running: bool

    def as_json(self) -> dict[str, bool]:
        return {
            "exists": self.exists,
            "enabled": self.enabled,
            "was_running": self.was_running,
            "system_was_running": self.system_was_running,
        }

    @classmethod
    def from_json(cls, value: Any) -> "TaskState":
        if not isinstance(value, dict) or set(value) != {
            "exists",
            "enabled",
            "was_running",
            "system_was_running",
        }:
            raise UpdateError("修复状态中的计划任务信息非法")
        if any(not isinstance(item, bool) for item in value.values()):
            raise UpdateError("修复状态中的计划任务信息必须是布尔值")
        return cls(
            exists=value["exists"],
            enabled=value["enabled"],
            was_running=value["was_running"],
            system_was_running=value["system_was_running"],
        )


@dataclass(frozen=True)
class LegacyStateInfo:
    stage: str
    package_version: Optional[str]
    transaction_id: Optional[str]
    task_state: Optional[TaskState]
    raw: bytes

    @property
    def explicitly_committed(self) -> bool:
        return (
            self.package_version == TARGET_VERSION
            and self.stage in {"version_committed", "service_restored"}
        )


class SystemController:
    def inspect_state(self, install_root: Path) -> TaskState:
        raise NotImplementedError

    def stop_and_disable(self, install_root: Path) -> None:
        raise NotImplementedError

    def restore_task_state(self, install_root: Path, state: TaskState) -> None:
        raise NotImplementedError


class PassiveSystemController(SystemController):
    """单元测试和非 Windows 故障注入使用。"""

    def __init__(self, state: Optional[TaskState] = None) -> None:
        self.state = state or TaskState(False, False, False, False)
        self.restored: Optional[TaskState] = None
        self.stop_calls = 0

    def inspect_state(self, install_root: Path) -> TaskState:
        del install_root
        return self.state

    def stop_and_disable(self, install_root: Path) -> None:
        del install_root
        self.stop_calls += 1

    def restore_task_state(self, install_root: Path, state: TaskState) -> None:
        del install_root
        self.restored = state


class WindowsSystemController(SystemController):
    POWERSHELL = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )

    def _run_powershell(
        self,
        script: str,
        *,
        install_root: Path,
        extra_env: Optional[Mapping[str, str]] = None,
    ) -> str:
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        environment = os.environ.copy()
        environment["MR_INSTALL_ROOT"] = str(install_root)
        if extra_env:
            environment.update(extra_env)
        result = subprocess.run(
            [
                str(self.POWERSHELL),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        )
        if result.returncode != 0:
            details = (result.stderr or result.stdout).strip()
            raise UpdateError(f"Windows 服务或计划任务处理失败：{details}")
        return result.stdout.strip()

    def inspect_state(self, install_root: Path) -> TaskState:
        script = r"""
$ErrorActionPreference = 'Stop'
function Full([string]$p) { return [IO.Path]::GetFullPath($p).TrimEnd('\') }
function Same([string]$a,[string]$b) {
    return [string]::Equals((Full $a),(Full $b),[StringComparison]::OrdinalIgnoreCase)
}
$root = Full $env:MR_INSTALL_ROOT
$program = Join-Path $root '_程序文件'
$python = Full (Join-Path $program 'runtime\python.exe')
$pythonw = Full (Join-Path $program 'runtime\pythonw.exe')
$server = Full (Join-Path $program 'server.py')
$tasks = @(Get-ScheduledTask -TaskName '会议室预约系统' -ErrorAction SilentlyContinue |
    Where-Object { [string]$_.TaskPath -eq '\' })
if ($tasks.Count -gt 1) { throw '发现多个同名计划任务，无法确认归属。' }
$taskExists = $tasks.Count -eq 1
$taskEnabled = $false
$taskWasRunning = $false
if ($taskExists) {
    $task = $tasks[0]
    $actions = @($task.Actions)
    if ($actions.Count -ne 1) { throw '同名计划任务动作数量不一致。' }
    $action = $actions[0]
    $actualExe = [Environment]::ExpandEnvironmentVariables([string]$action.Execute)
    $actualWork = [Environment]::ExpandEnvironmentVariables([string]$action.WorkingDirectory)
    $actualArgs = ([Environment]::ExpandEnvironmentVariables([string]$action.Arguments)).Trim()
    if ($actualArgs -notmatch '^"([^"]+)"$') { throw '同名计划任务启动参数不一致。' }
    if (-not (Same $actualExe $pythonw) -or -not (Same $matches[1] $server) -or
        ($actualWork -and -not (Same $actualWork $program))) {
        throw '同名计划任务指向另一套安装，拒绝停止。'
    }
    $taskEnabled = [bool]$task.Settings.Enabled
    $taskWasRunning = [string]$task.State -eq 'Running'
}
$owned = @()
try {
    $processes = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'")
} catch { throw ('无法枚举 Python 进程：' + $_.Exception.Message) }
foreach ($process in $processes) {
    if (-not $process.ExecutablePath -or -not $process.CommandLine) { continue }
    try { $exe = Full ([string]$process.ExecutablePath) } catch { continue }
    $runtimeOwned = (Same $exe $python) -or (Same $exe $pythonw)
    $cmd = [string]$process.CommandLine
    $absolute = '(?i)(^|\s)"?' + [regex]::Escape($server) + '"?(?=\s|$)'
    $legacy = '(?i)(^|\s)"?_程序文件[\\/]server\.py"?(?=\s|$)'
    if ($runtimeOwned -and ([regex]::IsMatch($cmd,$absolute) -or
        [regex]::IsMatch($cmd,$legacy))) { $owned += $process }
}
$wasRunning = $owned.Count -gt 0
[ordered]@{
    exists = [bool]$taskExists
    enabled = [bool]$taskEnabled
    was_running = [bool]$taskWasRunning
    system_was_running = [bool]$wasRunning
} | ConvertTo-Json -Compress
"""
        output = self._run_powershell(script, install_root=install_root)
        try:
            value = json.loads(output)
        except ValueError as error:
            raise UpdateError("Windows 计划任务状态返回值不是有效 JSON") from error
        state = TaskState.from_json(value)
        return state

    def stop_and_disable(self, install_root: Path) -> None:
        script = r"""
$ErrorActionPreference = 'Stop'
function Full([string]$p) { return [IO.Path]::GetFullPath($p).TrimEnd('\') }
function Same([string]$a,[string]$b) {
    return [string]::Equals((Full $a),(Full $b),[StringComparison]::OrdinalIgnoreCase)
}
$root = Full $env:MR_INSTALL_ROOT
$program = Join-Path $root '_程序文件'
$python = Full (Join-Path $program 'runtime\python.exe')
$pythonw = Full (Join-Path $program 'runtime\pythonw.exe')
$server = Full (Join-Path $program 'server.py')
$tasks = @(Get-ScheduledTask -TaskName '会议室预约系统' -ErrorAction SilentlyContinue |
    Where-Object { [string]$_.TaskPath -eq '\' })
if ($tasks.Count -gt 1) { throw '发现多个同名计划任务，无法确认归属。' }
if ($tasks.Count -eq 1) {
    $task = $tasks[0]
    $actions = @($task.Actions)
    if ($actions.Count -ne 1) { throw '同名计划任务动作数量不一致。' }
    $action = $actions[0]
    $actualExe = [Environment]::ExpandEnvironmentVariables([string]$action.Execute)
    $actualWork = [Environment]::ExpandEnvironmentVariables([string]$action.WorkingDirectory)
    $actualArgs = ([Environment]::ExpandEnvironmentVariables([string]$action.Arguments)).Trim()
    if ($actualArgs -notmatch '^"([^"]+)"$') { throw '同名计划任务启动参数不一致。' }
    if (-not (Same $actualExe $pythonw) -or -not (Same $matches[1] $server) -or
        ($actualWork -and -not (Same $actualWork $program))) {
        throw '同名计划任务指向另一套安装，拒绝停止。'
    }
    Disable-ScheduledTask -TaskName '会议室预约系统' -TaskPath '\' | Out-Null
    Stop-ScheduledTask -TaskName '会议室预约系统' -TaskPath '\' -ErrorAction SilentlyContinue
}
$owned = @()
try {
    $processes = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'")
} catch { throw ('无法枚举 Python 进程：' + $_.Exception.Message) }
foreach ($process in $processes) {
    if (-not $process.ExecutablePath -or -not $process.CommandLine) { continue }
    try { $exe = Full ([string]$process.ExecutablePath) } catch { continue }
    $runtimeOwned = (Same $exe $python) -or (Same $exe $pythonw)
    $cmd = [string]$process.CommandLine
    $absolute = '(?i)(^|\s)"?' + [regex]::Escape($server) + '"?(?=\s|$)'
    $legacy = '(?i)(^|\s)"?_程序文件[\\/]server\.py"?(?=\s|$)'
    if ($runtimeOwned -and ([regex]::IsMatch($cmd,$absolute) -or
        [regex]::IsMatch($cmd,$legacy))) { $owned += $process }
}
foreach ($process in $owned) {
    Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction Stop
}
$deadline = (Get-Date).AddSeconds(10)
do {
    $remaining = @()
    try {
        $remaining = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
            Where-Object {
                $_.ExecutablePath -and
                ((Same ([string]$_.ExecutablePath) $python) -or
                 (Same ([string]$_.ExecutablePath) $pythonw))
            })
    } catch {}
    if ($remaining.Count -eq 0) { break }
    Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $deadline)
if ($remaining.Count -gt 0) { throw '本安装目录的服务进程未能完全停止。' }
"""
        self._run_powershell(script, install_root=install_root)

    def restore_task_state(self, install_root: Path, state: TaskState) -> None:
        if not state.exists:
            return
        script = r"""
$ErrorActionPreference = 'Stop'
$task = @(Get-ScheduledTask -TaskName '会议室预约系统' -ErrorAction SilentlyContinue |
    Where-Object { [string]$_.TaskPath -eq '\' })
if ($task.Count -ne 1) { throw '无法恢复计划任务：同名任务数量变化。' }
if ($env:MR_TASK_ENABLED -eq '1') {
    Enable-ScheduledTask -TaskName '会议室预约系统' -TaskPath '\' | Out-Null
} else {
    Disable-ScheduledTask -TaskName '会议室预约系统' -TaskPath '\' | Out-Null
}
"""
        self._run_powershell(
            script,
            install_root=install_root,
            extra_env={"MR_TASK_ENABLED": "1" if state.enabled else "0"},
        )


def _port_in_use(port: int) -> bool:
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connection.settimeout(0.35)
    try:
        return connection.connect_ex(("127.0.0.1", port)) == 0
    finally:
        connection.close()


def _managed_records_from_files(files: Mapping[str, bytes]) -> list[dict[str, Any]]:
    return [
        {"path": path, "size": len(content), "sha256": _sha256_bytes(content)}
        for path, content in sorted(files.items())
    ]


def _installed_managed_records(
    install_root: Path,
    *,
    include_version: bool,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    fixed = list(TOP_LEVEL_FILES) + list(PROGRAM_FILES)
    for relative in fixed:
        if not include_version and relative == "_程序文件/版本.txt":
            continue
        path = install_root.joinpath(*relative.split("/"))
        if path.is_file() and not _is_reparse_or_link(path):
            records.append(
                {
                    "path": relative,
                    "size": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
    for tree in MANAGED_TREES:
        root = install_root.joinpath(*tree.split("/"))
        if not root.exists():
            continue
        _assert_descendants_plain(root, f"安装目录 {tree}")
        for path in sorted(root.rglob("*")):
            if path.is_file():
                records.append(
                    {
                        "path": path.relative_to(install_root).as_posix(),
                        "size": path.stat().st_size,
                        "sha256": _sha256_file(path),
                    }
                )
    return sorted(records, key=lambda item: str(item["path"]))


def _assert_managed_install_paths_plain(install_root: Path) -> None:
    for relative in list(TOP_LEVEL_FILES) + list(PROGRAM_FILES):
        path = install_root.joinpath(*relative.split("/"))
        if _is_reparse_or_link(path):
            raise UpdateError(f"受管文件不是普通文件：{path}")
        if not path.exists():
            continue
        if not path.is_file():
            raise UpdateError(f"受管文件不是普通文件：{path}")
    for tree in MANAGED_TREES:
        path = install_root.joinpath(*tree.split("/"))
        if _is_reparse_or_link(path):
            raise UpdateError(f"受管目录不是普通目录：{path}")
        if not path.exists():
            continue
        _assert_descendants_plain(path, f"受管目录 {tree}")
    cache = install_root / "_程序文件" / "__pycache__"
    if _is_reparse_or_link(cache):
        raise UpdateError(f"受管缓存不是普通目录：{cache}")
    if cache.exists():
        if not cache.is_dir():
            raise UpdateError(f"受管缓存不是普通目录：{cache}")
        _assert_descendants_plain(cache, "受管缓存")


def _assert_installed_payload(
    install_root: Path,
    payload: Payload,
    *,
    include_version: bool,
) -> None:
    expected = [
        record
        for record in payload.records
        if include_version or record["path"] != "_程序文件/版本.txt"
    ]
    actual = _installed_managed_records(
        install_root, include_version=include_version
    )
    _assert_record_sets_equal(expected, actual, f"已安装 {payload.version} 受管程序")


def _remove_path_safely(path: Path) -> None:
    if _is_reparse_or_link(path):
        raise UpdateError(f"拒绝删除链接或重解析点：{path}")
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _write_managed_payload(
    install_root: Path,
    payload: Payload,
    txid: str,
    *,
    include_version: bool,
) -> None:
    program_root = install_root / "_程序文件"
    fixed_paths = list(TOP_LEVEL_FILES) + list(PROGRAM_FILES)
    for relative in fixed_paths:
        if not include_version and relative == "_程序文件/版本.txt":
            continue
        content = payload.files[relative]
        destination = install_root.joinpath(*relative.split("/"))
        if _is_reparse_or_link(destination):
            raise UpdateError(f"受管文件是链接或重解析点：{destination}")
        _atomic_write(destination, content)

    for tree in MANAGED_TREES:
        name = tree.rsplit("/", 1)[1]
        destination = install_root.joinpath(*tree.split("/"))
        parent = destination.parent
        staged = parent / f".{name}.repair-{txid}.new"
        old = parent / f".{name}.repair-{txid}.old"
        for leftover in (staged, old):
            _remove_path_safely(leftover)
        staged.mkdir(parents=True)
        prefix = tree + "/"
        for relative, content in sorted(payload.files.items()):
            if not relative.startswith(prefix):
                continue
            child = staged.joinpath(*relative[len(prefix) :].split("/"))
            _atomic_write(child, content)
        if _is_reparse_or_link(destination):
            raise UpdateError(f"受管目录是链接或重解析点：{destination}")
        if destination.exists():
            os.replace(str(destination), str(old))
        os.replace(str(staged), str(destination))
        _remove_path_safely(old)

    cache = program_root / "__pycache__"
    if _is_reparse_or_link(cache) or cache.exists():
        _remove_path_safely(cache)


def _snapshot_current_program(
    install_root: Path, snapshot_root: Path
) -> dict[str, Any]:
    destination = snapshot_root / "original-program"
    destination.mkdir(parents=True)
    existence: list[dict[str, Any]] = []
    for relative in list(TOP_LEVEL_FILES) + list(PROGRAM_FILES):
        source = install_root.joinpath(*relative.split("/"))
        exists = source.is_file() and not _is_reparse_or_link(source)
        existence.append({"path": relative, "kind": "file", "existed": exists})
        if exists:
            target = destination.joinpath(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for tree in MANAGED_TREES:
        source = install_root.joinpath(*tree.split("/"))
        exists = source.is_dir() and not _is_reparse_or_link(source)
        existence.append({"path": tree, "kind": "directory", "existed": exists})
        if exists:
            _copy_tree_verified(
                source, destination.joinpath(*tree.split("/"))
            )
    records = _records_for_tree(destination)
    manifest = {"existence": existence, "files": records}
    _atomic_write(snapshot_root / "original-program-manifest.json", _json_bytes(manifest))
    return manifest


def _restore_original_program(
    install_root: Path, snapshot_root: Path, manifest: Mapping[str, Any]
) -> None:
    snapshot = snapshot_root / "original-program"
    _assert_record_sets_equal(
        manifest["files"],
        _records_for_tree(snapshot),
        "原程序快照",
    )
    for relative in list(TOP_LEVEL_FILES) + list(PROGRAM_FILES):
        _remove_path_safely(install_root.joinpath(*relative.split("/")))
    for tree in MANAGED_TREES:
        _remove_path_safely(install_root.joinpath(*tree.split("/")))
    for record in manifest["files"]:
        relative = str(record["path"])
        source = snapshot.joinpath(*relative.split("/"))
        destination = install_root.joinpath(*relative.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    expected_records = list(manifest["files"])
    actual_records = _installed_managed_records(install_root, include_version=True)
    _assert_record_sets_equal(expected_records, actual_records, "原程序恢复结果")


def _runtime_skip(relative: str) -> bool:
    parts = relative.split("/")
    return "__pycache__" in parts or relative.endswith(".pyc")


def _assert_installed_runtime(bundle: Bundle, install_root: Path) -> None:
    runtime = install_root / "_程序文件" / "runtime"
    _assert_descendants_plain(runtime, "已安装 runtime")
    _assert_record_sets_equal(
        bundle.runtime_records,
        _records_for_tree(runtime, skip=_runtime_skip),
        "已安装 runtime",
    )


def _assert_runtime_recovery_safe(
    bundle: Bundle, install_root: Path, snapshot_root: Path
) -> None:
    try:
        _assert_installed_runtime(bundle, install_root)
        return
    except UpdateError:
        pass
    manifest_path = snapshot_root / "original-runtime-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise UpdateError(
            "runtime 既不匹配冻结版本，也没有可验证的原 runtime 恢复清单"
        ) from error
    if not isinstance(manifest, dict) or set(manifest) != {"kind", "files"}:
        raise UpdateError("原 runtime 恢复清单字段非法")
    runtime = install_root / "_程序文件" / "runtime"
    if manifest["kind"] == "directory":
        _assert_plain_path(runtime, "恢复后的原 runtime", directory=True)
        _assert_record_sets_equal(
            manifest["files"],
            _records_for_tree(runtime),
            "恢复后的原 runtime",
        )
        return
    if manifest["kind"] == "file":
        _assert_plain_path(runtime, "恢复后的原 runtime 文件", directory=False)
        records = _records_map(manifest["files"])
        record = records.get("runtime")
        if (
            len(records) != 1
            or record is None
            or runtime.stat().st_size != record["size"]
            or _sha256_file(runtime) != record["sha256"]
        ):
            raise UpdateError("恢复后的原 runtime 文件与清单不一致")
        return
    raise UpdateError("原 runtime 恢复清单类型非法")


def _ensure_installed_runtime(
    bundle: Bundle, install_root: Path, snapshot_root: Path, txid: str
) -> bool:
    program_root = install_root / "_程序文件"
    runtime = program_root / "runtime"
    expected = list(bundle.runtime_records)
    if runtime.is_dir() and not _is_reparse_or_link(runtime):
        try:
            _assert_installed_runtime(bundle, install_root)
            return False
        except UpdateError:
            pass

    staged = program_root / f".runtime.repair-{txid}.new"
    _remove_path_safely(staged)
    _copy_tree_verified(bundle.tool_root / "runtime", staged)
    _assert_record_sets_equal(
        expected,
        _records_for_tree(staged, skip=_runtime_skip),
        "待安装 runtime",
    )
    old_runtime = snapshot_root / "original-runtime"
    moved_original = False
    if runtime.exists():
        if _is_reparse_or_link(runtime):
            raise UpdateError(f"已安装 runtime 是链接或重解析点：{runtime}")
        if runtime.is_dir():
            original_runtime_manifest: dict[str, Any] = {
                "kind": "directory",
                "files": _records_for_tree(runtime),
            }
        elif runtime.is_file():
            original_runtime_manifest = {
                "kind": "file",
                "files": [
                    {
                        "path": "runtime",
                        "size": runtime.stat().st_size,
                        "sha256": _sha256_file(runtime),
                    }
                ],
            }
        else:
            raise UpdateError(f"已安装 runtime 是不受支持的特殊文件：{runtime}")
        if old_runtime.exists():
            raise UpdateError("原 runtime 快照目标已存在，拒绝覆盖")
        _atomic_write(
            snapshot_root / "original-runtime-manifest.json",
            _json_bytes(original_runtime_manifest),
        )
        os.replace(str(runtime), str(old_runtime))
        moved_original = True
    try:
        if moved_original:
            if original_runtime_manifest["kind"] == "directory":
                _assert_record_sets_equal(
                    original_runtime_manifest["files"],
                    _records_for_tree(old_runtime),
                    "原 runtime 快照",
                )
            elif (
                not old_runtime.is_file()
                or _sha256_file(old_runtime)
                != original_runtime_manifest["files"][0]["sha256"]
            ):
                raise UpdateError("原 runtime 文件快照校验失败")
        os.replace(str(staged), str(runtime))
        _assert_record_sets_equal(
            expected,
            _records_for_tree(runtime, skip=_runtime_skip),
            "修复后的 runtime",
        )
    except BaseException:
        if moved_original and old_runtime.exists():
            with contextlib.suppress(OSError, UpdateError):
                if runtime.exists():
                    _remove_path_safely(runtime)
                os.replace(str(old_runtime), str(runtime))
        raise
    return True


def _read_current_version(program_root: Path) -> str:
    path = program_root / "版本.txt"
    if not path.exists():
        return "1.0.0"
    _assert_plain_path(path, "安装版本文件", directory=False)
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise UpdateError("安装版本文件不能带 UTF-8 BOM")
    try:
        value = raw.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise UpdateError("安装版本文件不是有效 UTF-8") from error
    _version_tuple(value)
    return value


def _validate_install_root(path: Path) -> Path:
    try:
        root = path.expanduser().resolve(strict=True)
    except OSError as error:
        raise UpdateError(f"安装目录不存在或无法读取：{path}") from error
    _assert_plain_path(root, "安装根目录", directory=True)
    program = root / "_程序文件"
    data = program / "data"
    database = data / "reservation.db"
    for target, description, directory in (
        (program, "_程序文件", True),
        (data, "客户 data", True),
        (database, "客户数据库", False),
    ):
        _assert_plain_path(target, description, directory=directory)
    identity_paths = (
        root / "① 启动系统.bat",
        root / "使用说明.txt",
        program / "版本.txt",
        program / "runtime" / "python.exe",
        program / LEGACY_STATE_NAME,
        program / STATE_NAME,
    )
    if not any(candidate.is_file() for candidate in identity_paths):
        raise UpdateError(f"所选目录缺少可确认的会议室预约系统身份：{root}")
    for optional in ("backups", "logs"):
        target = program / optional
        if _is_reparse_or_link(target):
            raise UpdateError(
                f"_程序文件\\{optional} 不能是链接或重解析点：{target}"
            )
        if target.exists():
            _assert_plain_path(target, f"_程序文件\\{optional}", directory=True)
    runtime = program / "runtime"
    if _is_reparse_or_link(runtime):
        raise UpdateError(
            f"_程序文件\\runtime 是链接、重解析点或特殊文件：{runtime}"
        )
    if runtime.exists():
        if not (runtime.is_dir() or runtime.is_file()):
            raise UpdateError(
                f"_程序文件\\runtime 是链接、重解析点或特殊文件：{runtime}"
            )
    if os.name == "nt":
        drive = root.drive
        if not drive or str(root).startswith("\\\\"):
            raise UpdateError("安装目录必须位于本机固定磁盘，不能使用网络共享路径")
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\")
        if drive_type != 3:  # DRIVE_FIXED
            raise UpdateError("安装目录必须位于本机固定磁盘")
    return root


def _assert_tool_location(tool_root: Path, install_root: Path) -> None:
    resolved_tool = tool_root.resolve()
    program_root = (install_root / "_程序文件").resolve()
    try:
        resolved_tool.relative_to(program_root)
    except ValueError:
        return
    raise UpdateError(
        "修复工具不能放在安装目录的“_程序文件”内部；"
        "请把完整修复 ZIP 解压到桌面、下载目录或系统根目录后重试"
    )


def _is_install_candidate(path: Path) -> bool:
    try:
        _validate_install_root(path)
        return True
    except UpdateError:
        return False


def _find_install_candidates(tool_root: Path) -> list[Path]:
    raw_candidates: list[Path] = []
    environment = os.environ.get("MEETING_ROOM_UPDATE_INSTALL_ROOT")
    if environment:
        raw_candidates.append(Path(environment))

    current = tool_root
    for _ in range(5):
        raw_candidates.append(current)
        if current.parent == current:
            break
        current = current.parent
    raw_candidates.append(Path.cwd())

    for parent in (tool_root.parent, tool_root.parent.parent):
        if not parent.is_dir() or _is_reparse_or_link(parent):
            continue
        try:
            children = list(parent.iterdir())
        except OSError:
            continue
        for child in children[:200]:
            if child.is_dir() and not _is_reparse_or_link(child):
                raw_candidates.append(child)

    if os.name == "nt":
        for drive in ("C:", "D:", "E:"):
            raw_candidates.append(Path(drive + r"\会议室预约系统"))
        user_home = Path(os.environ.get("USERPROFILE", str(Path.home())))
        for folder in ("Desktop", "Downloads"):
            parent = user_home / folder
            if not parent.is_dir():
                continue
            with contextlib.suppress(OSError):
                raw_candidates.extend(
                    child
                    for child in list(parent.iterdir())[:200]
                    if child.is_dir() and not _is_reparse_or_link(child)
                )

    results: list[Path] = []
    keys: set[str] = set()
    for candidate in raw_candidates:
        if not _is_install_candidate(candidate):
            continue
        resolved = candidate.resolve()
        key = str(resolved).casefold()
        if key not in keys:
            keys.add(key)
            results.append(resolved)
    return results


def _select_install_root(tool_root: Path, *, noninteractive: bool) -> Path:
    candidates = _find_install_candidates(tool_root)
    if len(candidates) == 1:
        return candidates[0]
    if noninteractive:
        if not candidates:
            raise UpdateError("没有找到唯一的会议室预约系统安装目录")
        raise UpdateError(f"发现多套安装，非交互模式拒绝猜测：{candidates}")
    if candidates:
        print()
        print("发现多套会议室预约系统，请选择需要修复更新的一套：")
        for index, candidate in enumerate(candidates, start=1):
            print(f"  {index}. {candidate}")
        print("  0. 取消")
        value = input("请输入序号：").strip()
        if value == "0":
            raise UpdateCancelled("用户取消选择安装目录")
        try:
            selected = int(value)
        except ValueError as error:
            raise UpdateCancelled("没有选择有效的安装目录") from error
        if selected < 1 or selected > len(candidates):
            raise UpdateCancelled("没有选择有效的安装目录")
        return candidates[selected - 1]

    print()
    print("没有自动找到会议室预约系统。")
    print("请复制系统文件夹的完整路径并粘贴到这里，直接回车表示取消。")
    value = input("系统文件夹：").strip().strip('"')
    if not value:
        raise UpdateCancelled("用户取消选择安装目录")
    return _validate_install_root(Path(value))


def _load_state(path: Path, install_root: Path, bundle: Bundle) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if len(raw) > 1024 * 1024:
            raise UpdateError("修复状态文件过大")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise UpdateError("修复状态文件损坏，已停止以保护 data") from error
    expected = {
        "schema",
        "release",
        "transaction_id",
        "stage",
        "install_root",
        "baseline_zip_sha256",
        "target_zip_sha256",
        "runtime_repaired",
        "task_state",
        "created_at",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise UpdateError("修复状态文件字段非法，已停止以保护 data")
    if (
        value["schema"] != STATE_SCHEMA
        or value["release"] != bundle.release
        or not TXID_RE.fullmatch(str(value["transaction_id"]))
        or str(value["stage"]) not in NEW_STAGES
        or str(value["baseline_zip_sha256"]) != bundle.baseline.zip_sha256
        or str(value["target_zip_sha256"]) != bundle.target.zip_sha256
        or not isinstance(value["runtime_repaired"], bool)
    ):
        raise UpdateError("修复状态文件版本、阶段或负载哈希非法")
    if Path(str(value["install_root"])).resolve() != install_root.resolve():
        raise UpdateError("修复状态文件指向另一个安装目录")
    TaskState.from_json(value["task_state"])
    return value


def _write_state(path: Path, state: Mapping[str, Any]) -> None:
    _atomic_write(path, _json_bytes(state))


def _read_legacy_state(program_root: Path) -> Optional[LegacyStateInfo]:
    legacy_state = program_root / LEGACY_STATE_NAME
    if not legacy_state.exists():
        return None
    _assert_plain_path(legacy_state, "旧升级状态", directory=False)
    raw = legacy_state.read_bytes()
    if len(raw) > 1024 * 1024:
        raise UpdateError("旧升级状态文件过大，拒绝猜测恢复")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise UpdateError("旧升级状态 JSON 损坏，拒绝猜测恢复") from error
    if not isinstance(value, dict):
        raise UpdateError("旧升级状态不是 JSON 对象，拒绝猜测恢复")
    stage = str(value.get("Stage", value.get("stage", "")))
    if stage not in KNOWN_LEGACY_STAGES:
        raise UpdateError("旧升级状态阶段未知，拒绝猜测恢复")
    package_version_raw = value.get(
        "PackageVersion", value.get("package_version")
    )
    package_version = (
        str(package_version_raw) if package_version_raw is not None else None
    )
    if package_version is not None and package_version not in {
        BASELINE_VERSION,
        TARGET_VERSION,
    }:
        raise UpdateError("旧升级状态的目标版本不受本修复工具支持")

    transaction_raw = value.get("TransactionId", value.get("transaction_id"))
    transaction_id: Optional[str] = None
    if transaction_raw is not None:
        candidate = str(transaction_raw).lower()
        if not TXID_RE.fullmatch(candidate):
            raise UpdateError("旧升级状态的事务 ID 非法")
        transaction_id = candidate

    task_state: Optional[TaskState] = None
    task_fields = ("TaskExists", "TaskEnabled", "TaskWasRunning", "WasRunning")
    if all(field in value for field in task_fields):
        if any(not isinstance(value[field], bool) for field in task_fields):
            raise UpdateError("旧升级状态中的运行信息非法")
        exists = bool(value["TaskExists"])
        enabled = bool(value["TaskEnabled"])
        task_was_running = bool(value["TaskWasRunning"])
        system_was_running = bool(value["WasRunning"])
        if (
            (not exists and (enabled or task_was_running))
            or (task_was_running and not system_was_running)
        ):
            raise UpdateError("旧升级状态中的运行信息互相矛盾")
        task_state = TaskState(
            exists=exists,
            enabled=enabled,
            was_running=task_was_running,
            system_was_running=system_was_running,
        )
    return LegacyStateInfo(
        stage=stage,
        package_version=package_version,
        transaction_id=transaction_id,
        task_state=task_state,
        raw=raw,
    )


def _inspect_legacy_state(program_root: Path, evidence_root: Path) -> Optional[str]:
    info = _read_legacy_state(program_root)
    if info is None:
        return None
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / LEGACY_STATE_NAME).write_bytes(info.raw)
    return info.stage


def _archive_legacy_residue(
    program_root: Path,
    log: EventLog,
    *,
    include_locked_legacy_lock: bool = False,
    locked_legacy_lock_bytes: Optional[bytes] = None,
) -> None:
    legacy_items: list[Path] = []
    for name in (LEGACY_STATE_NAME,):
        path = program_root / name
        if path.exists():
            legacy_items.append(path)
    if include_locked_legacy_lock:
        if locked_legacy_lock_bytes is None:
            raise UpdateError("旧升级锁证据没有从已持有的锁句柄中捕获")
        legacy_lock = program_root / LEGACY_LOCK_NAME
        legacy_items.append(legacy_lock)
    legacy_items.extend(
        path
        for path in program_root.glob(".版本.txt.upgrade-*.tmp")
        if path.is_file()
    )
    if not legacy_items:
        return
    archive = (
        program_root
        / "logs"
        / f"V1.0.2旧升级残留_{dt.datetime.now():%Y%m%d_%H%M%S_%f}"
    )
    archive.mkdir(parents=True, exist_ok=True)
    _assert_plain_path(archive, "旧升级残留归档目录", directory=True)
    for path in legacy_items:
        if _is_reparse_or_link(path):
            raise UpdateError(f"旧升级残留是链接或重解析点：{path}")
        destination = archive / path.name
        if destination.exists():
            raise UpdateError(f"旧升级残留归档目标已经存在：{destination}")
        if path.name == LEGACY_LOCK_NAME:
            if locked_legacy_lock_bytes is None:
                raise UpdateError("旧升级锁归档内容缺失")
            _atomic_write(destination, locked_legacy_lock_bytes)
        else:
            os.replace(str(path), str(destination))
        log.write(f"旧升级残留已保留到：{destination}", "WARN")
    rollback = program_root / LEGACY_ROLLBACK_NAME
    if rollback.exists():
        log.write(
            f"旧升级回滚证据继续原样保留，未自动删除：{rollback}",
            "WARN",
        )


def _cleanup_repair_staging(program_root: Path, log: EventLog) -> None:
    patterns = (
        re.compile(r"\.(?:static|templates|runtime)\.repair-[0-9a-f]{32}\.(?:new|old)"),
        re.compile(r"\.(?:版本\.txt|_V102覆盖更新状态\.json)\..+\.tmp"),
    )
    for path in list(program_root.iterdir()):
        if not any(pattern.fullmatch(path.name) for pattern in patterns):
            continue
        _remove_path_safely(path)
        log.write(f"已清理本修复工具的确定性临时残留：{path}", "WARN")


def _make_validation_copy(
    data_source: Path, snapshot_root: Path
) -> tuple[Path, list[dict[str, Any]]]:
    validation = snapshot_root / "validation-data"
    _remove_path_safely(validation)
    records = _copy_tree_verified(data_source, validation)
    return validation, records


def _database_integrity_check(database: Path) -> None:
    try:
        connection = sqlite3.connect(str(database), timeout=10)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            row = connection.execute(
                "SELECT value FROM app_meta WHERE key='schema_version'"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise UpdateError(f"数据库副本完整性检查失败：{error}") from error
    if integrity != [("ok",)] or foreign_keys:
        raise UpdateError(
            f"数据库副本完整性检查失败：integrity={integrity}，foreign_keys={foreign_keys}"
        )
    if row is None or str(row[0]) != "1":
        raise UpdateError(f"数据库结构版本不是 1：{row}")


def _make_permanent_backup(validation_data: Path, backups: Path) -> Path:
    backups.mkdir(parents=True, exist_ok=True)
    if _is_reparse_or_link(backups):
        raise UpdateError(f"backups 不能是链接或重解析点：{backups}")
    destination = (
        backups / f"pre_v102_repair_{dt.datetime.now():%Y-%m-%d_%H%M%S_%f}.db"
    )
    temporary = destination.with_suffix(".db.part")
    if destination.exists() or temporary.exists():
        raise UpdateError("修复前数据库备份目标发生极低概率重名，拒绝覆盖")
    try:
        with contextlib.closing(
            sqlite3.connect(
                str(validation_data / "reservation.db"), timeout=10
            )
        ) as source, contextlib.closing(
            sqlite3.connect(str(temporary))
        ) as target:
            source.execute("PRAGMA query_only = ON")
            source.backup(target)
        _database_integrity_check(temporary)
        os.replace(str(temporary), str(destination))
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
    return destination


def _free_loopback_port() -> int:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


def _validate_target_with_clone(
    install_root: Path,
    validation_data: Path,
    snapshot_root: Path,
    log: EventLog,
) -> None:
    program_root = install_root / "_程序文件"
    python = program_root / "runtime" / "python.exe"
    migrate = program_root / "migrate_check.py"
    server = program_root / "server.py"
    for path, description in (
        (python, "已安装 Python"),
        (migrate, "V1.0.2 数据检查程序"),
        (server, "V1.0.2 服务程序"),
    ):
        _assert_plain_path(path, description, directory=False)
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "MEETING_ROOM_DATA_DIR": str(validation_data),
            "MEETING_ROOM_UPGRADE_CHECK": "1",
            "MEETING_ROOM_OPEN_BROWSER": "0",
        }
    )
    migration = subprocess.run(
        [str(python), str(migrate), "--migrate"],
        cwd=str(program_root),
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=90,
    )
    for line in (migration.stdout, migration.stderr):
        if line.strip():
            log.write(line.strip(), "CHECK")
    if migration.returncode != 0:
        raise UpdateError(
            f"V1.0.2 对数据副本的迁移/自检失败，退出码 {migration.returncode}"
        )
    _database_integrity_check(validation_data / "reservation.db")

    port = _free_loopback_port()
    environment["MEETING_ROOM_PORT"] = str(port)
    health_log = snapshot_root / "healthcheck.log"
    with health_log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [str(python), str(server)],
            cwd=str(program_root),
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            healthy = False
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/healthz",
                        headers={"User-Agent": "MeetingRoomRepairUpdater/1.0"},
                    )
                    with urllib.request.urlopen(request, timeout=1.5) as response:
                        body = json.loads(response.read(8193).decode("utf-8"))
                        healthy = bool(
                            response.status == 200
                            and response.headers.get("X-Meeting-Room-System") == "1"
                            and body.get("ok") is True
                            and body.get("mode") == "upgrade-check"
                        )
                    if healthy:
                        break
                except (
                    OSError,
                    UnicodeError,
                    ValueError,
                    urllib.error.URLError,
                ):
                    pass
                time.sleep(0.25)
            if not healthy:
                raise UpdateError("V1.0.2 回环健康检查没有在 30 秒内通过")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    log.write("V1.0.2 已使用 data 副本通过数据库和回环健康检查")


class RepairUpdater:
    def __init__(
        self,
        bundle: Bundle,
        install_root: Path,
        controller: SystemController,
        *,
        log: Optional[EventLog] = None,
        validate_target: Callable[
            [Path, Path, Path, EventLog], None
        ] = _validate_target_with_clone,
        fault_hook: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.bundle = bundle
        self.install_root = _validate_install_root(install_root)
        self.program_root = self.install_root / "_程序文件"
        self.data_root = self.program_root / "data"
        self.controller = controller
        self.log = log or EventLog()
        self.validate_target = validate_target
        self.fault_hook = fault_hook
        self.state_path = self.program_root / STATE_NAME
        self.lock_path = self.program_root / LOCK_NAME
        self.legacy_lock_path = self.program_root / LEGACY_LOCK_NAME
        self.rollback_root = self.program_root / ROLLBACK_NAME
        for path, description in (
            (self.state_path, "修复状态文件"),
            (self.lock_path, "修复锁文件"),
            (self.legacy_lock_path, "旧升级锁文件"),
        ):
            if _is_reparse_or_link(path):
                raise UpdateError(f"{description}不能是链接或重解析点：{path}")
            if path.exists():
                _assert_plain_path(path, description, directory=False)
        self.legacy_lock_preexisted = self.legacy_lock_path.exists()
        self.legacy_lock_evidence: Optional[bytes] = None
        if _is_reparse_or_link(self.rollback_root):
            raise UpdateError("修复事务回滚目录不能是链接或重解析点")
        if self.rollback_root.exists():
            _assert_plain_path(
                self.rollback_root, "修复事务回滚目录", directory=True
            )

    def _set_stage(self, state: dict[str, Any], stage: str) -> None:
        if stage not in NEW_STAGES:
            raise UpdateError(f"内部错误：未知修复阶段 {stage}")
        state["stage"] = stage
        _write_state(self.state_path, state)
        self.log.write(f"修复事务阶段：{stage}")
        if self.fault_hook is not None:
            self.fault_hook(stage)

    def _new_state(self, task_state: TaskState) -> dict[str, Any]:
        return {
            "schema": STATE_SCHEMA,
            "release": self.bundle.release,
            "transaction_id": uuid.uuid4().hex,
            "stage": "preflight",
            "install_root": str(self.install_root),
            "baseline_zip_sha256": self.bundle.baseline.zip_sha256,
            "target_zip_sha256": self.bundle.target.zip_sha256,
            "runtime_repaired": False,
            "task_state": task_state.as_json(),
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    def _snapshot_root(self, state: Mapping[str, Any]) -> Path:
        txid = str(state["transaction_id"])
        if not TXID_RE.fullmatch(txid):
            raise UpdateError("修复事务 ID 非法")
        return self.rollback_root / txid

    def _snapshot_data(self, state: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._snapshot_root(state)
        if snapshot.exists():
            _remove_path_safely(snapshot)
        snapshot.mkdir(parents=True)
        original_program = _snapshot_current_program(
            self.install_root, snapshot
        )
        data_snapshot = snapshot / "data-snapshot"
        data_records = _copy_tree_verified(self.data_root, data_snapshot)
        if not any(record["path"] == "reservation.db" for record in data_records):
            raise UpdateError("data 安全副本缺少 reservation.db")
        _atomic_write(
            snapshot / "data-manifest.json",
            _json_bytes({"files": data_records}),
        )
        legacy_stage = _inspect_legacy_state(
            self.program_root, snapshot / "legacy-evidence"
        )
        if self.legacy_lock_preexisted:
            if self.legacy_lock_evidence is None:
                raise UpdateError("旧升级锁证据没有从已持有的锁句柄中捕获")
            (snapshot / "legacy-evidence").mkdir(parents=True, exist_ok=True)
            _atomic_write(
                snapshot / "legacy-evidence" / LEGACY_LOCK_NAME,
                self.legacy_lock_evidence,
            )
        _atomic_write(
            snapshot / "snapshot-metadata.json",
            _json_bytes(
                {
                    "legacy_stage": legacy_stage,
                    "original_program": original_program,
                }
            ),
        )
        self._set_stage(state, "snapshot_ready")
        return {
            "root": snapshot,
            "original_program": original_program,
            "data_records": data_records,
        }

    def _load_snapshot(self, state: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = self._snapshot_root(state)
        _assert_plain_path(snapshot, "修复事务快照", directory=True)
        try:
            data_manifest = json.loads(
                (snapshot / "data-manifest.json").read_text(encoding="utf-8")
            )
            program_manifest = json.loads(
                (snapshot / "original-program-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, UnicodeError, ValueError) as error:
            raise UpdateError("修复事务快照清单缺失或损坏") from error
        _assert_record_sets_equal(
            data_manifest["files"],
            _records_for_tree(snapshot / "data-snapshot"),
            "data 事务快照",
        )
        _assert_record_sets_equal(
            program_manifest["files"],
            _records_for_tree(snapshot / "original-program"),
            "原程序事务快照",
        )
        return {
            "root": snapshot,
            "original_program": program_manifest,
            "data_records": data_manifest["files"],
        }

    def _assert_real_data_unchanged(
        self, expected: Sequence[Mapping[str, Any]]
    ) -> None:
        actual = _records_for_tree(self.data_root)
        _assert_record_sets_equal(expected, actual, "真实客户 data")

    def _prepare_runtime_and_baseline(
        self, state: dict[str, Any], snapshot: Mapping[str, Any]
    ) -> None:
        txid = str(state["transaction_id"])
        repaired = _ensure_installed_runtime(
            self.bundle, self.install_root, snapshot["root"], txid
        )
        if repaired:
            state["runtime_repaired"] = True
            _write_state(self.state_path, state)
            self.log.write("已用冻结 runtime 修复安装目录中的运行环境", "WARN")
        self._set_stage(state, "runtime_ready")
        self._set_stage(state, "baseline_applying")
        _write_managed_payload(
            self.install_root,
            self.bundle.baseline,
            txid,
            include_version=True,
        )
        _assert_installed_payload(
            self.install_root, self.bundle.baseline, include_version=True
        )
        self._assert_real_data_unchanged(snapshot["data_records"])
        self._set_stage(state, "baseline_verified")
        self.log.write(
            "受管程序已严格恢复并校验为冻结 V1.0.1；真实 data 未改变"
        )
        _archive_legacy_residue(
            self.program_root,
            self.log,
            include_locked_legacy_lock=self.legacy_lock_preexisted,
            locked_legacy_lock_bytes=self.legacy_lock_evidence,
        )
        _cleanup_repair_staging(self.program_root, self.log)

    def _apply_and_validate_target(
        self, state: dict[str, Any], snapshot: Mapping[str, Any]
    ) -> Path:
        txid = str(state["transaction_id"])
        self._set_stage(state, "target_applying")
        _write_managed_payload(
            self.install_root,
            self.bundle.target,
            txid,
            include_version=False,
        )
        _assert_installed_payload(
            self.install_root, self.bundle.target, include_version=False
        )
        if _read_current_version(self.program_root) != BASELINE_VERSION:
            raise UpdateError("V1.0.2 健康检查前版本文件不再是 1.0.1")
        self._assert_real_data_unchanged(snapshot["data_records"])
        self._set_stage(state, "target_verified")

        validation_data, _records = _make_validation_copy(
            snapshot["root"] / "data-snapshot", snapshot["root"]
        )
        _database_integrity_check(validation_data / "reservation.db")
        backup = _make_permanent_backup(
            validation_data, self.program_root / "backups"
        )
        self.log.write(f"已生成独立保留的修复前数据库备份：{backup}")
        self.validate_target(
            self.install_root, validation_data, snapshot["root"], self.log
        )
        self._assert_real_data_unchanged(snapshot["data_records"])
        self._set_stage(state, "healthcheck_passed")
        return backup

    def _commit_target(self, state: dict[str, Any], snapshot: Mapping[str, Any]) -> None:
        version_path = self.program_root / "版本.txt"
        _atomic_write(
            version_path,
            self.bundle.target.files["_程序文件/版本.txt"],
        )
        if self.fault_hook is not None:
            self.fault_hook("version_file_replaced")
        self._set_stage(state, "target_committed")
        _assert_installed_payload(
            self.install_root, self.bundle.target, include_version=True
        )
        self._assert_real_data_unchanged(snapshot["data_records"])
        self.log.write("V1.0.2 版本文件已最后提交；真实客户 data 全树哈希未改变")

    def _retire_old_v102_bats(self, transaction_id: str) -> None:
        candidates = [
            path
            for path in self.install_root.glob("升级到V1.0.2*.bat")
            if (
                path.is_file()
                and not _is_reparse_or_link(path)
                and _sha256_file(path) == BROKEN_V102_PACKAGE_SHA256
            )
        ]
        if not candidates:
            return
        destination = (
            self.program_root
            / "backups"
            / f"旧V1.0.2升级文件_{transaction_id}"
        )
        destination.mkdir(parents=True, exist_ok=True)
        _assert_plain_path(destination, "旧 V1.0.2 BAT 归档目录", directory=True)
        for path in candidates:
            target = destination / path.name
            if target.exists():
                raise UpdateError(f"旧 V1.0.2 BAT 归档目标已经存在：{target}")
            os.replace(str(path), str(target))
            self.log.write(f"旧故障升级 BAT 已停用并保留：{target}", "WARN")

    def _finish_success(self, state: dict[str, Any]) -> None:
        self._retire_old_v102_bats(str(state["transaction_id"]))
        self.controller.restore_task_state(
            self.install_root, TaskState.from_json(state["task_state"])
        )
        self._set_stage(state, "complete")
        if self.rollback_root.is_dir() and not _is_reparse_or_link(
            self.rollback_root
        ):
            for child in list(self.rollback_root.iterdir()):
                if (
                    TXID_RE.fullmatch(child.name)
                    and child.is_dir()
                    and not _is_reparse_or_link(child)
                ):
                    _remove_path_safely(child)
                else:
                    self.log.write(
                        f"发现非本工具事务目录，已保留未动：{child}",
                        "WARN",
                    )
        with contextlib.suppress(OSError):
            if self.rollback_root.is_dir() and not any(self.rollback_root.iterdir()):
                self.rollback_root.rmdir()
        _cleanup_repair_staging(self.program_root, self.log)
        with contextlib.suppress(FileNotFoundError):
            self.state_path.unlink()
        self.log.write(
            "V1.0.2-r1 修复更新完成；计划任务启用状态已恢复，系统保持停止等待普通用户启动"
        )

    def _recover_after_failure(
        self,
        state: Optional[dict[str, Any]],
        snapshot: Optional[Mapping[str, Any]],
        error: BaseException,
    ) -> bool:
        if state is None:
            return True
        stage = str(state.get("stage", ""))
        self.log.write(f"修复更新失败，阶段={stage}：{error}", "ERROR")
        if stage in {"target_committed", "complete"}:
            try:
                _assert_installed_payload(
                    self.install_root,
                    self.bundle.target,
                    include_version=True,
                )
                _assert_installed_runtime(self.bundle, self.install_root)
            except UpdateError as verify_error:
                self.log.write(
                    f"目标已提交但程序/runtime 未能确认完整：{verify_error}",
                    "ERROR",
                )
                return False
            self.log.write(
                "目标版本已经提交且完整，按照提交边界不会自动降级或覆盖 data",
                "WARN",
            )
            return True
        if snapshot is None:
            with contextlib.suppress(UpdateError):
                snapshot = self._load_snapshot(state)
        if snapshot is None:
            return stage in {"preflight", "stopped"}
        try:
            if stage in {
                "baseline_verified",
                "target_applying",
                "target_verified",
                "healthcheck_passed",
                "baseline_rollback_complete",
            }:
                _write_managed_payload(
                    self.install_root,
                    self.bundle.baseline,
                    str(state["transaction_id"]),
                    include_version=True,
                )
                _assert_installed_payload(
                    self.install_root,
                    self.bundle.baseline,
                    include_version=True,
                )
                self._assert_real_data_unchanged(snapshot["data_records"])
                self._set_stage(state, "baseline_rollback_complete")
                self.log.write(
                    "目标更新失败，受管程序已安全收敛到冻结 V1.0.1；真实 data 未改变",
                    "WARN",
                )
            else:
                _restore_original_program(
                    self.install_root,
                    snapshot["root"],
                    snapshot["original_program"],
                )
                self._assert_real_data_unchanged(snapshot["data_records"])
                self.log.write(
                    "V1.0.1 基线尚未验证，已恢复进入修复前的原程序；真实 data 未改变",
                    "WARN",
                )
            _assert_runtime_recovery_safe(
                self.bundle, self.install_root, snapshot["root"]
            )
            return True
        except BaseException as rollback_error:
            self.log.write(
                f"程序恢复没有完整完成，请停止操作并保留现场：{rollback_error}",
                "ERROR",
            )
            return False

    def run(self) -> None:
        current_version: Optional[str]
        version_error: Optional[UpdateError] = None
        try:
            current_version = _read_current_version(self.program_root)
        except UpdateError as error:
            if not (
                self.state_path.exists()
                or (self.program_root / LEGACY_STATE_NAME).exists()
            ):
                raise
            current_version = None
            version_error = error
        if (
            current_version is not None
            and _version_tuple(current_version) > _version_tuple(TARGET_VERSION)
        ):
            raise UpdateError(
                f"当前版本 {current_version} 高于修复目标 {TARGET_VERSION}，拒绝降级"
            )
        launcher_log = Path(tempfile.gettempdir()) / "meetingroom_v102_repair_launcher.log"
        self.log.add_path(launcher_log)
        self.log.write(
            "V1.0.2-r1 修复入口启动，"
            f"安装目录={self.install_root}，当前版本={current_version or '损坏/未知'}"
        )
        if version_error is not None:
            self.log.write(
                f"检测到事务残留且版本文件异常，将先按事务提交边界判断：{version_error}",
                "WARN",
            )

        state: Optional[dict[str, Any]] = None
        snapshot: Optional[dict[str, Any]] = None
        task_state: Optional[TaskState] = None
        own_lock_acquired = False
        legacy_lock_acquired = False
        stop_attempted = False
        self.legacy_lock_evidence = None
        try:
            with contextlib.ExitStack() as locks:
                locks.enter_context(ExclusiveLock(self.lock_path))
                own_lock_acquired = True
                try:
                    legacy_lock = locks.enter_context(
                        ExclusiveLock(self.legacy_lock_path)
                    )
                    legacy_lock_acquired = True
                    if self.legacy_lock_preexisted:
                        if legacy_lock.original_bytes is None:
                            raise UpdateError(
                                "旧升级锁证据没有从已持有的锁句柄中捕获"
                            )
                        self.legacy_lock_evidence = legacy_lock.original_bytes
                except UpdateBusy as error:
                    raise UpdateBusy(
                        "旧 V1.0.2 升级器仍在运行，拒绝并发修复"
                    ) from error

                legacy_info = _read_legacy_state(self.program_root)
                logs = self.program_root / "logs"
                logs.mkdir(parents=True, exist_ok=True)
                if _is_reparse_or_link(logs):
                    raise UpdateError("logs 不能是链接或重解析点")
                self.log.add_path(
                    logs
                    / f"repair-update-{dt.datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}.log"
                )
                _assert_managed_install_paths_plain(self.install_root)

                if self.state_path.exists():
                    previous_state = _load_state(
                        self.state_path, self.install_root, self.bundle
                    )
                    self.log.write(
                        f"发现未完成的修复事务，阶段={previous_state['stage']}，正在安全收敛",
                        "WARN",
                    )
                    task_state = TaskState.from_json(previous_state["task_state"])
                    if previous_state["stage"] in {"target_committed", "complete"}:
                        _assert_installed_payload(
                            self.install_root,
                            self.bundle.target,
                            include_version=True,
                        )
                        _assert_installed_runtime(
                            self.bundle, self.install_root
                        )
                        if legacy_info is not None:
                            _archive_legacy_residue(
                                self.program_root,
                                self.log,
                                include_locked_legacy_lock=self.legacy_lock_preexisted,
                                locked_legacy_lock_bytes=self.legacy_lock_evidence,
                            )
                        self._finish_success(previous_state)
                        return
                    try:
                        disk_version = _read_current_version(self.program_root)
                    except UpdateError as error:
                        if previous_state["stage"] in {
                            "baseline_verified",
                            "target_applying",
                            "target_verified",
                            "healthcheck_passed",
                            "baseline_rollback_complete",
                        }:
                            raise UpdateError(
                                "未提交状态的版本文件异常，无法排除已经跨过提交边界；"
                                "已停止且不会自动降级"
                            ) from error
                        disk_version = None
                    if disk_version == TARGET_VERSION:
                        if previous_state["stage"] != "healthcheck_passed":
                            raise UpdateError(
                                "状态显示尚未提交，但磁盘版本已经是 V1.0.2；"
                                "无法安全判断提交边界，已停止且不会自动降级"
                            )
                        _assert_installed_payload(
                            self.install_root,
                            self.bundle.target,
                            include_version=True,
                        )
                        _assert_installed_runtime(
                            self.bundle, self.install_root
                        )
                        self.log.write(
                            "识别到版本文件已提交但状态尚未来得及更新的中断窗口；"
                            "将按已提交 V1.0.2 收尾",
                            "WARN",
                        )
                        self._set_stage(previous_state, "target_committed")
                        if legacy_info is not None:
                            _archive_legacy_residue(
                                self.program_root,
                                self.log,
                                include_locked_legacy_lock=self.legacy_lock_preexisted,
                                locked_legacy_lock_bytes=self.legacy_lock_evidence,
                            )
                        self._finish_success(previous_state)
                        return
                    if (
                        previous_state["stage"]
                        in {
                            "baseline_verified",
                            "target_applying",
                            "target_verified",
                            "healthcheck_passed",
                            "baseline_rollback_complete",
                        }
                        and disk_version != BASELINE_VERSION
                    ):
                        raise UpdateError(
                            "未提交事务的磁盘版本不再是 V1.0.1，"
                            "无法安全判断外部修改，已停止且不会猜测恢复"
                        )

                    _assert_sufficient_space(
                        self.bundle, self.install_root, self.data_root
                    )
                    previous_txid = str(previous_state["transaction_id"])
                    state = self._new_state(task_state)
                    _write_state(self.state_path, state)
                    if previous_state["stage"] in {"preflight", "stopped"}:
                        previous_snapshot = self._snapshot_root(previous_state)
                        if previous_snapshot.exists():
                            _remove_path_safely(previous_snapshot)
                    self.log.write(
                        f"旧修复事务 {previous_txid} 的证据继续保留；"
                        "本次以停止后的最新 data 建立新事务",
                        "WARN",
                    )
                else:
                    if (
                        legacy_info is None
                        and current_version == TARGET_VERSION
                    ):
                        try:
                            _assert_installed_payload(
                                self.install_root,
                                self.bundle.target,
                                include_version=True,
                            )
                            _assert_installed_runtime(
                                self.bundle, self.install_root
                            )
                        except UpdateError:
                            self.log.write(
                                "磁盘版本是 V1.0.2，但程序或 runtime 未严格匹配；"
                                "将按完整修复事务重新规范化",
                                "WARN",
                            )
                        else:
                            transaction_id = uuid.uuid4().hex
                            self._retire_old_v102_bats(transaction_id)
                            _cleanup_repair_staging(
                                self.program_root, self.log
                            )
                            self.log.write(
                                "当前受管程序和 runtime 已严格匹配 V1.0.2-r1；"
                                "本次无需停机、无需回写 data；"
                                "已同时收敛本工具可确认的旧故障入口和临时残留"
                            )
                            return

                    legacy_committed = bool(
                        legacy_info is not None
                        and (
                            legacy_info.explicitly_committed
                            or (
                                legacy_info.package_version == TARGET_VERSION
                                and
                                legacy_info.stage == "healthcheck_passed"
                                and current_version == TARGET_VERSION
                            )
                        )
                    )
                    if legacy_committed:
                        if current_version != TARGET_VERSION:
                            raise UpdateError(
                                "旧升级状态已经越过提交边界，但磁盘版本不是 V1.0.2；"
                                "拒绝自动降级或猜测修复"
                            )
                        _assert_installed_payload(
                            self.install_root,
                            self.bundle.target,
                            include_version=True,
                        )
                        _assert_installed_runtime(
                            self.bundle, self.install_root
                        )
                        if (
                            legacy_info is not None
                            and legacy_info.task_state is not None
                        ):
                            task_state = legacy_info.task_state
                        else:
                            task_state = self.controller.inspect_state(
                                self.install_root
                            )
                        committed_state = self._new_state(task_state)
                        committed_state["stage"] = "target_committed"
                        _write_state(self.state_path, committed_state)
                        _archive_legacy_residue(
                            self.program_root,
                            self.log,
                            include_locked_legacy_lock=self.legacy_lock_preexisted,
                            locked_legacy_lock_bytes=self.legacy_lock_evidence,
                        )
                        self._finish_success(committed_state)
                        self.log.write(
                            "旧 V1.0.2 事务已确认提交；本次只完成残留收尾，"
                            "没有回写程序或 data"
                        )
                        return

                    _assert_sufficient_space(
                        self.bundle, self.install_root, self.data_root
                    )
                    observed_task_state = self.controller.inspect_state(
                        self.install_root
                    )
                    if legacy_info is not None and legacy_info.task_state is not None:
                        task_state = legacy_info.task_state
                        self.log.write(
                            "将使用旧升级状态中停机前记录的计划任务配置，"
                            "避免把失败后已禁用误当成原始状态",
                            "WARN",
                        )
                    else:
                        task_state = observed_task_state
                    state = self._new_state(task_state)
                    _write_state(self.state_path, state)

                stop_attempted = True
                self.controller.stop_and_disable(self.install_root)
                if _port_in_use(SERVICE_PORT):
                    raise UpdateError(
                        f"端口 {SERVICE_PORT} 仍被其他程序占用；"
                        "为避免覆盖错误安装，更新已停止"
                    )
                self._set_stage(state, "stopped")
                self.log.write(
                    "属于本安装的服务已停止，计划任务已在事务期间禁用"
                )
                snapshot = self._snapshot_data(state)
                self._prepare_runtime_and_baseline(state, snapshot)
                self._apply_and_validate_target(state, snapshot)
                self._commit_target(state, snapshot)
                self._finish_success(state)
        except BaseException as error:
            recovery_safe = self._recover_after_failure(
                state, snapshot, error
            )
            if (
                task_state is not None
                and stop_attempted
                and recovery_safe
            ):
                try:
                    self.controller.restore_task_state(
                        self.install_root, task_state
                    )
                except BaseException as task_error:
                    self.log.write(
                        f"计划任务启用状态恢复失败：{task_error}", "ERROR"
                    )
            elif task_state is not None and stop_attempted:
                self.log.write(
                    "程序/runtime 未能确认恢复完整；为避免下次开机运行混合版本，"
                    "计划任务保持禁用，请保留现场并联系维护人员",
                    "ERROR",
                )
            raise
        finally:
            if legacy_lock_acquired:
                with contextlib.suppress(OSError):
                    if self.legacy_lock_path.exists():
                        self.legacy_lock_path.unlink()
            if own_lock_acquired:
                with contextlib.suppress(OSError):
                    if self.lock_path.exists():
                        self.lock_path.unlink()


def _is_admin() -> bool:
    if os.name != "nt":
        return os.geteuid() == 0 if hasattr(os, "geteuid") else True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _encode_elevation_context(install_root: Path) -> str:
    payload = _json_bytes(
        {
            "schema": 1,
            "install_root": str(install_root),
            "nonce": uuid.uuid4().hex,
        }
    )
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_elevation_context(value: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,8192}", value or ""):
        raise UpdateError("管理员启动上下文格式非法")
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(value + padding)
        context = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeError, ValueError) as error:
        raise UpdateError("管理员启动上下文无法解码") from error
    if (
        not isinstance(context, dict)
        or set(context) != {"schema", "install_root", "nonce"}
        or context["schema"] != 1
        or not re.fullmatch(r"[0-9a-f]{32}", str(context["nonce"]))
    ):
        raise UpdateError("管理员启动上下文字段非法")
    return _validate_install_root(Path(str(context["install_root"])))


def _run_elevated(tool_root: Path, context_value: str) -> int:
    if os.name != "nt":
        raise UpdateError("管理员提权入口只能在 Windows 上使用")

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
    script = tool_root / "update.py"
    parameters = subprocess.list2cmdline(
        [str(script), "--elevated-context", context_value]
    )
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
            raise UpdateCancelled("用户取消 Windows 管理员授权")
        raise UpdateError(f"无法启动管理员修复进程，Win32={error}")
    if not info.hProcess:
        raise UpdateError("管理员修复进程没有返回有效进程句柄")
    try:
        wait_result = kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        if wait_result == 0xFFFFFFFF:
            raise UpdateError(
                f"等待管理员修复进程失败，Win32={ctypes.get_last_error()}"
            )
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(
            info.hProcess, ctypes.byref(code)
        ):
            raise UpdateError("无法取得管理员修复进程退出码")
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--elevated-context")
    parser.add_argument("--install-root", type=Path)
    parser.add_argument("--noninteractive", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    tool_root = Path(__file__).resolve().parent
    log = EventLog()
    try:
        log.add_path(
            Path(tempfile.gettempdir())
            / "meetingroom_v102_repair_launcher.log"
        )
        print()
        print("会议室预约系统 V1.0.2 修复更新")
        print("正在校验修复工具，请稍候……")
        bundle = Bundle.load(tool_root)

        if arguments.elevated_context:
            install_root = _decode_elevation_context(
                arguments.elevated_context
            )
            if os.name == "nt" and not _is_admin():
                raise UpdateError("管理员修复进程没有取得管理员权限")
        elif arguments.install_root is not None:
            install_root = _validate_install_root(arguments.install_root)
        else:
            install_root = _select_install_root(
                tool_root, noninteractive=arguments.noninteractive
            )
        _assert_tool_location(tool_root, install_root)

        if os.name == "nt" and not _is_admin():
            print()
            print("即将请求 Windows 管理员授权。")
            context_value = _encode_elevation_context(install_root)
            return _run_elevated(tool_root, context_value)
        if os.name != "nt" and os.environ.get("MEETING_ROOM_UPDATE_TEST_MODE") != "1":
            raise UpdateError("正式修复更新只能在 Windows 10/11 上运行")

        controller: SystemController
        if os.name == "nt":
            controller = WindowsSystemController()
        else:
            controller = PassiveSystemController()
        updater = RepairUpdater(
            bundle,
            install_root,
            controller,
            log=log,
        )
        updater.run()
        print()
        print("修复更新成功：受管程序已经是 V1.0.2。")
        print("真实 data、账号、会议室和预约记录未被覆盖。")
        print("请回到系统文件夹双击“① 启动系统.bat”。")
        return 0
    except UpdateCancelled as error:
        log.write(str(error), "WARN")
        print(f"\n{error}")
        return 3
    except UpdateBusy as error:
        log.write(str(error), "WARN")
        print(f"\n{error}")
        return 4
    except BaseException as error:
        log.write(f"修复更新失败：{error}", "ERROR")
        print()
        print(f"修复更新失败：{error}")
        print("真实 data 不会被猜测性覆盖。请保留旧系统和日志并联系维护人员。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""制作 V1.0.2-r1 全量覆盖修复更新 ZIP。

本工具只在维护者电脑上运行。它只接受仓库中已经冻结的 V1.0.1/V1.0.2
正式升级 BAT，从其中提取经过原生成器验证的完整累计 Payload；修复工具
runtime 则只取自冻结 V1.0.1 Windows 部署目录。最终 ZIP 使用固定条目顺序、
时间戳和权限生成，并在交付前通过随包 ``覆盖更新.py`` 的 ``Bundle.load``
契约做一次真正的反向加载。

旧正式 BAT、旧 V1.0.2 发布清单和历史候选目录均为只读输入。本工具默认只
新建 ``发布暂存/V1.0.2-r1``，目标已存在时拒绝覆盖。
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

import 制作升级包 as package_builder


RELEASE = "V1.0.2-r1"
BASELINE_VERSION = "1.0.1"
TARGET_VERSION = "1.0.2"
ARTIFACT_NAME = "会议室预约系统-V1.0.2-修复更新-r1.zip"
EXTERNAL_MANIFEST_NAME = "V1.0.2-r1-发布清单.json"

TOOL_DIR = Path(__file__).resolve().parent
DEVELOPMENT_DIR = TOOL_DIR.parent
REPOSITORY_ROOT = DEVELOPMENT_DIR.parent
DEFAULT_RELEASE_ROOT = DEVELOPMENT_DIR / "发布暂存"

FROZEN_PACKAGE_DIR = TOOL_DIR / "输出-待实机验收"
FROZEN_V101_PACKAGE = FROZEN_PACKAGE_DIR / "升级到V1.0.1.bat"
FROZEN_V102_PACKAGE = FROZEN_PACKAGE_DIR / "升级到V1.0.2.bat"
FROZEN_V102_MANIFEST = FROZEN_PACKAGE_DIR / "V1.0.2-发布清单.json"
FROZEN_RUNTIME_ROOT = (
    DEVELOPMENT_DIR
    / "Windows部署目录-V1.0.1-待实机验收"
    / "_程序文件"
    / "runtime"
)

FROZEN_V101_PACKAGE_SHA256 = (
    "cd0d52b9ffb5d2864e7ad98d8969b86376d8577391399c30295d0722d34848cd"
)
FROZEN_V102_PACKAGE_SHA256 = (
    "2e7e78a61de9a403f3facd37b47c1580c35bce38b91465f4919326fa72d77730"
)
FROZEN_V102_MANIFEST_SHA256 = (
    "4296359c2b438869bd37489ea04d58aea6297f2249d13176743b85e214ed136e"
)
FROZEN_RUNTIME_TREE_SHA256 = (
    "b778df06bfc98d699c2aa4c68d4f146f8c6c3d55a0ce1cc7b6811251ed5aad14"
)

UPDATER_SOURCE = TOOL_DIR / "覆盖更新.py"
LAUNCHER_SOURCE = TOOL_DIR / "覆盖更新入口.bat"
GUIDE_SOURCE = TOOL_DIR / "覆盖更新使用说明.txt"
DELIVERED_LAUNCHER = "修复并更新到V1.0.2.bat"
DELIVERED_GUIDE = "修复更新使用说明.txt"
DELIVERED_TOOL_ROOT = "_V1.0.2更新工具"
DELIVERED_UPDATER = f"{DELIVERED_TOOL_ROOT}/update.py"
DELIVERED_BUNDLE_MANIFEST = f"{DELIVERED_TOOL_ROOT}/manifest.json"
BASELINE_ZIP_NAME = "baseline-v1.0.1.zip"
TARGET_ZIP_NAME = "target-v1.0.2.zip"
PAYLOAD_MARKER = package_builder.PAYLOAD_MARKER
BASE64_LINE_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}")


class RepairPackageBuildError(RuntimeError):
    """冻结输入、更新模板或输出不符合修复发布契约。"""


@dataclass(frozen=True)
class FrozenPayload:
    version: str
    source_package: Path
    source_package_sha256: str
    zip_bytes: bytes
    zip_sha256: str
    files: Mapping[str, bytes]
    records: tuple[Mapping[str, Any], ...]
    tree_sha256: str


@dataclass(frozen=True)
class BuildResult:
    release_dir: Path
    artifact_path: Path
    manifest_path: Path
    artifact_sha256: str
    artifact_size: int
    baseline_zip_sha256: str
    target_zip_sha256: str
    runtime_tree_sha256: str


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _tree_digest(records: Iterable[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda item: str(item["path"])):
        digest.update(str(record["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _record(path: str, content: bytes) -> dict[str, Any]:
    return {
        "path": path,
        "size": len(content),
        "sha256": _sha256_bytes(content),
    }


def _assert_outer_relative_path(relative: str) -> tuple[str, ...]:
    """校验修复 ZIP 自身路径；它有意允许专用工具目录下的 runtime。"""

    if (
        not relative
        or relative.startswith(("/", "\\"))
        or "\\" in relative
        or ":" in relative
        or "\x00" in relative
    ):
        raise RepairPackageBuildError(f"修复更新成品路径非法：{relative!r}")
    parts = tuple(relative.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise RepairPackageBuildError(
            f"修复更新成品路径包含空段、. 或 ..：{relative}"
        )
    allowed_exact = {
        DELIVERED_LAUNCHER,
        DELIVERED_GUIDE,
        DELIVERED_UPDATER,
        DELIVERED_BUNDLE_MANIFEST,
        f"{DELIVERED_TOOL_ROOT}/{BASELINE_ZIP_NAME}",
        f"{DELIVERED_TOOL_ROOT}/{TARGET_ZIP_NAME}",
    }
    runtime_prefix = f"{DELIVERED_TOOL_ROOT}/runtime/"
    if relative not in allowed_exact and not relative.startswith(runtime_prefix):
        raise RepairPackageBuildError(
            f"修复更新成品出现白名单外路径：{relative}"
        )
    for part in parts:
        invalid = next(
            (character for character in '<>"|?*' if character in part),
            None,
        )
        if invalid is not None or any(ord(character) < 32 for character in part):
            raise RepairPackageBuildError(
                f"修复更新成品路径包含 Windows 非法字符：{relative}"
            )
        if part.endswith((" ", ".")):
            raise RepairPackageBuildError(
                f"修复更新成品路径不能以空格或句点结尾：{relative}"
            )
        if len(part.encode("utf-16-le")) // 2 > 255:
            raise RepairPackageBuildError(
                f"修复更新成品单个名称超过 Windows 限制：{relative}"
            )
        if (
            part.split(".", 1)[0].casefold()
            in package_builder.RESERVED_WINDOWS_BASENAMES
        ):
            raise RepairPackageBuildError(
                f"修复更新成品使用 Windows 保留名称：{relative}"
            )
    return parts


def _build_outer_zip(files: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    seen_windows_paths: Dict[str, str] = {}
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for relative in sorted(files):
            _assert_outer_relative_path(relative)
            package_builder._register_windows_path(
                seen_windows_paths, relative
            )
            info = zipfile.ZipInfo(
                relative, date_time=package_builder.FIXED_ZIP_TIMESTAMP
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.internal_attr = 0
            info.extra = b""
            archive.writestr(
                info,
                files[relative],
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    return output.getvalue()


def _assert_plain_file(path: Path, description: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise RepairPackageBuildError(f"{description}必须是普通文件：{path}")


def _assert_plain_tree(root: Path, description: str) -> None:
    if root.is_symlink() or not root.is_dir():
        raise RepairPackageBuildError(f"{description}必须是普通目录：{root}")
    for current, directories, files in os.walk(str(root), followlinks=False):
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            if path.is_symlink():
                raise RepairPackageBuildError(
                    f"{description}禁止符号链接：{path}"
                )
        for name in files:
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                raise RepairPackageBuildError(
                    f"{description}出现特殊文件：{path}"
                )


def _read_frozen_file(
    path: Path, expected_sha256: str, description: str
) -> bytes:
    _assert_plain_file(path, description)
    content = path.read_bytes()
    actual = _sha256_bytes(content)
    if actual != expected_sha256:
        raise RepairPackageBuildError(
            f"{description} SHA-256 不一致：期望 {expected_sha256}，实际 {actual}"
        )
    return content


def _payload_records(files: Mapping[str, bytes]) -> tuple[Mapping[str, Any], ...]:
    return tuple(_record(path, files[path]) for path in sorted(files))


def _read_payload_zip(
    zip_bytes: bytes, expected_version: str, description: str
) -> Dict[str, bytes]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes), mode="r")
    except (OSError, zipfile.BadZipFile) as error:
        raise RepairPackageBuildError(
            f"{description} Payload 不是有效 ZIP"
        ) from error

    files: Dict[str, bytes] = {}
    seen_windows_paths: Dict[str, str] = {}
    with archive:
        if archive.comment:
            raise RepairPackageBuildError(
                f"{description} Payload ZIP 不允许注释"
            )
        for info in archive.infolist():
            relative = info.filename
            package_builder._assert_safe_relative_path(relative)
            if info.is_dir() or relative.endswith("/"):
                raise RepairPackageBuildError(
                    f"{description} Payload ZIP 不允许目录条目：{relative}"
                )
            if not package_builder._is_allowed_file(relative):
                raise RepairPackageBuildError(
                    f"{description} Payload 出现白名单外文件：{relative}"
                )
            if relative in files:
                raise RepairPackageBuildError(
                    f"{description} Payload 路径重复：{relative}"
                )
            package_builder._register_windows_path(
                seen_windows_paths, relative
            )
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_IFMT(unix_mode) == stat.S_IFLNK:
                raise RepairPackageBuildError(
                    f"{description} Payload 禁止符号链接：{relative}"
                )
            if info.flag_bits & 0x1:
                raise RepairPackageBuildError(
                    f"{description} Payload 禁止加密文件：{relative}"
                )
            try:
                files[relative] = archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                raise RepairPackageBuildError(
                    f"{description} Payload 无法读取：{relative}"
                ) from error
        bad = archive.testzip()
        if bad is not None:
            raise RepairPackageBuildError(
                f"{description} Payload CRC 校验失败：{bad}"
            )

    missing = sorted(package_builder.REQUIRED_FILES - set(files))
    if missing:
        raise RepairPackageBuildError(
            f"{description} Payload 缺少固定文件：{missing}"
        )
    for prefix in package_builder.MANAGED_TREE_PREFIXES:
        if not any(path.startswith(prefix) for path in files):
            raise RepairPackageBuildError(
                f"{description} Payload 目录为空：{prefix.rstrip('/')}"
            )
    version_bytes = files["_程序文件/版本.txt"]
    if version_bytes.startswith(b"\xef\xbb\xbf"):
        raise RepairPackageBuildError(
            f"{description} Payload 版本.txt 不允许 BOM"
        )
    try:
        version_text = version_bytes.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise RepairPackageBuildError(
            f"{description} Payload 版本.txt 不是 UTF-8"
        ) from error
    if version_text != expected_version:
        raise RepairPackageBuildError(
            f"{description} Payload 版本错误：期望 {expected_version}，"
            f"实际 {version_text}"
        )

    # 复用旧生成器的反向 ZIP 校验，确保这里和正式单 BAT 的路径安全契约一致。
    package_builder._verify_zip_contents(zip_bytes, files)
    return files


def _extract_frozen_payload(
    package_path: Path,
    expected_package_sha256: str,
    expected_version: str,
) -> FrozenPayload:
    description = f"冻结 V{expected_version} 正式 BAT"
    package_bytes = _read_frozen_file(
        package_path, expected_package_sha256, description
    )
    if package_bytes.startswith(b"\xef\xbb\xbf"):
        raise RepairPackageBuildError(f"{description} 不允许 UTF-8 BOM")
    try:
        text = package_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RepairPackageBuildError(f"{description} 不是 UTF-8") from error

    matches = list(
        re.finditer(r"(?m)^%s\r?$" % re.escape(PAYLOAD_MARKER), text)
    )
    if len(matches) != 1:
        raise RepairPackageBuildError(
            f"{description} Payload 标记必须恰好出现一次，实际 {len(matches)}"
        )
    payload_start = matches[0].end()
    if payload_start >= len(text) or text[payload_start] != "\n":
        raise RepairPackageBuildError(
            f"{description} Payload 标记后缺少换行"
        )
    encoded_region = text[payload_start + 1 :]
    if not encoded_region.endswith(("\n", "\r")):
        raise RepairPackageBuildError(
            f"{description} Base64 Payload 末尾缺少换行"
        )
    payload_lines = encoded_region.splitlines()
    if not payload_lines or any(not line for line in payload_lines):
        raise RepairPackageBuildError(
            f"{description} Base64 Payload 包含空行"
        )
    for index, line in enumerate(payload_lines):
        if index < len(payload_lines) - 1 and len(line) != 76:
            raise RepairPackageBuildError(
                f"{description} Base64 非末行必须为 76 字符"
            )
        if len(line) > 76 or BASE64_LINE_RE.fullmatch(line) is None:
            raise RepairPackageBuildError(
                f"{description} Base64 含非法字符或行过长"
            )
    try:
        zip_bytes = base64.b64decode(
            "".join(payload_lines), validate=True
        )
    except (binascii.Error, ValueError) as error:
        raise RepairPackageBuildError(
            f"{description} Base64 无法解码"
        ) from error

    files = _read_payload_zip(zip_bytes, expected_version, description)
    records = _payload_records(files)
    return FrozenPayload(
        version=expected_version,
        source_package=package_path,
        source_package_sha256=expected_package_sha256,
        zip_bytes=zip_bytes,
        zip_sha256=_sha256_bytes(zip_bytes),
        files=files,
        records=records,
        tree_sha256=_tree_digest(records),
    )


def _collect_runtime() -> tuple[Dict[str, bytes], tuple[Mapping[str, Any], ...]]:
    _assert_plain_tree(FROZEN_RUNTIME_ROOT, "冻结 V1.0.1 runtime")
    files: Dict[str, bytes] = {}
    seen_windows_paths: Dict[str, str] = {}
    for path in sorted(FROZEN_RUNTIME_ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(FROZEN_RUNTIME_ROOT).as_posix()
        package_builder._assert_safe_relative_path(relative)
        package_builder._register_windows_path(
            seen_windows_paths, relative
        )
        files[relative] = path.read_bytes()
    if not files:
        raise RepairPackageBuildError("冻结 V1.0.1 runtime 为空")
    for required in ("python.exe", "pythonw.exe"):
        if required not in files:
            raise RepairPackageBuildError(
                f"冻结 V1.0.1 runtime 缺少 {required}"
            )
    records = _payload_records(files)
    actual_digest = _tree_digest(records)
    if actual_digest != FROZEN_RUNTIME_TREE_SHA256:
        raise RepairPackageBuildError(
            "冻结 V1.0.1 runtime 树哈希不一致："
            f"期望 {FROZEN_RUNTIME_TREE_SHA256}，实际 {actual_digest}"
        )
    return files, records


def _payload_manifest_section(
    payload: FrozenPayload, delivered_name: str
) -> dict[str, Any]:
    return {
        "version": payload.version,
        "file": delivered_name,
        "sha256": payload.zip_sha256,
        "size": len(payload.zip_bytes),
        "files": list(payload.records),
    }


def _read_windows_text(path: Path, description: str) -> str:
    _assert_plain_file(path, description)
    content = path.read_bytes()
    if content.startswith(b"\xef\xbb\xbf"):
        raise RepairPackageBuildError(f"{description} 不允许 UTF-8 BOM")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RepairPackageBuildError(
            f"{description} 不是 UTF-8"
        ) from error


def _as_crlf_bytes(path: Path, description: str) -> bytes:
    text = _read_windows_text(path, description)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized.replace("\n", "\r\n").encode("utf-8")


def _load_updater_source() -> bytes:
    text = _read_windows_text(UPDATER_SOURCE, "覆盖更新 Python 核心")
    if "\x00" in text:
        raise RepairPackageBuildError("覆盖更新 Python 核心包含 NUL")
    if "class Bundle:" not in text or "def load(" not in text:
        raise RepairPackageBuildError(
            "覆盖更新 Python 核心缺少 Bundle.load 契约"
        )
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _build_bundle_files(
    baseline: FrozenPayload,
    target: FrozenPayload,
    runtime_files: Mapping[str, bytes],
    runtime_records: Sequence[Mapping[str, Any]],
) -> tuple[Dict[str, bytes], bytes]:
    manifest = {
        "schema": 1,
        "release": RELEASE,
        "baseline": _payload_manifest_section(
            baseline, BASELINE_ZIP_NAME
        ),
        "target": _payload_manifest_section(target, TARGET_ZIP_NAME),
        "runtime": {
            "tree_sha256": FROZEN_RUNTIME_TREE_SHA256,
            "files": list(runtime_records),
        },
    }
    bundle_manifest_bytes = _json_bytes(manifest)
    outer_files: Dict[str, bytes] = {
        DELIVERED_LAUNCHER: _as_crlf_bytes(
            LAUNCHER_SOURCE, "覆盖更新 BAT 入口"
        ),
        DELIVERED_GUIDE: _as_crlf_bytes(
            GUIDE_SOURCE, "覆盖更新使用说明"
        ),
        DELIVERED_UPDATER: _load_updater_source(),
        DELIVERED_BUNDLE_MANIFEST: bundle_manifest_bytes,
        f"{DELIVERED_TOOL_ROOT}/{BASELINE_ZIP_NAME}": baseline.zip_bytes,
        f"{DELIVERED_TOOL_ROOT}/{TARGET_ZIP_NAME}": target.zip_bytes,
    }
    for relative, content in sorted(runtime_files.items()):
        outer_files[
            f"{DELIVERED_TOOL_ROOT}/runtime/{relative}"
        ] = content
    return outer_files, bundle_manifest_bytes


def _verify_outer_zip(
    artifact_bytes: bytes,
    expected_files: Mapping[str, bytes],
    baseline: FrozenPayload,
    target: FrozenPayload,
) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(artifact_bytes), mode="r")
    except (OSError, zipfile.BadZipFile) as error:
        raise RepairPackageBuildError("修复更新成品不是有效 ZIP") from error
    with archive:
        actual_names = archive.namelist()
        if actual_names != sorted(expected_files):
            raise RepairPackageBuildError(
                "修复更新成品 ZIP 条目顺序或集合不一致"
            )
        for info in archive.infolist():
            _assert_outer_relative_path(info.filename)
            if info.is_dir() or info.filename.endswith("/"):
                raise RepairPackageBuildError(
                    f"修复更新成品不允许目录条目：{info.filename}"
                )
            if info.date_time != package_builder.FIXED_ZIP_TIMESTAMP:
                raise RepairPackageBuildError(
                    f"修复更新成品时间戳不固定：{info.filename}"
                )
            actual = archive.read(info)
            if actual != expected_files[info.filename]:
                raise RepairPackageBuildError(
                    f"修复更新成品内容不一致：{info.filename}"
                )
        bad = archive.testzip()
        if bad is not None:
            raise RepairPackageBuildError(
                f"修复更新成品 CRC 校验失败：{bad}"
            )

    # 按真正交付后的目录形态加载，避免构建器和更新器 manifest 契约漂移。
    with tempfile.TemporaryDirectory(prefix="meetingroom-repair-verify-") as name:
        extracted = Path(name)
        with zipfile.ZipFile(io.BytesIO(artifact_bytes), mode="r") as archive:
            archive.extractall(extracted)
        update_path = extracted / DELIVERED_UPDATER
        module_name = "_meetingroom_repair_bundle_verify"
        spec = importlib.util.spec_from_file_location(module_name, update_path)
        if spec is None or spec.loader is None:
            raise RepairPackageBuildError(
                "无法加载成品中的覆盖更新 Python 核心"
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            bundle = module.Bundle.load(
                extracted / DELIVERED_TOOL_ROOT
            )
        except BaseException as error:
            raise RepairPackageBuildError(
                f"成品无法通过覆盖更新 Bundle.load 校验：{error}"
            ) from error
        finally:
            sys.modules.pop(module_name, None)
        if (
            bundle.release != RELEASE
            or bundle.baseline.zip_sha256 != baseline.zip_sha256
            or bundle.target.zip_sha256 != target.zip_sha256
            or bundle.runtime_tree_sha256 != FROZEN_RUNTIME_TREE_SHA256
        ):
            raise RepairPackageBuildError(
                "成品 Bundle.load 返回的发布身份或哈希不一致"
            )


def _assert_v102_manifest_matches(target: FrozenPayload) -> None:
    content = _read_frozen_file(
        FROZEN_V102_MANIFEST,
        FROZEN_V102_MANIFEST_SHA256,
        "冻结 V1.0.2 正式发布清单",
    )
    try:
        manifest = json.loads(content.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise RepairPackageBuildError(
            "冻结 V1.0.2 正式发布清单不是有效 UTF-8 JSON"
        ) from error
    if (
        str(manifest.get("version")) != TARGET_VERSION
        or str(manifest.get("package_sha256"))
        != FROZEN_V102_PACKAGE_SHA256
        or str(manifest.get("payload_zip_sha256")) != target.zip_sha256
    ):
        raise RepairPackageBuildError(
            "冻结 V1.0.2 正式发布清单与正式 BAT/Payload 不一致"
        )


def _path_is_within(candidate: Path, directory: Path) -> bool:
    try:
        return (
            os.path.commonpath((str(candidate), str(directory)))
            == str(directory)
        )
    except ValueError:
        return False


def _validate_release_target(release_root: Path) -> Path:
    release_root = release_root.expanduser().resolve()
    target = release_root / RELEASE
    protected_roots = (
        TOOL_DIR.resolve(),
        FROZEN_RUNTIME_ROOT.resolve(),
        (REPOSITORY_ROOT / "01_版本归档").resolve(),
        (DEVELOPMENT_DIR / "Windows部署目录-V1.0.0").resolve(),
        (
            DEVELOPMENT_DIR
            / "Windows部署目录-V1.0.1-待实机验收"
        ).resolve(),
    )
    for protected in protected_roots:
        if target == protected or _path_is_within(target, protected):
            raise RepairPackageBuildError(
                f"修复发布暂存不能位于受保护输入内：{protected}"
            )
    if target.exists():
        raise RepairPackageBuildError(
            f"修复发布暂存已经存在，拒绝覆盖：{target}"
        )
    return target


def _write_durable(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def build_repair_release(
    release_root: Path = DEFAULT_RELEASE_ROOT,
) -> BuildResult:
    """构建并原子交付 V1.0.2-r1；任何既存目标都不会被覆盖。"""

    target_dir = _validate_release_target(Path(release_root))
    baseline = _extract_frozen_payload(
        FROZEN_V101_PACKAGE,
        FROZEN_V101_PACKAGE_SHA256,
        BASELINE_VERSION,
    )
    target = _extract_frozen_payload(
        FROZEN_V102_PACKAGE,
        FROZEN_V102_PACKAGE_SHA256,
        TARGET_VERSION,
    )
    _assert_v102_manifest_matches(target)
    runtime_files, runtime_records = _collect_runtime()

    outer_files, bundle_manifest_bytes = _build_bundle_files(
        baseline, target, runtime_files, runtime_records
    )
    artifact_bytes = _build_outer_zip(outer_files)
    _verify_outer_zip(artifact_bytes, outer_files, baseline, target)
    artifact_sha256 = _sha256_bytes(artifact_bytes)
    outer_records = tuple(
        _record(path, outer_files[path]) for path in sorted(outer_files)
    )
    external_manifest = {
        "schema": 1,
        "release": RELEASE,
        "target_version": TARGET_VERSION,
        "baseline_version": BASELINE_VERSION,
        "artifact": {
            "file": ARTIFACT_NAME,
            "size": len(artifact_bytes),
            "sha256": artifact_sha256,
            "file_count": len(outer_records),
            "tree_sha256": _tree_digest(outer_records),
        },
        "bundle_manifest_sha256": _sha256_bytes(bundle_manifest_bytes),
        "baseline": {
            "source_file": FROZEN_V101_PACKAGE.name,
            "source_package_sha256": baseline.source_package_sha256,
            "payload_zip_sha256": baseline.zip_sha256,
            "payload_zip_size": len(baseline.zip_bytes),
            "payload_tree_sha256": baseline.tree_sha256,
            "payload_file_count": len(baseline.records),
        },
        "target": {
            "source_file": FROZEN_V102_PACKAGE.name,
            "source_package_sha256": target.source_package_sha256,
            "source_manifest_file": FROZEN_V102_MANIFEST.name,
            "source_manifest_sha256": FROZEN_V102_MANIFEST_SHA256,
            "payload_zip_sha256": target.zip_sha256,
            "payload_zip_size": len(target.zip_bytes),
            "payload_tree_sha256": target.tree_sha256,
            "payload_file_count": len(target.records),
        },
        "runtime": {
            "source": (
                "Windows部署目录-V1.0.1-待实机验收/"
                "_程序文件/runtime"
            ),
            "tree_sha256": FROZEN_RUNTIME_TREE_SHA256,
            "file_count": len(runtime_records),
        },
        "immutability": {
            "old_v1_0_2_package_and_manifest_are_inputs_only": True,
            "output_directory_must_not_preexist": True,
        },
    }
    external_manifest_bytes = _json_bytes(external_manifest)

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{RELEASE}.", dir=str(target_dir.parent)
        )
    )
    delivered = False
    try:
        temporary_artifact = temporary_dir / ARTIFACT_NAME
        temporary_manifest = temporary_dir / EXTERNAL_MANIFEST_NAME
        _write_durable(temporary_artifact, artifact_bytes)
        _write_durable(temporary_manifest, external_manifest_bytes)
        if (
            _sha256_file(temporary_artifact) != artifact_sha256
            or temporary_artifact.stat().st_size != len(artifact_bytes)
        ):
            raise RepairPackageBuildError(
                "写盘后的修复更新 ZIP 大小或哈希不一致"
            )
        written_manifest = json.loads(
            temporary_manifest.read_text(encoding="utf-8")
        )
        if written_manifest != external_manifest:
            raise RepairPackageBuildError(
                "写盘后的外部发布清单内容不一致"
            )
        _verify_outer_zip(
            temporary_artifact.read_bytes(),
            outer_files,
            baseline,
            target,
        )

        os.replace(str(temporary_dir), str(target_dir))
        delivered = True
    finally:
        if not delivered:
            shutil.rmtree(temporary_dir, ignore_errors=True)

    # 最后再次证明只读的旧正式交付没有被当前构建过程改写。
    _read_frozen_file(
        FROZEN_V101_PACKAGE,
        FROZEN_V101_PACKAGE_SHA256,
        "冻结 V1.0.1 正式 BAT",
    )
    _read_frozen_file(
        FROZEN_V102_PACKAGE,
        FROZEN_V102_PACKAGE_SHA256,
        "冻结 V1.0.2 正式 BAT",
    )
    _read_frozen_file(
        FROZEN_V102_MANIFEST,
        FROZEN_V102_MANIFEST_SHA256,
        "冻结 V1.0.2 正式发布清单",
    )

    artifact_path = target_dir / ARTIFACT_NAME
    manifest_path = target_dir / EXTERNAL_MANIFEST_NAME
    return BuildResult(
        release_dir=target_dir,
        artifact_path=artifact_path,
        manifest_path=manifest_path,
        artifact_sha256=artifact_sha256,
        artifact_size=len(artifact_bytes),
        baseline_zip_sha256=baseline.zip_sha256,
        target_zip_sha256=target.zip_sha256,
        runtime_tree_sha256=FROZEN_RUNTIME_TREE_SHA256,
    )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="制作会议室预约系统 V1.0.2-r1 覆盖修复更新 ZIP"
    )
    parser.add_argument(
        "--release-root",
        type=Path,
        default=DEFAULT_RELEASE_ROOT,
        help="版本化发布暂存的父目录（默认：02_开发工作区/发布暂存）",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    try:
        result = build_repair_release(arguments.release_root)
    except (
        RepairPackageBuildError,
        package_builder.PackageBuildError,
        OSError,
        ValueError,
    ) as error:
        print(f"制作失败：{error}", file=sys.stderr)
        return 1
    print(f"修复发布暂存：{result.release_dir}")
    print(f"修复更新 ZIP：{result.artifact_path}")
    print(f"修复更新 ZIP SHA-256：{result.artifact_sha256}")
    print(f"修复更新 ZIP 大小：{result.artifact_size} 字节")
    print(f"外部发布清单：{result.manifest_path}")
    print(f"V1.0.1 Payload ZIP SHA-256：{result.baseline_zip_sha256}")
    print(f"V1.0.2 Payload ZIP SHA-256：{result.target_zip_sha256}")
    print(f"冻结 runtime 树 SHA-256：{result.runtime_tree_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""从已组装 V2 payload 和冻结 Windows runtime 制作确定性安装 ZIP。"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from .installer_core import (
        GENERATION_FILE,
        HEALTH_PATH,
        INSTALLED_MANIFEST,
        INSTALL_INFO,
        LAN_BIND,
        MANIFEST_SCHEMA,
        PRODUCT_GENERATION,
        RECEIPT_FILE,
        RELEASE,
        SERVICE_ENTRYPOINT,
        SERVICE_PORT,
        SETUP_BIND,
        TASK_NAME,
        TASK_PATH,
        TRANSACTION_FILE,
        VERSION,
        VERSION_FILE,
        Bundle,
        InstallerError,
        assert_plain_file,
        assert_plain_tree,
        json_bytes,
        records_for_tree,
        safe_relative_path,
        sha256_bytes,
        sha256_file,
        tree_digest,
    )
except ImportError:
    from installer_core import (  # type: ignore
        GENERATION_FILE,
        HEALTH_PATH,
        INSTALLED_MANIFEST,
        INSTALL_INFO,
        LAN_BIND,
        MANIFEST_SCHEMA,
        PRODUCT_GENERATION,
        RECEIPT_FILE,
        RELEASE,
        SERVICE_ENTRYPOINT,
        SERVICE_PORT,
        SETUP_BIND,
        TASK_NAME,
        TASK_PATH,
        TRANSACTION_FILE,
        VERSION,
        VERSION_FILE,
        Bundle,
        InstallerError,
        assert_plain_file,
        assert_plain_tree,
        json_bytes,
        records_for_tree,
        safe_relative_path,
        sha256_bytes,
        sha256_file,
        tree_digest,
    )


TOOL_DIR = Path(__file__).resolve().parent
LAUNCHER_SOURCE = TOOL_DIR / "安装V2.0.0.bat"
GUIDE_SOURCE = TOOL_DIR / "安装说明.txt"
ENTRY_SOURCE = TOOL_DIR / "install.py"
CORE_SOURCE = TOOL_DIR / "installer_core.py"

ARTIFACT_NAME = "会议室预约系统-V2.0.0-安装包.zip"
DELIVERED_LAUNCHER = "安装V2.0.0.bat"
DELIVERED_GUIDE = "安装说明.txt"
DELIVERED_TOOL = "_V2安装工具"
DELIVERED_ENTRY = f"{DELIVERED_TOOL}/install.py"
DELIVERED_CORE = f"{DELIVERED_TOOL}/installer_core.py"
DELIVERED_MANIFEST = f"{DELIVERED_TOOL}/manifest.json"
PAYLOAD_NAME = "payload-v2.0.0.zip"
DELIVERED_PAYLOAD = f"{DELIVERED_TOOL}/{PAYLOAD_NAME}"

PROTECTED_PAYLOAD_PREFIXES = (
    "_程序文件/data/",
    "_程序文件/backups/",
    "_程序文件/logs/",
    "_程序文件/runtime/",
)
PROTECTED_PAYLOAD_EXACT = frozenset(
    {
        INSTALL_INFO,
        VERSION_FILE,
        GENERATION_FILE,
        INSTALLED_MANIFEST,
        TRANSACTION_FILE,
        RECEIPT_FILE,
        "_程序文件/data",
        "_程序文件/backups",
        "_程序文件/logs",
        "_程序文件/runtime",
    }
)


class PackageBuildError(InstallerError):
    """候选包输入、输出或反向验证不符合约定。"""


@dataclass(frozen=True)
class BuildResult:
    artifact_path: Path
    sha256_path: Path
    external_manifest_path: Path
    artifact_sha256: str
    artifact_size: int
    payload_sha256: str
    runtime_tree_sha256: str


def _read_utf8(path: Path, description: str) -> str:
    assert_plain_file(path, description)
    content = path.read_bytes()
    if content.startswith(b"\xef\xbb\xbf"):
        raise PackageBuildError(f"{description}不允许 UTF-8 BOM")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PackageBuildError(f"{description}不是 UTF-8") from error
    if "\x00" in text:
        raise PackageBuildError(f"{description}包含 NUL")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _crlf_bytes(path: Path, description: str) -> bytes:
    text = _read_utf8(path, description)
    if not text.endswith("\n"):
        text += "\n"
    return text.replace("\n", "\r\n").encode("utf-8")


def _lf_bytes(path: Path, description: str) -> bytes:
    text = _read_utf8(path, description)
    if not text.endswith("\n"):
        text += "\n"
    return text.encode("utf-8")


def _assert_payload_safe(payload_root: Path) -> tuple[Mapping[str, Any], ...]:
    records = records_for_tree(payload_root)
    if not records:
        raise PackageBuildError("V2 payload 为空")
    folded: dict[str, str] = {}
    for record in records:
        relative = str(record["path"])
        lower = relative.casefold()
        if lower in {path.casefold() for path in PROTECTED_PAYLOAD_EXACT} or any(
            lower.startswith(prefix.casefold()) for prefix in PROTECTED_PAYLOAD_PREFIXES
        ):
            raise PackageBuildError(f"V2 payload 试图携带现场可变或事务文件：{relative}")
        if relative.startswith(f"{DELIVERED_TOOL}/") or relative == DELIVERED_TOOL:
            raise PackageBuildError(f"V2 payload 不能伪造安装工具目录：{relative}")
        key = lower
        if key in folded:
            raise PackageBuildError(
                f"V2 payload 包含 Windows 大小写冲突：{folded[key]} / {relative}"
            )
        folded[key] = relative
    paths = {str(record["path"]) for record in records}
    if SERVICE_ENTRYPOINT not in paths:
        raise PackageBuildError(f"V2 payload 缺少服务入口：{SERVICE_ENTRYPOINT}")
    return records


def _deterministic_zip(files: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        previous_folded: dict[str, str] = {}
        for relative in sorted(files):
            safe_relative_path(relative)
            key = relative.casefold()
            if key in previous_folded:
                raise PackageBuildError(
                    f"ZIP 包含 Windows 大小写冲突：{previous_folded[key]} / {relative}"
                )
            previous_folded[key] = relative
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, files[relative], compress_type=zipfile.ZIP_DEFLATED)
    return output.getvalue()


def _records_from_files(files: Mapping[str, bytes]) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        {
            "path": relative,
            "size": len(files[relative]),
            "sha256": sha256_bytes(files[relative]),
        }
        for relative in sorted(files)
    )


def _payload_files(payload_root: Path) -> tuple[dict[str, bytes], tuple[Mapping[str, Any], ...]]:
    records = _assert_payload_safe(payload_root)
    files = {
        str(record["path"]): payload_root.joinpath(
            *str(record["path"]).split("/")
        ).read_bytes()
        for record in records
    }
    return files, records


def _runtime_files(runtime_root: Path) -> tuple[dict[str, bytes], tuple[Mapping[str, Any], ...]]:
    records = records_for_tree(runtime_root)
    paths = {str(record["path"]) for record in records}
    required = {"python.exe", "pythonw.exe"}
    if not required.issubset(paths):
        raise PackageBuildError(f"冻结 runtime 缺少：{sorted(required - paths)}")
    files = {
        str(record["path"]): runtime_root.joinpath(
            *str(record["path"]).split("/")
        ).read_bytes()
        for record in records
    }
    return files, records


def _manifest(
    payload_zip: bytes,
    payload_records: Sequence[Mapping[str, Any]],
    runtime_records: Sequence[Mapping[str, Any]],
    tool_records: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "kind": "fresh-install",
        "product_generation": PRODUCT_GENERATION,
        "release": RELEASE,
        "version": VERSION,
        "service": {
            "port": SERVICE_PORT,
            "setup_bind": SETUP_BIND,
            "lan_bind": LAN_BIND,
            "health_path": HEALTH_PATH,
            "task_path": TASK_PATH,
            "task_name": TASK_NAME,
            "entrypoint": SERVICE_ENTRYPOINT,
        },
        "payload": {
            "file": PAYLOAD_NAME,
            "size": len(payload_zip),
            "sha256": sha256_bytes(payload_zip),
            "tree_sha256": tree_digest(payload_records),
            "files": list(payload_records),
        },
        "runtime": {
            "directory": "runtime",
            "tree_sha256": tree_digest(runtime_records),
            "files": list(runtime_records),
        },
        "tool": {
            "tree_sha256": tree_digest(tool_records),
            "files": list(tool_records),
        },
        "acceptance": {
            "status": "candidate",
            "formal_external_release_allowed": False,
        },
    }


def _outer_files(
    payload_zip: bytes,
    manifest: Mapping[str, Any],
    runtime_files: Mapping[str, bytes],
    tool_files: Mapping[str, bytes],
) -> dict[str, bytes]:
    files = {
        DELIVERED_LAUNCHER: _crlf_bytes(LAUNCHER_SOURCE, "V2 零参数 BAT"),
        DELIVERED_GUIDE: _crlf_bytes(GUIDE_SOURCE, "V2 安装说明"),
        DELIVERED_ENTRY: tool_files["install.py"],
        DELIVERED_CORE: tool_files["installer_core.py"],
        DELIVERED_MANIFEST: json_bytes(manifest),
        DELIVERED_PAYLOAD: payload_zip,
    }
    for relative, content in runtime_files.items():
        files[f"{DELIVERED_TOOL}/runtime/{relative}"] = content
    return files


def verify_outer_package(content: bytes, expected_files: Mapping[str, bytes]) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content), "r")
    except (OSError, zipfile.BadZipFile) as error:
        raise PackageBuildError("外层安装包不是有效 ZIP") from error
    extracted_files: dict[str, bytes] = {}
    with archive:
        for info in archive.infolist():
            relative = info.filename
            safe_relative_path(relative)
            if info.is_dir() or relative.endswith("/"):
                raise PackageBuildError(f"外层安装包不允许目录条目：{relative}")
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_IFMT(mode) == stat.S_IFLNK or info.flag_bits & 0x1:
                raise PackageBuildError(f"外层安装包包含链接或加密条目：{relative}")
            if relative in extracted_files:
                raise PackageBuildError(f"外层安装包路径重复：{relative}")
            extracted_files[relative] = archive.read(info)
        if archive.testzip() is not None:
            raise PackageBuildError("外层安装包 CRC 校验失败")
    if extracted_files != dict(expected_files):
        raise PackageBuildError("外层安装包写盘内容与构建输入不一致")

    with tempfile.TemporaryDirectory(prefix="meeting-room-v2-package-verify-") as name:
        root = Path(name)
        for relative, data in extracted_files.items():
            destination = root.joinpath(*relative.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        loaded = Bundle.load(root / DELIVERED_TOOL)
        if loaded.manifest["version"] != VERSION:
            raise PackageBuildError("反向加载得到的安装包版本错误")


def _sidecar_paths(output: Path) -> tuple[Path, Path]:
    return (
        output.with_name(output.name + ".sha256.txt"),
        output.with_name(output.name + ".manifest.json"),
    )


def build_package(
    payload_root: Path,
    runtime_root: Path,
    output: Path,
) -> BuildResult:
    payload_root = Path(payload_root).resolve(strict=True)
    runtime_root = Path(runtime_root).resolve(strict=True)
    output = Path(output).resolve()
    assert_plain_tree(payload_root, "V2 payload 源目录")
    assert_plain_tree(runtime_root, "冻结 Windows runtime")
    if output.name != ARTIFACT_NAME:
        raise PackageBuildError(f"V2 候选 ZIP 必须命名为：{ARTIFACT_NAME}")
    sha_path, external_manifest_path = _sidecar_paths(output)
    for path in (output, sha_path, external_manifest_path):
        if path.exists():
            raise PackageBuildError(f"发布输出已经存在，拒绝覆盖：{path}")
    for source in (payload_root, runtime_root, TOOL_DIR):
        if output == source or source in output.parents:
            raise PackageBuildError(f"发布输出不能位于受保护输入目录：{source}")

    payload_files, payload_records = _payload_files(payload_root)
    payload_zip = _deterministic_zip(payload_files)
    runtime_files, runtime_records = _runtime_files(runtime_root)
    tool_files = {
        "install.py": _lf_bytes(ENTRY_SOURCE, "V2 Python 安装入口"),
        "installer_core.py": _lf_bytes(CORE_SOURCE, "V2 安装事务核心"),
    }
    tool_records = _records_from_files(tool_files)
    manifest = _manifest(payload_zip, payload_records, runtime_records, tool_records)
    outer_files = _outer_files(payload_zip, manifest, runtime_files, tool_files)
    artifact = _deterministic_zip(outer_files)
    verify_outer_package(artifact, outer_files)
    artifact_sha = sha256_bytes(artifact)
    external_manifest = {
        "schema": 1,
        "release": RELEASE,
        "version": VERSION,
        "status": "windows_acceptance_candidate_only",
        "formal_external_release_allowed": False,
        "artifact": {
            "file": output.name,
            "size": len(artifact),
            "sha256": artifact_sha,
        },
        "payload": {
            "file_count": len(payload_records),
            "sha256": sha256_bytes(payload_zip),
            "tree_sha256": tree_digest(payload_records),
        },
        "runtime": {
            "file_count": len(runtime_records),
            "tree_sha256": tree_digest(runtime_records),
        },
        "tool": {
            "file_count": len(tool_records),
            "tree_sha256": tree_digest(tool_records),
        },
        "service": {
            "port": SERVICE_PORT,
            "setup_bind": SETUP_BIND,
            "lan_bind": LAN_BIND,
        },
    }
    sha_text = f"{artifact_sha}  {output.name}\n".encode("utf-8")
    manifest_content = json_bytes(external_manifest)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=".v2-package-", dir=str(output.parent)))
    delivered: list[Path] = []
    try:
        staged_artifact = temporary_dir / output.name
        staged_sha = temporary_dir / sha_path.name
        staged_manifest = temporary_dir / external_manifest_path.name
        staged_artifact.write_bytes(artifact)
        staged_sha.write_bytes(sha_text)
        staged_manifest.write_bytes(manifest_content)
        if sha256_file(staged_artifact) != artifact_sha:
            raise PackageBuildError("V2 候选 ZIP 落盘后哈希不一致")
        verify_outer_package(staged_artifact.read_bytes(), outer_files)
        for staged, final in (
            (staged_artifact, output),
            (staged_sha, sha_path),
            (staged_manifest, external_manifest_path),
        ):
            if final.exists():
                raise PackageBuildError(f"发布输出在交付前出现，拒绝覆盖：{final}")
            os.replace(staged, final)
            delivered.append(final)
    except BaseException:
        for path in delivered:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)

    return BuildResult(
        artifact_path=output,
        sha256_path=sha_path,
        external_manifest_path=external_manifest_path,
        artifact_sha256=artifact_sha,
        artifact_size=len(artifact),
        payload_sha256=sha256_bytes(payload_zip),
        runtime_tree_sha256=tree_digest(runtime_records),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="制作 V2.0.0 全新安装候选 ZIP")
    parser.add_argument("--payload-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = build_package(
            arguments.payload_root,
            arguments.runtime_root,
            arguments.output,
        )
    except (PackageBuildError, InstallerError, OSError, ValueError) as error:
        print(f"V2 安装包制作失败：{error}")
        return 1
    print(f"V2 候选 ZIP：{result.artifact_path}")
    print(f"SHA-256：{result.artifact_sha256}")
    print(f"发布清单：{result.external_manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

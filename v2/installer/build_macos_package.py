#!/usr/bin/env python3
"""组装 macOS 自托管便携包：可复现 ZIP、供应链伴生文件与 latest-macos.json 清单。

ZIP 是字节级可复现的事实源（排序、固定 mtime、固定权限、无目录条目）；
DMG 由 `build_macos_dmg.py` 从同一 staging 内容另行生成。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from .frontend_supply_chain import build_frontend_component_evidence
    from .installer_core import (
        InstallerError,
        safe_relative_path,
        sha256_bytes,
        sha256_file,
    )
except ImportError:
    from frontend_supply_chain import build_frontend_component_evidence  # type: ignore
    from installer_core import (  # type: ignore
        InstallerError,
        safe_relative_path,
        sha256_bytes,
        sha256_file,
    )


TOP_FOLDER = "会议室预约系统V2-macOS"
BACKEND_ROOT_FILES = (
    "service.py",
    "server.py",
    "backup.py",
    "restore.py",
    "requirements.txt",
    "requirements-macos-arm64.lock",
)
IGNORED_DIRECTORY_NAMES = {"__pycache__", ".git", ".pytest_cache", "node_modules"}
IGNORED_FILE_SUFFIXES = (".pyc", ".pyo")
EDITION_FILE_NAME = "EDITION"
EDITION_CONTENT = "macos-selfhost\n"
TEMPLATE_NAMES = ("启动.command", "停止.command", "使用说明.txt")
FORBIDDEN_FIELD_DIRECTORIES = ("data", "backups", "logs")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
MANIFEST_PRODUCT = "meeting-room-reservation-system-v2"
MANIFEST_CHANNEL = "macos-selfhost"


class MacPackageBuildError(InstallerError):
    """macOS 便携包组装违反本方案的固定边界。"""


def _iter_plain_files(root: Path, label: str) -> list[str]:
    files: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if any(part in IGNORED_DIRECTORY_NAMES for part in relative.split("/")[:-1]):
            continue
        safe_relative_path(relative)
        if path.is_symlink():
            raise MacPackageBuildError(f"{label} 不允许符号链接：{relative}")
        if path.is_dir():
            if path.name in IGNORED_DIRECTORY_NAMES:
                continue
            continue
        if not path.is_file():
            raise MacPackageBuildError(f"{label} 含特殊条目：{relative}")
        if path.suffix in IGNORED_FILE_SUFFIXES:
            continue
        files.append(relative)
    return files


def _copy_tree(source: Path, destination: Path, label: str) -> None:
    for relative in _iter_plain_files(source, label):
        target = destination.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source.joinpath(*relative.split("/")), target)


def _normalized_text(raw: bytes, label: str) -> bytes:
    if raw.startswith(b"\xef\xbb\xbf") or b"\x00" in raw:
        raise MacPackageBuildError(f"{label} 带 BOM 或包含 NUL")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MacPackageBuildError(f"{label} 不是 UTF-8") from error
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _read_version(backend_root: Path) -> str:
    version_path = backend_root.parent / "VERSION"
    version = version_path.read_text(encoding="utf-8").strip()
    if not VERSION_PATTERN.fullmatch(version):
        raise MacPackageBuildError(f"v2/VERSION 不是 x.y.z：{version}")
    return version


def _mode_for(relative: str) -> int:
    if relative.endswith(".command"):
        return 0o755
    if "/runtime/bin/" in relative:
        return 0o755
    return 0o644


def _deterministic_zip(
    files: Mapping[str, bytes], modes: Mapping[str, int]
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        seen: dict[str, str] = {}
        for relative in sorted(files):
            safe_relative_path(relative)
            key = relative.casefold()
            if key in seen:
                raise MacPackageBuildError(
                    f"ZIP 含大小写冲突：{seen[key]} / {relative}"
                )
            seen[key] = relative
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (
                stat.S_IFREG | modes.get(relative, 0o644)
            ) << 16
            archive.writestr(info, files[relative], compress_type=zipfile.ZIP_DEFLATED)
    return output.getvalue()


def _assemble_staging(
    staging_parent: Path,
    *,
    backend_root: Path,
    frontend_dist: Path,
    runtime_root: Path,
    templates_dir: Path,
) -> Path:
    if not (frontend_dist / "index.html").is_file():
        raise MacPackageBuildError("前端产物缺少 index.html")
    for name in TEMPLATE_NAMES:
        if not (templates_dir / name).is_file():
            raise MacPackageBuildError(f"缺少 macOS 模板：{name}")
    staging = staging_parent / TOP_FOLDER
    if staging.exists():
        raise MacPackageBuildError(f"staging 已存在，拒绝覆盖：{staging}")
    staging.mkdir(parents=True)
    app = staging / "app"
    app.mkdir()
    for name in BACKEND_ROOT_FILES:
        source = backend_root / name
        if not source.is_file():
            raise MacPackageBuildError(f"后端缺少必需文件：{name}")
        shutil.copyfile(source, app / name)
    _copy_tree(backend_root / "v2app", app / "v2app", "v2app")
    _copy_tree(frontend_dist, app / "static", "前端产物")
    # newline 必须显式固定为 LF：Windows 宿主的文本模式默认会把 \n
    # 翻译成 \r\n，破坏交付包字节一致性（V241-B2）。
    (app / EDITION_FILE_NAME).write_text(
        EDITION_CONTENT, encoding="utf-8", newline="\n"
    )
    frontend_lock = backend_root.parent / "frontend" / "package-lock.json"
    (app / "frontend-production-components.json").write_bytes(
        build_frontend_component_evidence(frontend_lock)
    )
    _copy_tree(runtime_root, staging / "runtime", "冻结 macOS runtime")
    for name in TEMPLATE_NAMES:
        raw = (templates_dir / name).read_bytes()
        (staging / name).write_bytes(_normalized_text(raw, f"模板 {name}"))
    for directory in FORBIDDEN_FIELD_DIRECTORIES:
        if (staging / directory).exists():
            raise MacPackageBuildError(f"交付包不得携带现场目录：{directory}")
    for required in (
        "app/service.py",
        "app/v2app/__init__.py",
        "app/static/index.html",
        f"app/{EDITION_FILE_NAME}",
        "app/frontend-production-components.json",
        "runtime/bin/python3.13",
        "runtime/lib/python3.13/site-packages",
    ):
        if not (staging / required).exists():
            raise MacPackageBuildError(f"交付包缺少必需路径：{required}")
    # 字节级校验：read_text 会把 \r\n 反向翻译回 \n，掩盖写入侧的换行
    # 漂移；只有比对原始字节才能真正拦住跨平台不一致。
    edition = (staging / "app" / EDITION_FILE_NAME).read_bytes()
    if edition != EDITION_CONTENT.encode("utf-8"):
        raise MacPackageBuildError("EDITION 标记内容不符")
    return staging


def build_macos_package(
    *,
    backend_root: Path,
    frontend_dist: Path,
    runtime_root: Path,
    templates_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    backend_root = Path(backend_root).resolve(strict=True)
    frontend_dist = Path(frontend_dist).resolve(strict=True)
    runtime_root = Path(runtime_root).resolve(strict=True)
    templates_dir = Path(templates_dir).resolve(strict=True)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    version = _read_version(backend_root)
    artifact_name = f"会议室预约系统-V{version}-macOS-arm64.zip"

    workspace = Path(tempfile.mkdtemp(prefix=".v2-macos-package-", dir=str(output_dir)))
    try:
        staging = _assemble_staging(
            workspace,
            backend_root=backend_root,
            frontend_dist=frontend_dist,
            runtime_root=runtime_root,
            templates_dir=templates_dir,
        )
        files: dict[str, bytes] = {}
        modes: dict[str, int] = {}
        for relative in _iter_plain_files(staging, "macOS 交付 staging"):
            packaged = f"{TOP_FOLDER}/{relative}"
            files[packaged] = staging.joinpath(*relative.split("/")).read_bytes()
            modes[packaged] = _mode_for(packaged)
        for marker in ("demo123", "TEST-2026", "INITIAL_CALENDAR_BOOKINGS", "/api/v2"):
            for packaged, content in files.items():
                if marker.encode("utf-8") in content and not packaged.endswith(".py"):
                    raise MacPackageBuildError(f"交付包含禁用合成标记：{packaged}")
        artifact = _deterministic_zip(files, modes)
        artifact_sha = sha256_bytes(artifact)

        # 反向验证：重新解包并对齐条目、权限与内容。
        with zipfile.ZipFile(io.BytesIO(artifact)) as archive:
            names = archive.namelist()
            if sorted(names) != sorted(files) or len(names) != len(set(names)):
                raise MacPackageBuildError("ZIP 反向验证失败：条目不一致")
            for info in archive.infolist():
                mode = (info.external_attr >> 16) & 0o777
                if mode != modes[info.filename]:
                    raise MacPackageBuildError(
                        f"ZIP 权限不符：{info.filename} {oct(mode)}"
                    )
                if archive.read(info.filename) != files[info.filename]:
                    raise MacPackageBuildError(f"ZIP 内容不符：{info.filename}")

        manifest = {
            "product": MANIFEST_PRODUCT,
            "channel": MANIFEST_CHANNEL,
            "version": version,
            "tag": f"v{version}",
        }
        records = [
            {"path": name, "size": len(files[name]), "sha256": sha256_bytes(files[name])}
            for name in sorted(files)
        ]
        external_manifest = {
            "schema": 1,
            "platform": "macos-arm64",
            "edition": MANIFEST_CHANNEL,
            "version": f"V{version}",
            "top_folder": TOP_FOLDER,
            "distribution_channel": "GitHub Release",
            "release_gate": (
                "以 v2/docs/RELEASE-CHECKLIST.md 的当前版本条目与全部 macOS 正式分发门禁为准"
            ),
            "entrypoints": ["启动.command", "停止.command"],
            "files": records,
        }
        supply_chain = runtime_root / "supply-chain"
        staged = workspace / "output"
        staged.mkdir()
        staged.joinpath(artifact_name).write_bytes(artifact)
        staged.joinpath(artifact_name + ".sha256.txt").write_bytes(
            f"{artifact_sha}  {artifact_name}\n".encode("utf-8")
        )
        staged.joinpath(artifact_name + ".manifest.json").write_bytes(
            json.dumps(external_manifest, ensure_ascii=False, indent=2, sort_keys=True)
            .encode("utf-8")
            + b"\n"
        )
        for source_name, sidecar_suffix in (
            ("sbom.cdx.json", ".sbom.cdx.json"),
            ("THIRD-PARTY-NOTICES.txt", ".THIRD-PARTY-NOTICES.txt"),
            ("runtime-provenance.json", ".runtime-provenance.json"),
        ):
            source = supply_chain / source_name
            if not source.is_file():
                raise MacPackageBuildError(f"runtime 缺少供应链文件：{source_name}")
            staged.joinpath(artifact_name + sidecar_suffix).write_bytes(
                source.read_bytes()
            )
        staged.joinpath("latest-macos.json").write_bytes(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode(
                "utf-8"
            )
            + b"\n"
        )

        for delivered in staged.iterdir():
            destination = output_dir / delivered.name
            if destination.exists():
                raise MacPackageBuildError(f"发布输出已存在，拒绝覆盖：{destination}")
        for delivered in sorted(staged.iterdir()):
            os.replace(delivered, output_dir / delivered.name)
        if sha256_file(output_dir / artifact_name) != artifact_sha:
            raise MacPackageBuildError("交付 ZIP 落盘后哈希不一致")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return {
        "version": version,
        "artifact": str(output_dir / artifact_name),
        "sha256": artifact_sha,
        "file_count": len(files),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="组装 V2 macOS 自托管便携包 ZIP")
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--frontend-dist", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "payload_templates_macos",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        summary = build_macos_package(
            backend_root=arguments.backend_root,
            frontend_dist=arguments.frontend_dist,
            runtime_root=arguments.runtime_root,
            templates_dir=arguments.templates_dir,
            output_dir=arguments.output_dir,
        )
    except (MacPackageBuildError, InstallerError, OSError, ValueError) as error:
        print(f"V2 macOS 便携包组装失败：{error}")
        return 1
    print(f"macOS 便携包：{summary['artifact']}")
    print(f"SHA-256：{summary['sha256']}")
    print(f"文件数：{summary['file_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

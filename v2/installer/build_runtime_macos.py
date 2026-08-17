#!/usr/bin/env python3
"""从核验过的 python-build-standalone tarball 与哈希 wheelhouse 组装 macOS arm64 冻结 runtime。

与 Windows `build_runtime.py` 的差异：
- 解释器来源是 python-build-standalone 的 install_only tarball（不是 embed ZIP）；
- 按固定规则裁剪不参与运行的文件，并把保留的符号链接物化为真实文件，
  保证 runtime 树是纯普通文件（与既有供应链断言的“plain tree”一致）；
- 不做 Windows 专属的 .pth 注入与 `validate_runtime_supply_chain` 批准树校验，
  但保留 tarball SHA-256 锁定、wheel 哈希锁、SBOM、许可证清单与来源证明。
"""

from __future__ import annotations

import argparse
import os
import shutil
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from .build_runtime import (
        _archive_files,
        _license_summary,
        _load_wheels,
        _make_sbom,
        _project_url,
        _write_archive,
    )
    from .installer_core import (
        InstallerError,
        json_bytes,
        parse_hashed_requirements_lock,
        records_for_tree,
        sha256_bytes,
        sha256_file,
        tree_digest,
    )
except ImportError:
    from build_runtime import (  # type: ignore
        _archive_files,
        _license_summary,
        _load_wheels,
        _make_sbom,
        _project_url,
        _write_archive,
    )
    from installer_core import (  # type: ignore
        InstallerError,
        json_bytes,
        parse_hashed_requirements_lock,
        records_for_tree,
        sha256_bytes,
        sha256_file,
        tree_digest,
    )


MACOS_PYTHON_VERSION = "3.13.14"
MACOS_PYTHON_BUILD_TAG = "20260718"
MACOS_PYTHON_SOURCE_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{MACOS_PYTHON_BUILD_TAG}/"
    f"cpython-3.13.14+{MACOS_PYTHON_BUILD_TAG}-aarch64-apple-darwin-install_only.tar.gz"
)
MACOS_PYTHON_SOURCE_SHA256 = (
    "dca7c3bac21f023cf294705b27f4f3e9c70399c40790ebb81e8d0eff15b00770"
)
DEFAULT_LOCK = (
    Path(__file__).resolve().parent.parent
    / "backend"
    / "requirements-macos-arm64.lock"
)

TARBALL_ROOT = "python/"
EXCLUDED_DIRECTORY_PREFIXES = (
    "share/",
    "include/",
    "lib/pkgconfig/",
)
EXCLUDED_FILE_PREFIXES = (
    "bin/idle3",
    "bin/pydoc",
    "bin/pip",
    "bin/python3-config",
    "bin/python3.13-config",
    "lib/python3.13/site-packages/pip",
)
EXCLUDED_FILE_NAMES = {"BUILD"}
SUPPLY_CHAIN_DIR = "supply-chain"
RUNTIME_LOCK_NAME = "requirements-macos-arm64.lock"
PROVENANCE_NAME = "runtime-provenance.json"
SBOM_NAME = "sbom.cdx.json"
NOTICES_NAME = "THIRD-PARTY-NOTICES.txt"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


class MacRuntimeBuildError(InstallerError):
    """macOS runtime 来源或组装结果不能证明与冻结供应链一致。"""


@dataclass(frozen=True)
class MacRuntimeBuildResult:
    runtime_root: Path
    python_source_sha256: str
    component_count: int


def _is_excluded(relative: str) -> bool:
    parts = relative.split("/")
    name = parts[-1]
    directory = "/".join(parts[:-1])
    if name in EXCLUDED_FILE_NAMES:
        return True
    for prefix in EXCLUDED_FILE_PREFIXES:
        if relative == prefix:
            return True
        if relative.startswith(prefix):
            following = relative[len(prefix)]
            # 边界字符覆盖 pip3、pydoc3、idle3.13、pip-26.x.dist-info 等变体。
            if following in "-./_0123456789":
                return True
    for prefix in EXCLUDED_DIRECTORY_PREFIXES:
        if directory == prefix.rstrip("/") or directory.startswith(prefix):
            return True
    return False


def _extract_tarball(tarball: Path, staging: Path) -> None:
    pending_symlinks: list[tuple[str, str]] = []
    extracted_files: set[str] = set()
    total = 0
    try:
        with tarfile.open(tarball, "r:gz") as archive:
            for member in archive.getmembers():
                name = member.name
                if name.startswith("./"):
                    name = name[2:]
                if not name.startswith(TARBALL_ROOT):
                    raise MacRuntimeBuildError(f"tarball 条目不在 python/ 根下：{member.name}")
                relative = name[len(TARBALL_ROOT):]
                if not relative or relative.startswith("/") or ".." in relative.split("/"):
                    raise MacRuntimeBuildError(f"tarball 条目路径异常：{member.name}")
                if _is_excluded(relative):
                    continue
                target = staging.joinpath(*relative.split("/"))
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if member.issym():
                    pending_symlinks.append((relative, member.linkname))
                    continue
                if not member.isfile():
                    raise MacRuntimeBuildError(f"tarball 含不支持的特殊条目：{member.name}")
                if member.size > MAX_ARCHIVE_BYTES:
                    raise MacRuntimeBuildError(f"tarball 单文件体积异常：{member.name}")
                total += member.size
                if total > 8 * MAX_ARCHIVE_BYTES:
                    raise MacRuntimeBuildError("tarball 解压总体积异常")
                source = archive.extractfile(member)
                if source is None:
                    raise MacRuntimeBuildError(f"tarball 条目无法读取：{member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as handle:
                    shutil.copyfileobj(source, handle)
                mode = 0o755 if relative.startswith("bin/") else 0o644
                os.chmod(target, mode)
                extracted_files.add(relative)
    except tarfile.TarError as error:
        raise MacRuntimeBuildError(f"tarball 无法解析：{error}") from error

    # 物化保留的符号链接：目标是树内已解出的真实文件；目标被裁剪的链接直接丢弃。
    for relative, linkname in pending_symlinks:
        if linkname.startswith("/") or ".." in linkname.split("/"):
            raise MacRuntimeBuildError(f"tarball 符号链接指向树外：{relative} -> {linkname}")
        base = "/".join(relative.split("/")[:-1])
        target_relative = "/".join((base + "/" + linkname).split("/")) if base else linkname
        target_relative = _normalize_relative(target_relative)
        if target_relative not in extracted_files:
            continue
        link_path = staging.joinpath(*relative.split("/"))
        link_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(staging.joinpath(*target_relative.split("/")), link_path)
        mode = 0o755 if relative.startswith("bin/") else 0o644
        os.chmod(link_path, mode)
        extracted_files.add(relative)


def _normalize_relative(relative: str) -> str:
    parts: list[str] = []
    for part in relative.split("/"):
        if part == "" or part == ".":
            continue
        if part == "..":
            if not parts:
                return ""
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _make_macos_notices(
    materials: Mapping[str, Any],
    python_version: str,
    python_url: str,
    python_sha256: str,
    python_license_text: str,
) -> bytes:
    chunks = [
        "会议室预约系统 V2 macOS runtime 第三方组件与许可证\n",
        "本文件由经哈希验证的 python-build-standalone tarball 与 wheel METADATA/许可证文件生成。\n",
        f"\n===== Python / CPython {python_version} (python-build-standalone) =====\n",
        f"Source: {python_url}\nSHA-256: {python_sha256}\n\n",
        python_license_text.rstrip() + "\n",
    ]
    for normalized in sorted(materials):
        material = materials[normalized]
        chunks.extend(
            [
                f"\n===== {material.name} {material.version} =====\n",
                f"Source: {_project_url(material.metadata)}\n",
                f"Wheel: {material.path.name}\nSHA-256: {material.sha256}\n",
                f"License metadata: {_license_summary(material)}\n",
            ]
        )
        for relative, text in material.license_files:
            chunks.append(f"\n--- {relative} ---\n{text.rstrip()}\n")
    return "".join(chunks).encode("utf-8")


def build_macos_runtime(
    python_tarball: Path,
    wheelhouse: Path,
    lock_file: Path,
    output: Path,
    *,
    _test_fixture: bool = False,
) -> MacRuntimeBuildResult:
    python_tarball = Path(python_tarball).resolve(strict=True)
    wheelhouse = Path(wheelhouse).resolve(strict=True)
    lock_file = Path(lock_file).resolve(strict=True)
    output = Path(output).resolve()
    if output.exists():
        raise MacRuntimeBuildError(f"runtime 输出必须不存在，拒绝覆盖：{output}")
    output_parent = output.parent.resolve(strict=True)
    output = output_parent / output.name
    for source in (python_tarball, wheelhouse, lock_file):
        if output == source or source in output.parents or output in source.parents:
            raise MacRuntimeBuildError(f"runtime 输出不能与供应链输入嵌套：{source}")
    python_sha256 = sha256_file(python_tarball)
    if not _test_fixture and python_sha256 != MACOS_PYTHON_SOURCE_SHA256:
        raise MacRuntimeBuildError(
            "python-build-standalone tarball SHA-256 不符；"
            f"期望={MACOS_PYTHON_SOURCE_SHA256}，实际={python_sha256}"
        )
    lock_raw = lock_file.read_bytes()
    if lock_raw.startswith(b"\xef\xbb\xbf") or len(lock_raw) > 4 * 1024 * 1024:
        raise MacRuntimeBuildError("macOS runtime lock 带 BOM 或体积异常")
    try:
        lock = parse_hashed_requirements_lock(lock_raw.decode("utf-8"))
    except (UnicodeError, InstallerError) as error:
        raise MacRuntimeBuildError(f"macOS runtime lock 无法解析：{error}") from error
    materials = _load_wheels(wheelhouse, lock)

    staging = output_parent / f".{output.name}.building-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        _extract_tarball(python_tarball, staging)
        for required in (
            "bin/python3.13",
            "lib/python3.13",
            "lib/python3.13/LICENSE.txt",
        ):
            candidate = staging / required
            if not candidate.exists():
                raise MacRuntimeBuildError(f"runtime 缺少必需路径：{required}")
        for path in sorted(staging.rglob("*")):
            if path.is_symlink():
                raise MacRuntimeBuildError(f"runtime 树不允许残留符号链接：{path.name}")

        site_packages = staging / "lib" / "python3.13" / "site-packages"
        site_packages.mkdir(parents=True, exist_ok=True)
        occupied: dict[str, str] = {}
        for normalized in sorted(materials):
            material = materials[normalized]
            wheel, wheel_infos = _archive_files(material.path, "runtime wheel")
            try:
                _write_archive(
                    wheel,
                    wheel_infos,
                    site_packages,
                    reject_data_layout=True,
                    occupied=occupied,
                )
            finally:
                wheel.close()

        supply_chain = staging / SUPPLY_CHAIN_DIR
        supply_chain.mkdir()
        (supply_chain / RUNTIME_LOCK_NAME).write_bytes(lock_raw)
        sbom = _make_sbom(materials, MACOS_PYTHON_VERSION, python_sha256)
        notices = _make_macos_notices(
            materials,
            MACOS_PYTHON_VERSION,
            MACOS_PYTHON_SOURCE_URL,
            python_sha256,
            (staging / "lib" / "python3.13" / "LICENSE.txt").read_text(
                encoding="utf-8", errors="replace"
            ),
        )
        (supply_chain / SBOM_NAME).write_bytes(sbom)
        (supply_chain / NOTICES_NAME).write_bytes(notices)
        provenance_relative = f"{SUPPLY_CHAIN_DIR}/{PROVENANCE_NAME}"
        runtime_records = tuple(
            record
            for record in records_for_tree(staging)
            if record["path"] != provenance_relative
        )
        provenance = {
            "schema": 1,
            "python": {
                "implementation": "CPython",
                "version": MACOS_PYTHON_VERSION,
                "architecture": "arm64",
                "build_tag": MACOS_PYTHON_BUILD_TAG,
                "source_url": MACOS_PYTHON_SOURCE_URL,
                "source_sha256": python_sha256,
            },
            "components": {
                material.name: material.version
                for _, material in sorted(materials.items())
            },
            "artifacts": {
                "requirements_lock": {
                    "file": f"{SUPPLY_CHAIN_DIR}/{RUNTIME_LOCK_NAME}",
                    "sha256": sha256_bytes(lock_raw),
                },
                "sbom": {
                    "file": f"{SUPPLY_CHAIN_DIR}/{SBOM_NAME}",
                    "sha256": sha256_bytes(sbom),
                },
                "third_party_notices": {
                    "file": f"{SUPPLY_CHAIN_DIR}/{NOTICES_NAME}",
                    "sha256": sha256_bytes(notices),
                },
            },
            "runtime": {
                "tree_sha256": tree_digest(runtime_records),
                "files": list(runtime_records),
            },
        }
        (staging / provenance_relative).write_bytes(json_bytes(provenance))
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return MacRuntimeBuildResult(output, python_sha256, len(materials))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="组装 V2 冻结 macOS arm64 runtime")
    parser.add_argument("--python-tarball", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = build_macos_runtime(
            arguments.python_tarball,
            arguments.wheelhouse,
            arguments.lock_file,
            arguments.output,
        )
    except (MacRuntimeBuildError, InstallerError, OSError, ValueError) as error:
        print(f"V2 macOS runtime 组装失败：{error}")
        return 1
    print(f"冻结 macOS runtime：{result.runtime_root}")
    print(f"CPython 来源 SHA-256：{result.python_source_sha256}")
    print(f"哈希锁定组件数：{result.component_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

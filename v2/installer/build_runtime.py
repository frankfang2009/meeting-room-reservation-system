#!/usr/bin/env python3
"""从已核验 CPython embeddable ZIP 与哈希 wheelhouse 组装冻结 runtime。"""

from __future__ import annotations

import argparse
import email.policy
import os
import shutil
import stat
import uuid
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import quote

try:
    from .installer_core import (
        APPROVED_PYTHON_SOURCE_SHA256,
        APPROVED_PYTHON_SOURCE_URL,
        APPROVED_PYTHON_VERSION,
        RUNTIME_LOCK_FILE,
        RUNTIME_NOTICES_FILE,
        RUNTIME_PROVENANCE_FILE,
        RUNTIME_PTH_FILE,
        RUNTIME_PTH_LINES,
        RUNTIME_SBOM_FILE,
        InstallerError,
        assert_plain_file,
        assert_plain_tree,
        is_reparse_or_link,
        json_bytes,
        normalize_distribution_name,
        parse_hashed_requirements_lock,
        records_for_tree,
        safe_relative_path,
        sha256_bytes,
        sha256_file,
        tree_digest,
        validate_runtime_supply_chain,
    )
except ImportError:
    from installer_core import (  # type: ignore
        APPROVED_PYTHON_SOURCE_SHA256,
        APPROVED_PYTHON_SOURCE_URL,
        APPROVED_PYTHON_VERSION,
        RUNTIME_LOCK_FILE,
        RUNTIME_NOTICES_FILE,
        RUNTIME_PROVENANCE_FILE,
        RUNTIME_PTH_FILE,
        RUNTIME_PTH_LINES,
        RUNTIME_SBOM_FILE,
        InstallerError,
        assert_plain_file,
        assert_plain_tree,
        is_reparse_or_link,
        json_bytes,
        normalize_distribution_name,
        parse_hashed_requirements_lock,
        records_for_tree,
        safe_relative_path,
        sha256_bytes,
        sha256_file,
        tree_digest,
        validate_runtime_supply_chain,
    )


PYTHON_VERSION = APPROVED_PYTHON_VERSION
PYTHON_SOURCE_URL = APPROVED_PYTHON_SOURCE_URL
PYTHON_SOURCE_SHA256 = APPROVED_PYTHON_SOURCE_SHA256
DEFAULT_LOCK = (
    Path(__file__).resolve().parent.parent / "backend" / "requirements-win-amd64.lock"
)
MAX_ARCHIVE_FILE = 128 * 1024 * 1024
MAX_ARCHIVE_TOTAL = 1024 * 1024 * 1024


class RuntimeBuildError(InstallerError):
    """runtime 来源或组装结果不能证明与冻结供应链一致。"""


@dataclass(frozen=True)
class WheelMaterial:
    path: Path
    name: str
    version: str
    sha256: str
    metadata: Any
    license_files: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RuntimeBuildResult:
    runtime_root: Path
    python_source_sha256: str
    component_count: int


def _read_lock(path: Path) -> tuple[bytes, Mapping[str, Mapping[str, str]]]:
    assert_plain_file(path, "Windows runtime lock")
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf") or len(raw) > 4 * 1024 * 1024:
        raise RuntimeBuildError("Windows runtime lock 带 BOM 或体积异常")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeBuildError("Windows runtime lock 不是 UTF-8") from error
    return raw, parse_hashed_requirements_lock(text)


def _archive_files(path: Path, description: str) -> tuple[zipfile.ZipFile, tuple[zipfile.ZipInfo, ...]]:
    assert_plain_file(path, description)
    try:
        archive = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile) as error:
        raise RuntimeBuildError(f"{description}不是有效 ZIP：{path}") from error
    infos: list[zipfile.ZipInfo] = []
    seen: dict[str, str] = {}
    total = 0
    try:
        for info in archive.infolist():
            relative = info.filename.rstrip("/")
            if not relative:
                continue
            safe_relative_path(relative)
            folded = relative.casefold()
            if folded in seen:
                raise RuntimeBuildError(
                    f"{description}含 Windows 重复路径：{seen[folded]} / {relative}"
                )
            seen[folded] = relative
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_IFMT(mode) == stat.S_IFLNK or info.flag_bits & 0x1:
                raise RuntimeBuildError(f"{description}含链接或加密条目：{relative}")
            if info.file_size > MAX_ARCHIVE_FILE:
                raise RuntimeBuildError(f"{description}单文件体积异常：{relative}")
            total += info.file_size
            if total > MAX_ARCHIVE_TOTAL:
                raise RuntimeBuildError(f"{description}解压总体积异常")
            infos.append(info)
        if archive.testzip() is not None:
            raise RuntimeBuildError(f"{description}CRC 校验失败")
    except BaseException:
        archive.close()
        raise
    return archive, tuple(infos)


def _write_archive(
    archive: zipfile.ZipFile,
    infos: Sequence[zipfile.ZipInfo],
    destination: Path,
    *,
    reject_data_layout: bool = False,
    occupied: Optional[dict[str, str]] = None,
) -> None:
    occupied = occupied if occupied is not None else {}
    for info in infos:
        relative = info.filename.rstrip("/")
        if not relative:
            continue
        parts = safe_relative_path(relative)
        if reject_data_layout and any(part.casefold().endswith(".data") for part in parts):
            raise RuntimeBuildError(f"wheel 使用未支持的 .data 安装布局：{relative}")
        target = destination.joinpath(*parts)
        if info.is_dir() or info.filename.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue
        folded = relative.casefold()
        if folded in occupied or target.exists():
            raise RuntimeBuildError(
                f"runtime wheel 文件冲突：{occupied.get(folded, relative)} / {relative}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        content = archive.read(info)
        with target.open("xb") as handle:
            handle.write(content)
        occupied[folded] = relative


def _metadata_fields(metadata: Any) -> tuple[str, str]:
    name = str(metadata.get("Name", "")).strip()
    version = str(metadata.get("Version", "")).strip()
    if not name or not version:
        raise RuntimeBuildError("wheel METADATA 缺少 Name/Version")
    return name, version


def _wheel_material(path: Path) -> WheelMaterial:
    archive, infos = _archive_files(path, "runtime wheel")
    try:
        metadata_infos = [
            info
            for info in infos
            if info.filename.casefold().endswith(".dist-info/metadata") and not info.is_dir()
        ]
        if len(metadata_infos) != 1:
            raise RuntimeBuildError(f"wheel 必须且只能含一份 dist-info/METADATA：{path.name}")
        raw_metadata = archive.read(metadata_infos[0])
        metadata = BytesParser(policy=email.policy.default).parsebytes(raw_metadata)
        name, version = _metadata_fields(metadata)
        dist_root = metadata_infos[0].filename.rsplit("/", 1)[0] + "/"
        license_files: list[tuple[str, str]] = []
        for info in infos:
            if info.is_dir() or not info.filename.startswith(dist_root):
                continue
            basename = info.filename.rsplit("/", 1)[-1].casefold()
            if not (
                "/licenses/" in info.filename.casefold()
                or basename.startswith(("license", "copying", "notice"))
            ):
                continue
            raw = archive.read(info)
            if len(raw) > 2 * 1024 * 1024:
                raise RuntimeBuildError(f"wheel 许可证文件体积异常：{info.filename}")
            license_files.append((info.filename, raw.decode("utf-8", errors="replace")))
        license_evidence = [
            str(metadata.get("License-Expression", "")).strip(),
            str(metadata.get("License", "")).strip(),
            *(
                str(value)
                for value in metadata.get_all("Classifier", [])
                if str(value).startswith("License ::")
            ),
        ]
        if not license_files and not any(license_evidence):
            raise RuntimeBuildError(f"wheel 缺少许可证材料：{name} {version}")
        return WheelMaterial(
            path=path,
            name=name,
            version=version,
            sha256=sha256_file(path),
            metadata=metadata,
            license_files=tuple(sorted(license_files)),
        )
    finally:
        archive.close()


def _load_wheels(
    wheelhouse: Path, lock: Mapping[str, Mapping[str, str]]
) -> Mapping[str, WheelMaterial]:
    assert_plain_tree(wheelhouse, "Windows runtime wheelhouse")
    wheel_paths = sorted(wheelhouse.glob("*.whl"), key=lambda path: path.name.casefold())
    if not wheel_paths:
        raise RuntimeBuildError("Windows runtime wheelhouse 为空")
    materials: dict[str, WheelMaterial] = {}
    for path in wheel_paths:
        material = _wheel_material(path)
        normalized = normalize_distribution_name(material.name)
        if normalized in materials:
            raise RuntimeBuildError(f"wheelhouse 组件重复：{material.name}")
        expected = lock.get(normalized)
        if expected is None:
            raise RuntimeBuildError(f"wheelhouse 含 lock 之外的组件：{material.name}")
        if material.version != expected["version"] or material.sha256 != expected["sha256"]:
            raise RuntimeBuildError(f"wheel 版本或 SHA-256 与 lock 不一致：{path.name}")
        materials[normalized] = material
    if set(materials) != set(lock):
        raise RuntimeBuildError(
            f"wheelhouse 缺少 lock 组件：{sorted(set(lock) - set(materials))}"
        )
    return materials


def _project_url(metadata: Any) -> str:
    for value in metadata.get_all("Project-URL", []):
        parts = str(value).split(",", 1)
        if len(parts) == 2 and parts[1].strip().startswith("https://"):
            return parts[1].strip()
    home = str(metadata.get("Home-page", "")).strip()
    return home if home.startswith("https://") else "https://pypi.org/"


def _license_summary(material: WheelMaterial) -> str:
    values: list[str] = []
    for key in ("License-Expression", "License"):
        value = str(material.metadata.get(key, "")).strip()
        if value and value.casefold() != "unknown":
            values.append(value)
    values.extend(
        str(value)[len("License ::") :].strip()
        for value in material.metadata.get_all("Classifier", [])
        if str(value).startswith("License ::")
    )
    return "; ".join(dict.fromkeys(values)) or "See embedded license file"


def _make_sbom(
    materials: Mapping[str, WheelMaterial], python_version: str, python_sha256: str
) -> bytes:
    components: list[Mapping[str, Any]] = [
        {
            "type": "framework",
            "name": "Python",
            "version": python_version,
            "purl": f"pkg:generic/cpython@{python_version}",
            "hashes": [{"alg": "SHA-256", "content": python_sha256}],
        }
    ]
    for normalized in sorted(materials):
        material = materials[normalized]
        components.append(
            {
                "type": "library",
                "name": material.name,
                "version": material.version,
                "purl": f"pkg:pypi/{quote(normalized)}@{quote(material.version)}",
                "hashes": [{"alg": "SHA-256", "content": material.sha256}],
                "licenses": [{"license": {"name": _license_summary(material)}}],
                "externalReferences": [
                    {"type": "website", "url": _project_url(material.metadata)}
                ],
            }
        )
    return json_bytes(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "serialNumber": "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, python_sha256)),
            "version": 1,
            "metadata": {"component": {"type": "application", "name": "会议室预约系统 V2 runtime"}},
            "components": components,
        }
    )


def _make_notices(
    staging: Path,
    materials: Mapping[str, WheelMaterial],
    python_version: str,
    python_url: str,
    python_sha256: str,
) -> bytes:
    python_license = staging / "LICENSE.txt"
    assert_plain_file(python_license, "CPython LICENSE.txt")
    license_text = python_license.read_text(encoding="utf-8", errors="replace")
    chunks = [
        "会议室预约系统 V2 runtime 第三方组件与许可证\n",
        "本文件由经哈希验证的 CPython embeddable ZIP 与 wheel METADATA/许可证文件生成。\n",
        f"\n===== Python / CPython {python_version} =====\n",
        f"Source: {python_url}\nSHA-256: {python_sha256}\n\n",
        license_text.rstrip() + "\n",
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


def build_runtime(
    python_embed_zip: Path,
    wheelhouse: Path,
    lock_file: Path,
    output: Path,
    *,
    _python_version: str = PYTHON_VERSION,
    _python_source_url: str = PYTHON_SOURCE_URL,
    _expected_python_sha256: str = PYTHON_SOURCE_SHA256,
    _test_fixture: bool = False,
) -> RuntimeBuildResult:
    python_embed_zip = Path(python_embed_zip).resolve(strict=True)
    wheelhouse = Path(wheelhouse).resolve(strict=True)
    lock_file = Path(lock_file).resolve(strict=True)
    output = Path(output).resolve()
    assert_plain_file(python_embed_zip, "CPython embeddable ZIP")
    assert_plain_tree(wheelhouse, "Windows runtime wheelhouse")
    assert_plain_file(lock_file, "Windows runtime lock")
    if output.exists() or is_reparse_or_link(output):
        raise RuntimeBuildError(f"runtime 输出必须不存在，拒绝覆盖：{output}")
    output_parent = output.parent.resolve(strict=True)
    output = output_parent / output.name
    for source in (python_embed_zip, wheelhouse, lock_file):
        if source == output or output in source.parents or source in output.parents:
            raise RuntimeBuildError(f"runtime 输出不能与供应链输入嵌套：{source}")
    python_sha256 = sha256_file(python_embed_zip)
    if not _test_fixture and (
        _python_version != PYTHON_VERSION
        or _python_source_url != PYTHON_SOURCE_URL
        or _expected_python_sha256 != PYTHON_SOURCE_SHA256
    ):
        raise RuntimeBuildError("正式 runtime 构建不接受替换 CPython 身份")
    if python_sha256 != _expected_python_sha256:
        raise RuntimeBuildError(
            "CPython embeddable ZIP SHA-256 不符；"
            f"期望={_expected_python_sha256}，实际={python_sha256}"
        )
    lock_raw, lock = _read_lock(lock_file)
    materials = _load_wheels(wheelhouse, lock)

    staging = output_parent / f".{output.name}.building-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        archive, infos = _archive_files(python_embed_zip, "CPython embeddable ZIP")
        try:
            _write_archive(archive, infos, staging)
        finally:
            archive.close()
        for required in ("python.exe", "pythonw.exe", "python313.dll", "python313.zip", "LICENSE.txt"):
            assert_plain_file(staging / required, f"CPython embeddable {required}")
        (staging / RUNTIME_PTH_FILE).write_bytes(
            ("\r\n".join(RUNTIME_PTH_LINES) + "\r\n").encode("ascii")
        )
        site_packages = staging / "Lib" / "site-packages"
        site_packages.mkdir(parents=True)
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

        supply_chain = staging / "supply-chain"
        supply_chain.mkdir()
        lock_path = staging.joinpath(*RUNTIME_LOCK_FILE.split("/"))
        lock_path.write_bytes(lock_raw)
        sbom = _make_sbom(materials, _python_version, python_sha256)
        notices = _make_notices(
            staging,
            materials,
            _python_version,
            _python_source_url,
            python_sha256,
        )
        sbom_path = staging.joinpath(*RUNTIME_SBOM_FILE.split("/"))
        notices_path = staging.joinpath(*RUNTIME_NOTICES_FILE.split("/"))
        sbom_path.write_bytes(sbom)
        notices_path.write_bytes(notices)
        runtime_records = tuple(
            record
            for record in records_for_tree(staging)
            if record["path"] != RUNTIME_PROVENANCE_FILE
        )
        provenance = {
            "schema": 1,
            "python": {
                "implementation": "CPython",
                "version": _python_version,
                "architecture": "amd64",
                "source_url": _python_source_url,
                "source_sha256": python_sha256,
            },
            "components": {
                material.name: material.version
                for _, material in sorted(materials.items())
            },
            "artifacts": {
                "requirements_lock": {
                    "file": RUNTIME_LOCK_FILE,
                    "sha256": sha256_bytes(lock_raw),
                },
                "sbom": {"file": RUNTIME_SBOM_FILE, "sha256": sha256_bytes(sbom)},
                "third_party_notices": {
                    "file": RUNTIME_NOTICES_FILE,
                    "sha256": sha256_bytes(notices),
                },
            },
            "runtime": {
                "tree_sha256": tree_digest(runtime_records),
                "files": list(runtime_records),
            },
        }
        staging.joinpath(*RUNTIME_PROVENANCE_FILE.split("/")).write_bytes(
            json_bytes(provenance)
        )
        validate_runtime_supply_chain(
            staging,
            _test_fixture=_test_fixture,
            _building_approved_runtime=not _test_fixture,
        )
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return RuntimeBuildResult(output, python_sha256, len(materials))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="组装 V2 冻结 Windows AMD64 runtime")
    parser.add_argument("--python-embed-zip", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = build_runtime(
            arguments.python_embed_zip,
            arguments.wheelhouse,
            arguments.lock_file,
            arguments.output,
        )
    except (RuntimeBuildError, InstallerError, OSError, ValueError) as error:
        print(f"V2 Windows runtime 组装失败：{error}")
        return 1
    print(f"冻结 runtime：{result.runtime_root}")
    print(f"CPython 来源 SHA-256：{result.python_source_sha256}")
    print(f"哈希锁定组件数：{result.component_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

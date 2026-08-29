#!/usr/bin/env python3
"""从同一已组装 payload 和冻结 runtime 制作确定性 V2 累计更新包。"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from .build_package import (
        PackageBuildError,
        _crlf_bytes,
        _deterministic_zip,
        _lf_bytes,
        _records_from_files,
        _runtime_files,
    )
    from .frontend_supply_chain import (
        FRONTEND_COMPONENTS_FILE,
        FrontendSupplyChainError,
        load_frontend_component_evidence,
        make_artifact_notices,
        make_artifact_sbom,
    )
    from .installer_core import (
        PRODUCT_GENERATION,
        RUNTIME_LOCK_FILE,
        RUNTIME_NOTICES_FILE,
        RUNTIME_PROVENANCE_FILE,
        RUNTIME_SBOM_FILE,
        VERSION,
        assert_plain_tree,
        json_bytes,
        records_for_tree,
        sha256_bytes,
        sha256_file,
        tree_digest,
    )
    from .update_core import SUPPORTED_SOURCE_VERSIONS, assert_update_payload_safe
except ImportError:
    from build_package import (  # type: ignore
        PackageBuildError,
        _crlf_bytes,
        _deterministic_zip,
        _lf_bytes,
        _records_from_files,
        _runtime_files,
    )
    from frontend_supply_chain import (  # type: ignore
        FRONTEND_COMPONENTS_FILE,
        FrontendSupplyChainError,
        load_frontend_component_evidence,
        make_artifact_notices,
        make_artifact_sbom,
    )
    from installer_core import (  # type: ignore
        PRODUCT_GENERATION,
        RUNTIME_LOCK_FILE,
        RUNTIME_NOTICES_FILE,
        RUNTIME_PROVENANCE_FILE,
        RUNTIME_SBOM_FILE,
        VERSION,
        assert_plain_tree,
        json_bytes,
        records_for_tree,
        sha256_bytes,
        sha256_file,
        tree_digest,
    )
    from update_core import SUPPORTED_SOURCE_VERSIONS, assert_update_payload_safe  # type: ignore


TOOL_DIR = Path(__file__).resolve().parent
LAUNCHER_SOURCE = TOOL_DIR / f"升级到V{VERSION}.bat"
GUIDE_SOURCE = TOOL_DIR / "升级说明.txt"
ENTRY_SOURCE = TOOL_DIR / "update.py"
UPDATE_CORE_SOURCE = TOOL_DIR / "update_core.py"
INSTALLER_CORE_SOURCE = TOOL_DIR / "installer_core.py"

ARTIFACT_NAME = f"会议室预约系统-V{VERSION}-累计升级包.zip"
DELIVERED_LAUNCHER = f"升级到V{VERSION}.bat"
DELIVERED_GUIDE = "升级说明.txt"
DELIVERED_TOOL = "_V2更新工具"
PAYLOAD_NAME = "payload-update.zip"


@dataclass(frozen=True)
class UpdateBuildResult:
    artifact_path: Path
    sha256_path: Path
    manifest_path: Path
    sbom_path: Path
    notices_path: Path
    runtime_provenance_path: Path
    artifact_sha256: str
    payload_sha256: str


def _payload_files(payload_root: Path, runtime_files: Mapping[str, bytes]) -> tuple[
    dict[str, bytes], tuple[Mapping[str, Any], ...]
]:
    files = {
        str(record["path"]): payload_root.joinpath(
            *str(record["path"]).split("/")
        ).read_bytes()
        for record in records_for_tree(payload_root)
    }
    if any(relative.startswith("_程序文件/runtime/") for relative in files):
        raise PackageBuildError("组装 payload 不应自带 runtime")
    for relative, content in runtime_files.items():
        files[f"_程序文件/runtime/{relative}"] = content
    records = _records_from_files(files)
    assert_update_payload_safe(records)
    required = {
        "_程序文件/app/service.py",
        "_程序文件/app/static/index.html",
        "_程序文件/app/static/help/index.html",
        "_程序文件/runtime/python.exe",
        "_程序文件/runtime/pythonw.exe",
        FRONTEND_COMPONENTS_FILE,
    }
    if not required.issubset(files):
        raise PackageBuildError(f"V2 累计更新 payload 缺少：{sorted(required - set(files))}")
    return files, records


def _verify_outer(content: bytes, expected: Mapping[str, bytes]) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content), "r")
    except (OSError, zipfile.BadZipFile) as error:
        raise PackageBuildError("V2 累计更新外层不是有效 ZIP") from error
    with archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise PackageBuildError("V2 累计更新外层文件集不一致")
        for name, value in expected.items():
            if archive.read(name) != value:
                raise PackageBuildError(f"V2 累计更新外层文件不一致：{name}")


def build_update_package(
    payload_root: Path,
    runtime_root: Path,
    output: Path,
    *,
    _test_fixture: bool = False,
) -> UpdateBuildResult:
    payload_root = Path(payload_root).resolve(strict=True)
    runtime_root = Path(runtime_root).resolve(strict=True)
    output = Path(output).resolve()
    assert_plain_tree(payload_root, "V2 已组装 payload")
    assert_plain_tree(runtime_root, "V2 冻结 runtime")
    if output.name != ARTIFACT_NAME:
        raise PackageBuildError(f"V2 累计更新 ZIP 必须命名为：{ARTIFACT_NAME}")
    sidecars = {
        "sha": output.with_name(output.name + ".sha256"),
        "manifest": output.with_name(output.name + ".manifest.json"),
        "sbom": output.with_name(output.name + ".sbom.cdx.json"),
        "notices": output.with_name(output.name + ".THIRD-PARTY-NOTICES.txt"),
        "provenance": output.with_name(output.name + ".runtime-provenance.json"),
    }
    for path in (output, *sidecars.values()):
        if path.exists():
            raise PackageBuildError(f"更新包输出已存在，拒绝覆盖：{path}")
    runtime_files, runtime_records = _runtime_files(
        runtime_root, _test_fixture=_test_fixture
    )
    payload_files, payload_records = _payload_files(payload_root, runtime_files)
    if payload_files["_程序文件/app/requirements-win-amd64.lock"] != runtime_files[RUNTIME_LOCK_FILE]:
        raise PackageBuildError("更新程序依赖 lock 与冻结 runtime 不一致")
    payload_zip = _deterministic_zip(payload_files)
    frontend_content = payload_files[FRONTEND_COMPONENTS_FILE]
    try:
        load_frontend_component_evidence(frontend_content)
        sbom_content = make_artifact_sbom(
            runtime_files[RUNTIME_SBOM_FILE], frontend_content, payload_records
        )
        notices_content = make_artifact_notices(
            runtime_files[RUNTIME_NOTICES_FILE], frontend_content
        )
    except FrontendSupplyChainError as error:
        raise PackageBuildError("更新包前端供应链证据失败") from error
    provenance_content = runtime_files[RUNTIME_PROVENANCE_FILE]
    tool_files = {
        "app/update.py": _lf_bytes(ENTRY_SOURCE, "V2 更新入口"),
        "app/update_core.py": _lf_bytes(UPDATE_CORE_SOURCE, "V2 更新事务核心"),
        "app/installer_core.py": _lf_bytes(INSTALLER_CORE_SOURCE, "V2 安装共同核心"),
    }
    tool_records = _records_from_files(tool_files)
    supply_chain = {
        "frontend_components_sha256": sha256_bytes(frontend_content),
        "runtime_provenance_sha256": sha256_bytes(provenance_content),
        "sbom_sha256": sha256_bytes(sbom_content),
        "third_party_notices_sha256": sha256_bytes(notices_content),
    }
    manifest = {
        "schema": 1,
        "kind": "v2-cumulative-update",
        "product_generation": PRODUCT_GENERATION,
        "release": f"V{VERSION}",
        "version": VERSION,
        "supported_source_versions": sorted(SUPPORTED_SOURCE_VERSIONS),
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
        "supply_chain": supply_chain,
        "acceptance": {
            "status": "candidate",
            "formal_external_release_allowed": False,
        },
    }
    outer = {
        DELIVERED_LAUNCHER: _crlf_bytes(LAUNCHER_SOURCE, "V2 累计更新 BAT"),
        DELIVERED_GUIDE: _crlf_bytes(GUIDE_SOURCE, "V2 累计更新说明"),
        f"{DELIVERED_TOOL}/manifest.json": json_bytes(manifest),
        f"{DELIVERED_TOOL}/{PAYLOAD_NAME}": payload_zip,
    }
    for relative, content in tool_files.items():
        outer[f"{DELIVERED_TOOL}/{relative}"] = content
    for relative, content in runtime_files.items():
        outer[f"{DELIVERED_TOOL}/runtime/{relative}"] = content
    artifact = _deterministic_zip(outer)
    _verify_outer(artifact, outer)
    artifact_sha = sha256_bytes(artifact)
    external = {
        "schema": 1,
        "kind": "v2-cumulative-update",
        "release": f"V{VERSION}",
        "version": VERSION,
        "supported_source_versions": sorted(SUPPORTED_SOURCE_VERSIONS),
        "status": "windows_acceptance_candidate_only",
        "formal_external_release_allowed": False,
        "artifact": {"file": output.name, "size": len(artifact), "sha256": artifact_sha},
        "payload": {
            "file_count": len(payload_records),
            "sha256": sha256_bytes(payload_zip),
            "tree_sha256": tree_digest(payload_records),
        },
        "runtime": {"file_count": len(runtime_records), "tree_sha256": tree_digest(runtime_records)},
        "tool": {"file_count": len(tool_records), "tree_sha256": tree_digest(tool_records)},
        "supply_chain": supply_chain,
    }
    contents = {
        output: artifact,
        sidecars["sha"]: f"{artifact_sha}  {output.name}\n".encode("utf-8"),
        sidecars["manifest"]: json_bytes(external),
        sidecars["sbom"]: sbom_content,
        sidecars["notices"]: notices_content,
        sidecars["provenance"]: provenance_content,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".v2-update-package-", dir=str(output.parent)))
    delivered: list[Path] = []
    try:
        for final, content in contents.items():
            staged = temporary / final.name
            staged.write_bytes(content)
            if final.exists():
                raise PackageBuildError(f"更新包交付前输出已存在：{final}")
            os.link(staged, final)
            delivered.append(final)
        if sha256_file(output) != artifact_sha:
            raise PackageBuildError("更新包落盘后 SHA-256 不一致")
        _verify_outer(output.read_bytes(), outer)
    except BaseException:
        for path in delivered:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return UpdateBuildResult(
        artifact_path=output,
        sha256_path=sidecars["sha"],
        manifest_path=sidecars["manifest"],
        sbom_path=sidecars["sbom"],
        notices_path=sidecars["notices"],
        runtime_provenance_path=sidecars["provenance"],
        artifact_sha256=artifact_sha,
        payload_sha256=sha256_bytes(payload_zip),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"制作 V{VERSION} 累计升级候选包")
    parser.add_argument("--payload-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_update_package(args.payload_root, args.runtime_root, args.output)
    except (PackageBuildError, OSError, ValueError) as error:
        print(f"V2 累计更新包构建失败：{error}")
        return 1
    print(f"V2 累计更新包已构建：{result.artifact_path}")
    print(f"SHA-256：{result.artifact_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

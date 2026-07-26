#!/usr/bin/env python3
"""从唯一源码组装会议室预约系统的版本化发布暂存。

本工具不修改源码、历史候选物或正式归档。它从“源代码工作区”和稳定的
“发布骨架”组装完整累计负载，复用既有升级包生成器，并从同一份负载生成
Windows 候选目录和可核验的发布清单。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

import 制作升级包 as package_builder


TOOL_DIR = Path(__file__).resolve().parent
DEVELOPMENT_DIR = TOOL_DIR.parent
REPOSITORY_ROOT = DEVELOPMENT_DIR.parent
SOURCE_DIR = DEVELOPMENT_DIR / "源代码工作区"
SKELETON_DIR = TOOL_DIR / "发布骨架"
DEFAULT_RELEASE_ROOT = DEVELOPMENT_DIR / "发布暂存"
DEFAULT_RUNTIME_SOURCE = (
    DEVELOPMENT_DIR
    / "Windows部署目录-V1.0.1-待实机验收"
    / "_程序文件"
    / "runtime"
)
FROZEN_V101_PACKAGE = (
    TOOL_DIR / "输出-待实机验收" / "升级到V1.0.1.bat"
)
FROZEN_V101_SHA256 = (
    "cd0d52b9ffb5d2864e7ad98d8969b86376d8577391399c30295d0722d34848cd"
)
FROZEN_RUNTIME_TREE_SHA256 = (
    "b778df06bfc98d699c2aa4c68d4f146f8c6c3d55a0ce1cc7b6811251ed5aad14"
)

PROGRAM_FILES = (
    "app.py",
    "server.py",
    "backup.py",
    "migrate_check.py",
    "requirements.txt",
    "版本.txt",
)
PROGRAM_TREES = ("static", "templates")
RESOURCE_FILE_SUFFIXES = {
    "static": frozenset(
        {
            ".css",
            ".gif",
            ".ico",
            ".jpeg",
            ".jpg",
            ".js",
            ".json",
            ".otf",
            ".png",
            ".svg",
            ".ttf",
            ".webp",
            ".woff",
            ".woff2",
        }
    ),
    "templates": frozenset({".html", ".jinja", ".jinja2"}),
}
FORBIDDEN_RESOURCE_PARTS = frozenset(
    {"backups", "data", "logs", "test", "tests"}
)
TOP_LEVEL_FILES = tuple(sorted(package_builder.TOP_LEVEL_FILES))
CANDIDATE_INFO_FILES = ("版本与校验信息.txt",)
CHECKLIST_TEMPLATE = "给网管的首次验收清单模板.txt"
HISTORICAL_ROOTS = (
    REPOSITORY_ROOT / "01_版本归档",
    DEVELOPMENT_DIR / "Windows部署目录-V1.0.0",
    DEVELOPMENT_DIR / "Windows部署目录-V1.0.1-待实机验收",
    TOOL_DIR / "负载示例",
)


class ReleasePreparationError(RuntimeError):
    """发布输入、输出或一致性校验不符合约定。"""


@dataclass(frozen=True)
class ReleaseResult:
    version: str
    release_dir: Path
    payload_dir: Path
    candidate_dir: Optional[Path]
    package_path: Path
    manifest_path: Path
    package_sha256: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_records(records: Iterable[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for relative, file_hash in sorted(records):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _read_release_version(source_dir: Path) -> str:
    version_path = source_dir / "版本.txt"
    try:
        raw = version_path.read_bytes()
    except OSError as error:
        raise ReleasePreparationError(
            f"无法读取唯一源码版本文件：{version_path}"
        ) from error
    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
        raise ReleasePreparationError("版本.txt 必须是 UTF-8 无 BOM、LF 结尾")
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ReleasePreparationError("版本.txt 不是合法 UTF-8") from error
    if value != value.strip() + "\n":
        raise ReleasePreparationError("版本.txt 只能包含版本号和一个末尾换行")
    version = value.strip()
    package_builder.validate_version(version)
    if package_builder.validate_version(version) <= (1, 0, 1):
        raise ReleasePreparationError(
            "准备发布工具只允许生成高于 V1.0.1 的新版本"
        )
    return version


def _read_schema_version(app_path: Path) -> int:
    try:
        tree = ast.parse(app_path.read_text(encoding="utf-8"), filename=str(app_path))
    except (OSError, SyntaxError, UnicodeError) as error:
        raise ReleasePreparationError(f"无法解析 SCHEMA_VERSION：{app_path}") from error
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            names = [
                target.id
                for target in statement.targets
                if isinstance(target, ast.Name)
            ]
            if "SCHEMA_VERSION" in names and isinstance(
                statement.value, ast.Constant
            ):
                value = statement.value.value
                if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                    return value
    raise ReleasePreparationError("app.py 缺少可静态读取的 SCHEMA_VERSION 整数")


def _assert_plain_file(path: Path, description: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ReleasePreparationError(f"{description}必须是普通文件：{path}")


def _assert_plain_tree(root: Path, description: str) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ReleasePreparationError(f"{description}必须是普通目录：{root}")
    for entry in root.rglob("*"):
        if entry.is_symlink():
            raise ReleasePreparationError(f"{description}禁止符号链接：{entry}")
        if not entry.is_dir() and not entry.is_file():
            raise ReleasePreparationError(f"{description}出现特殊文件：{entry}")


def _assert_output_path_is_safe(
    path: Path, protected_roots: Sequence[Path], description: str
) -> None:
    for protected in protected_roots:
        protected = protected.resolve()
        if path == protected or protected in path.parents:
            raise ReleasePreparationError(
                f"{description}不能位于受保护的源码或历史目录：{protected}"
            )


def _copy_tree_without_junk(
    source: Path,
    destination: Path,
    allowed_suffixes: frozenset[str],
) -> None:
    _assert_plain_tree(source, "源码资源目录")
    destination.mkdir(parents=True)
    copied = 0
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        folded_parts = tuple(part.casefold() for part in relative.parts)
        if "__pycache__" in folded_parts or path.name.casefold() == ".ds_store":
            continue
        if any(
            part.startswith(".") or part in FORBIDDEN_RESOURCE_PARTS
            for part in folded_parts
        ):
            raise ReleasePreparationError(
                f"源码资源目录包含禁止发布的路径：{path}"
            )
        if path.is_dir():
            (destination / relative).mkdir(exist_ok=True)
            continue
        if path.suffix.casefold() not in allowed_suffixes:
            raise ReleasePreparationError(
                f"源码资源目录包含未允许的文件类型：{path}"
            )
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    if copied == 0:
        raise ReleasePreparationError(f"源码资源目录为空：{source}")


def _copy_release_inputs(
    source_dir: Path, skeleton_dir: Path, payload_dir: Path
) -> None:
    payload_program = payload_dir / "_程序文件"
    payload_program.mkdir(parents=True)

    for name in TOP_LEVEL_FILES:
        source = skeleton_dir / name
        _assert_plain_file(source, "发布骨架文件")
        shutil.copy2(source, payload_dir / name)

    for name in PROGRAM_FILES:
        source = source_dir / name
        _assert_plain_file(source, "唯一源码文件")
        shutil.copy2(source, payload_program / name)

    for name in PROGRAM_TREES:
        _copy_tree_without_junk(
            source_dir / name,
            payload_program / name,
            RESOURCE_FILE_SUFFIXES[name],
        )


def _copy_runtime(runtime_source: Path, candidate_program: Path) -> str:
    _assert_plain_tree(runtime_source, "Windows runtime")
    destination = candidate_program / "runtime"
    shutil.copytree(runtime_source, destination)
    records = [
        (
            path.relative_to(destination).as_posix(),
            _sha256_file(path),
        )
        for path in destination.rglob("*")
        if path.is_file()
    ]
    if not records:
        raise ReleasePreparationError("Windows runtime 为空")
    return _sha256_records(records)


def _payload_file_records(
    payload_dir: Path, file_paths: Sequence[str]
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for relative in sorted(file_paths):
        path = payload_dir.joinpath(*relative.split("/"))
        records.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return records


def _verify_candidate_matches_payload(
    payload_dir: Path, candidate_dir: Path, file_paths: Sequence[str]
) -> None:
    for relative in file_paths:
        payload_path = payload_dir.joinpath(*relative.split("/"))
        candidate_path = candidate_dir.joinpath(*relative.split("/"))
        if not candidate_path.is_file():
            raise ReleasePreparationError(
                f"Windows 候选目录缺少受管文件：{relative}"
            )
        if _sha256_file(payload_path) != _sha256_file(candidate_path):
            raise ReleasePreparationError(
                f"Windows 候选目录与累计负载不一致：{relative}"
            )


def _assert_frozen_v101_unchanged() -> None:
    _assert_plain_file(FROZEN_V101_PACKAGE, "V1.0.1 冻结候选包")
    actual = _sha256_file(FROZEN_V101_PACKAGE)
    if actual != FROZEN_V101_SHA256:
        raise ReleasePreparationError(
            "V1.0.1 冻结候选包发生变化，禁止继续组装新版本"
        )


def _stage_copy(source: Path, destination: Path) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=str(destination.parent),
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as source_handle, os.fdopen(
            descriptor, "wb"
        ) as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        return temporary
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _atomic_copy_batch(copies: Sequence[tuple[Path, Path]]) -> None:
    staged: list[tuple[Path, Optional[Path], Path]] = []
    replaced: list[tuple[Optional[Path], Path]] = []
    try:
        for source, destination in copies:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and not destination.is_file():
                raise ReleasePreparationError(
                    f"外部交付目标不是普通文件：{destination}"
                )
            temporary = _stage_copy(source, destination)
            try:
                backup = (
                    _stage_copy(destination, destination)
                    if destination.is_file()
                    else None
                )
            except BaseException:
                temporary.unlink()
                raise
            staged.append((temporary, backup, destination))

        for temporary, backup, destination in staged:
            os.replace(temporary, destination)
            replaced.append((backup, destination))
    except BaseException as error:
        rollback_errors: list[str] = []
        for backup, destination in reversed(replaced):
            try:
                if backup is None:
                    destination.unlink()
                else:
                    os.replace(backup, destination)
            except OSError as rollback_error:
                rollback_errors.append(f"{destination}: {rollback_error}")
        if rollback_errors:
            raise ReleasePreparationError(
                "外部交付失败且旧文件恢复不完整："
                + "；".join(rollback_errors)
            ) from error
        raise
    finally:
        for temporary, backup, _destination in staged:
            for path in (temporary, backup):
                if path is None:
                    continue
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


def prepare_release(
    *,
    source_dir: Path = SOURCE_DIR,
    skeleton_dir: Path = SKELETON_DIR,
    release_root: Path = DEFAULT_RELEASE_ROOT,
    runtime_source: Path = DEFAULT_RUNTIME_SOURCE,
    include_runtime: bool = True,
    package_output: Optional[Path] = None,
    manifest_output: Optional[Path] = None,
) -> ReleaseResult:
    """组装一个新版本；目标目录已存在时拒绝覆盖。"""

    _assert_frozen_v101_unchanged()
    source_dir = Path(source_dir).resolve()
    skeleton_dir = Path(skeleton_dir).resolve()
    release_root = Path(release_root).resolve()
    runtime_source = Path(runtime_source).resolve()

    _assert_plain_tree(source_dir, "唯一源码目录")
    _assert_plain_tree(skeleton_dir, "发布骨架目录")
    version = _read_release_version(source_dir)
    schema_version = _read_schema_version(source_dir / "app.py")
    target_dir = release_root / f"V{version}"
    protected_roots = (
        source_dir,
        skeleton_dir,
        runtime_source,
        *(root.resolve() for root in HISTORICAL_ROOTS),
    )
    _assert_output_path_is_safe(
        target_dir, protected_roots, "发布暂存"
    )
    if target_dir.exists():
        raise ReleasePreparationError(
            f"发布暂存已经存在，拒绝覆盖：{target_dir}"
        )

    package_name = f"升级到V{version}.bat"
    if package_output is not None:
        package_output = Path(package_output).resolve()
        if package_output.name != package_name:
            raise ReleasePreparationError(
                f"候选升级包必须命名为：{package_name}"
            )
        if package_output == FROZEN_V101_PACKAGE.resolve():
            raise ReleasePreparationError("禁止覆盖 V1.0.1 冻结候选包")
        _assert_output_path_is_safe(
            package_output,
            (*protected_roots, target_dir),
            "候选升级包",
        )
    if manifest_output is not None:
        manifest_output = Path(manifest_output).resolve()
        expected_manifest_name = f"V{version}-发布清单.json"
        if manifest_output.name != expected_manifest_name:
            raise ReleasePreparationError(
                f"外部发布清单必须命名为：{expected_manifest_name}"
            )
        _assert_output_path_is_safe(
            manifest_output,
            (*protected_roots, target_dir),
            "外部发布清单",
        )

    release_root.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".V{version}.", dir=str(release_root))
    )
    delivered = False
    try:
        payload_dir = temporary_dir / "完整累计负载"
        payload_dir.mkdir()
        _copy_release_inputs(source_dir, skeleton_dir, payload_dir)

        package_path = temporary_dir / package_name
        build_result = package_builder.build_package(
            payload_dir,
            version,
            package_path,
        )
        payload_records = _payload_file_records(
            payload_dir, build_result.file_paths
        )
        source_tree_sha256 = _sha256_records(
            (
                str(record["path"]),
                str(record["sha256"]),
            )
            for record in payload_records
        )

        candidate_dir: Optional[Path] = None
        runtime_tree_sha256: Optional[str] = None
        if include_runtime:
            candidate_dir = (
                temporary_dir
                / f"Windows部署目录-V{version}-待实机验收"
            )
            shutil.copytree(payload_dir, candidate_dir)
            candidate_program = candidate_dir / "_程序文件"
            runtime_tree_sha256 = _copy_runtime(
                runtime_source, candidate_program
            )
            if runtime_tree_sha256 != FROZEN_RUNTIME_TREE_SHA256:
                raise ReleasePreparationError(
                    "Windows runtime 与冻结基线不一致，禁止生成完整候选"
                )

            checklist_template = (
                skeleton_dir / "_程序文件" / CHECKLIST_TEMPLATE
            )
            _assert_plain_file(checklist_template, "网管验收清单模板")
            checklist = checklist_template.read_text(encoding="utf-8").format(
                version=version
            )
            checklist_bytes = (
                checklist.replace("\r\n", "\n")
                .replace("\r", "\n")
                .replace("\n", "\r\n")
                .encode("utf-8")
            )
            (candidate_program / "给网管的首次验收清单.txt").write_bytes(
                checklist_bytes
            )
            for name in CANDIDATE_INFO_FILES:
                source = skeleton_dir / "_程序文件" / name
                _assert_plain_file(source, "候选部署说明文件")
                shutil.copy2(source, candidate_program / name)
            _verify_candidate_matches_payload(
                payload_dir, candidate_dir, build_result.file_paths
            )

        package_sha256 = _sha256_file(package_path)
        manifest = {
            "version": version,
            "database_schema_version": schema_version,
            "database_migration_required": schema_version > 1,
            "supported_upgrade_sources": ["1.0.0", "1.0.1"],
            "candidate_status": "待 Windows 10/11 实机人工验收",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_tree_sha256": source_tree_sha256,
            "payload_file_count": len(payload_records),
            "payload_files": payload_records,
            "payload_zip_sha256": build_result.zip_sha256,
            "payload_zip_size": build_result.zip_size,
            "package_file": package_name,
            "package_sha256": package_sha256,
            "package_size": build_result.package_size,
            "windows_candidate_included": include_runtime,
            "runtime_tree_sha256": runtime_tree_sha256,
            "frozen_runtime_tree_sha256": FROZEN_RUNTIME_TREE_SHA256,
            "v1_0_1_frozen_package_sha256": FROZEN_V101_SHA256,
        }
        manifest_path = temporary_dir / "发布清单.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        os.replace(temporary_dir, target_dir)
        try:
            internal_package = target_dir / package_name
            internal_manifest = target_dir / "发布清单.json"
            external_copies: list[tuple[Path, Path]] = []
            if package_output is not None:
                external_copies.append((internal_package, package_output))
            if manifest_output is not None:
                external_copies.append((internal_manifest, manifest_output))
            _atomic_copy_batch(external_copies)
        except BaseException:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise
        delivered = True
        final_package = package_output or internal_package
        final_manifest = manifest_output or internal_manifest

        return ReleaseResult(
            version=version,
            release_dir=target_dir,
            payload_dir=target_dir / "完整累计负载",
            candidate_dir=(
                target_dir / f"Windows部署目录-V{version}-待实机验收"
                if include_runtime
                else None
            ),
            package_path=final_package,
            manifest_path=final_manifest,
            package_sha256=package_sha256,
        )
    finally:
        if not delivered:
            shutil.rmtree(temporary_dir, ignore_errors=True)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从唯一源码准备会议室预约系统的新版本发布暂存"
    )
    parser.add_argument(
        "--release-root",
        type=Path,
        default=DEFAULT_RELEASE_ROOT,
        help="版本化发布暂存的父目录",
    )
    parser.add_argument(
        "--runtime-source",
        type=Path,
        default=DEFAULT_RUNTIME_SOURCE,
        help="完整 Windows 候选使用的冻结 runtime",
    )
    parser.add_argument(
        "--package-only",
        action="store_true",
        help="只生成累计负载、升级 BAT 和清单，不生成完整 Windows 候选",
    )
    parser.add_argument(
        "--package-out",
        type=Path,
        help="通过全部校验后，额外交付候选 BAT 到此路径",
    )
    parser.add_argument(
        "--manifest-out",
        type=Path,
        help="通过全部校验后，额外交付发布清单到此路径",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        result = prepare_release(
            release_root=args.release_root,
            runtime_source=args.runtime_source,
            include_runtime=not args.package_only,
            package_output=args.package_out,
            manifest_output=args.manifest_out,
        )
    except (ReleasePreparationError, package_builder.PackageBuildError, OSError) as error:
        print(f"准备发布失败：{error}")
        return 1

    print("发布暂存准备完成")
    print(f"版本：V{result.version}")
    print(f"目录：{result.release_dir}")
    print(f"升级包：{result.package_path}")
    print(f"升级包 SHA-256：{result.package_sha256}")
    print(f"发布清单：{result.manifest_path}")
    if result.candidate_dir is None:
        print("Windows 完整候选：未生成（package-only）")
    else:
        print(f"Windows 完整候选：{result.candidate_dir}")
    print("状态：待 Windows 10/11 实机人工验收")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

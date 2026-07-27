#!/usr/bin/env python3
"""制作 V1.0.3 完整候选更新 ZIP。

本构建器沿用 V1.0.2-r1 已验证的事务引擎，但不会修改或重建冻结 r1 成品。
当前引擎由冻结源按固定白名单替换生成；旧 r1 引擎和 V1.0.1 恢复负载随包
保留，只用于收敛未完成的 r1 事务。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

import 制作升级包 as package_builder
import 制作覆盖更新包 as repair_builder
import 准备发布 as release_builder


RELEASE = "V1.0.3-candidate"
BASELINE_VERSION = "1.0.2"
TARGET_VERSION = "1.0.3"
ARTIFACT_NAME = "会议室预约系统-V1.0.3-候选更新.zip"
EXTERNAL_MANIFEST_NAME = "V1.0.3-候选更新清单.json"

TOOL_DIR = Path(__file__).resolve().parent
DEVELOPMENT_DIR = TOOL_DIR.parent
SOURCE_DIR = DEVELOPMENT_DIR / "源代码工作区"
SKELETON_DIR = TOOL_DIR / "发布骨架"
DEFAULT_RELEASE_ROOT = DEVELOPMENT_DIR / "发布暂存"

FROZEN_ENGINE_SOURCE = TOOL_DIR / "覆盖更新.py"
FROZEN_ENGINE_SHA256 = (
    "160420ac95a59b20635871136e112f0c83b2bff0ae3d55187cde907c18006694"
)
ENTRY_SOURCE = TOOL_DIR / "正式更新.py"
LAUNCHER_SOURCE = TOOL_DIR / "正式更新入口.bat"
GUIDE_SOURCE = TOOL_DIR / "正式更新使用说明.txt"

DELIVERED_LAUNCHER = "更新到V1.0.3-候选版.bat"
DELIVERED_GUIDE = "候选更新使用说明.txt"
DELIVERED_TOOL_ROOT = "_V1.0.3更新工具"
DELIVERED_ENTRY = f"{DELIVERED_TOOL_ROOT}/update.py"
DELIVERED_FORMAL_ENGINE = f"{DELIVERED_TOOL_ROOT}/_formal_engine.py"
DELIVERED_V102_ENGINE = f"{DELIVERED_TOOL_ROOT}/_v102_engine.py"
DELIVERED_MANIFEST = f"{DELIVERED_TOOL_ROOT}/manifest.json"
BASELINE_ZIP_NAME = "baseline-v1.0.2.zip"
TARGET_ZIP_NAME = "target-v1.0.3.zip"
RECOVERY_ROOT = f"{DELIVERED_TOOL_ROOT}/_v102-recovery"
RECOVERY_MANIFEST = f"{RECOVERY_ROOT}/manifest.json"
RECOVERY_BASELINE_ZIP_NAME = "baseline-v1.0.1.zip"
RECOVERY_BASELINE_ZIP = f"{RECOVERY_ROOT}/{RECOVERY_BASELINE_ZIP_NAME}"


class FormalPackageBuildError(RuntimeError):
    """正式更新通道的输入、生成物或写盘结果不符合约定。"""


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


def _record(path: str, content: bytes) -> dict[str, Any]:
    return {
        "path": path,
        "size": len(content),
        "sha256": _sha256_bytes(content),
    }


def _tree_digest(records: Iterable[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda item: str(item["path"])):
        digest.update(str(record["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _read_utf8(path: Path, description: str) -> str:
    repair_builder._assert_plain_file(path, description)
    content = path.read_bytes()
    if content.startswith(b"\xef\xbb\xbf"):
        raise FormalPackageBuildError(f"{description}不允许 UTF-8 BOM")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FormalPackageBuildError(f"{description}不是 UTF-8") from error
    if "\x00" in text:
        raise FormalPackageBuildError(f"{description}包含 NUL")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _as_crlf_bytes(path: Path, description: str) -> bytes:
    text = _read_utf8(path, description)
    if not text.endswith("\n"):
        text += "\n"
    return text.replace("\n", "\r\n").encode("utf-8")


def _formal_engine_bytes() -> bytes:
    content = FROZEN_ENGINE_SOURCE.read_bytes()
    if _sha256_bytes(content) != FROZEN_ENGINE_SHA256:
        raise FormalPackageBuildError(
            "冻结 V1.0.2-r1 事务引擎发生变化，禁止猜测生成当前引擎"
        )
    text = content.decode("utf-8")
    replacements = (
        ('TARGET_VERSION = "1.0.2"', 'TARGET_VERSION = "1.0.3"'),
        ('BASELINE_VERSION = "1.0.1"', 'BASELINE_VERSION = "1.0.2"'),
        ('REPAIR_RELEASE = "V1.0.2-r1"', 'REPAIR_RELEASE = "V1.0.3-candidate"'),
        ('STATE_NAME = "_V102覆盖更新状态.json"', 'STATE_NAME = "_正式更新状态.json"'),
        ('LOCK_NAME = "_V102覆盖更新锁"', 'LOCK_NAME = "_正式更新锁"'),
        ('ROLLBACK_NAME = "_V102覆盖更新回滚"', 'ROLLBACK_NAME = "_正式更新回滚"'),
        (
            r"\.(?:版本\.txt|_V102覆盖更新状态\.json)\..+\.tmp",
            r"\.(?:版本\.txt|_正式更新状态\.json)\..+\.tmp",
        ),
        ("meetingroom_v102_repair_launcher.log", "meetingroom_formal_update_launcher.log"),
        ("repair-update-", "formal-update-"),
        ("pre_v102_repair_", "pre_v103_update_"),
        ("V1.0.2 数据检查程序", "V1.0.3 数据检查程序"),
        ("V1.0.2 服务程序", "V1.0.3 服务程序"),
        ("V1.0.2 对数据副本", "V1.0.3 对数据副本"),
        ("V1.0.2 回环健康检查", "V1.0.3 回环健康检查"),
        ("V1.0.2 已使用 data 副本", "V1.0.3 已使用 data 副本"),
        (
            "V1.0.2 健康检查前版本文件不再是 1.0.1",
            "V1.0.3 健康检查前版本文件不再是 1.0.2",
        ),
        ("V1.0.2 版本文件已最后提交", "V1.0.3 版本文件已最后提交"),
        ("磁盘版本已经是 V1.0.2；", "磁盘版本已经是 V1.0.3；"),
        ("将按已提交 V1.0.2 收尾", "将按已提交 V1.0.3 收尾"),
        ("磁盘版本是 V1.0.2，但程序", "磁盘版本是 V1.0.3，但程序"),
        (
            "当前受管程序和 runtime 已严格匹配 V1.0.2-r1；",
            "当前受管程序和 runtime 已严格匹配 V1.0.3-candidate；",
        ),
        (
            "旧升级状态已经越过提交边界，但磁盘版本不是 V1.0.2；",
            "旧升级状态已经越过提交边界，但磁盘版本不是 V1.0.3；",
        ),
        (
            "旧 V1.0.2 事务已确认提交；本次只完成残留收尾，",
            "旧累计升级事务已确认提交；本次只完成残留收尾，",
        ),
        (
            "旧 V1.0.2 升级器仍在运行，拒绝并发修复",
            "旧累计升级器仍在运行，拒绝并发更新",
        ),
        ("会议室预约系统 V1.0.2 修复更新", "会议室预约系统 V1.0.3 候选更新"),
        ("修复更新成功：受管程序已经是 V1.0.2。", "候选更新成功：受管程序已经是 V1.0.3。"),
    )
    for old, new in replacements:
        expected_count = (
            2 if old == "meetingroom_v102_repair_launcher.log" else 1
        )
        if text.count(old) != expected_count:
            raise FormalPackageBuildError(
                f"事务引擎白名单替换项数量异常：{old!r}"
            )
        text = text.replace(old, new)
    text = text.replace("V1.0.2-r1", "V1.0.3-candidate")
    text = text.replace("冻结 V1.0.1", "冻结 V1.0.2")
    text = text.replace("V1.0.1 基线", "V1.0.2 基线")
    text = text.replace("不再是 V1.0.1", "不再是 V1.0.2")
    text = text.replace("再覆盖 V1.0.2；", "再覆盖 V1.0.3；")
    text = text.replace("* V1.0.2 的数据库", "* V1.0.3 的数据库")
    text = text.replace("已知良好的 V1.0.1", "已知良好的 V1.0.2")
    text = text.replace("修复更新清单", "正式更新清单")
    text = text.replace("修复工具 runtime", "更新工具 runtime")
    text = text.replace("修复事务", "更新事务")
    text = text.replace("修复更新", "正式更新")
    text = text.replace("修复入口", "正式更新入口")
    return text.encode("utf-8")


def _collect_target_payload() -> repair_builder.FrozenPayload:
    with tempfile.TemporaryDirectory(prefix="meetingroom-v103-payload-") as name:
        payload_root = Path(name) / "payload"
        payload_root.mkdir()
        release_builder._copy_release_inputs(
            SOURCE_DIR,
            SKELETON_DIR,
            payload_root,
        )
        files = package_builder.collect_payload(
            payload_root,
            TARGET_VERSION,
            release_builder.DEFAULT_RUNTIME_SOURCE.parent / "requirements.txt",
        )
        zip_bytes = package_builder.build_deterministic_zip(files)
    records = repair_builder._payload_records(files)
    tree_sha256 = _tree_digest(records)
    return repair_builder.FrozenPayload(
        version=TARGET_VERSION,
        source_package=SOURCE_DIR,
        source_package_sha256=tree_sha256,
        zip_bytes=zip_bytes,
        zip_sha256=_sha256_bytes(zip_bytes),
        files=files,
        records=records,
        tree_sha256=tree_sha256,
    )


def _collect_runtime(
    runtime_root: Path,
) -> tuple[Dict[str, bytes], tuple[Mapping[str, Any], ...]]:
    runtime_root = Path(runtime_root)
    repair_builder._assert_plain_tree(runtime_root, "冻结 Windows runtime")
    files: Dict[str, bytes] = {}
    seen: Dict[str, str] = {}
    for path in sorted(runtime_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(runtime_root).as_posix()
        package_builder._assert_safe_relative_path(relative)
        package_builder._register_windows_path(seen, relative)
        files[relative] = path.read_bytes()
    for required in ("python.exe", "pythonw.exe"):
        if required not in files:
            raise FormalPackageBuildError(f"冻结 runtime 缺少 {required}")
    records = repair_builder._payload_records(files)
    digest = _tree_digest(records)
    if digest != repair_builder.FROZEN_RUNTIME_TREE_SHA256:
        raise FormalPackageBuildError(
            "冻结 runtime 树哈希不一致："
            f"期望 {repair_builder.FROZEN_RUNTIME_TREE_SHA256}，实际 {digest}"
        )
    return files, records


def _allowed_outer_path(relative: str) -> None:
    if (
        not relative
        or relative.startswith(("/", "\\"))
        or "\\" in relative
        or ":" in relative
        or "\x00" in relative
    ):
        raise FormalPackageBuildError(f"候选更新 ZIP 路径非法：{relative!r}")
    parts = tuple(relative.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise FormalPackageBuildError(
            f"候选更新 ZIP 路径包含空段、. 或 ..：{relative}"
        )
    for part in parts:
        if (
            any(character in part for character in '<>"|?*')
            or any(ord(character) < 32 for character in part)
            or part.endswith((" ", "."))
            or len(part.encode("utf-16-le")) // 2 > 255
            or part.split(".", 1)[0].casefold()
            in package_builder.RESERVED_WINDOWS_BASENAMES
        ):
            raise FormalPackageBuildError(
                f"候选更新 ZIP 路径不符合 Windows 约定：{relative}"
            )
    allowed = {
        DELIVERED_LAUNCHER,
        DELIVERED_GUIDE,
        DELIVERED_ENTRY,
        DELIVERED_FORMAL_ENGINE,
        DELIVERED_V102_ENGINE,
        DELIVERED_MANIFEST,
        f"{DELIVERED_TOOL_ROOT}/{BASELINE_ZIP_NAME}",
        f"{DELIVERED_TOOL_ROOT}/{TARGET_ZIP_NAME}",
        RECOVERY_MANIFEST,
        RECOVERY_BASELINE_ZIP,
    }
    runtime_prefix = f"{DELIVERED_TOOL_ROOT}/runtime/"
    if relative not in allowed and not relative.startswith(runtime_prefix):
        raise FormalPackageBuildError(f"候选更新 ZIP 出现白名单外路径：{relative}")


def _build_outer_zip(files: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    seen: Dict[str, str] = {}
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for relative in sorted(files):
            _allowed_outer_path(relative)
            package_builder._register_windows_path(seen, relative)
            info = zipfile.ZipInfo(
                relative,
                date_time=package_builder.FIXED_ZIP_TIMESTAMP,
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(
                info,
                files[relative],
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    return output.getvalue()


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise FormalPackageBuildError(f"无法加载候选更新模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(name, None)


def _verify_outer_zip(
    artifact_bytes: bytes,
    expected_files: Mapping[str, bytes],
    baseline: repair_builder.FrozenPayload,
    target: repair_builder.FrozenPayload,
) -> None:
    with zipfile.ZipFile(io.BytesIO(artifact_bytes), "r") as archive:
        if archive.namelist() != sorted(expected_files):
            raise FormalPackageBuildError("候选更新 ZIP 条目顺序或集合不一致")
        for info in archive.infolist():
            _allowed_outer_path(info.filename)
            if (
                info.is_dir()
                or info.filename.endswith("/")
                or info.date_time != package_builder.FIXED_ZIP_TIMESTAMP
                or archive.read(info) != expected_files[info.filename]
            ):
                raise FormalPackageBuildError(
                    f"候选更新 ZIP 条目不符合约定：{info.filename}"
                )
        if archive.testzip() is not None:
            raise FormalPackageBuildError("候选更新 ZIP CRC 校验失败")

    with tempfile.TemporaryDirectory(prefix="meetingroom-v103-verify-") as name:
        extracted = Path(name)
        with zipfile.ZipFile(io.BytesIO(artifact_bytes), "r") as archive:
            archive.extractall(extracted)
        tool_root = extracted / DELIVERED_TOOL_ROOT
        formal = _load_module(tool_root / "_formal_engine.py", "_verify_formal")
        bundle = formal.Bundle.load(tool_root)
        entry = _load_module(tool_root / "update.py", "_verify_entry")
        recovery = _load_module(
            tool_root / "_v102_engine.py",
            "_verify_recovery",
        )
        recovery_bundle = entry._load_recovery_bundle(
            recovery,
            tool_root,
            bundle,
        )
        if (
            bundle.release != RELEASE
            or bundle.baseline.zip_sha256 != baseline.zip_sha256
            or bundle.target.zip_sha256 != target.zip_sha256
            or recovery_bundle.target.zip_sha256 != baseline.zip_sha256
            or recovery_bundle.baseline.version != "1.0.1"
        ):
            raise FormalPackageBuildError("候选更新 Bundle 反向加载结果不一致")


def _bundle_files(
    recovery_baseline: repair_builder.FrozenPayload,
    baseline: repair_builder.FrozenPayload,
    target: repair_builder.FrozenPayload,
    runtime_files: Mapping[str, bytes],
    runtime_records: Sequence[Mapping[str, Any]],
) -> tuple[Dict[str, bytes], bytes]:
    manifest = {
        "schema": 1,
        "release": RELEASE,
        "baseline": repair_builder._payload_manifest_section(
            baseline,
            BASELINE_ZIP_NAME,
        ),
        "target": repair_builder._payload_manifest_section(
            target,
            TARGET_ZIP_NAME,
        ),
        "runtime": {
            "tree_sha256": repair_builder.FROZEN_RUNTIME_TREE_SHA256,
            "files": list(runtime_records),
        },
    }
    recovery_manifest = {
        "schema": 1,
        "release": repair_builder.RELEASE,
        "baseline": repair_builder._payload_manifest_section(
            recovery_baseline,
            RECOVERY_BASELINE_ZIP_NAME,
        ),
        "target": {
            "version": BASELINE_VERSION,
            "sha256": baseline.zip_sha256,
        },
    }
    manifest_bytes = _json_bytes(manifest)
    files: Dict[str, bytes] = {
        DELIVERED_LAUNCHER: _as_crlf_bytes(
            LAUNCHER_SOURCE,
            "正式更新 BAT 入口",
        ),
        DELIVERED_GUIDE: _as_crlf_bytes(
            GUIDE_SOURCE,
            "正式更新使用说明",
        ),
        DELIVERED_ENTRY: _read_utf8(
            ENTRY_SOURCE,
            "正式更新 Python 入口",
        ).encode("utf-8"),
        DELIVERED_FORMAL_ENGINE: _formal_engine_bytes(),
        DELIVERED_V102_ENGINE: FROZEN_ENGINE_SOURCE.read_bytes(),
        DELIVERED_MANIFEST: manifest_bytes,
        f"{DELIVERED_TOOL_ROOT}/{BASELINE_ZIP_NAME}": baseline.zip_bytes,
        f"{DELIVERED_TOOL_ROOT}/{TARGET_ZIP_NAME}": target.zip_bytes,
        RECOVERY_MANIFEST: _json_bytes(recovery_manifest),
        RECOVERY_BASELINE_ZIP: recovery_baseline.zip_bytes,
    }
    for relative, content in sorted(runtime_files.items()):
        files[f"{DELIVERED_TOOL_ROOT}/runtime/{relative}"] = content
    return files, manifest_bytes


def _validate_target(release_root: Path) -> Path:
    release_root = release_root.expanduser().resolve()
    target = release_root / RELEASE
    protected = (
        TOOL_DIR.resolve(),
        SOURCE_DIR.resolve(),
        (DEVELOPMENT_DIR / "Windows部署目录-V1.0.1-待实机验收").resolve(),
        (DEVELOPMENT_DIR.parent / "01_版本归档").resolve(),
    )
    for root in protected:
        if target == root or root in target.parents:
            raise FormalPackageBuildError(f"候选暂存不能位于受保护目录：{root}")
    if target.exists():
        raise FormalPackageBuildError(f"候选暂存已经存在，拒绝覆盖：{target}")
    return target


def _write_durable(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def build_formal_candidate(
    release_root: Path = DEFAULT_RELEASE_ROOT,
    runtime_source: Optional[Path] = None,
) -> BuildResult:
    target_dir = _validate_target(Path(release_root))
    recovery_baseline = repair_builder._extract_frozen_payload(
        repair_builder.FROZEN_V101_PACKAGE,
        repair_builder.FROZEN_V101_PACKAGE_SHA256,
        "1.0.1",
    )
    baseline = repair_builder._extract_frozen_payload(
        repair_builder.FROZEN_V102_PACKAGE,
        repair_builder.FROZEN_V102_PACKAGE_SHA256,
        BASELINE_VERSION,
    )
    target = _collect_target_payload()
    runtime_files, runtime_records = _collect_runtime(
        Path(runtime_source or repair_builder.FROZEN_RUNTIME_ROOT)
    )
    files, manifest_bytes = _bundle_files(
        recovery_baseline,
        baseline,
        target,
        runtime_files,
        runtime_records,
    )
    artifact_bytes = _build_outer_zip(files)
    _verify_outer_zip(artifact_bytes, files, baseline, target)
    artifact_sha256 = _sha256_bytes(artifact_bytes)
    outer_records = tuple(
        _record(path, files[path]) for path in sorted(files)
    )
    external_manifest = {
        "schema": 1,
        "release": RELEASE,
        "status": "windows_acceptance_candidate_only",
        "target_version": TARGET_VERSION,
        "baseline_version": BASELINE_VERSION,
        "artifact": {
            "file": ARTIFACT_NAME,
            "size": len(artifact_bytes),
            "sha256": artifact_sha256,
            "file_count": len(outer_records),
            "tree_sha256": _tree_digest(outer_records),
        },
        "bundle_manifest_sha256": _sha256_bytes(manifest_bytes),
        "baseline": {
            "source_package_sha256": baseline.source_package_sha256,
            "payload_zip_sha256": baseline.zip_sha256,
            "payload_tree_sha256": baseline.tree_sha256,
        },
        "target": {
            "source": "源代码工作区 + 发布骨架",
            "payload_zip_sha256": target.zip_sha256,
            "payload_tree_sha256": target.tree_sha256,
        },
        "recovery": {
            "release": repair_builder.RELEASE,
            "baseline_payload_zip_sha256": recovery_baseline.zip_sha256,
            "target_payload_zip_sha256": baseline.zip_sha256,
        },
        "runtime": {
            "tree_sha256": repair_builder.FROZEN_RUNTIME_TREE_SHA256,
            "file_count": len(runtime_records),
            "changed": False,
        },
        "acceptance": {
            "automation_is_not_uac_smartscreen_edr_or_lan_acceptance": True,
            "formal_external_release_allowed": False,
        },
    }
    external_bytes = _json_bytes(external_manifest)

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{RELEASE}.", dir=str(target_dir.parent))
    )
    delivered = False
    try:
        artifact = temporary / ARTIFACT_NAME
        manifest = temporary / EXTERNAL_MANIFEST_NAME
        _write_durable(artifact, artifact_bytes)
        _write_durable(manifest, external_bytes)
        if (
            _sha256_file(artifact) != artifact_sha256
            or json.loads(manifest.read_text(encoding="utf-8"))
            != external_manifest
        ):
            raise FormalPackageBuildError("候选更新写盘后校验失败")
        _verify_outer_zip(artifact.read_bytes(), files, baseline, target)
        os.replace(str(temporary), str(target_dir))
        delivered = True
    finally:
        if not delivered:
            shutil.rmtree(temporary, ignore_errors=True)

    return BuildResult(
        release_dir=target_dir,
        artifact_path=target_dir / ARTIFACT_NAME,
        manifest_path=target_dir / EXTERNAL_MANIFEST_NAME,
        artifact_sha256=artifact_sha256,
        artifact_size=len(artifact_bytes),
        baseline_zip_sha256=baseline.zip_sha256,
        target_zip_sha256=target.zip_sha256,
        runtime_tree_sha256=repair_builder.FROZEN_RUNTIME_TREE_SHA256,
    )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="制作会议室预约系统 V1.0.3 Windows 实机验收候选更新 ZIP"
    )
    parser.add_argument(
        "--release-root",
        type=Path,
        default=DEFAULT_RELEASE_ROOT,
    )
    parser.add_argument(
        "--runtime-source",
        type=Path,
        default=repair_builder.FROZEN_RUNTIME_ROOT,
        help="冻结 Windows runtime 目录；内容仍必须通过固定树哈希",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    try:
        result = build_formal_candidate(
            arguments.release_root,
            runtime_source=arguments.runtime_source,
        )
    except (
        FormalPackageBuildError,
        repair_builder.RepairPackageBuildError,
        package_builder.PackageBuildError,
        release_builder.ReleasePreparationError,
        OSError,
        ValueError,
    ) as error:
        print(f"制作失败：{error}", file=sys.stderr)
        return 1
    print(f"候选暂存：{result.release_dir}")
    print(f"完整更新 ZIP：{result.artifact_path}")
    print(f"ZIP SHA-256：{result.artifact_sha256}")
    print(f"ZIP 大小：{result.artifact_size} 字节")
    print(f"候选清单：{result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

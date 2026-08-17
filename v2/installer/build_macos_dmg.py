#!/usr/bin/env python3
"""从便携包 staging 生成 macOS DMG，并反向校验挂载内容与 staging 一致。

DMG 只是便携包的展示外壳：可复现的事实源始终是 ZIP。hdiutil 生成的
UDZO 镜像元数据不保证跨构建字节一致，因此本脚本只做内容级校验，
字节一致性门禁由 ZIP 承担。
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat as stat_module
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, Sequence

try:
    from .installer_core import InstallerError, sha256_file
except ImportError:
    from installer_core import InstallerError, sha256_file  # type: ignore


VOLUME_NAME = "会议室预约系统V2"
VERIFY_RELATIVE_PATHS = (
    "app/service.py",
    "app/static/index.html",
    "runtime/bin/python3.13",
    "使用说明.txt",
)


class MacDmgBuildError(InstallerError):
    """DMG 生成或校验失败。"""


def _run(command: Sequence[str], *, timeout: float = 600.0) -> str:
    completed = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise MacDmgBuildError(f"命令失败（{command[0]}）：{detail[:400]}")
    return completed.stdout


def staging_from_zip(artifact: Path, destination: Path) -> Path:
    """把已交付的 ZIP 解包为可挂载 staging，并按 ZIP 权限位恢复可执行位。"""

    artifact = Path(artifact).resolve(strict=True)
    destination = Path(destination).resolve()
    if destination.exists():
        raise MacDmgBuildError(f"解包目标必须不存在：{destination}")
    destination.mkdir(parents=True)
    with zipfile.ZipFile(artifact) as archive:
        for info in archive.infolist():
            relative = info.filename
            if relative.endswith("/"):
                continue
            target = destination.joinpath(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("xb") as handle:
                shutil.copyfileobj(source, handle)
            mode = (info.external_attr >> 16) & 0o777
            if stat_module.S_IMODE(mode):
                os.chmod(target, mode)
    # ZIP 的根条目是便携包顶层文件夹；返回它作为 staging 根。
    children = [child for child in destination.iterdir() if child.name != ".DS_Store"]
    if len(children) != 1 or not children[0].is_dir():
        raise MacDmgBuildError("ZIP 根不是唯一的便携包顶层文件夹")
    return children[0]


def build_macos_dmg(
    staging_root: Path,
    output: Path,
    *,
    keep_staging: bool = False,
) -> str:
    staging_root = Path(staging_root).resolve(strict=True)
    output = Path(output).resolve()
    if output.exists():
        raise MacDmgBuildError(f"DMG 输出必须不存在，拒绝覆盖：{output}")
    if not (staging_root / "app" / "service.py").is_file():
        raise MacDmgBuildError("staging 不是 macOS 便携包根目录")
    for directory in ("data", "backups", "logs"):
        if (staging_root / directory).exists():
            raise MacDmgBuildError(f"staging 不得包含现场目录：{directory}")

    workspace = Path(tempfile.mkdtemp(prefix=".v2-macos-dmg-"))
    mounted = False
    try:
        # hdiutil -srcfolder 会把目录内容放在卷根；包一层让顶层文件夹
        # 本身出现在卷里，保持“把整个文件夹拖出去”的安装体验。
        wrapper = workspace / "wrapper"
        wrapper.mkdir()
        # copy2 保留执行位（python3.13 与 .command 必须可执行）。
        shutil.copytree(staging_root, wrapper / staging_root.name)
        _run(
            (
                "hdiutil",
                "create",
                "-volname",
                VOLUME_NAME,
                "-srcfolder",
                str(wrapper),
                "-format",
                "UDZO",
                "-ov",
                "-o",
                str(output),
            )
        )
        if not output.is_file():
            raise MacDmgBuildError("hdiutil 未生成 DMG")
        mount_point = workspace / "mount"
        mount_point.mkdir()
        _run(
            (
                "hdiutil",
                "attach",
                str(output),
                "-nobrowse",
                "-readonly",
                "-mountpoint",
                str(mount_point),
            )
        )
        mounted = True
        volume_root = mount_point / staging_root.name
        if not volume_root.is_dir():
            raise MacDmgBuildError("挂载卷中缺少便携包顶层文件夹")
        for directory in ("data", "backups", "logs"):
            if (volume_root / directory).exists():
                raise MacDmgBuildError(f"DMG 内出现现场目录：{directory}")
        for relative in VERIFY_RELATIVE_PATHS:
            staged = staging_root.joinpath(*relative.split("/"))
            mounted_file = volume_root.joinpath(*relative.split("/"))
            if not mounted_file.is_file() or not staged.is_file():
                raise MacDmgBuildError(f"DMG 校验缺少文件：{relative}")
            if sha256_file(staged) != sha256_file(mounted_file):
                raise MacDmgBuildError(f"DMG 内容与 staging 不一致：{relative}")
    finally:
        if mounted:
            try:
                _run(("hdiutil", "detach", str(mount_point), "-force"), timeout=120.0)
            except (MacDmgBuildError, subprocess.TimeoutExpired):
                shutil.rmtree(mount_point, ignore_errors=True)
        if not keep_staging:
            shutil.rmtree(workspace, ignore_errors=True)
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成 V2 macOS 自托管 DMG")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--staging-root",
        type=Path,
        help="便携包顶层文件夹（含 启动.command 与 app/）",
    )
    source.add_argument(
        "--from-zip",
        type=Path,
        help="从已交付的便携包 ZIP 反解出 staging 再生成 DMG",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    workspace: Optional[Path] = None
    try:
        if arguments.from_zip is not None:
            workspace = Path(tempfile.mkdtemp(prefix=".v2-macos-dmg-src-"))
            staging_root = staging_from_zip(arguments.from_zip, workspace / "staging")
        else:
            staging_root = arguments.staging_root
        path = build_macos_dmg(staging_root, arguments.output)
    except (MacDmgBuildError, InstallerError, OSError, ValueError) as error:
        print(f"V2 macOS DMG 生成失败：{error}")
        return 1
    finally:
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)
    print(f"macOS DMG：{path}")
    print(f"SHA-256：{sha256_file(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

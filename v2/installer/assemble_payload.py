#!/usr/bin/env python3
"""从已验收后端与前端 dist 组装 V2.0.0 客户程序负载。"""

from __future__ import annotations

import argparse
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional, Sequence

try:
    from .frontend_supply_chain import (
        FRONTEND_COMPONENTS_FILE,
        FrontendSupplyChainError,
        build_frontend_component_evidence,
    )
except ImportError:
    from frontend_supply_chain import (  # type: ignore
        FRONTEND_COMPONENTS_FILE,
        FrontendSupplyChainError,
        build_frontend_component_evidence,
    )

try:
    from .installer_core import (
        InstallerError,
        assert_plain_file,
        assert_plain_tree,
        is_reparse_or_link,
        parse_hashed_requirements_lock,
        records_for_tree,
        safe_relative_path,
    )
except ImportError:
    from installer_core import (  # type: ignore
        InstallerError,
        assert_plain_file,
        assert_plain_tree,
        is_reparse_or_link,
        parse_hashed_requirements_lock,
        records_for_tree,
        safe_relative_path,
    )


TEMPLATE_ROOT = Path(__file__).resolve().parent / "payload_templates"
CUSTOMER_FILES = (
    "① 启动系统.bat",
    "② 立即备份.bat",
    "③ 设置开机自动启动.bat",
    "④ 停止本次后台系统.bat",
    "⑤ 取消开机自动启动.bat",
    "⑥ 从备份恢复.bat",
    "使用说明.txt",
)
BACKEND_ROOT_FILES = (
    "service.py",
    "server.py",
    "backup.py",
    "restore.py",
    "requirements.txt",
    "requirements-win-amd64.lock",
)
IGNORED_SOURCE_PARTS = frozenset({"__pycache__", ".git", ".pytest_cache"})


class PayloadAssemblyError(InstallerError):
    """后端、前端或客户模板不能安全组装为 V2 payload。"""


def _copy_plain_file(source: Path, destination: Path) -> None:
    assert_plain_file(source, "V2 payload 源文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_handle, destination.open("xb") as output_handle:
        shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)


def _copy_tree(source: Path, destination: Path, description: str) -> None:
    assert_plain_tree(source, description)
    for record in records_for_tree(source):
        relative = str(record["path"])
        parts = safe_relative_path(relative)
        if any(part.casefold() in IGNORED_SOURCE_PARTS for part in parts):
            continue
        if any(part.casefold().endswith((".pyc", ".pyo")) for part in parts):
            continue
        _copy_plain_file(source.joinpath(*parts), destination.joinpath(*parts))


def _copy_customer_templates(staging: Path) -> None:
    assert_plain_tree(TEMPLATE_ROOT, "V2 客户入口模板")
    for name in CUSTOMER_FILES:
        source = TEMPLATE_ROOT / name
        assert_plain_file(source, "V2 客户入口模板")
        raw = source.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raise PayloadAssemblyError(f"客户入口模板不允许 UTF-8 BOM：{name}")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PayloadAssemblyError(f"客户入口模板不是 UTF-8：{name}") from error
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not text.endswith("\n"):
            text += "\n"
        content = (
            text.replace("\n", "\r\n").encode("utf-8")
            if name.casefold().endswith(".bat")
            else text.encode("utf-8")
        )
        destination = staging / name
        with destination.open("xb") as handle:
            handle.write(content)


def assemble_payload(
    backend_root: Path,
    frontend_dist: Path,
    frontend_lock: Path,
    output: Path,
) -> Path:
    backend_root = Path(backend_root).resolve(strict=True)
    frontend_dist = Path(frontend_dist).resolve(strict=True)
    frontend_lock = Path(frontend_lock).resolve(strict=True)
    output = Path(output).resolve()
    assert_plain_tree(backend_root, "V2 后端源目录")
    assert_plain_tree(frontend_dist, "V2 前端 dist")
    assert_plain_file(frontend_lock, "V2 前端 package-lock.json")
    assert_plain_file(frontend_dist / "index.html", "V2 前端 dist/index.html")
    assert_plain_tree(backend_root / "v2app", "V2 后端 v2app")
    for name in BACKEND_ROOT_FILES:
        assert_plain_file(backend_root / name, f"V2 后端 {name}")
    try:
        runtime_lock = (backend_root / "requirements-win-amd64.lock").read_text(
            encoding="utf-8"
        )
    except UnicodeError as error:
        raise PayloadAssemblyError("Windows runtime lock 不是 UTF-8") from error
    locked = parse_hashed_requirements_lock(runtime_lock)
    if locked.get("flask", {}).get("version") != "3.1.3" or locked.get(
        "waitress", {}
    ).get("version") != "3.0.2":
        raise PayloadAssemblyError("Windows runtime lock 与后端直接依赖版本不一致")
    frontend_evidence = build_frontend_component_evidence(frontend_lock)
    if output.exists() or is_reparse_or_link(output):
        raise PayloadAssemblyError(f"payload 输出必须不存在，拒绝覆盖：{output}")
    output_parent = output.parent.resolve(strict=True)
    output = output_parent / output.name
    for source in (backend_root, frontend_dist, frontend_lock, TEMPLATE_ROOT):
        if source == output or source in output.parents or output in source.parents:
            raise PayloadAssemblyError(f"payload 输出不能与输入目录嵌套：{source}")

    staging = output_parent / f".{output.name}.assembling-{uuid.uuid4().hex}"
    if staging.exists():
        raise PayloadAssemblyError(f"payload 临时目录异常存在：{staging}")
    staging.mkdir()
    try:
        _copy_customer_templates(staging)
        program = staging / "_程序文件"
        program.mkdir()
        app = program / "app"
        app.mkdir()
        for name in BACKEND_ROOT_FILES:
            _copy_plain_file(backend_root / name, app / name)
        app.joinpath(*FRONTEND_COMPONENTS_FILE.split("/")[2:]).write_bytes(
            frontend_evidence
        )
        _copy_tree(backend_root / "v2app", app / "v2app", "V2 后端 v2app")
        _copy_tree(frontend_dist, app / "static", "V2 前端 dist")
        records = records_for_tree(staging)
        paths = {str(record["path"]) for record in records}
        required = {
            "_程序文件/app/service.py",
            "_程序文件/app/server.py",
            "_程序文件/app/backup.py",
            "_程序文件/app/restore.py",
            "_程序文件/app/requirements-win-amd64.lock",
            FRONTEND_COMPONENTS_FILE,
            "_程序文件/app/v2app/__init__.py",
            "_程序文件/app/static/index.html",
            *CUSTOMER_FILES,
        }
        missing = sorted(required - paths)
        if missing:
            raise PayloadAssemblyError(f"组装后的 V2 payload 缺少：{missing}")
        forbidden = (
            "_程序文件/data/",
            "_程序文件/backups/",
            "_程序文件/logs/",
            "_程序文件/runtime/",
        )
        if any(
            str(record["path"]).casefold().startswith(prefix.casefold())
            for record in records
            for prefix in forbidden
        ):
            raise PayloadAssemblyError("组装后的 payload 携带了现场可变目录")
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="组装 V2.0.0 客户程序 payload")
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--frontend-dist", type=Path, required=True)
    parser.add_argument("--frontend-lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        output = assemble_payload(
            arguments.backend_root,
            arguments.frontend_dist,
            arguments.frontend_lock,
            arguments.output,
        )
    except (
        PayloadAssemblyError,
        FrontendSupplyChainError,
        InstallerError,
        OSError,
        ValueError,
    ) as error:
        print(f"V2 payload 组装失败：{error}")
        return 1
    print(f"V2 payload 已组装：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

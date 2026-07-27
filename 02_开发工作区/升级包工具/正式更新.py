#!/usr/bin/env python3
"""会议室预约系统正式更新通道的零参数 Python 入口。

交付包同时携带两份经过构建器逐字校验的事务引擎：冻结的 V1.0.2-r1
恢复引擎，以及由同一引擎按白名单替换生成的当前版本引擎。本入口只负责
校验、定位、直接请求 Windows 提权和编排旧修复事务的收敛。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any, Optional, Sequence


FORMAL_ENGINE_NAME = "_formal_engine.py"
V102_ENGINE_NAME = "_v102_engine.py"
V102_RECOVERY_DIR = "_v102-recovery"
V102_STATE_NAME = "_V102覆盖更新状态.json"
V102_LOCK_NAME = "_V102覆盖更新锁"
V102_ROLLBACK_NAME = "_V102覆盖更新回滚"
FORMAL_STATE_NAME = "_正式更新状态.json"
RECOVERY_RELEASE = "V1.0.2-r1"
RECOVERY_BASELINE_VERSION = "1.0.1"
RECOVERY_TARGET_VERSION = "1.0.2"


def _load_module(path: Path, name: str) -> ModuleType:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"更新事务引擎缺失或不是普通文件：{path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载更新事务引擎：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _load_recovery_bundle(
    engine: ModuleType,
    tool_root: Path,
    formal_bundle: Any,
) -> Any:
    recovery_root = tool_root / V102_RECOVERY_DIR
    manifest_path = recovery_root / "manifest.json"
    engine._assert_plain_path(manifest_path, "V1.0.2 恢复清单", directory=False)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise engine.UpdateError("V1.0.2 恢复清单无法读取或 JSON 已损坏") from error
    if set(manifest) != {"schema", "release", "baseline", "target"}:
        raise engine.UpdateError("V1.0.2 恢复清单字段不符合约定")
    if manifest["schema"] != 1 or manifest["release"] != RECOVERY_RELEASE:
        raise engine.UpdateError("V1.0.2 恢复清单版本不受支持")
    baseline = engine.Bundle._load_payload(
        recovery_root,
        manifest["baseline"],
        RECOVERY_BASELINE_VERSION,
    )
    target_identity = manifest["target"]
    if (
        not isinstance(target_identity, dict)
        or set(target_identity) != {"version", "sha256"}
        or target_identity["version"] != RECOVERY_TARGET_VERSION
        or target_identity["sha256"] != formal_bundle.baseline.zip_sha256
    ):
        raise engine.UpdateError("V1.0.2 恢复目标与正式更新基线不一致")
    target = formal_bundle.baseline
    return engine.Bundle(
        tool_root=tool_root,
        release=RECOVERY_RELEASE,
        baseline=baseline,
        target=target,
        runtime_records=formal_bundle.runtime_records,
        runtime_tree_sha256=formal_bundle.runtime_tree_sha256,
    )


def _controller(engine: ModuleType) -> Any:
    if os.name == "nt":
        return engine.WindowsSystemController()
    return engine.PassiveSystemController()


def _archive_v102_rollback(engine: ModuleType, install_root: Path, log: Any) -> None:
    program_root = install_root / "_程序文件"
    rollback = program_root / V102_ROLLBACK_NAME
    if not rollback.exists():
        return
    engine._assert_plain_path(rollback, "V1.0.2 修复回滚残留", directory=True)
    destination_root = program_root / "backups"
    destination_root.mkdir(parents=True, exist_ok=True)
    engine._assert_plain_path(destination_root, "备份目录", directory=True)
    destination = destination_root / (
        "旧V102修复回滚残留_"
        + engine.dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    )
    os.replace(str(rollback), str(destination))
    log.write(f"旧 V1.0.2 修复回滚证据已归档保留：{destination}", "WARN")


def main(argv: Optional[Sequence[str]] = None) -> int:
    tool_root = Path(__file__).resolve().parent
    formal: Optional[ModuleType] = None
    recovery_engine: Optional[ModuleType] = None
    log: Any = None
    try:
        formal = _load_module(tool_root / FORMAL_ENGINE_NAME, "_meetingroom_formal")
        arguments = formal._argument_parser().parse_args(argv)
        log = formal.EventLog()
        log.add_path(
            Path(tempfile.gettempdir()) / "meetingroom_formal_update_launcher.log"
        )
        print()
        print("会议室预约系统 V1.0.3 候选更新")
        print("正在校验完整更新工具，请稍候……")
        bundle = formal.Bundle.load(tool_root)

        recovery_engine = _load_module(
            tool_root / V102_ENGINE_NAME,
            "_meetingroom_v102_recovery",
        )
        recovery_bundle = _load_recovery_bundle(
            recovery_engine,
            tool_root,
            bundle,
        )

        if arguments.elevated_context:
            install_root = formal._decode_elevation_context(
                arguments.elevated_context
            )
            if os.name == "nt" and not formal._is_admin():
                raise formal.UpdateError("管理员更新进程没有取得管理员权限")
        elif arguments.install_root is not None:
            install_root = formal._validate_install_root(arguments.install_root)
        else:
            install_root = formal._select_install_root(
                tool_root,
                noninteractive=arguments.noninteractive,
            )
        formal._assert_tool_location(tool_root, install_root)

        if os.name == "nt" and not formal._is_admin():
            print()
            print("即将请求 Windows 管理员授权。")
            context = formal._encode_elevation_context(install_root)
            return formal._run_elevated(tool_root, context)
        if os.name != "nt" and os.environ.get("MEETING_ROOM_UPDATE_TEST_MODE") != "1":
            raise formal.UpdateError("正式更新只能在 Windows 10/11 上运行")

        program_root = install_root / "_程序文件"
        formal_state = program_root / FORMAL_STATE_NAME
        v102_state = program_root / V102_STATE_NAME
        if not formal_state.exists() and v102_state.exists():
            log.write(
                "发现 V1.0.2-r1 未完成事务，先用冻结恢复包安全收敛",
                "WARN",
            )
            recovery_engine.RepairUpdater(
                recovery_bundle,
                install_root,
                _controller(recovery_engine),
                log=log,
            ).run()

        v102_lock = program_root / V102_LOCK_NAME
        with formal.ExclusiveLock(v102_lock):
            formal.RepairUpdater(
                bundle,
                install_root,
                _controller(formal),
                log=log,
            ).run()
            recovery_engine._cleanup_repair_staging(program_root, log)
        try:
            if v102_lock.exists():
                v102_lock.unlink()
        except OSError as error:
            log.write(f"旧 V1.0.2 修复锁残留未能清理：{error}", "WARN")
        _archive_v102_rollback(formal, install_root, log)

        print()
        print("候选更新成功：受管程序已经是 V1.0.3。")
        print("真实 data、密钥、账号、会议室、预约记录和地址状态未被覆盖。")
        print("请回到系统文件夹双击“① 启动系统.bat”。")
        return 0
    except KeyboardInterrupt:
        if log is not None:
            log.write("用户中断更新", "WARN")
        print("\n更新已中断。")
        return 3
    except BaseException as error:
        cancelled = bool(
            (formal is not None and isinstance(error, formal.UpdateCancelled))
            or (
                recovery_engine is not None
                and isinstance(error, recovery_engine.UpdateCancelled)
            )
        )
        busy = bool(
            (formal is not None and isinstance(error, formal.UpdateBusy))
            or (
                recovery_engine is not None
                and isinstance(error, recovery_engine.UpdateBusy)
            )
        )
        if log is not None:
            log.write(f"候选更新失败：{error}", "WARN" if cancelled or busy else "ERROR")
        print()
        print(f"候选更新未完成：{error}")
        if cancelled:
            return 3
        if busy:
            return 4
        print("请保留旧系统、data 和更新日志，不要手工删除事务文件。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

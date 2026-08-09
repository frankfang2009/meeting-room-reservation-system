#!/usr/bin/env python3
"""V2.0.0 全新安装的零参数 Python 入口。"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import Optional, Sequence

try:
    from .installer_core import (
        SERVICE_PORT,
        VERSION,
        Bundle,
        EventLog,
        InstallBusy,
        InstallCancelled,
        InstallCommittedError,
        InstallTransaction,
        InstallerError,
        PassiveSystemController,
        RollbackError,
        WindowsSystemController,
        decode_elevation_context,
        encode_elevation_context,
        is_admin,
        run_elevated,
        validate_target,
    )
except ImportError:  # 交付包中 install.py 与 installer_core.py 位于同一目录
    from installer_core import (  # type: ignore
        SERVICE_PORT,
        VERSION,
        Bundle,
        EventLog,
        InstallBusy,
        InstallCancelled,
        InstallCommittedError,
        InstallTransaction,
        InstallerError,
        PassiveSystemController,
        RollbackError,
        WindowsSystemController,
        decode_elevation_context,
        encode_elevation_context,
        is_admin,
        run_elevated,
        validate_target,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="会议室预约系统 V2.0.0 全新安装")
    parser.add_argument("--elevated-context", help=argparse.SUPPRESS)
    parser.add_argument("--install-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--noninteractive", action="store_true", help=argparse.SUPPRESS)
    return parser


def _choose_target(explicit: Optional[Path], *, noninteractive: bool) -> Path:
    environment = os.environ.get("MEETING_ROOM_V2_INSTALL_ROOT")
    if explicit is not None:
        requested = explicit
    elif environment:
        requested = Path(environment)
    elif noninteractive:
        raise InstallerError("非交互安装必须明确提供 V2 目标目录")
    elif os.name == "nt":
        print()
        print("请输入 V2 全新安装目录。")
        print(r"直接回车使用默认目录：C:\会议室预约系统V2")
        raw = input("安装目录：").strip().strip('"')
        requested = Path(raw or r"C:\会议室预约系统V2")
    else:
        raise InstallerError("V2 正式安装只支持 Windows 10/11")
    target, _ = validate_target(requested)
    return target


def _confirm_fresh_install(*, noninteractive: bool) -> None:
    if os.environ.get("MEETING_ROOM_V2_INSTALL_CONFIRM") == "1":
        return
    if noninteractive:
        raise InstallCancelled("非交互安装没有确认 V1 数据舍弃边界")
    print()
    print("重要说明：")
    print("- 这是 V2 全新安装，不读取、不迁移也不删除任何 V1 文件夹或数据库；")
    print("- 目标目录必须不存在或完全为空；")
    print("- 如需丢弃旧系统，请由您在安装前自行停止并删除旧目录。")
    answer = input("确认继续全新安装？请输入 YES：").strip()
    if answer != "YES":
        raise InstallCancelled("用户没有确认 V2 全新安装")


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    tool_root = Path(__file__).resolve().parent
    log = EventLog()
    log.add_path(Path(tempfile.gettempdir()) / "meetingroom_v2_install_launcher.log")
    try:
        print()
        print(f"会议室预约系统 V{VERSION} 全新安装")
        print("正在校验完整安装包，请稍候……")
        bundle = Bundle.load(tool_root)

        elevated = arguments.elevated_context is not None
        if elevated:
            target = decode_elevation_context(
                arguments.elevated_context,
                bundle.manifest_sha256,
            )
            if os.name == "nt" and not is_admin():
                raise InstallerError("管理员安装进程没有取得管理员权限")
        else:
            target = _choose_target(arguments.install_root, noninteractive=arguments.noninteractive)
            _confirm_fresh_install(noninteractive=arguments.noninteractive)

        bundle.assert_fits_target(target)
        if os.name == "nt" and not is_admin():
            print()
            print("即将请求 Windows 管理员授权。取消授权不会修改任何文件。")
            context = encode_elevation_context(target, bundle.manifest_sha256)
            result = run_elevated(tool_root, context)
            if result == 0:
                webbrowser.open(f"http://127.0.0.1:{SERVICE_PORT}/setup")
            return result

        test_mode = os.environ.get("MEETING_ROOM_V2_INSTALL_TEST_MODE") == "1"
        if os.name != "nt" and not test_mode:
            raise InstallerError("V2 正式安装只支持 Windows 10/11")
        controller = PassiveSystemController() if test_mode else WindowsSystemController()
        transaction_arguments = {}
        if test_mode or os.environ.get("MEETING_ROOM_V2_INSTALL_SKIP_HEALTH") == "1":
            transaction_arguments["health_probe"] = None
        result = InstallTransaction(
            bundle,
            target,
            controller,
            log=log,
            **transaction_arguments,
        ).run()
        print()
        print(f"V{VERSION} 已安装到：{result.install_root}")
        print("首次设置完成前，服务只允许本机回环地址访问。")
        print(f"请在本机打开：{result.setup_url}")
        if os.name == "nt" and not elevated:
            webbrowser.open(result.setup_url)
        return 0
    except InstallCancelled as error:
        log.write(str(error), "WARN")
        print(f"\n安装已取消：{error}")
        return 3
    except InstallBusy as error:
        log.write(str(error), "WARN")
        print(f"\n安装未开始：{error}")
        return 4
    except InstallCommittedError as error:
        log.write(str(error), "ERROR")
        print(f"\n安装文件已经提交，但启动或检查没有完成：{error}")
        print("请保留 V2 目录和日志交给维护人员，不要删除其中可能产生的新数据。")
        return 5
    except RollbackError as error:
        log.write(str(error), "ERROR")
        print(f"\n安装前置事务失败，且无法证明可以安全清理：{error}")
        print("V2 目标现场已保留，请勿手工覆盖；请把安装日志交给维护人员修复。")
        return 6
    except (InstallerError, OSError) as error:
        log.write(str(error), "ERROR")
        print(f"\nV2 安装没有完成：{error}")
        print("安装器没有搜索或删除任何 V1 业务目录。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

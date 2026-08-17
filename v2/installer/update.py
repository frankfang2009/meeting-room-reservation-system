#!/usr/bin/env python3
"""V2.2.0 离线累计更新的零参数 Python 入口。"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
from pathlib import Path
from typing import Optional, Sequence

try:
    from .installer_core import (
        InstallerError,
        VERSION,
        encode_elevation_context,
        is_admin,
        run_elevated,
    )
    from .update_core import (
        PassiveUpdateSystemController,
        UpdateBundle,
        UpdatePolicyError,
        UpdateRollbackError,
        V2UpdateTransaction,
        WindowsUpdateSystemController,
        load_v2_identity,
        resolve_install_root,
    )
except ImportError:
    from installer_core import (  # type: ignore
        InstallerError,
        VERSION,
        encode_elevation_context,
        is_admin,
        run_elevated,
    )
    from update_core import (  # type: ignore
        PassiveUpdateSystemController,
        UpdateBundle,
        UpdatePolicyError,
        UpdateRollbackError,
        V2UpdateTransaction,
        WindowsUpdateSystemController,
        load_v2_identity,
        resolve_install_root,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"会议室预约系统 V{VERSION} 离线累计更新")
    parser.add_argument("--elevated-context", help=argparse.SUPPRESS)
    return parser


def _decode_update_context(value: str, manifest_sha256: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,16384}", value or ""):
        raise UpdatePolicyError("管理员更新上下文格式非法")
    try:
        padding = "=" * ((4 - len(value) % 4) % 4)
        context = json.loads(base64.urlsafe_b64decode(value + padding).decode("utf-8"))
    except (binascii.Error, UnicodeError, ValueError) as error:
        raise UpdatePolicyError("管理员更新上下文无法解码") from error
    expected = {"schema", "install_root", "manifest_sha256", "port", "nonce"}
    if (
        not isinstance(context, dict)
        or set(context) != expected
        or context["schema"] != 1
        or context["manifest_sha256"] != manifest_sha256
        or context["port"] != 8080
        or not re.fullmatch(r"[0-9a-f]{32}", str(context["nonce"]))
    ):
        raise UpdatePolicyError("管理员更新上下文身份不一致")
    root = Path(str(context["install_root"]))
    identity = load_v2_identity(root)
    return identity.root


def _confirm(identity_version: str) -> None:
    print()
    print(f"将把已登记的 V{identity_version} 累计更新到 V{VERSION}。")
    print("更新器会先备份和快照 V2 数据，不会搜索或读取 V1。")
    if input("确认继续？请输入 YES：").strip() != "YES":
        raise UpdatePolicyError("用户没有确认 V2 累计更新")


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    _test_root: Optional[Path] = None,
    _test_controller: Optional[PassiveUpdateSystemController] = None,
) -> int:
    arguments = _parser().parse_args(argv)
    tool_root = Path(__file__).resolve().parent.parent
    try:
        print()
        print(f"会议室预约系统 V{VERSION} 离线累计更新")
        print("正在校验完整更新包，请稍候……")
        bundle = UpdateBundle.load(tool_root)
        test_mode = _test_root is not None
        if arguments.elevated_context:
            install_root = _decode_update_context(
                arguments.elevated_context, bundle.manifest_sha256
            )
            if os.name == "nt" and not is_admin():
                raise UpdatePolicyError("更新进程没有取得管理员权限")
        else:
            install_root = resolve_install_root(_test_root)
            identity = load_v2_identity(install_root)
            if not test_mode:
                _confirm(identity.version)
        if os.name == "nt" and not is_admin() and not test_mode:
            print("即将请求 Windows 管理员授权；取消授权不会修改程序或数据。")
            context = encode_elevation_context(install_root, bundle.manifest_sha256)
            return run_elevated(tool_root, context, entrypoint="update.py")
        if os.name != "nt" and not test_mode:
            raise UpdatePolicyError("V2 正式更新只支持 Windows 10/11")
        controller = _test_controller if test_mode else WindowsUpdateSystemController()
        if controller is None:
            controller = PassiveUpdateSystemController()
        transaction_arguments = (
            {"online_backup": None, "health_probe": None} if test_mode else {}
        )
        result = V2UpdateTransaction(
            bundle,
            install_root,
            controller,
            **transaction_arguments,
        ).run()
        print()
        print(f"更新完成：V{result.source_version} -> V{result.target_version}")
        print("账号、预约、设置、备份、日志和 install_id 已保留。")
        return 0
    except UpdateRollbackError as error:
        print(f"\nV2 更新未能安全收尾：{error}")
        print("服务已保持在可控状态；请保留 _程序文件\\logs 与回滚材料联系维护人员。")
        return 6
    except (UpdatePolicyError, InstallerError, OSError) as error:
        print(f"\nV2 更新没有完成：{error}")
        print("更新器没有扫描、读取或删除任何 V1 业务目录。")
        return 1


if __name__ == "__main__":
    product_result = main()
    print(f"MRV2_UPDATER_RESULT={product_result}")
    raise SystemExit(product_result)

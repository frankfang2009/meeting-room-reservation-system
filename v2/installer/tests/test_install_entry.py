from __future__ import annotations

import io
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from v2.installer.install import _confirm_fresh_install, _console, _say
from v2.installer.installer_core import InstallCancelled


class ConsoleMirrorTests(unittest.TestCase):
    def test_console_swallows_missing_console_device(self) -> None:
        # CI / 无人值守环境没有 CONOUT$，必须静默降级而不是让安装崩溃。
        with mock.patch("builtins.open", side_effect=OSError("no console")):
            _console("任意提示")

    def test_say_writes_stdout_for_install_log(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer), mock.patch("v2.installer.install._console") as mirror:
            _say("安装进度行")
        self.assertIn("安装进度行", buffer.getvalue())
        mirror.assert_called_once_with("安装进度行")


class ConfirmFreshInstallTests(unittest.TestCase):
    def test_yes_proceeds(self) -> None:
        with mock.patch("v2.installer.install._console"), mock.patch(
            "builtins.input", return_value=" YES "
        ):
            _confirm_fresh_install()

    def test_non_yes_cancels(self) -> None:
        with mock.patch("v2.installer.install._console"), mock.patch(
            "builtins.input", return_value=""
        ):
            with self.assertRaises(InstallCancelled):
                _confirm_fresh_install()

    def test_closed_stdin_cancels_instead_of_traceback(self) -> None:
        # BAT 在无人值守/管道下 stdin 可能已关闭：必须干净取消（RC_3），
        # 不能让 EOFError 变成 traceback（RC_1）。
        with mock.patch("v2.installer.install._console"), mock.patch(
            "builtins.input", side_effect=EOFError
        ):
            with self.assertRaises(InstallCancelled):
                _confirm_fresh_install()

    def test_prompt_reaches_console_not_only_stdout(self) -> None:
        # T2-B3 回归：确认提示必须走 CONOUT$（stdout 被安装 BAT 重定向进日志）。
        seen: list[str] = []

        def fake_console(text: str) -> None:
            seen.append(text)

        with mock.patch("v2.installer.install._console", side_effect=fake_console), mock.patch(
            "builtins.input", return_value="YES"
        ):
            _confirm_fresh_install()
        self.assertTrue(any("请输入 YES" in line for line in seen))


class InstallEntryElevationTests(unittest.TestCase):
    """FIX-13 提权链核查的自动化钉子（随 Windows CI 车道真实执行）。

    现行安全模型：管理员进程从同一个用户可写解压目录重新 `Bundle.load`
    全量哈希校验，并用非管理员阶段钉住的 manifest SHA 解码提权上下文——
    验证后被替换的数据文件会被拒绝。进程内测试无法消除的残余 TOCTOU
    （提权瞬间启动的解释器/入口脚本本身仍来自用户可写目录）依赖
    Authenticode 签名或管理员专用不可变 bootstrap，当前两者皆缺，
    属于外部发布阻断项，不在本轮伪造解决。
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_elevated_entry_reverifies_bundle_before_decoding_context(self) -> None:
        import v2.installer.install as entry
        from v2.installer.installer_core import InstallerError

        class TamperedBundle:
            @classmethod
            def load(cls, tool_root: Path) -> "TamperedBundle":
                raise InstallerError("V2 安装工具校验失败（模拟验证后替换）")

        def fail_decode(*args: object, **kwargs: object) -> None:
            raise AssertionError("管理员进程必须先完整重校验工具包，再解码提权上下文")

        with (
            mock.patch.object(entry, "Bundle", TamperedBundle),
            mock.patch.object(entry, "decode_elevation_context", fail_decode),
            # POSIX 上 open("CONOUT$") 会创建普通文件而不是打开控制台设备，
            # 必须屏蔽，避免测试在仓库里留下 CONOUT$ 残留文件。
            mock.patch.object(entry, "_console"),
        ):
            with redirect_stdout(io.StringIO()):
                code = entry.main(["--elevated-context", "elevated-context-token"])
        self.assertEqual(code, 1)

    def test_non_admin_context_pins_the_verified_manifest_sha(self) -> None:
        import v2.installer.install as entry

        captured: dict[str, object] = {}

        class StubBundle:
            manifest_sha256 = "c" * 64

            @classmethod
            def load(cls, tool_root: Path) -> "StubBundle":
                return cls()

            def assert_fits_target(self, target: Path) -> None:
                captured["fits_target"] = target

        def fake_encode(target: Path, manifest_sha256: str) -> str:
            captured["target"] = target
            captured["manifest_sha256"] = manifest_sha256
            return "elevated-context-token"

        def fake_run_elevated(tool_root: Path, context_value: str) -> int:
            captured["run_elevated"] = context_value
            return 0

        patches = [
            mock.patch.object(entry, "Bundle", StubBundle),
            mock.patch.object(entry, "encode_elevation_context", fake_encode),
            mock.patch.object(entry, "run_elevated", fake_run_elevated),
            mock.patch.object(entry, "is_admin", lambda: False),
            # 只替换入口模块看到的 os.name；全局改 os.name 会让 pathlib
            # 在 POSIX 上尝试实例化 WindowsPath。
            mock.patch.object(entry, "os", types.SimpleNamespace(name="nt")),
            mock.patch.object(entry, "webbrowser"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        with redirect_stdout(io.StringIO()), mock.patch.object(entry, "_console"):
            code = entry.main([], _test_target=self.root / "fresh-target")
        self.assertEqual(code, 0)
        # 提权上下文必须钉住非管理员阶段已验证的 manifest SHA，
        # 管理员进程据此拒绝验证后被整体替换的清单与负载。
        self.assertEqual(captured["manifest_sha256"], "c" * 64)
        self.assertEqual(captured["run_elevated"], "elevated-context-token")


if __name__ == "__main__":
    unittest.main()

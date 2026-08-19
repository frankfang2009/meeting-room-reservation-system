from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
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


if __name__ == "__main__":
    unittest.main()

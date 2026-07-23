from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


TOOL_DIR = Path(__file__).resolve().parents[1]
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

import 制作升级包 as builder  # noqa: E402


VERSION = "1.0.1"
FROZEN_REQUIREMENTS = "Flask>=3.0,<4\nwaitress>=3.0,<4\n"


class PackageBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.payload = self.root / "Payload"
        self.templates = self.root / "模板"
        self.output = self.root / "升级到V1.0.1.bat"
        self.frozen_requirements = self.root / "冻结-requirements.txt"

        self.payload.mkdir()
        self.templates.mkdir()
        (self.payload / "_程序文件" / "static" / "nested").mkdir(parents=True)
        (self.payload / "_程序文件" / "templates").mkdir(parents=True)

        top_level_contents = {
            "① 启动系统.bat": "@echo off\r\n",
            "② 立即备份.bat": "@echo off\r\n",
            "③ 设置开机自动启动.bat": "@echo off\r\n",
            "④ 停止本次后台系统.bat": "@echo off\r\n",
            "⑤ 取消开机自动启动.bat": "@echo off\r\n",
            "使用说明.txt": "测试说明\n",
        }
        for relative, content in top_level_contents.items():
            (self.payload / relative).write_text(content, encoding="utf-8")

        program_contents = {
            "app.py": "APP = 'test'\n",
            "server.py": "SERVER = True\n",
            "backup.py": "BACKUP = True\n",
            "migrate_check.py": "MIGRATE = True\n",
            "requirements.txt": FROZEN_REQUIREMENTS,
            "版本.txt": VERSION + "\n",
        }
        program_dir = self.payload / "_程序文件"
        for relative, content in program_contents.items():
            (program_dir / relative).write_text(content, encoding="utf-8")
        (program_dir / "static" / "app.css").write_bytes(b"body{}\n")
        (program_dir / "static" / "nested" / "app.js").write_bytes(b"let x = 1;\n")
        (program_dir / "templates" / "index.html").write_bytes(
            "<h1>测试</h1>\n".encode("utf-8")
        )

        # 标记文字可以出现在普通命令中；只有生成器插入的独占整行才算边界。
        (self.templates / "bat头部模板.bat").write_text(
            "@echo off\n"
            "chcp 65001 >nul\n"
            "set PS_NAME=__UPGRADE_PS1_BELOW__\n"
            "set PAYLOAD_NAME=__UPGRADE_PAYLOAD_BELOW__\n"
            "exit /b %RC%\n",
            encoding="utf-8",
        )
        (self.templates / "升级主逻辑.ps1").write_text(
            "Set-StrictMode -Version Latest\n"
            "$PackageVersion = '__PACKAGE_VERSION__'\n"
            "$PayloadSha256 = '__PAYLOAD_SHA256__'\n"
            "$PayloadMarker = '__UPGRADE_PAYLOAD_BELOW__'\n"
            "Write-Output $PackageVersion\n",
            encoding="utf-8",
        )
        self.frozen_requirements.write_text(
            FROZEN_REQUIREMENTS, encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def build(self, output: Path = None) -> builder.BuildResult:
        return builder.build_package(
            self.payload,
            VERSION,
            output or self.output,
            template_dir=self.templates,
            frozen_requirements_path=self.frozen_requirements,
        )

    def expected_files(self):
        return builder.collect_payload(
            self.payload, VERSION, self.frozen_requirements
        )

    @staticmethod
    def split_package(raw: bytes):
        text = raw.decode("utf-8").replace("\r\n", "\n")
        ps_matches = list(
            re.finditer(r"(?m)^%s$" % re.escape(builder.PS_MARKER), text)
        )
        payload_matches = list(
            re.finditer(r"(?m)^%s$" % re.escape(builder.PAYLOAD_MARKER), text)
        )
        if len(ps_matches) != 1 or len(payload_matches) != 1:
            raise AssertionError("成品标记数量错误")
        ps_match = ps_matches[0]
        payload_match = payload_matches[0]
        powershell = text[ps_match.end() + 1 : payload_match.start()]
        payload_text = text[payload_match.end() + 1 :]
        lines = payload_text.rstrip("\n").split("\n")
        return text, powershell, lines, base64.b64decode("".join(lines), validate=True)

    def test_build_has_unique_ordered_markers_crlf_and_round_trips(self):
        result = self.build()
        raw = self.output.read_bytes()

        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        without_crlf = raw.replace(b"\r\n", b"")
        self.assertNotIn(b"\n", without_crlf)
        self.assertNotIn(b"\r", without_crlf)

        text, powershell, payload_lines, zip_bytes = self.split_package(raw)
        self.assertEqual(
            len(re.findall(r"(?m)^%s$" % re.escape(builder.PS_MARKER), text)), 1
        )
        self.assertEqual(
            len(
                re.findall(
                    r"(?m)^%s$" % re.escape(builder.PAYLOAD_MARKER), text
                )
            ),
            1,
        )
        self.assertLess(text.index(builder.PS_MARKER + "\n"), text.index(builder.PAYLOAD_MARKER + "\n"))
        self.assertNotRegex(
            powershell,
            r"(?m)^%s$" % re.escape(builder.PAYLOAD_MARKER),
        )
        self.assertNotIn(payload_lines[0], powershell)
        self.assertNotIn(builder.VERSION_PLACEHOLDER, powershell)
        self.assertNotIn(builder.SHA256_PLACEHOLDER, powershell)
        self.assertIn("$PackageVersion = '1.0.1'", powershell)

        self.assertTrue(all(len(line) == 76 for line in payload_lines[:-1]))
        self.assertLessEqual(len(payload_lines[-1]), 76)
        self.assertEqual(hashlib.sha256(zip_bytes).hexdigest(), result.zip_sha256)

        expected = self.expected_files()
        returned_zip = builder.verify_package_bytes(
            raw, expected, result.zip_sha256
        )
        self.assertEqual(returned_zip, zip_bytes)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            self.assertEqual(archive.namelist(), sorted(expected))
            for info in archive.infolist():
                self.assertEqual(info.date_time, builder.FIXED_ZIP_TIMESTAMP)
                self.assertEqual(archive.read(info), expected[info.filename])

        self.assertEqual(result.package_size, len(raw))
        self.assertEqual(result.file_paths, tuple(sorted(expected)))

    def test_same_inputs_produce_identical_zip_and_bat(self):
        first_output = self.root / "first.bat"
        second_output = self.root / "second.bat"
        first = self.build(first_output)
        second = self.build(second_output)

        self.assertEqual(first.zip_sha256, second.zip_sha256)
        self.assertEqual(first_output.read_bytes(), second_output.read_bytes())
        expected = self.expected_files()
        self.assertEqual(
            builder.build_deterministic_zip(expected),
            builder.build_deterministic_zip(expected),
        )

    def test_missing_required_file_is_rejected(self):
        (self.payload / "④ 停止本次后台系统.bat").unlink()
        with self.assertRaisesRegex(builder.PackageBuildError, "缺少文件"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_extra_file_and_directory_are_rejected(self):
        for relative in ("额外文件.txt", "_程序文件/extra.py"):
            with self.subTest(relative=relative):
                target = self.payload.joinpath(*relative.split("/"))
                target.write_text("extra", encoding="utf-8")
                with self.assertRaisesRegex(builder.PackageBuildError, "白名单外"):
                    self.build()
                target.unlink()

        extra_directory = self.payload / "额外目录"
        extra_directory.mkdir()
        with self.assertRaisesRegex(builder.PackageBuildError, "白名单外目录"):
            self.build()

    def test_recursive_blacklist_is_case_insensitive(self):
        forbidden = self.payload / "_程序文件" / "static" / "LoGs"
        forbidden.mkdir()
        (forbidden / "new.log").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(builder.PackageBuildError, "禁止名称"):
            self.build()

    def test_path_validator_rejects_traversal_absolute_drive_and_ads(self):
        invalid_paths = (
            "../evil.txt",
            "static/../../evil.txt",
            "/absolute/evil.txt",
            r"..\evil.txt",
            "C:/evil.txt",
            "safe.txt:stream",
            "safe//evil.txt",
            "bad<name.css",
            "bad>name.css",
            'bad"name.css',
            "bad|name.css",
            "bad?name.css",
            "bad*name.css",
        )
        for invalid in invalid_paths:
            with self.subTest(path=invalid):
                with self.assertRaises(builder.PackageBuildError):
                    builder._assert_safe_relative_path(invalid)

        # 非 Windows 系统允许创建这些名称，借此验证目录遍历层也会拒绝它们。
        # Windows 文件系统本身不会创建这些名称，上面的直接校验已覆盖同一风险。
        if os.name != "nt":
            dangerous = self.payload / "_程序文件" / "static" / r"..\outside.css"
            dangerous.write_text("bad", encoding="utf-8")
            with self.assertRaisesRegex(builder.PackageBuildError, "反斜杠"):
                self.build()

            dangerous.unlink()
            windows_invalid = self.payload / "_程序文件" / "static" / "bad?.css"
            windows_invalid.write_text("bad", encoding="utf-8")
            with self.assertRaisesRegex(builder.PackageBuildError, "Windows 非法字符"):
                self.build()

    def test_windows_case_collision_registration_is_rejected(self):
        seen = {}
        builder._register_windows_path(seen, "_程序文件/static/App.CSS")
        with self.assertRaisesRegex(builder.PackageBuildError, "Windows 下重复"):
            builder._register_windows_path(seen, "_程序文件/static/app.css")

    def test_windows_component_length_and_superscript_devices_are_rejected(self):
        for invalid in (
            "_程序文件/static/%s.css" % ("a" * 256),
            "_程序文件/static/%s.css" % ("😀" * 128),
        ):
            with self.subTest(path=invalid):
                with self.assertRaisesRegex(builder.PackageBuildError, "255"):
                    builder._assert_safe_relative_path(invalid)

        for device_name in ("COM¹", "com².txt", "Com³.css", "LPT¹", "lpt².js", "Lpt³"):
            with self.subTest(device=device_name):
                with self.assertRaisesRegex(builder.PackageBuildError, "Windows 保留名称"):
                    builder._assert_safe_relative_path(
                        "_程序文件/static/%s" % device_name
                    )

        device_file = self.payload / "_程序文件" / "static" / "COM¹.css"
        device_file.write_text("bad", encoding="utf-8")
        with self.assertRaisesRegex(builder.PackageBuildError, "Windows 保留名称"):
            self.build()

    def test_inline_payload_marker_is_allowed_but_second_marker_line_is_rejected(self):
        # 实际 PS1 必须以内联字符串找到 BAT 中第二个标记。
        result = self.build()
        _, powershell, _, _ = self.split_package(self.output.read_bytes())
        self.assertIn("$PayloadMarker = '__UPGRADE_PAYLOAD_BELOW__'", powershell)
        self.assertEqual(len(result.file_paths), len(self.expected_files()))

        expected = self.expected_files()
        zip_bytes = builder.build_deterministic_zip(expected)
        invalid_powershell = (
            "$PackageVersion = '__PACKAGE_VERSION__'\n"
            "$PayloadSha256 = '__PAYLOAD_SHA256__'\n"
            + builder.PAYLOAD_MARKER
            + "\n"
        )
        package, sha256, stub, rendered_powershell = builder.render_package(
            "@echo off\nexit /b %RC%\n",
            invalid_powershell,
            VERSION,
            zip_bytes,
        )
        with self.assertRaisesRegex(builder.PackageBuildError, "恰好出现一次"):
            builder.verify_package_bytes(
                package, expected, sha256, stub, rendered_powershell
            )

    def test_symbolic_link_is_rejected(self):
        link = self.payload / "_程序文件" / "static" / "linked.css"
        try:
            os.symlink(
                str(self.payload / "_程序文件" / "static" / "app.css"), str(link)
            )
        except (OSError, NotImplementedError) as exc:
            self.skipTest("当前文件系统不能创建符号链接：%s" % exc)
        with self.assertRaisesRegex(builder.PackageBuildError, "符号链接"):
            self.build()

    def test_invalid_versions_and_payload_version_mismatch_are_rejected(self):
        for invalid in ("", "1", "1.0", "v1.0.1", "01.0.1", "1.-1.0", "1.0.0.1"):
            with self.subTest(version=invalid):
                with self.assertRaises(builder.PackageBuildError):
                    builder.collect_payload(
                        self.payload, invalid, self.frozen_requirements
                    )

        (self.payload / "_程序文件" / "版本.txt").write_text(
            "1.0.2\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(builder.PackageBuildError, "不一致"):
            self.build()

    def test_invalid_version_file_format_and_bom_are_rejected(self):
        version_file = self.payload / "_程序文件" / "版本.txt"
        for invalid_bytes in (
            b"1.0.1\nextra\n",
            b" 1.0.1\n",
            b"1.0.1\n\n",
            b"\xef\xbb\xbf1.0.1\n",
        ):
            with self.subTest(value=invalid_bytes):
                version_file.write_bytes(invalid_bytes)
                with self.assertRaises(builder.PackageBuildError):
                    self.build()

    def test_requirements_must_match_frozen_runtime(self):
        requirements = self.payload / "_程序文件" / "requirements.txt"
        requirements.write_text(
            FROZEN_REQUIREMENTS + "requests>=2\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(builder.PackageBuildError, "冻结依赖"):
            self.build()

    def test_static_and_templates_must_both_contain_files(self):
        for relative in (
            "_程序文件/static/app.css",
            "_程序文件/static/nested/app.js",
        ):
            self.payload.joinpath(*relative.split("/")).unlink()
        with self.assertRaisesRegex(builder.PackageBuildError, "static 目录不能为空"):
            self.build()

    def test_placeholders_must_each_appear_exactly_once(self):
        zip_bytes = builder.build_deterministic_zip(self.expected_files())
        valid_stub = "@echo off\nexit /b %RC%\n"
        invalid_templates = (
            "$PackageVersion = 'missing'\n$PayloadSha256 = '__PAYLOAD_SHA256__'\n",
            "$PackageVersion = '__PACKAGE_VERSION____PACKAGE_VERSION__'\n"
            "$PayloadSha256 = '__PAYLOAD_SHA256__'\n",
            "$PackageVersion = '__PACKAGE_VERSION__'\n$PayloadSha256 = 'missing'\n",
            "$PackageVersion = '__PACKAGE_VERSION__'\n"
            "$PayloadSha256 = '__PAYLOAD_SHA256____PAYLOAD_SHA256__'\n",
        )
        for template in invalid_templates:
            with self.subTest(template=template):
                with self.assertRaisesRegex(builder.PackageBuildError, "恰好出现一次"):
                    builder.render_package(valid_stub, template, VERSION, zip_bytes)

    def test_stub_must_end_with_exit_b_before_embedded_sections(self):
        zip_bytes = builder.build_deterministic_zip(self.expected_files())
        powershell = (
            "$PackageVersion = '__PACKAGE_VERSION__'\n"
            "$PayloadSha256 = '__PAYLOAD_SHA256__'\n"
        )
        with self.assertRaisesRegex(builder.PackageBuildError, "exit /b"):
            builder.render_package(
                "@echo off\necho missing final exit\n",
                powershell,
                VERSION,
                zip_bytes,
            )

    def test_reverse_verifier_rejects_tampered_base64(self):
        result = self.build()
        raw = self.output.read_bytes()
        marker_line = ("\r\n" + builder.PAYLOAD_MARKER + "\r\n").encode("ascii")
        payload_start = raw.index(marker_line) + len(marker_line)
        tampered = bytearray(raw)
        original = tampered[payload_start]
        tampered[payload_start] = ord("B") if original != ord("B") else ord("C")
        with self.assertRaisesRegex(builder.PackageBuildError, "SHA-256"):
            builder.verify_package_bytes(
                bytes(tampered), self.expected_files(), result.zip_sha256
            )

    def test_template_validation_failure_does_not_replace_existing_output(self):
        self.output.write_bytes(b"previous-good-package")
        powershell_template = self.templates / "升级主逻辑.ps1"
        powershell_template.write_text(
            "$PackageVersion = '__PACKAGE_VERSION__'\n"
            "$PayloadSha256 = 'missing-placeholder'\n",
            encoding="utf-8",
        )
        with self.assertRaises(builder.PackageBuildError):
            self.build()
        self.assertEqual(self.output.read_bytes(), b"previous-good-package")

    def test_second_reverse_validation_failure_is_atomic_and_cleans_temp(self):
        self.output.write_bytes(b"previous-good-package")
        failure = builder.PackageBuildError("模拟写盘后的反向校验失败")
        with mock.patch.object(
            builder, "verify_package_bytes", side_effect=[b"zip", failure]
        ) as verify:
            with self.assertRaisesRegex(builder.PackageBuildError, "模拟写盘后"):
                self.build()

        self.assertEqual(verify.call_count, 2)
        self.assertEqual(self.output.read_bytes(), b"previous-good-package")
        self.assertEqual(list(self.root.glob(".%s.*.tmp" % self.output.name)), [])

    def test_output_cannot_alias_template_or_payload_source(self):
        stub = self.templates / "bat头部模板.bat"
        original_stub = stub.read_bytes()
        stub_hardlink = self.root / "stub-hardlink.bat"
        os.link(str(stub), str(stub_hardlink))
        with self.assertRaisesRegex(builder.PackageBuildError, "模板或冻结清单"):
            self.build(stub_hardlink)
        self.assertEqual(stub.read_bytes(), original_stub)

        payload_source = self.payload / "① 启动系统.bat"
        original_source = payload_source.read_bytes()
        payload_hardlink = self.root / "payload-hardlink.bat"
        os.link(str(payload_source), str(payload_hardlink))
        with self.assertRaisesRegex(builder.PackageBuildError, "源 Payload"):
            self.build(payload_hardlink)
        self.assertEqual(payload_source.read_bytes(), original_source)

        # 默认大小写不敏感的 APFS 会让这个别名指向真实模板；不得靠字符串比较。
        case_alias = self.templates / "BAT头部模板.BAT"
        self.assertTrue(case_alias.exists(), "本机测试卷应为大小写不敏感 APFS")
        self.assertTrue(os.path.samefile(str(case_alias), str(stub)))
        with self.assertRaisesRegex(builder.PackageBuildError, "模板或冻结清单"):
            self.build(case_alias)
        self.assertEqual(stub.read_bytes(), original_stub)

    def test_output_cannot_use_case_alias_or_link_into_payload_directory(self):
        # 默认 macOS 文件系统上 PAYLOAD 与 Payload 是同一目录。
        case_alias_directory = self.root / "PAYLOAD"
        self.assertTrue(
            case_alias_directory.exists(), "本机测试卷应为大小写不敏感 APFS"
        )
        self.assertTrue(
            os.path.samefile(str(case_alias_directory), str(self.payload))
        )
        with self.assertRaisesRegex(builder.PackageBuildError, "负载目录内部"):
            self.build(case_alias_directory / "generated.bat")

        linked_directory = self.root / "payload-directory-link"
        try:
            os.symlink(str(self.payload), str(linked_directory))
        except (OSError, NotImplementedError) as exc:
            self.skipTest("当前文件系统不能创建目录符号链接：%s" % exc)
        with self.assertRaisesRegex(builder.PackageBuildError, "负载目录内部"):
            self.build(linked_directory / "generated.bat")

    def test_real_windows_templates_keep_critical_recovery_contracts(self):
        stub = (TOOL_DIR / "bat头部模板.bat").read_text(encoding="utf-8")
        powershell = (TOOL_DIR / "升级主逻辑.ps1").read_text(encoding="utf-8")

        # 最终 BAT 无 BOM，但 WinPS 5.1 读取含中文的临时 PS1 时必须有 BOM。
        self.assertIn("Text.UTF8Encoding($true)", stub)
        self.assertNotIn("Text.UTF8Encoding($false)", stub)

        # native stderr 不能在全局 Stop 策略下变成提前终止的 NativeCommandError。
        self.assertIn("Diagnostics.ProcessStartInfo", powershell)
        self.assertGreaterEqual(powershell.count("ReadToEndAsync()"), 2)
        self.assertNotRegex(
            powershell, r"&\s+\$FilePath\s+@Arguments\s+2>&1"
        )
        self.assertIn("function Replace-FileAtomic", powershell)
        self.assertIn(
            "[IO.File]::Replace($Temporary, $Destination, $backup, $true)",
            powershell,
        )
        self.assertNotIn("[IO.File]::Replace($temporary, $Path, $null)", powershell)
        self.assertNotIn(
            "[IO.File]::Replace($temporary, $destination, $null)", powershell
        )

        invoke_upgrade = powershell[powershell.index("function Invoke-Upgrade") :]
        self.assertLess(
            invoke_upgrade.index("Assert-PackageLocationSafe"),
            invoke_upgrade.index("if (Test-Path -LiteralPath $statePath)"),
        )
        self.assertIn("Stage = 'preparing'", invoke_upgrade)
        self.assertIn(
            "$durableState = (Read-Utf8NoBom -Path $statePath) | ConvertFrom-Json",
            invoke_upgrade,
        )
        self.assertIn("$transactionCommitted = $true", invoke_upgrade)

        # 定义一次，并在安装新版及恢复旧版两条路径各调用一次。
        self.assertGreaterEqual(powershell.count("Clear-RootPythonCache"), 3)

    def test_real_windows_templates_cover_cold_review_regressions(self):
        stub = (TOOL_DIR / "bat头部模板.bat").read_text(encoding="utf-8")
        powershell = (TOOL_DIR / "升级主逻辑.ps1").read_text(encoding="utf-8")

        # UAC 子进程句柄或 ExitCode 异常不得被 PowerShell 当作成功。
        self.assertIn("$null -ne $child.ExitCode", stub)
        self.assertIn("[System.ComponentModel.Win32Exception]", stub)
        self.assertIn("$nativeCode -eq 1223", stub)
        self.assertIn("goto :elevation_failed", stub)
        self.assertIn(":elevation_failed", stub)
        self.assertIn("exit /b 1", stub)

        # 旧 .NET 缺少 ExternalAttributes 时必须能力探测并安全退化。
        self.assertIn(
            "$entry.PSObject.Properties['ExternalAttributes']", powershell
        )
        self.assertNotIn("$entry.ExternalAttributes", powershell)
        self.assertIn("PayloadAttributeCheckDegraded", powershell)

        # 外部命令必须有超时和强杀路径，Python 输出固定 UTF-8。
        self.assertIn("$process.WaitForExit($TimeoutSeconds * 1000)", powershell)
        self.assertIn("$process.Kill()", powershell)
        self.assertIn("$env:PYTHONIOENCODING = 'utf-8'", powershell)
        self.assertIn(
            "Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue",
            powershell,
        )

        # 版本提交之后必须持久化 version_committed；任何收尾失败都不能回滚。
        invoke_upgrade = powershell[powershell.index("function Invoke-Upgrade") :]
        commit_index = invoke_upgrade.index("Commit-VersionFile")
        committed_flag_index = invoke_upgrade.index(
            "$transactionCommitted = $true", commit_index
        )
        committed_stage_index = invoke_upgrade.index(
            "-Stage 'version_committed'", committed_flag_index
        )
        state_delete_index = invoke_upgrade.index(
            "Remove-Item -LiteralPath $statePath -Force", committed_stage_index
        )
        self.assertLess(commit_index, committed_flag_index)
        self.assertLess(committed_flag_index, committed_stage_index)
        self.assertLess(committed_stage_index, state_delete_index)

        committed_recovery = powershell[
            powershell.index("function Recover-CommittedTransaction") :
            powershell.index("function Invoke-Rollback")
        ]
        self.assertNotIn("Invoke-Rollback", committed_recovery)
        self.assertNotIn("Restore-ExpectedRunState", committed_recovery)
        self.assertIn("现有程序和 data 均未回滚", committed_recovery)
        self.assertIn("Test-CommittedTransactionState", invoke_upgrade)


if __name__ == "__main__":
    unittest.main()

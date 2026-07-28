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
            "set BROKER_NAME=__UPGRADE_BROKER_PS1_BELOW__\n"
            "set PS_NAME=__UPGRADE_PS1_BELOW__\n"
            "set PAYLOAD_NAME=__UPGRADE_PAYLOAD_BELOW__\n"
            "exit /b %RC%\n",
            encoding="utf-8",
        )
        (self.templates / "升级入口代理.ps1").write_text(
            "Set-StrictMode -Version Latest\n"
            "Write-Output 'broker'\n",
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
        broker_matches = list(
            re.finditer(r"(?m)^%s$" % re.escape(builder.BROKER_MARKER), text)
        )
        ps_matches = list(
            re.finditer(r"(?m)^%s$" % re.escape(builder.PS_MARKER), text)
        )
        payload_matches = list(
            re.finditer(r"(?m)^%s$" % re.escape(builder.PAYLOAD_MARKER), text)
        )
        if (
            len(broker_matches) != 1
            or len(ps_matches) != 1
            or len(payload_matches) != 1
        ):
            raise AssertionError("成品标记数量错误")
        broker_match = broker_matches[0]
        ps_match = ps_matches[0]
        payload_match = payload_matches[0]
        broker = text[broker_match.end() + 1 : ps_match.start()]
        powershell = text[ps_match.end() + 1 : payload_match.start()]
        payload_text = text[payload_match.end() + 1 :]
        lines = payload_text.rstrip("\n").split("\n")
        return (
            text,
            broker,
            powershell,
            lines,
            base64.b64decode("".join(lines), validate=True),
        )

    def test_build_has_unique_ordered_markers_crlf_and_round_trips(self):
        result = self.build()
        raw = self.output.read_bytes()

        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        without_crlf = raw.replace(b"\r\n", b"")
        self.assertNotIn(b"\n", without_crlf)
        self.assertNotIn(b"\r", without_crlf)

        text, broker, powershell, payload_lines, zip_bytes = self.split_package(raw)
        self.assertEqual(
            len(
                re.findall(
                    r"(?m)^%s$" % re.escape(builder.BROKER_MARKER), text
                )
            ),
            1,
        )
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
        self.assertLess(
            text.index(builder.BROKER_MARKER + "\n"),
            text.index(builder.PS_MARKER + "\n"),
        )
        self.assertLess(
            text.index(builder.PS_MARKER + "\n"),
            text.index(builder.PAYLOAD_MARKER + "\n"),
        )
        self.assertIn("Write-Output 'broker'", broker)
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
        _, _, powershell, _, _ = self.split_package(self.output.read_bytes())
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
        (
            package,
            sha256,
            stub,
            rendered_broker,
            rendered_powershell,
        ) = builder.render_package(
            "@echo off\nexit /b %RC%\n",
            "Write-Output 'broker'\n",
            invalid_powershell,
            VERSION,
            zip_bytes,
        )
        with self.assertRaisesRegex(builder.PackageBuildError, "恰好出现一次"):
            builder.verify_package_bytes(
                package,
                expected,
                sha256,
                stub,
                rendered_broker,
                rendered_powershell,
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
                    builder.render_package(
                        valid_stub,
                        "Write-Output 'broker'\n",
                        template,
                        VERSION,
                        zip_bytes,
                    )

    def test_stub_must_end_with_exit_b_before_embedded_sections(self):
        zip_bytes = builder.build_deterministic_zip(self.expected_files())
        powershell = (
            "$PackageVersion = '__PACKAGE_VERSION__'\n"
            "$PayloadSha256 = '__PAYLOAD_SHA256__'\n"
        )
        with self.assertRaisesRegex(builder.PackageBuildError, "exit /b"):
            builder.render_package(
                "@echo off\necho missing final exit\n",
                "Write-Output 'broker'\n",
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
        run_upgrade_stub = stub[stub.index("\n:run_upgrade\n") :]
        self.assertNotIn("Text.UTF8Encoding($false)", run_upgrade_stub)

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
        broker = (TOOL_DIR / "升级入口代理.ps1").read_text(encoding="utf-8")
        powershell = (TOOL_DIR / "升级主逻辑.ps1").read_text(encoding="utf-8")
        windows_ci = (
            TOOL_DIR.parents[1]
            / ".github"
            / "scripts"
            / "windows-upgrade-integration.ps1"
        ).read_text(encoding="utf-8")
        overlay_windows_ci = (
            TOOL_DIR.parents[1]
            / ".github"
            / "scripts"
            / "windows-overlay-update-integration.ps1"
        ).read_text(encoding="utf-8-sig")

        # UAC 子进程与普通用户服务都必须取得严格的正进程 ID。
        self.assertIn(
            "-Verb RunAs -PassThru -ErrorAction Stop", broker
        )
        self.assertIn("$null -eq $child -or [int]$child.Id -le 0", broker)
        self.assertIn("$elevationStarted = $false", broker)
        self.assertIn(
            "$elevationStarted = $true", broker
        )
        self.assertIn("while (-not $child.HasExited)", broker)
        self.assertIn(
            "if (-not $elevationStarted -and $nativeCode -eq 1223)",
            broker,
        )
        self.assertNotIn(
            "if($nativeCode -eq 1223){exit 3}",
            broker,
        )
        self.assertIn("$child.Dispose()", broker)
        self.assertIn("[System.ComponentModel.Win32Exception]", broker)
        self.assertIn("$nativeCode -eq 1223", broker)
        self.assertIn("meetingroom_upgrade_launcher.log", broker)
        self.assertIn("Write-LauncherLog", broker)
        self.assertIn("goto :elevation_failed", stub)
        self.assertIn(":elevation_failed", stub)
        self.assertIn(":unexpected_launcher_failure", stub)
        self.assertIn(":upgrade_not_completed", stub)
        self.assertIn("错误代码：%UPGRADE_RC%", stub)
        self.assertIn("升级没有正常完成，返回代码：%UPGRADE_RC%", stub)
        for exit_code in ("1", "2", "4", "5"):
            self.assertNotIn(
                f'if "%UPGRADE_RC%"=="{exit_code}" exit /b',
                stub,
            )
        self.assertIn("MEETING_ROOM_UPGRADE_LAUNCH_LOG", stub)
        self.assertIn("where powershell.exe", stub)
        self.assertIn(":powershell_unavailable", stub)
        self.assertIn("exit /b 1", stub)

        # Windows 候选复核必须跟随打包接口，同时加载并校验入口代理。
        verify_candidate = windows_ci[
            windows_ci.index("$verifyCandidateCode = @'") :
            windows_ci.index(
                "Invoke-PythonCodeChecked -Python $hostPython "
                "-Code $verifyCandidateCode"
            )
        ]
        self.assertIn(
            'pathlib.Path(sys.argv[1]) / "升级入口代理.ps1"',
            verify_candidate,
        )
        self.assertIn("broker_text,", verify_candidate)
        self.assertIn("expected_broker,", verify_candidate)
        self.assertIn(
            "builder.render_package(\n"
            "        stub_text,\n"
            "        broker_text,\n"
            "        powershell_text,\n"
            "        version,\n"
            "        zip_bytes,\n"
            "    )",
            verify_candidate,
        )
        self.assertIn(
            "expected_stub,\n"
            "    expected_broker,\n"
            "    expected_powershell,\n",
            verify_candidate,
        )

        # V1.0.3 正式交付不再提交旧累计 BAT；旧通道 CI 只能测试临时生成物。
        self.assertIn(
            "$candidatePackage = Join-Path $preparedRelease $targetPackageName",
            windows_ci,
        )
        self.assertIn(
            "$candidateManifest = $preparedManifestPath",
            windows_ci,
        )
        self.assertNotIn(
            '"输出-待实机验收\\$targetPackageName"',
            windows_ci,
        )

        # r1 未完成事务 fixture 来自篡改目标包，进入正式恢复通道前必须把
        # 身份字段校正为正式候选内冻结的 r1 目标哈希；生产校验不得放宽。
        self.assertIn(
            "$failureState.target_zip_sha256 = [string](",
            overlay_windows_ci,
        )
        self.assertIn(
            "$formalManifest.recovery.target_payload_zip_sha256",
            overlay_windows_ci,
        )
        self.assertIn(
            "$failureState.install_root = [string](",
            overlay_windows_ci,
        )
        self.assertIn(
            "(Resolve-Path -LiteralPath $failureInstall).Path",
            overlay_windows_ci,
        )
        self.assertIn(
            "-Content (($failureState | ConvertTo-Json -Depth 8)",
            overlay_windows_ci,
        )

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
        restore_index = committed_recovery.index("Restore-ExpectedRunState")
        service_guard_index = committed_recovery.index(
            "if ([string]$State.Stage -ne 'service_restored')"
        )
        exposed_branch_index = committed_recovery.index(
            "# 服务已向用户开放后", restore_index
        )
        self.assertLess(service_guard_index, restore_index)
        self.assertLess(restore_index, exposed_branch_index)
        self.assertNotIn(
            "Restore-ExpectedRunState",
            committed_recovery[
                exposed_branch_index :
                committed_recovery.index(
                    "function Assert-RollbackRestoredState",
                    exposed_branch_index,
                )
            ],
        )
        self.assertIn("现有程序和 data 均未回滚", committed_recovery)
        self.assertIn("Test-CommittedTransactionState", invoke_upgrade)

        # 升级前不允许执行旧 server.py；运行探测只能来自 WMI/端口/HTTP。
        running_probe = powershell[
            powershell.index("function Test-SystemRunning") :
            powershell.index("function Get-PortListeners")
        ]
        self.assertNotIn("Invoke-NativeCommand", running_probe)
        self.assertIn("Get-OwnedServerProcesses", running_probe)
        self.assertNotIn("@($server, '--check')", powershell)

        # 安装 runtime 在任何 Python 调用前先做冻结全树哈希校验。
        self.assertIn("function Assert-TrustedRuntime", powershell)
        self.assertIn(
            "b778df06bfc98d699c2aa4c68d4f146f8c6c3d55a0ce1cc7b6811251ed5aad14",
            powershell,
        )
        self.assertLess(
            invoke_upgrade.index("Assert-TrustedRuntime"),
            invoke_upgrade.index("if (Test-Path -LiteralPath $statePath)"),
        )

        # 维护态只绑定回环，提交状态耐久后才恢复真实服务。
        health_index = invoke_upgrade.index("Test-NewVersionByStartingService")
        version_commit_index = invoke_upgrade.index("Commit-VersionFile")
        durable_commit_index = invoke_upgrade.index(
            "-Stage 'version_committed'", version_commit_index
        )
        restore_service_index = invoke_upgrade.index(
            "Restore-ExpectedRunState", durable_commit_index
        )
        self.assertLess(health_index, version_commit_index)
        self.assertLess(version_commit_index, durable_commit_index)
        self.assertLess(durable_commit_index, restore_service_index)
        self.assertIn("$env:MEETING_ROOM_UPGRADE_CHECK = '1'", powershell)
        self.assertIn("Assert-LoopbackListenerOwnedByProcess", powershell)
        self.assertIn("-ExpectedMode 'upgrade-check'", powershell)

        # 无/禁用任务通过未提升父进程 broker 启动，不能从管理员 PS 直接启动 BAT。
        self.assertIn("MEETING_ROOM_UPGRADE_BROKER_REQUEST", stub)
        self.assertIn("MEETING_ROOM_UPGRADE_DIRECT_ADMIN", stub)
        self.assertIn('if /i "%~1"=="--upgrade-broker"', stub)
        self.assertIn(builder.BROKER_MARKER, stub)
        self.assertIn("升级入口代理为空", stub)
        self.assertIn(
            "meetingroom_upgrade_launcher_{0}.ps1' -f "
            "[Guid]::NewGuid().ToString('N')",
            stub,
        )
        self.assertIn("-File $tmp -PackagePath $bat", stub)
        self.assertIn("-ArgumentList $brokerArguments -Verb RunAs", broker)
        self.assertIn("$responseTemp = $response + '.tmp.' + $PID", broker)
        self.assertIn("[IO.File]::Move($responseTemp, $response)", broker)
        self.assertIn("Remove-Item -LiteralPath $request", broker)
        self.assertIn("$info = New-Object Diagnostics.ProcessStartInfo", broker)
        self.assertIn("$info.UseShellExecute = $true", broker)
        self.assertIn(
            "$info.WorkingDirectory = Split-Path -Parent $python",
            broker,
        )
        self.assertNotIn("$info.WorkingDirectory = $work", broker)
        self.assertIn(
            "$info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Minimized",
            broker,
        )
        self.assertIn("$launched = New-Object Diagnostics.Process", broker)
        self.assertIn("if (-not $launched.Start())", broker)
        self.assertIn("$launchedId = [int]$launched.Id", broker)
        self.assertIn("if ($launchedId -le 0)", broker)
        self.assertIn("Stop-Process -Id $launchedId", broker)
        self.assertIn("$launched.Dispose()", broker)
        self.assertIn(
            "[Management.Automation.Language.Parser]::ParseInput",
            windows_ci,
        )
        self.assertIn(
            "入口代理不能被 Windows PowerShell 5.1 解析",
            windows_ci,
        )
        self.assertIn("Request-UnelevatedServiceStart", powershell)
        self.assertIn("function Start-ServiceWithCurrentAdministratorToken", powershell)
        self.assertIn(
            "Start-ServiceWithCurrentAdministratorToken -InstallRoot",
            powershell,
        )
        direct_admin = powershell[
            powershell.index(
                "function Start-ServiceWithCurrentAdministratorToken"
            ) :
            powershell.index("function Request-UnelevatedServiceStart")
        ]
        self.assertIn("New-Object Diagnostics.ProcessStartInfo", direct_admin)
        self.assertIn("$info.UseShellExecute = $true", direct_admin)
        self.assertIn(
            "$info.WorkingDirectory = Split-Path -Parent $python",
            direct_admin,
        )
        self.assertNotIn(
            "$info.WorkingDirectory = $programRoot",
            direct_admin,
        )
        self.assertIn(
            "$info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Minimized",
            direct_admin,
        )
        self.assertIn("if (-not $process.Start())", direct_admin)
        self.assertIn("$startedProcessId = [int]$process.Id", direct_admin)
        self.assertIn("if ($startedProcessId -le 0)", direct_admin)
        self.assertIn("Stop-Process -Id $startedProcessId", direct_admin)
        self.assertIn("$process.Dispose()", direct_admin)

        # CI broker 必须保留子作业的严格错误语义与诊断证据。
        ci_broker = windows_ci[
            windows_ci.index("function Start-TestUpgradeBroker") :
            windows_ci.index("if (Test-Path -LiteralPath $workRoot)")
        ]
        self.assertIn("Set-StrictMode -Version Latest", ci_broker)
        self.assertIn("$ErrorActionPreference = 'Stop'", ci_broker)
        self.assertIn(
            "[IO.File]::ReadAllText($RequestPath,"
            "(New-Object Text.UTF8Encoding($false,$true)))",
            ci_broker,
        )
        self.assertNotIn(
            "Get-Content -LiteralPath $RequestPath -Raw",
            ci_broker,
        )
        self.assertIn("New-Object Diagnostics.ProcessStartInfo", ci_broker)
        self.assertIn("$info.UseShellExecute = $true", ci_broker)
        self.assertIn(
            "$info.WorkingDirectory = Split-Path -Parent "
            "([string]$jobRequest.python_path)",
            ci_broker,
        )
        self.assertNotIn(
            "$info.WorkingDirectory = [string]$jobRequest.working_directory",
            ci_broker,
        )
        self.assertIn("if (-not $launched.Start())", ci_broker)
        self.assertIn("$launchedId = [int]$launched.Id", ci_broker)
        self.assertIn("Stop-Process -Id $launchedId", ci_broker)
        self.assertIn("$launched.Dispose()", ci_broker)
        self.assertLess(
            ci_broker.index("Receive-Job -Job $Broker.Job"),
            ci_broker.index("Remove-Job -Job $Broker.Job"),
        )
        persistent_start = powershell[
            powershell.index("function Start-PersistentSystem") :
            powershell.index("function Test-Database")
        ]
        self.assertIn("if ($TaskExists -and $TaskWasRunning)", persistent_start)
        self.assertNotIn("Shell.Application", persistent_start)
        self.assertNotIn("Start-Process -FilePath $startBat", persistent_start)
        self.assertIn("-not $TaskEnabled", persistent_start)

        # V1.0.0 的合法旧任务和相对 server.py token 仍可识别。
        task_probe = powershell[
            powershell.index("function Get-OwnedTaskState") :
            powershell.index("function Set-OwnedTaskEnabledState")
        ]
        self.assertIn("$actualWorkingDirectory -and", task_probe)
        self.assertIn("V1.0.0 兼容计划任务", task_probe)
        process_probe = powershell[
            powershell.index("function Get-OwnedServerProcesses") :
            powershell.index("function Test-SystemRunning")
        ]
        self.assertIn("[regex]::Escape($server)", process_probe)
        self.assertNotIn("IndexOf($server", process_probe)
        self.assertIn("_程序文件[\\\\/]server\\.py", process_probe)

        lock_function = powershell[
            powershell.index("function Open-UpgradeLock") :
            powershell.index("function Read-InstalledVersion")
        ]
        self.assertIn("[IO.FileShare]::None", lock_function)
        self.assertIn("Throw-UpgradeFailure", lock_function)
        self.assertIn("-ExitCode 4", lock_function)

        # rollback_restored 先落盘再开放旧服务，重复恢复不得再次覆盖 data。
        rollback = powershell[
            powershell.index("function Invoke-Rollback") :
            powershell.index("function Update-TransactionStage")
        ]
        self.assertLess(
            rollback.index("-Stage 'rollback_restored'"),
            rollback.index("Restore-ExpectedRunState"),
        )
        recovered_rollback = powershell[
            powershell.index("function Recover-RollbackRestoredTransaction") :
            powershell.index("function Invoke-Rollback")
        ]
        self.assertNotIn("Invoke-Robocopy", recovered_rollback)
        self.assertNotIn("Move-Item", recovered_rollback)

        # install_id 必须是 canonical lowercase UUIDv4；nil、UUIDv1 与旧宽松
        # version 1-5 正则都不能进入健康检查或已提交恢复。
        uuid_v4_pattern = (
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
        self.assertIn(uuid_v4_pattern, powershell)
        self.assertNotIn("[1-5][0-9a-f]{3}", powershell)
        self.assertIsNone(re.fullmatch(uuid_v4_pattern, ""))
        self.assertIsNone(
            re.fullmatch(
                uuid_v4_pattern,
                "123e4567-e89b-12d3-a456-426614174000",
            )
        )
        self.assertIsNotNone(
            re.fullmatch(
                uuid_v4_pattern,
                "123e4567-e89b-42d3-a456-426614174000",
            )
        )

        # V1.0.2 状态显式使用 Schema=2；只有冻结 V1.0.1 的精确旧字段
        # 集合能够被规范化，已提交旧事务走不要求 install_id 的独立收尾。
        self.assertIn("$script:TransactionStateSchema = 2", powershell)
        self.assertIn("function Assert-CurrentTransactionStateEnvelope", powershell)
        legacy_normalizer = powershell[
            powershell.index("function Convert-LegacyV101TransactionState") :
            powershell.index("function Assert-PreparingState")
        ]
        for field in (
            "OriginalVersion",
            "OriginalVersionExisted",
            "PackageVersion",
            "SnapshotPath",
            "Stage",
            "TaskExists",
            "TransactionId",
            "WasRunning",
        ):
            self.assertIn("'%s'" % field, legacy_normalizer)
        self.assertIn("[string]$State.PackageVersion -cne '1.0.1'", legacy_normalizer)
        self.assertIn(
            "$taskWasRunning = [bool]$State.TaskExists -and [bool]$State.WasRunning",
            legacy_normalizer,
        )
        self.assertIn("function Recover-LegacyV101CommittedTransaction", powershell)
        legacy_committed = powershell[
            powershell.index("function Recover-LegacyV101CommittedTransaction") :
            powershell.index("function Assert-RollbackRestoredState")
        ]
        self.assertIn("Test-NormalInstallRoot", legacy_committed)
        self.assertNotIn("Remove-SuccessfulTransactionSnapshot", legacy_committed)
        self.assertIn(
            "Write-JsonAtomic -Path $statePath -Value $legacyV101State",
            invoke_upgrade,
        )

        # 两次任务采样只允许 Running 由任一次观测贡献；任务存在/启用状态
        # 若发生竞态则在写事务状态前 fail closed。
        self.assertIn(
            "[bool]$taskState.Exists -ne [bool]$latestTaskState.Exists",
            invoke_upgrade,
        )
        self.assertIn(
            "[bool]$taskState.Enabled -ne [bool]$latestTaskState.Enabled",
            invoke_upgrade,
        )
        self.assertIn(
            "TaskWasRunning=$taskWasRunning",
            invoke_upgrade,
        )
        self.assertIn("function Assert-RunStateInvariants", powershell)

        # 地址提醒只接受 canonical 的显式 RFC1918 IPv4:port 与带时区的
        # ISO 形状，不能依赖 [Uri]/本地文化做宽松纠正。
        lan_url_validator = powershell[
            powershell.index("function Test-LanHttpUrl") :
            powershell.index("function Get-LanAddressUpgradeNotice")
        ]
        self.assertIn("^http://(?<address>", lan_url_validator)
        self.assertIn("[StringComparison]::Ordinal", lan_url_validator)
        self.assertNotIn("try { $uri = [Uri]$Value }", lan_url_validator)
        self.assertIn("[Globalization.CultureInfo]::InvariantCulture", powershell)
        self.assertIn("$isoOffsetShape", powershell)

        health_validator = powershell[
            powershell.index("function Get-ValidatedServiceHealth") :
            powershell.index("function Wait-ServiceHealth")
        ]
        self.assertIn("install_id,lan_url,mode,ok", health_validator)
        self.assertIn("$body.ok -isnot [bool]", health_validator)
        self.assertIn("Test-CanonicalInstallId", health_validator)
        self.assertIn("Test-LanHttpUrl", health_validator)
        self.assertIn("$null -ne $body.lan_url", health_validator)
        lan_notice = powershell[
            powershell.index("function Show-LanAddressUpgradeNotice") :
            powershell.index("function Invoke-Upgrade")
        ]
        self.assertIn("Get-ValidatedServiceHealth", lan_notice)
        self.assertIn("[string]$health.LanUrl", lan_notice)
        self.assertIn("[string]$notice.CurrentUrl", lan_notice)
        self.assertIn("本次升级不对同事访问地址作结论", lan_notice)


if __name__ == "__main__":
    unittest.main()

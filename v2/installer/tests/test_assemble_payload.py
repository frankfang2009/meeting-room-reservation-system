from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from v2.installer.assemble_payload import (
    CUSTOMER_FILES,
    PayloadAssemblyError,
    assemble_payload,
)
from v2.installer.build_package import ARTIFACT_NAME, build_package as production_build_package
from v2.installer.tests.helpers import create_inputs


def build_package(*args, **kwargs):
    kwargs["_test_fixture"] = True
    return production_build_package(*args, **kwargs)


class AssemblePayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.backend = self.root / "backend"
        runtime_code = self.backend / "v2app" / "runtime"
        runtime_code.mkdir(parents=True)
        (self.backend / "service.py").write_text("# service fixture\n", encoding="utf-8")
        (self.backend / "server.py").write_text("# server fixture\n", encoding="utf-8")
        (self.backend / "backup.py").write_text("# backup fixture\n", encoding="utf-8")
        (self.backend / "restore.py").write_text("# restore fixture\n", encoding="utf-8")
        (self.backend / "requirements.txt").write_text(
            "Flask==3.1.3\nwaitress==3.0.2\n", encoding="utf-8"
        )
        (self.backend / "requirements-win-amd64.lock").write_bytes(
            b"Flask==3.1.3 --hash=sha256:"
            + b"1" * 64
            + b"\nwaitress==3.0.2 --hash=sha256:"
            + b"2" * 64
            + b"\n"
        )
        (self.backend / "v2app" / "__init__.py").write_text("# app\n", encoding="utf-8")
        (runtime_code / "identity.py").write_text("# identity\n", encoding="utf-8")
        self.frontend = self.root / "frontend-dist"
        (self.frontend / "assets").mkdir(parents=True)
        (self.frontend / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
        (self.frontend / "assets" / "app.js").write_text("// app\n", encoding="utf-8")
        self.frontend_lock = self.root / "package-lock.json"
        integrity = "sha512-AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8gISIjJCUmJygpKissLS4vMDEyMzQ1Njc4OTo7PD0+Pw=="
        self.frontend_lock.write_text(json.dumps({
            "name": "meeting-room-v2-frontend",
            "version": "2.1.0",
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "meeting-room-v2-frontend", "version": "2.1.0"},
                "node_modules/react": {
                    "version": "19.2.0", "integrity": integrity,
                    "license": "MIT", "resolved": "https://registry.npmjs.org/react/-/react-19.2.0.tgz",
                },
                "node_modules/vite": {
                    "version": "6.4.3", "integrity": integrity,
                    "license": "MIT", "resolved": "https://registry.npmjs.org/vite/-/vite-6.4.3.tgz", "dev": True,
                },
            },
        }, sort_keys=True), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_assembles_backend_frontend_and_customer_entrypoints(self) -> None:
        output = assemble_payload(self.backend, self.frontend, self.frontend_lock, self.root / "payload")
        app = output / "_程序文件" / "app"
        self.assertTrue((app / "service.py").is_file())
        self.assertTrue((app / "server.py").is_file())
        self.assertTrue((app / "restore.py").is_file())
        self.assertTrue((app / "requirements-win-amd64.lock").is_file())
        self.assertTrue((app / "v2app" / "runtime" / "identity.py").is_file())
        self.assertTrue((app / "static" / "index.html").is_file())
        evidence = json.loads((app / "frontend-production-components.json").read_text())
        self.assertEqual([item["name"] for item in evidence["components"]], ["react"])
        for name in CUSTOMER_FILES:
            self.assertTrue((output / name).is_file(), name)

    def test_assembled_payload_builds_into_reverse_verified_package(self) -> None:
        payload = assemble_payload(self.backend, self.frontend, self.frontend_lock, self.root / "payload-build")
        fixture = self.root / "runtime-fixture"
        fixture.mkdir()
        _, runtime = create_inputs(fixture)
        payload_lock = payload / "_程序文件" / "app" / "requirements-win-amd64.lock"
        runtime_lock = runtime / "supply-chain" / "requirements.lock"
        self.assertEqual(
            payload_lock.read_bytes(),
            runtime_lock.read_bytes(),
        )
        output = self.root / "out"
        output.mkdir()
        result = build_package(payload, runtime, output / ARTIFACT_NAME)
        self.assertTrue(result.artifact_path.is_file())

    def test_existing_output_is_never_overwritten(self) -> None:
        output = self.root / "existing"
        output.mkdir()
        sentinel = output / "keep.txt"
        sentinel.write_bytes(b"keep")
        with self.assertRaises(PayloadAssemblyError):
            assemble_payload(self.backend, self.frontend, self.frontend_lock, output)
        self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_stop_entrypoints_delegate_to_identity_checked_service(self) -> None:
        output = assemble_payload(self.backend, self.frontend, self.frontend_lock, self.root / "payload-stop")
        for name in ("④ 停止本次后台系统.bat", "⑤ 取消开机自动启动.bat"):
            script = (output / name).read_text(encoding="utf-8")
            self.assertIn("service.py", script)
            self.assertIn("--stop", script)
            self.assertIn("registered.InstallId", script)
            self.assertIn("registered.InstallRoot", script)
            self.assertNotIn("Get-CimInstance", script)
            self.assertNotIn("taskkill", script.casefold())
            self.assertNotIn("netstat", script.casefold())

    def test_all_customer_maintenance_entrypoints_are_uac_and_resource_bound(self) -> None:
        output = assemble_payload(self.backend, self.frontend, self.frontend_lock, self.root / "payload-contracts")
        for name in CUSTOMER_FILES:
            if not name.casefold().endswith(".bat"):
                continue
            with self.subTest(name=name):
                script = (output / name).read_text(encoding="utf-8")
                self.assertIn("Start-Process", script)
                self.assertIn("-Verb RunAs", script)
                self.assertIn("PSModulePath", script)
                self.assertIn("WindowsPowerShell\\v1.0\\Modules", script)
                self.assertIn("SpecialFolder]::ProgramFiles", script)
                self.assertIn("会议室预约系统V2", script)
                self.assertIn("TransactionInstallId", script)
                self.assertIn("SecurityInstallId", script)
                self.assertIn("SecurityDescriptorVersion", script)
                self.assertIn("expectedServiceArguments", script)
                self.assertIn("expectedBackupArguments", script)
                self.assertIn("MSFT_TaskBootTrigger", script)
                self.assertIn("MSFT_TaskDailyTrigger", script)
                self.assertIn("StartWhenAvailable", script)
                self.assertIn("IgnoreNew", script)
                self.assertIn("_程序文件\\app\\service.py", script)
                self.assertIn("_程序文件\\app\\backup.py", script)
                self.assertNotIn("Settings.Enabled", script)
                for forbidden in (
                    "taskkill",
                    "netstat",
                    "Get-CimInstance",
                    "Get-Process",
                    "Stop-Process",
                    "Stop-ScheduledTask",
                    "wmic",
                ):
                    self.assertNotIn(forbidden.casefold(), script.casefold())
                self.assertLess(
                    max(len(line.encode("utf-8")) for line in script.splitlines()),
                    8191,
                )

    def test_backup_and_restore_entrypoints_pass_current_install_id(self) -> None:
        output = assemble_payload(self.backend, self.frontend, self.frontend_lock, self.root / "payload-maintenance")
        immediate = (output / "② 立即备份.bat").read_text(encoding="utf-8")
        restore = (output / "⑥ 从备份恢复.bat").read_text(encoding="utf-8")
        self.assertIn("$env:MRV2_BACKUP --expected-install-id $installId", immediate)
        self.assertNotIn(
            "$env:MRV2_BACKUP --scheduled --expected-install-id $installId",
            immediate,
        )
        self.assertIn("人工备份确认成功", immediate)
        self.assertIn("$newSequence -le $beforeSequence", immediate)
        self.assertIn("_程序文件\\app\\restore.py", restore)
        self.assertIn("--backup $selected.FullName --expected-install-id $installId", restore)
        self.assertIn("Read-Host '请输入 RESTORE 确认", restore)
        self.assertIn("reservation-v2-backup-*.json", restore)
        self.assertIn("$mainWasEnabled", restore)
        self.assertIn("$backupWasEnabled", restore)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.installer.assemble_payload import (
    CUSTOMER_FILES,
    PayloadAssemblyError,
    assemble_payload,
)
from v2.installer.build_package import ARTIFACT_NAME, build_package


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
        (self.backend / "requirements.txt").write_text(
            "Flask==3.1.3\nwaitress==3.0.2\n", encoding="utf-8"
        )
        (self.backend / "v2app" / "__init__.py").write_text("# app\n", encoding="utf-8")
        (runtime_code / "identity.py").write_text("# identity\n", encoding="utf-8")
        self.frontend = self.root / "frontend-dist"
        (self.frontend / "assets").mkdir(parents=True)
        (self.frontend / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
        (self.frontend / "assets" / "app.js").write_text("// app\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_assembles_backend_frontend_and_customer_entrypoints(self) -> None:
        output = assemble_payload(self.backend, self.frontend, self.root / "payload")
        self.assertTrue((output / "_程序文件" / "service.py").is_file())
        self.assertTrue((output / "_程序文件" / "server.py").is_file())
        self.assertTrue((output / "_程序文件" / "v2app" / "runtime" / "identity.py").is_file())
        self.assertTrue((output / "_程序文件" / "static" / "index.html").is_file())
        for name in CUSTOMER_FILES:
            self.assertTrue((output / name).is_file(), name)

    def test_assembled_payload_builds_into_reverse_verified_package(self) -> None:
        payload = assemble_payload(self.backend, self.frontend, self.root / "payload-build")
        runtime = self.root / "windows-runtime"
        runtime.mkdir()
        (runtime / "python.exe").write_bytes(b"python")
        (runtime / "pythonw.exe").write_bytes(b"pythonw")
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
            assemble_payload(self.backend, self.frontend, output)
        self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_stop_entrypoints_delegate_to_identity_checked_service(self) -> None:
        output = assemble_payload(self.backend, self.frontend, self.root / "payload-stop")
        for name in ("④ 停止本次后台系统.bat", "⑤ 取消开机自动启动.bat"):
            script = (output / name).read_text(encoding="utf-8")
            self.assertIn("service.py", script)
            self.assertIn("--stop", script)
            self.assertIn("registered.InstallId", script)
            self.assertIn("registered.InstallRoot", script)
            self.assertNotIn("Get-CimInstance", script)
            self.assertNotIn("taskkill", script.casefold())
            self.assertNotIn("netstat", script.casefold())


if __name__ == "__main__":
    unittest.main()

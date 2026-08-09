from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from v2.installer.build_package import (
    ARTIFACT_NAME,
    PackageBuildError,
    build_package,
)
from v2.installer.installer_core import Bundle, InstallerError, safe_relative_path

from v2.installer.tests.helpers import create_inputs, extract_tool


class PackageBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _build(self, folder: str):
        base = self.root / folder
        base.mkdir()
        payload, runtime = create_inputs(base)
        output = base / "out"
        output.mkdir()
        return build_package(payload, runtime, output / ARTIFACT_NAME)

    def test_build_is_deterministic_and_reverse_loadable(self) -> None:
        first = self._build("first")
        second = self._build("second")
        self.assertEqual(first.artifact_sha256, second.artifact_sha256)
        self.assertEqual(first.artifact_path.read_bytes(), second.artifact_path.read_bytes())
        self.assertTrue(first.sha256_path.is_file())
        self.assertTrue(first.external_manifest_path.is_file())
        tool = extract_tool(first, self.root / "extracted")
        bundle = Bundle.load(tool)
        self.assertEqual(bundle.manifest["product_generation"], 2)
        self.assertEqual(bundle.manifest["service"]["port"], 8080)
        external = json.loads(first.external_manifest_path.read_text(encoding="utf-8"))
        self.assertFalse(external["formal_external_release_allowed"])

    def test_outer_zip_has_zero_argument_bat_and_no_dynamic_data(self) -> None:
        result = self._build("candidate")
        with zipfile.ZipFile(result.artifact_path, "r") as archive:
            names = set(archive.namelist())
            launcher = archive.read("安装V2.0.0.bat").decode("utf-8")
            manifest = json.loads(
                archive.read("_V2安装工具/manifest.json").decode("utf-8")
            )
        self.assertNotIn("%*", launcher)
        self.assertNotIn("--install-root", launcher)
        self.assertIn('"%INSTALL_TOOL%\\runtime\\python.exe"', launcher)
        self.assertFalse(any("/data/" in name.casefold() for name in names))
        self.assertFalse(any(".secret_key" in name for name in names))
        self.assertEqual(manifest["service"]["setup_bind"], "127.0.0.1")
        self.assertEqual(manifest["service"]["lan_bind"], "0.0.0.0")

    def test_existing_outputs_are_never_overwritten(self) -> None:
        base = self.root / "existing"
        base.mkdir()
        payload, runtime = create_inputs(base)
        output_dir = base / "out"
        output_dir.mkdir()
        output = output_dir / ARTIFACT_NAME
        output.write_bytes(b"keep")
        with self.assertRaises(PackageBuildError):
            build_package(payload, runtime, output)
        self.assertEqual(output.read_bytes(), b"keep")

    def test_payload_cannot_ship_data_logs_runtime_or_version(self) -> None:
        forbidden = (
            "data/customer.db",
            "backups/backup.db",
            "logs/server.log",
            "runtime/python.exe",
            "版本.txt",
        )
        for index, relative in enumerate(forbidden):
            with self.subTest(relative=relative):
                base = self.root / f"forbidden-{index}"
                base.mkdir()
                payload, runtime = create_inputs(base)
                target = payload / "_程序文件" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"must-not-ship")
                output = base / "out"
                output.mkdir()
                with self.assertRaises(PackageBuildError):
                    build_package(payload, runtime, output / ARTIFACT_NAME)

    def test_runtime_requires_both_windows_executables(self) -> None:
        base = self.root / "runtime-missing"
        base.mkdir()
        payload, runtime = create_inputs(base)
        (runtime / "pythonw.exe").unlink()
        output = base / "out"
        output.mkdir()
        with self.assertRaises(PackageBuildError):
            build_package(payload, runtime, output / ARTIFACT_NAME)

    def test_real_site_packages_prefixes_keep_manifest_strictly_sorted(self) -> None:
        base = self.root / "runtime-package-prefixes"
        base.mkdir()
        payload, runtime = create_inputs(base)
        package = runtime / "Lib" / "site-packages" / "flask"
        metadata = runtime / "Lib" / "site-packages" / "flask-3.1.3.dist-info"
        package.mkdir(parents=True)
        metadata.mkdir(parents=True)
        (package / "__init__.py").write_text("# runtime fixture\n", encoding="utf-8")
        (metadata / "METADATA").write_text("Version: 3.1.3\n", encoding="utf-8")
        output = base / "out"
        output.mkdir()
        result = build_package(payload, runtime, output / ARTIFACT_NAME)
        self.assertTrue(result.artifact_path.is_file())

    def test_tampered_payload_is_rejected_by_bundle_loader(self) -> None:
        result = self._build("tamper")
        tool = extract_tool(result, self.root / "tamper-extracted")
        payload = tool / "payload-v2.0.0.zip"
        payload.write_bytes(payload.read_bytes() + b"tamper")
        with self.assertRaises(InstallerError):
            Bundle.load(tool)

    def test_tampered_installer_core_is_rejected_by_bundle_loader(self) -> None:
        result = self._build("tamper-tool")
        tool = extract_tool(result, self.root / "tamper-tool-extracted")
        core = tool / "installer_core.py"
        core.write_bytes(core.read_bytes() + b"\n# tamper\n")
        with self.assertRaises(InstallerError):
            Bundle.load(tool)

    def test_path_validator_rejects_traversal_ads_reserved_and_backslash(self) -> None:
        invalid = (
            "../escape.txt",
            "/absolute.txt",
            "C:/drive.txt",
            "folder/name:ads",
            "folder/CON.txt",
            "folder\\windows.txt",
            "folder/trailing. ",
        )
        for relative in invalid:
            with self.subTest(relative=relative):
                with self.assertRaises(InstallerError):
                    safe_relative_path(relative)


if __name__ == "__main__":
    unittest.main()

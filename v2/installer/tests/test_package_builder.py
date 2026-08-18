from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from v2.installer.build_package import (
    ARTIFACT_NAME,
    PackageBuildError,
    build_package as production_build_package,
)
from v2.installer.installer_core import Bundle, InstallerError, safe_relative_path

from v2.installer.tests.helpers import create_inputs, extract_tool, refresh_runtime_mapping


def build_package(*args, **kwargs):
    kwargs["_test_fixture"] = True
    return production_build_package(*args, **kwargs)


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
        self.assertTrue(first.sbom_path.is_file())
        self.assertTrue(first.notices_path.is_file())
        self.assertTrue(first.runtime_provenance_path.is_file())
        tool = extract_tool(first, self.root / "extracted")
        bundle = Bundle.load(tool, _test_fixture=True)
        self.assertEqual(bundle.manifest["product_generation"], 2)
        self.assertEqual(bundle.manifest["service"]["port"], 8080)
        external = json.loads(first.external_manifest_path.read_text(encoding="utf-8"))
        self.assertFalse(external["formal_external_release_allowed"])

    def test_artifact_supply_chain_covers_runtime_and_frontend(self) -> None:
        result = self._build("supply-chain-sidecars")
        with zipfile.ZipFile(result.artifact_path, "r") as archive:
            runtime_sbom = archive.read(
                "_V2安装工具/runtime/supply-chain/sbom.cdx.json"
            )
            runtime_notices = archive.read(
                "_V2安装工具/runtime/supply-chain/THIRD-PARTY-NOTICES.txt"
            )
            internal_provenance = archive.read(
                "_V2安装工具/runtime/supply-chain/runtime-provenance.json"
            )
            internal_manifest = json.loads(
                archive.read("_V2安装工具/manifest.json").decode("utf-8")
            )
        artifact_sbom = result.sbom_path.read_bytes()
        artifact_notices = result.notices_path.read_bytes()
        self.assertNotEqual(artifact_sbom, runtime_sbom)
        self.assertTrue(artifact_notices.startswith(runtime_notices.rstrip()))
        component_names = {
            item["name"] for item in json.loads(artifact_sbom)["components"]
        }
        self.assertTrue({"Python", "Flask", "waitress", "react"}.issubset(component_names))
        self.assertIn(b"react 19.2.0", artifact_notices)
        self.assertEqual(result.runtime_provenance_path.read_bytes(), internal_provenance)
        manifest = json.loads(result.external_manifest_path.read_text(encoding="utf-8"))
        for key, content in (
            ("sbom", artifact_sbom),
            ("third_party_notices", artifact_notices),
            ("runtime_provenance", internal_provenance),
        ):
            self.assertEqual(
                manifest["supply_chain"][key]["sha256"],
                hashlib.sha256(content).hexdigest(),
            )
            self.assertEqual(
                internal_manifest["artifact_supply_chain"][key]["sha256"],
                hashlib.sha256(content).hexdigest(),
            )

    def test_outer_zip_has_zero_argument_bat_and_no_dynamic_data(self) -> None:
        result = self._build("candidate")
        with zipfile.ZipFile(result.artifact_path, "r") as archive:
            names = set(archive.namelist())
            launcher = archive.read("安装V2.2.2.bat").decode("utf-8")
            manifest = json.loads(
                archive.read("_V2安装工具/manifest.json").decode("utf-8")
            )
        self.assertNotIn("%*", launcher)
        self.assertNotIn("--install-root", launcher)
        self.assertIn('"%INSTALL_TOOL%\\runtime\\python.exe"', launcher)
        self.assertIn("MRV2_GATE=MISSING_TOOL_DIR", launcher)
        self.assertIn("MRV2_GATE=MISSING_RUNTIME_PYTHON", launcher)
        self.assertIn("MRV2_GATE=MISSING_PRODUCT_INPUT", launcher)
        self.assertIn("MRV2_GATE=PYTHON_START_FAILED", launcher)
        self.assertIn("MRV2_GATE=PRODUCT_RC_0", launcher)
        self.assertIn("MRV2_INSTALLER_RESULT=%INSTALL_RC%", launcher)
        self.assertIn("findstr /x", launcher)
        for code in (11, 12, 13, 14):
            self.assertIn(f"exit /b {code}", launcher)
        self.assertNotIn("MEETING_ROOM_V2_INSTALL_SKIP_HEALTH", launcher)
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

    def test_existing_supply_chain_sidecar_blocks_all_delivery(self) -> None:
        base = self.root / "existing-sidecar"
        base.mkdir()
        payload, runtime = create_inputs(base)
        output_dir = base / "out"
        output_dir.mkdir()
        output = output_dir / ARTIFACT_NAME
        sidecar = output.with_name(output.name + ".sbom.cdx.json")
        sidecar.write_bytes(b"keep")
        with self.assertRaises(PackageBuildError):
            build_package(payload, runtime, output)
        self.assertEqual(sidecar.read_bytes(), b"keep")
        self.assertFalse(output.exists())

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

    def test_payload_and_runtime_must_ship_the_identical_reviewed_lock(self) -> None:
        base = self.root / "lock-mismatch"
        base.mkdir()
        payload, runtime = create_inputs(base)
        payload_lock = payload / "_程序文件" / "app" / "requirements-win-amd64.lock"
        payload_lock.write_bytes(payload_lock.read_bytes() + b"# different reviewed input\n")
        output = base / "out"
        output.mkdir()
        with self.assertRaises(PackageBuildError):
            build_package(payload, runtime, output / ARTIFACT_NAME)

    def test_payload_rejects_missing_or_forged_frontend_dependency_evidence(self) -> None:
        for label, mutation in (
            ("missing", lambda path: path.unlink()),
            ("forged", lambda path: path.write_text('{"schema":1,"components":[]}\n', encoding="utf-8")),
        ):
            with self.subTest(label=label):
                base = self.root / f"frontend-evidence-{label}"
                base.mkdir()
                payload, runtime = create_inputs(base)
                mutation(payload / "_程序文件" / "app" / "frontend-production-components.json")
                output = base / "out"
                output.mkdir()
                with self.assertRaises(PackageBuildError):
                    build_package(payload, runtime, output / ARTIFACT_NAME)
        self.assertFalse((output / ARTIFACT_NAME).exists())

    def test_runtime_rejects_path_shadowing_import_site_and_wrong_architecture(self) -> None:
        variants = (
            ("bare-parent", "python313._pth", "python313.zip\n.\nLib\\site-packages\n..\n..\\app\n"),
            ("import-site", "python313._pth", "python313.zip\n.\nLib\\site-packages\n..\\app\nimport site\n"),
        )
        for folder, relative, content in variants:
            with self.subTest(folder=folder):
                base = self.root / folder
                base.mkdir()
                payload, runtime = create_inputs(base)
                (runtime / relative).write_text(content, encoding="utf-8")
                output = base / "out"
                output.mkdir()
                with self.assertRaises(InstallerError):
                    build_package(payload, runtime, output / ARTIFACT_NAME)

        base = self.root / "wrong-pe-architecture"
        base.mkdir()
        payload, runtime = create_inputs(base)
        executable = bytearray((runtime / "python.exe").read_bytes())
        executable[68:70] = (0x14C).to_bytes(2, "little")
        (runtime / "python.exe").write_bytes(executable)
        output = base / "out"
        output.mkdir()
        with self.assertRaises(InstallerError):
            build_package(payload, runtime, output / ARTIFACT_NAME)

    def test_runtime_rejects_untrusted_provenance_and_unhashed_lock(self) -> None:
        base = self.root / "bad-provenance"
        base.mkdir()
        payload, runtime = create_inputs(base)
        provenance_path = runtime / "supply-chain" / "runtime-provenance.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        provenance["python"]["source_sha256"] = "0" * 64
        provenance_path.write_text(json.dumps(provenance) + "\n", encoding="utf-8")
        output = base / "out"
        output.mkdir()
        with self.assertRaises(InstallerError):
            build_package(payload, runtime, output / ARTIFACT_NAME)

        base = self.root / "unhashed-lock"
        base.mkdir()
        payload, runtime = create_inputs(base)
        lock_path = runtime / "supply-chain" / "requirements.lock"
        lock_path.write_text("Flask==3.1.3\nwaitress==3.0.2\n", encoding="utf-8")
        provenance_path = runtime / "supply-chain" / "runtime-provenance.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        provenance["artifacts"]["requirements_lock"]["sha256"] = hashlib.sha256(
            lock_path.read_bytes()
        ).hexdigest()
        provenance_path.write_text(json.dumps(provenance) + "\n", encoding="utf-8")
        output = base / "out"
        output.mkdir()
        with self.assertRaises(InstallerError):
            build_package(payload, runtime, output / ARTIFACT_NAME)

    def test_formal_build_and_bundle_reject_synthetic_runtime_identity(self) -> None:
        base = self.root / "formal-rejects-fixture"
        base.mkdir()
        payload, runtime = create_inputs(base)
        output = base / "out"
        output.mkdir()
        with self.assertRaises(PackageBuildError):
            production_build_package(payload, runtime, output / ARTIFACT_NAME)

        result = build_package(payload, runtime, output / ARTIFACT_NAME)
        tool = extract_tool(result, self.root / "formal-rejects-fixture-extracted")
        with self.assertRaises(InstallerError):
            Bundle.load(tool)

    def test_real_site_packages_prefixes_keep_manifest_strictly_sorted(self) -> None:
        base = self.root / "runtime-package-prefixes"
        base.mkdir()
        payload, runtime = create_inputs(base)
        package = runtime / "Lib" / "site-packages" / "werkzeug"
        metadata = runtime / "Lib" / "site-packages" / "werkzeug-3.1.3.metadata"
        package.mkdir(parents=True)
        metadata.mkdir(parents=True)
        (package / "__init__.py").write_text("# runtime fixture\n", encoding="utf-8")
        (metadata / "METADATA").write_text("sorting fixture\n", encoding="utf-8")
        refresh_runtime_mapping(runtime)
        output = base / "out"
        output.mkdir()
        result = build_package(payload, runtime, output / ARTIFACT_NAME)
        self.assertTrue(result.artifact_path.is_file())

    def test_tampered_payload_is_rejected_by_bundle_loader(self) -> None:
        result = self._build("tamper")
        tool = extract_tool(result, self.root / "tamper-extracted")
        payload = tool / "payload-v2.2.2.zip"
        payload.write_bytes(payload.read_bytes() + b"tamper")
        with self.assertRaises(InstallerError):
            Bundle.load(tool, _test_fixture=True)

    def test_tampered_installer_core_is_rejected_by_bundle_loader(self) -> None:
        result = self._build("tamper-tool")
        tool = extract_tool(result, self.root / "tamper-tool-extracted")
        core = tool / "app" / "installer_core.py"
        core.write_bytes(core.read_bytes() + b"\n# tamper\n")
        with self.assertRaises(InstallerError):
            Bundle.load(tool, _test_fixture=True)

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

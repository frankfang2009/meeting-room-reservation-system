from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from v2.installer.build_update_package import (
    ARTIFACT_NAME,
    build_update_package,
)
from v2.installer.tests.helpers import create_inputs
from v2.installer.update_core import UpdateBundle, UpdatePolicyError


class UpdatePackageBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _build(self, name: str):
        source = self.root / name
        source.mkdir()
        payload, runtime = create_inputs(source)
        output = source / "out"
        output.mkdir()
        return build_update_package(
            payload,
            runtime,
            output / ARTIFACT_NAME,
            _test_fixture=True,
        )

    def test_update_package_is_deterministic_and_reverse_loadable(self) -> None:
        first = self._build("first")
        second = self._build("second")
        self.assertEqual(first.artifact_sha256, second.artifact_sha256)
        self.assertEqual(first.artifact_path.read_bytes(), second.artifact_path.read_bytes())
        extracted = self.root / "extracted"
        extracted.mkdir()
        with zipfile.ZipFile(first.artifact_path, "r") as archive:
            archive.extractall(extracted)
        bundle = UpdateBundle.load(extracted / "_V2更新工具")
        self.assertEqual(bundle.manifest["version"], "2.2.0")
        self.assertEqual(bundle.supported_source_versions, frozenset({"2.1.0"}))
        self.assertFalse(bundle.manifest["acceptance"]["formal_external_release_allowed"])

    def test_outer_package_has_zero_parameter_launcher_and_no_mutable_data(self) -> None:
        result = self._build("outer")
        with zipfile.ZipFile(result.artifact_path, "r") as archive:
            names = archive.namelist()
            launcher = archive.read("升级到V2.2.0.bat").decode("utf-8")
            manifest = json.loads(
                archive.read("_V2更新工具/manifest.json").decode("utf-8")
            )
        self.assertNotIn("%*", launcher)
        self.assertIn("MRV2_UPDATE_GATE=MISSING_TOOL_DIR", launcher)
        self.assertIn("MRV2_UPDATER_RESULT=%UPDATE_RC%", launcher)
        self.assertFalse(any("/_程序文件/data/" in name for name in names))
        self.assertEqual(manifest["kind"], "v2-cumulative-update")
        payload_paths = {item["path"] for item in manifest["payload"]["files"]}
        self.assertFalse(any(path.startswith("_程序文件/data/") for path in payload_paths))
        self.assertTrue(any(path.startswith("_程序文件/runtime/") for path in payload_paths))

    def test_tampered_payload_is_rejected(self) -> None:
        result = self._build("tamper")
        extracted = self.root / "tampered"
        extracted.mkdir()
        with zipfile.ZipFile(result.artifact_path, "r") as archive:
            archive.extractall(extracted)
        payload = extracted / "_V2更新工具" / "payload-update.zip"
        payload.write_bytes(payload.read_bytes() + b"tamper")
        with self.assertRaises(UpdatePolicyError):
            UpdateBundle.load(extracted / "_V2更新工具")


if __name__ == "__main__":
    unittest.main()

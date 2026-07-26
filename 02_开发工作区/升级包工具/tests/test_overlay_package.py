from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


TOOL_DIR = Path(__file__).resolve().parents[1]
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

import 制作覆盖更新包 as builder  # noqa: E402
import 覆盖更新 as updater  # noqa: E402


class OverlayPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary_directory.name)
        cls.first = builder.build_repair_release(cls.root / "first")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def test_release_contains_loadable_frozen_bundle(self) -> None:
        manifest = json.loads(
            self.first.manifest_path.read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["release"], updater.REPAIR_RELEASE)
        self.assertEqual(
            manifest["baseline"]["source_package_sha256"],
            builder.FROZEN_V101_PACKAGE_SHA256,
        )
        self.assertEqual(
            manifest["target"]["source_package_sha256"],
            builder.FROZEN_V102_PACKAGE_SHA256,
        )
        self.assertEqual(
            manifest["runtime"]["tree_sha256"],
            builder.FROZEN_RUNTIME_TREE_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(self.first.artifact_path.read_bytes()).hexdigest(),
            manifest["artifact"]["sha256"],
        )

        extracted = self.root / "extracted"
        with zipfile.ZipFile(self.first.artifact_path, "r") as archive:
            archive.extractall(extracted)
        bundle = updater.Bundle.load(
            extracted / builder.DELIVERED_TOOL_ROOT
        )
        self.assertEqual(bundle.baseline.version, updater.BASELINE_VERSION)
        self.assertEqual(bundle.target.version, updater.TARGET_VERSION)
        self.assertEqual(
            bundle.target.zip_sha256,
            "f393562a9d9534fd12a06c2d94094306f8b10dce051a877035335b3e5d37f034",
        )

    def test_launcher_is_zero_argument_crlf_contract(self) -> None:
        with zipfile.ZipFile(self.first.artifact_path, "r") as archive:
            launcher = archive.read(builder.DELIVERED_LAUNCHER)
        self.assertFalse(launcher.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(launcher.count(b"\n"), launcher.count(b"\r\n"))
        text = launcher.decode("utf-8")
        for forbidden in (
            "%1",
            "%*",
            "ArgumentList",
            "--upgrade-broker",
            "Start-Process",
            "cmd /c",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn(
            '"%UPDATE_TOOL%\\runtime\\python.exe" '
            '"%UPDATE_TOOL%\\update.py"',
            text,
        )
        self.assertIn('set "PYTHONDONTWRITEBYTECODE=1"', text)
        self.assertIn(
            'if not "%MEETING_ROOM_UPDATE_NO_PAUSE%"=="1" pause',
            text,
        )

    def test_build_is_byte_deterministic(self) -> None:
        second = builder.build_repair_release(self.root / "second")
        self.assertEqual(
            self.first.artifact_path.read_bytes(),
            second.artifact_path.read_bytes(),
        )
        self.assertEqual(
            self.first.manifest_path.read_bytes(),
            second.manifest_path.read_bytes(),
        )

    def test_existing_release_is_never_overwritten(self) -> None:
        collision_root = self.root / "collision"
        release = collision_root / builder.RELEASE
        release.mkdir(parents=True)
        sentinel = release / "客户文件.txt"
        sentinel.write_bytes(b"must-survive")

        with self.assertRaisesRegex(
            builder.RepairPackageBuildError, "已经存在，拒绝覆盖"
        ):
            builder.build_repair_release(collision_root)

        self.assertEqual(sentinel.read_bytes(), b"must-survive")


if __name__ == "__main__":
    unittest.main()

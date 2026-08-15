from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


TOOL_DIR = Path(__file__).resolve().parents[1]
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

import 制作正式更新包 as builder  # noqa: E402
import 制作覆盖更新包 as repair_builder  # noqa: E402


class FormalPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        runtime_override = os.environ.get("MEETING_ROOM_RUNTIME_SOURCE")
        if runtime_override:
            repair_builder.FROZEN_RUNTIME_ROOT = Path(runtime_override)
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary_directory.name)
        cls.first = builder.build_formal_candidate(cls.root / "first")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def test_candidate_manifest_is_explicitly_not_formal_acceptance(self) -> None:
        manifest = json.loads(
            self.first.manifest_path.read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["release"], "V1.0.3-candidate")
        self.assertEqual(
            manifest["status"],
            "windows_acceptance_candidate_only",
        )
        self.assertFalse(
            manifest["acceptance"]["formal_external_release_allowed"]
        )
        self.assertTrue(
            manifest["acceptance"][
                "automation_is_not_uac_smartscreen_edr_or_lan_acceptance"
            ]
        )
        self.assertFalse(manifest["runtime"]["changed"])
        self.assertEqual(
            hashlib.sha256(self.first.artifact_path.read_bytes()).hexdigest(),
            manifest["artifact"]["sha256"],
        )

    def test_zip_uses_zero_argument_bat_and_python_direct_elevation_channel(
        self,
    ) -> None:
        with zipfile.ZipFile(self.first.artifact_path, "r") as archive:
            names = set(archive.namelist())
            launcher = archive.read(builder.DELIVERED_LAUNCHER)
            entry = archive.read(builder.DELIVERED_ENTRY).decode("utf-8")
            engine = archive.read(builder.DELIVERED_FORMAL_ENGINE).decode(
                "utf-8"
            )
        self.assertFalse(launcher.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(launcher.count(b"\n"), launcher.count(b"\r\n"))
        launcher_text = launcher.decode("utf-8")
        for forbidden in (
            "%1",
            "%*",
            "ArgumentList",
            "Start-Process",
            "--upgrade-broker",
            "cmd /c",
        ):
            self.assertNotIn(forbidden, launcher_text)
        self.assertIn(
            '"%UPDATE_TOOL%\\runtime\\python.exe" '
            '"%UPDATE_TOOL%\\update.py"',
            launcher_text,
        )
        self.assertIn("formal._run_elevated(tool_root, context)", entry)
        self.assertIn('info.lpVerb = "runas"', engine)
        self.assertIn(builder.DELIVERED_MANIFEST, names)
        self.assertIn(builder.DELIVERED_V102_ENGINE, names)
        self.assertIn(builder.RECOVERY_MANIFEST, names)
        self.assertFalse(
            any(
                name.startswith("升级到V") and name.endswith(".bat")
                for name in names
            )
        )

    def test_build_is_byte_deterministic_and_does_not_overwrite(self) -> None:
        second = builder.build_formal_candidate(self.root / "second")
        self.assertEqual(
            self.first.artifact_path.read_bytes(),
            second.artifact_path.read_bytes(),
        )
        self.assertEqual(
            self.first.manifest_path.read_bytes(),
            second.manifest_path.read_bytes(),
        )

        collision_root = self.root / "collision"
        existing = collision_root / builder.RELEASE
        existing.mkdir(parents=True)
        sentinel = existing / "客户文件.txt"
        sentinel.write_bytes(b"must-survive")
        with self.assertRaisesRegex(
            builder.FormalPackageBuildError,
            "已经存在，拒绝覆盖",
        ):
            builder.build_formal_candidate(collision_root)
        self.assertEqual(sentinel.read_bytes(), b"must-survive")


if __name__ == "__main__":
    unittest.main()

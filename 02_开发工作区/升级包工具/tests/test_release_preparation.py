from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import 准备发布 as release


class ReleasePreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "源代码工作区"
        self.skeleton = self.root / "发布骨架"
        self.release_root = self.root / "发布暂存"
        self.runtime = self.root / "runtime"
        self.frozen_package = self.root / "升级到V1.0.1.bat"

        (self.source / "static").mkdir(parents=True)
        (self.source / "templates").mkdir()
        (self.source / "tests").mkdir()
        (self.source / "data").mkdir()
        (self.skeleton / "_程序文件").mkdir(parents=True)
        self.runtime.mkdir()

        requirements = release.package_builder.DEFAULT_FROZEN_REQUIREMENTS.read_text(
            encoding="utf-8"
        )
        source_files = {
            "app.py": "SCHEMA_VERSION = 1\n",
            "server.py": "print('server')\n",
            "backup.py": "print('backup')\n",
            "migrate_check.py": "print('migrate')\n",
            "requirements.txt": requirements,
            "版本.txt": "1.0.2\n",
        }
        for name, content in source_files.items():
            (self.source / name).write_text(content, encoding="utf-8")
        (self.source / "static" / "app.css").write_text(
            "body { color: black; }\n", encoding="utf-8"
        )
        (self.source / "templates" / "index.html").write_text(
            "<h1>会议室</h1>\n", encoding="utf-8"
        )
        (self.source / "tests" / "test_private.py").write_text(
            "SECRET = True\n", encoding="utf-8"
        )
        (self.source / "data" / "secret.db").write_bytes(b"private")

        for name in release.TOP_LEVEL_FILES:
            (self.skeleton / name).write_text(
                f"release skeleton: {name}\n", encoding="utf-8"
            )
        (
            self.skeleton
            / "_程序文件"
            / release.CHECKLIST_TEMPLATE
        ).write_text("候选 V{version}\n", encoding="utf-8")
        (
            self.skeleton / "_程序文件" / "版本与校验信息.txt"
        ).write_text("runtime metadata\n", encoding="utf-8")
        (self.runtime / "python.exe").write_bytes(b"runtime")

        self.frozen_package.write_bytes(b"frozen-v101")
        self.frozen_hash = hashlib.sha256(b"frozen-v101").hexdigest()
        self.runtime_hash = release._sha256_records(
            (
                (
                    "python.exe",
                    hashlib.sha256(b"runtime").hexdigest(),
                ),
            )
        )
        self.frozen_patches = (
            mock.patch.object(
                release, "FROZEN_V101_PACKAGE", self.frozen_package
            ),
            mock.patch.object(
                release, "FROZEN_V101_SHA256", self.frozen_hash
            ),
            mock.patch.object(
                release,
                "FROZEN_RUNTIME_TREE_SHA256",
                self.runtime_hash,
            ),
        )
        for patcher in self.frozen_patches:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.frozen_patches):
            patcher.stop()
        self.temp_dir.cleanup()

    def prepare(self, **kwargs):
        options = {
            "source_dir": self.source,
            "skeleton_dir": self.skeleton,
            "release_root": self.release_root,
            "runtime_source": self.runtime,
        }
        options.update(kwargs)
        return release.prepare_release(**options)

    def test_prepares_payload_package_candidate_and_manifest_from_one_source(self):
        delivered_package = self.root / "输出" / "升级到V1.0.2.bat"
        delivered_manifest = self.root / "输出" / "V1.0.2-发布清单.json"
        result = self.prepare(
            package_output=delivered_package,
            manifest_output=delivered_manifest,
        )

        self.assertEqual(result.version, "1.0.2")
        self.assertTrue(result.release_dir.is_dir())
        self.assertEqual(result.package_path, delivered_package.resolve())
        self.assertEqual(result.manifest_path, delivered_manifest.resolve())
        self.assertTrue(result.candidate_dir.is_dir())
        self.assertTrue(
            (result.candidate_dir / "_程序文件" / "runtime" / "python.exe").is_file()
        )
        self.assertEqual(
            (
                result.payload_dir / "_程序文件" / "static" / "app.css"
            ).read_text(encoding="utf-8"),
            "body { color: black; }\n",
        )
        self.assertFalse((result.payload_dir / "_程序文件" / "tests").exists())
        self.assertFalse((result.payload_dir / "_程序文件" / "data").exists())
        self.assertIn(
            "候选 V1.0.2",
            (
                result.candidate_dir
                / "_程序文件"
                / "给网管的首次验收清单.txt"
            ).read_text(encoding="utf-8"),
        )
        checklist_bytes = (
            result.candidate_dir
            / "_程序文件"
            / "给网管的首次验收清单.txt"
        ).read_bytes()
        self.assertIn(b"\r\n", checklist_bytes)
        self.assertNotIn(b"\n", checklist_bytes.replace(b"\r\n", b""))

        manifest = json.loads(delivered_manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "1.0.2")
        self.assertEqual(manifest["database_schema_version"], 1)
        self.assertFalse(manifest["database_migration_required"])
        self.assertEqual(manifest["payload_file_count"], 14)
        self.assertEqual(
            manifest["package_sha256"],
            hashlib.sha256(delivered_package.read_bytes()).hexdigest(),
        )
        self.assertTrue(manifest["windows_candidate_included"])
        self.assertTrue(manifest["runtime_tree_sha256"])

    def test_package_only_does_not_require_or_claim_runtime_candidate(self):
        missing_runtime = self.root / "missing-runtime"
        result = release.prepare_release(
            source_dir=self.source,
            skeleton_dir=self.skeleton,
            release_root=self.release_root,
            runtime_source=missing_runtime,
            include_runtime=False,
        )
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertIsNone(result.candidate_dir)
        self.assertFalse(manifest["windows_candidate_included"])
        self.assertIsNone(manifest["runtime_tree_sha256"])

    def test_existing_version_staging_is_never_overwritten(self):
        target = self.release_root / "V1.0.2"
        target.mkdir(parents=True)
        marker = target / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(
            release.ReleasePreparationError, "拒绝覆盖"
        ):
            self.prepare()
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_failure_cleans_temporary_staging_and_does_not_deliver_package(self):
        (self.skeleton / "使用说明.txt").unlink()
        output = self.root / "输出" / "升级到V1.0.2.bat"
        with self.assertRaises(release.ReleasePreparationError):
            self.prepare(package_output=output)
        self.assertFalse(output.exists())
        self.assertFalse((self.release_root / "V1.0.2").exists())
        self.assertEqual(
            list(self.release_root.iterdir()) if self.release_root.exists() else [],
            [],
        )

    def test_external_pair_failure_restores_old_files_and_removes_staging(self):
        package_output = self.root / "输出" / "升级到V1.0.2.bat"
        manifest_output = self.root / "输出" / "V1.0.2-发布清单.json"
        package_output.parent.mkdir()
        package_output.write_bytes(b"old-package")
        manifest_output.write_bytes(b"old-manifest")
        real_replace = release.os.replace

        def fail_manifest_replace(source, destination):
            if Path(destination) == manifest_output.resolve():
                raise OSError("deliberate manifest delivery failure")
            return real_replace(source, destination)

        with mock.patch.object(
            release.os, "replace", side_effect=fail_manifest_replace
        ):
            with self.assertRaisesRegex(
                OSError, "deliberate manifest delivery failure"
            ):
                self.prepare(
                    package_output=package_output,
                    manifest_output=manifest_output,
                )

        self.assertEqual(package_output.read_bytes(), b"old-package")
        self.assertEqual(manifest_output.read_bytes(), b"old-manifest")
        self.assertFalse((self.release_root / "V1.0.2").exists())
        self.assertEqual(
            list(package_output.parent.glob(".*.tmp")),
            [],
        )

    def test_rejects_historical_or_mismatched_release_version(self):
        (self.source / "版本.txt").write_text("1.0.1\n", encoding="utf-8")
        with self.assertRaisesRegex(
            release.ReleasePreparationError, "高于 V1.0.1"
        ):
            self.prepare()

        (self.source / "版本.txt").write_text("1.0.2\n", encoding="utf-8")
        with self.assertRaisesRegex(
            release.ReleasePreparationError, "必须命名"
        ):
            self.prepare(package_output=self.root / "wrong-name.bat")

    def test_frozen_v101_hash_is_a_hard_gate(self):
        self.frozen_package.write_bytes(b"changed")
        with self.assertRaisesRegex(
            release.ReleasePreparationError, "V1.0.1.*发生变化"
        ):
            self.prepare()
        self.assertFalse((self.release_root / "V1.0.2").exists())

    def test_frozen_runtime_hash_is_a_hard_gate(self):
        (self.runtime / "python.exe").write_bytes(b"changed-runtime")
        with self.assertRaisesRegex(
            release.ReleasePreparationError, "runtime.*冻结基线"
        ):
            self.prepare()
        self.assertFalse((self.release_root / "V1.0.2").exists())

    def test_resource_trees_fail_closed_on_sensitive_or_unknown_files(self):
        attempts = (
            self.source / "static" / ".env",
            self.source / "static" / "debug.log",
            self.source / "static" / "cache.db",
            self.source / "templates" / "tests" / "private.html",
        )
        for path in attempts:
            with self.subTest(path=path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("must not ship\n", encoding="utf-8")
                try:
                    with self.assertRaisesRegex(
                        release.ReleasePreparationError,
                        "禁止发布|未允许",
                    ):
                        self.prepare()
                    self.assertFalse(
                        (self.release_root / "V1.0.2").exists()
                    )
                finally:
                    path.unlink()
                    if path.parent.name == "tests":
                        path.parent.rmdir()

    def test_outputs_cannot_touch_source_skeleton_runtime_or_staging(self):
        attempts = (
            {
                "release_root": self.source,
            },
            {
                "package_output": self.source / "升级到V1.0.2.bat",
            },
            {
                "manifest_output": (
                    self.skeleton / "V1.0.2-发布清单.json"
                ),
            },
            {
                "package_output": (
                    self.release_root / "V1.0.2" / "升级到V1.0.2.bat"
                ),
            },
        )
        for attempt in attempts:
            with self.subTest(attempt=attempt):
                with self.assertRaisesRegex(
                    release.ReleasePreparationError, "受保护"
                ):
                    self.prepare(**attempt)
                self.assertFalse(
                    (self.release_root / "V1.0.2").exists()
                )


if __name__ == "__main__":
    unittest.main()

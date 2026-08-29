from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from v2.installer.build_update_package import (
    ARTIFACT_NAME,
    PackageBuildError,
    build_update_package,
)
from v2.installer.installer_core import sha256_bytes, tree_digest
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
        self.assertEqual(bundle.manifest["version"], "2.5.0")
        self.assertEqual(bundle.supported_source_versions, frozenset({"2.1.0"}))
        self.assertFalse(bundle.manifest["acceptance"]["formal_external_release_allowed"])

    def test_outer_package_has_zero_parameter_launcher_and_no_mutable_data(self) -> None:
        result = self._build("outer")
        with zipfile.ZipFile(result.artifact_path, "r") as archive:
            names = archive.namelist()
            launcher = archive.read("升级到V2.5.0.bat").decode("utf-8")
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

    def test_build_refuses_payload_without_help_entry(self) -> None:
        # 帮助中心入口是更新 payload 必需件：缺件必须在构建层拒绝，
        # 否则升级包会整体替换 app 树、把旧版本可用的帮助中心一并移除。
        source = self.root / "no-help-build"
        source.mkdir()
        payload, runtime = create_inputs(source)
        (payload / "_程序文件" / "app" / "static" / "help" / "index.html").unlink()
        output = source / "out"
        output.mkdir()
        with self.assertRaises(PackageBuildError) as raised:
            build_update_package(payload, runtime, output / ARTIFACT_NAME, _test_fixture=True)
        self.assertIn("help/index.html", str(raised.exception))

    def test_load_rejects_update_payload_without_help_entry(self) -> None:
        # 构建层已拒绝缺 help 的 payload；这里构造"绕过当前构建器"的更新包
        # （例如旧版工具或手工拼包），验证加载层 required 集同样拒绝加载。
        result = self._build("no-help-load")
        extracted = self.root / "no-help-load-extracted"
        extracted.mkdir()
        with zipfile.ZipFile(result.artifact_path, "r") as archive:
            archive.extractall(extracted)
        tool = extracted / "_V2更新工具"
        payload_zip_path = tool / "payload-update.zip"
        with zipfile.ZipFile(payload_zip_path, "r") as payload_archive:
            members = {
                info.filename: payload_archive.read(info.filename)
                for info in payload_archive.infolist()
            }
        help_entry = "_程序文件/app/static/help/index.html"
        self.assertIn(help_entry, members)
        del members[help_entry]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as rewritten:
            for name in sorted(members):
                rewritten.writestr(name, members[name])
        payload_zip = buffer.getvalue()
        payload_zip_path.write_bytes(payload_zip)
        manifest_path = tool / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        records = [
            record
            for record in manifest["payload"]["files"]
            if record["path"] != help_entry
        ]
        manifest["payload"].update(
            size=len(payload_zip),
            sha256=sha256_bytes(payload_zip),
            tree_sha256=tree_digest(records),
            files=records,
        )
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
        )
        with self.assertRaises(UpdatePolicyError) as raised:
            UpdateBundle.load(tool)
        self.assertIn("缺少服务或前端入口", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

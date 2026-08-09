from __future__ import annotations

import json
import importlib.util
import inspect
import re
import tempfile
import unittest
from pathlib import Path

from v2.installer.installer_core import (
    GENERATION_FILE,
    INSTALL_INFO,
    INSTALLED_MANIFEST,
    SECRET_FILE,
    VERSION_FILE,
    ExclusiveLock,
    InstallBusy,
    InstallCommittedError,
    InstallTransaction,
    InstallerError,
    PassiveSystemController,
    RollbackError,
    WindowsSystemController,
    decode_elevation_context,
    encode_elevation_context,
    validate_target,
)
from v2.installer.tests.helpers import load_fixture_bundle


_IDENTITY_PATH = (
    Path(__file__).resolve().parents[2] / "backend" / "v2app" / "runtime" / "identity.py"
)
_IDENTITY_SPEC = importlib.util.spec_from_file_location(
    "_v2_backend_runtime_identity_contract",
    _IDENTITY_PATH,
)
if _IDENTITY_SPEC is None or _IDENTITY_SPEC.loader is None:
    raise RuntimeError("无法加载 V2 后端身份契约")
_IDENTITY_MODULE = importlib.util.module_from_spec(_IDENTITY_SPEC)
_IDENTITY_SPEC.loader.exec_module(_IDENTITY_MODULE)
load_or_create_secret = _IDENTITY_MODULE.load_or_create_secret


class ActivateFailureController(PassiveSystemController):
    def activate(self, install_root: Path, install_id: str) -> None:
        del install_root, install_id
        raise InstallerError("synthetic activation failure")


class UncertainResourceController(PassiveSystemController):
    def configure_disabled(self, install_root: Path, install_id: str) -> None:
        del install_root, install_id
        raise InstallerError("synthetic partial resource failure")

    def rollback_resources(self, install_root: Path, install_id: str) -> None:
        del install_root, install_id
        raise InstallerError("synthetic resource rollback failure")


class InstallerCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        _, self.bundle = load_fixture_bundle(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_absent_target_installs_dynamic_v2_identity(self) -> None:
        target = self.root / "客户目录 中文 空格 & (1)"
        controller = PassiveSystemController()
        result = InstallTransaction(
            self.bundle,
            target,
            controller,
            health_probe=None,
        ).run()
        self.assertEqual(result.install_root, target.resolve())
        info = json.loads((target / INSTALL_INFO).read_text(encoding="utf-8"))
        self.assertEqual(info["product_generation"], 2)
        self.assertEqual(info["port"], 8080)
        self.assertEqual(info["setup_bind"], "127.0.0.1")
        self.assertFalse(info["setup_complete"])
        self.assertEqual((target / VERSION_FILE).read_text().strip(), "2.0.0")
        self.assertEqual((target / GENERATION_FILE).read_text().strip(), "2")
        self.assertTrue((target / SECRET_FILE).is_file())
        self.assertRegex(
            (target / SECRET_FILE).read_text().strip(),
            re.compile(r"[0-9a-f]{64}"),
        )
        self.assertEqual(
            load_or_create_secret(target / SECRET_FILE),
            (target / SECRET_FILE).read_text().strip(),
        )
        self.assertNotIn(
            (target / SECRET_FILE).read_text().strip(),
            (target / INSTALLED_MANIFEST).read_text(encoding="utf-8"),
        )
        self.assertTrue(controller.configured)
        self.assertTrue(controller.activated)

    def test_preexisting_empty_target_is_supported(self) -> None:
        target = self.root / "empty"
        target.mkdir()
        result = InstallTransaction(
            self.bundle,
            target,
            PassiveSystemController(),
            health_probe=None,
        ).run()
        self.assertEqual(result.install_root, target.resolve())
        self.assertTrue((target / VERSION_FILE).is_file())

    def test_nonempty_target_is_rejected_without_touching_sentinel(self) -> None:
        target = self.root / "nonempty"
        target.mkdir()
        sentinel = target / "old-customer-data.db"
        sentinel.write_bytes(b"keep-v1")
        with self.assertRaises(InstallerError):
            InstallTransaction(
                self.bundle,
                target,
                PassiveSystemController(),
                health_probe=None,
            )
        self.assertEqual(sentinel.read_bytes(), b"keep-v1")

    def test_adjacent_v1_tree_is_never_read_or_modified(self) -> None:
        old = self.root / "会议室预约系统"
        (old / "_程序文件" / "data").mkdir(parents=True)
        sentinel = old / "_程序文件" / "data" / "reservation.db"
        sentinel.write_bytes(b"synthetic-v1-data")
        target = self.root / "会议室预约系统V2"
        InstallTransaction(
            self.bundle,
            target,
            PassiveSystemController(),
            health_probe=None,
        ).run()
        self.assertEqual(sentinel.read_bytes(), b"synthetic-v1-data")
        self.assertEqual(sorted(path.name for path in old.rglob("*")), ["_程序文件", "data", "reservation.db"])

    def test_precommit_failure_removes_only_current_transaction(self) -> None:
        target = self.root / "rollback"
        controller = PassiveSystemController()

        def fault(stage: str) -> None:
            if stage == "resources_configured":
                raise RuntimeError("synthetic precommit fault")

        with self.assertRaises(RuntimeError):
            InstallTransaction(
                self.bundle,
                target,
                controller,
                health_probe=None,
                fault_hook=fault,
            ).run()
        self.assertFalse(target.exists())
        self.assertTrue(controller.rolled_back)

    def test_postcommit_failure_preserves_v2_files_and_dynamic_data(self) -> None:
        target = self.root / "committed"
        with self.assertRaises(InstallCommittedError):
            InstallTransaction(
                self.bundle,
                target,
                ActivateFailureController(),
                health_probe=None,
            ).run()
        self.assertTrue((target / VERSION_FILE).is_file())
        self.assertTrue((target / INSTALL_INFO).is_file())
        self.assertTrue((target / SECRET_FILE).is_file())

    def test_uncertain_external_rollback_preserves_target_for_repair(self) -> None:
        target = self.root / "uncertain-resources"
        with self.assertRaises(RollbackError):
            InstallTransaction(
                self.bundle,
                target,
                UncertainResourceController(),
                health_probe=None,
            ).run()
        self.assertTrue((target / INSTALL_INFO).is_file())
        self.assertFalse((target / VERSION_FILE).exists())

    def test_elevation_context_is_bound_to_manifest_and_target(self) -> None:
        target = self.root / "elevation"
        value = encode_elevation_context(target, self.bundle.manifest_sha256)
        decoded = decode_elevation_context(value, self.bundle.manifest_sha256)
        self.assertEqual(decoded, target.resolve())
        with self.assertRaises(InstallerError):
            decode_elevation_context(value, "0" * 64)

    def test_exclusive_lock_rejects_concurrent_holder(self) -> None:
        lock_path = self.root / "installer.lock"
        with ExclusiveLock(lock_path):
            with self.assertRaises(InstallBusy):
                with ExclusiveLock(lock_path):
                    pass

    def test_target_must_be_absolute_and_empty(self) -> None:
        with self.assertRaises(InstallerError):
            validate_target(Path("relative-target"))

    def test_windows_fresh_install_refuses_existing_registry_identity(self) -> None:
        source = inspect.getsource(WindowsSystemController.configure_disabled)
        registry_check = source.index("Test-Path -LiteralPath $env:MRV2_REGISTRY_KEY")
        registry_create = source.index("New-Item -Path $env:MRV2_REGISTRY_KEY")
        self.assertLess(registry_check, registry_create)
        self.assertIn("V2 专属安装登记已经存在，拒绝覆盖", source)
        self.assertNotIn(
            "New-Item -Path $env:MRV2_REGISTRY_KEY -Force",
            source,
        )

    def test_windows_resources_stay_disabled_until_owned_activation(self) -> None:
        configure = inspect.getsource(WindowsSystemController.configure_disabled)
        activate = inspect.getsource(WindowsSystemController.activate)
        self.assertEqual(configure.count("-Enabled False"), 2)
        self.assertIn("Disable-ScheduledTask", configure)
        self.assertIn("registered.InstallId", activate)
        self.assertIn("registered.InstallRoot", activate)
        self.assertIn("rules[0].Description", activate)
        self.assertEqual(activate.count("Enable-NetFirewallRule"), 1)
        self.assertLess(
            activate.index("Enable-NetFirewallRule"),
            activate.index("Start-ScheduledTask"),
        )


if __name__ == "__main__":
    unittest.main()

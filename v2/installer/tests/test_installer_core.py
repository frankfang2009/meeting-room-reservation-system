from __future__ import annotations

import json
import importlib.util
import inspect
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from v2.installer import install as install_entry
from v2.installer.installer_core import (
    GENERATION_FILE,
    INSTALL_INFO,
    INSTALLED_MANIFEST,
    SECRET_FILE,
    TRANSACTION_FILE,
    VERSION_FILE,
    ExclusiveLock,
    InstallBusy,
    InstallCommittedError,
    InstallTransaction,
    InstallerError,
    PassiveSystemController,
    RollbackError,
    WindowsSystemController,
    assert_service_port_available,
    decode_elevation_context,
    encode_elevation_context,
    production_install_root,
    validate_target,
    windows_system_directory,
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


class ContainmentFailureController(ActivateFailureController):
    def contain_committed(self, install_root: Path, install_id: str) -> None:
        del install_root, install_id
        raise InstallerError("synthetic containment failure")


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
        self.assertTrue(controller.staging_secured)
        self.assertEqual(controller.security_verifications, 2)

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

    def test_target_race_before_commit_never_overwrites_new_content(self) -> None:
        for preexisting in (False, True):
            with self.subTest(preexisting=preexisting):
                target = self.root / f"race-{preexisting}"
                if preexisting:
                    target.mkdir()
                transaction = InstallTransaction(
                    self.bundle,
                    target,
                    PassiveSystemController(),
                    health_probe=None,
                    fault_hook=lambda stage, root=target: (
                        (root.mkdir(exist_ok=True), (root / "new-data.db").write_bytes(b"keep"))
                        if stage == "files_verified"
                        else None
                    ),
                )
                with self.assertRaises(InstallerError):
                    transaction.run()
                self.assertEqual((target / "new-data.db").read_bytes(), b"keep")

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
        controller = ActivateFailureController()
        with self.assertRaises(InstallCommittedError):
            InstallTransaction(
                self.bundle,
                target,
                controller,
                health_probe=None,
            ).run()
        self.assertTrue((target / VERSION_FILE).is_file())
        self.assertTrue((target / INSTALL_INFO).is_file())
        self.assertTrue((target / SECRET_FILE).is_file())
        self.assertTrue(controller.contained)
        self.assertTrue(controller.configured)

    def test_postcommit_containment_failure_is_diagnostic_and_preserves_identity(self) -> None:
        target = self.root / "containment-failed"
        with self.assertRaisesRegex(
            InstallCommittedError, "synthetic containment failure"
        ):
            InstallTransaction(
                self.bundle,
                target,
                ContainmentFailureController(),
                health_probe=None,
            ).run()
        self.assertTrue((target / VERSION_FILE).is_file())
        self.assertTrue((target / INSTALL_INFO).is_file())
        self.assertTrue((target / SECRET_FILE).is_file())

    def test_precommit_cleanup_failure_is_rollback_error_and_preserves_scene(self) -> None:
        target = self.root / "cleanup-failed"

        def fault(stage: str) -> None:
            if stage == "resources_configured":
                raise RuntimeError("synthetic precommit fault")

        with mock.patch(
            "v2.installer.installer_core.shutil.rmtree",
            side_effect=OSError("synthetic rmtree failure"),
        ):
            with self.assertRaisesRegex(RollbackError, "synthetic rmtree failure"):
                InstallTransaction(
                    self.bundle,
                    target,
                    PassiveSystemController(),
                    health_probe=None,
                    fault_hook=fault,
                ).run()
        self.assertTrue((target / TRANSACTION_FILE).is_file())
        self.assertFalse((target / VERSION_FILE).exists())

    def test_precommit_identity_change_is_rollback_error(self) -> None:
        target = self.root / "identity-changed"

        def fault(stage: str) -> None:
            if stage == "resources_configured":
                (target / TRANSACTION_FILE).write_text(
                    '{"transaction_id":"different"}\n', encoding="utf-8"
                )
                raise RuntimeError("synthetic precommit fault")

        with self.assertRaisesRegex(RollbackError, "事务身份已变化"):
            InstallTransaction(
                self.bundle,
                target,
                PassiveSystemController(),
                health_probe=None,
                fault_hook=fault,
            ).run()
        self.assertTrue(target.is_dir())

    def test_preexisting_empty_restore_failure_is_rollback_error(self) -> None:
        target = self.root / "empty-restore-failed"
        target.mkdir()
        original_replace = os.replace

        def controlled_replace(source, destination):
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                source_path.name.startswith(f".{target.name}.empty-")
                and destination_path.name == target.name
            ):
                raise OSError("synthetic empty restore failure")
            return original_replace(source, destination)

        def fault(stage: str) -> None:
            if stage == "resources_configured":
                raise RuntimeError("synthetic precommit fault")

        with mock.patch(
            "v2.installer.installer_core.os.replace", side_effect=controlled_replace
        ):
            with self.assertRaisesRegex(
                RollbackError, "synthetic empty restore failure"
            ):
                InstallTransaction(
                    self.bundle,
                    target,
                    PassiveSystemController(),
                    health_probe=None,
                    fault_hook=fault,
                ).run()
        self.assertFalse(target.exists())
        self.assertEqual(
            len(list(self.root.glob(f".{target.name}.empty-*"))), 1
        )

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
        self.assertIn("TransactionInstallId", source)
        rollback = inspect.getsource(WindowsSystemController.rollback_resources)
        self.assertIn("defaultMarker", rollback)
        self.assertIn("拒绝删除", rollback)

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

    def test_windows_postcommit_containment_is_identity_bound_and_non_destructive(self) -> None:
        contain = inspect.getsource(WindowsSystemController.contain_committed)
        for identity_check in (
            "registered.TransactionInstallId",
            "registered.InstallId",
            "registered.SecurityInstallId",
            "registered.InstallRoot",
            "task.Description",
            "rules[0].Description",
        ):
            self.assertIn(identity_check, contain)
        self.assertEqual(contain.count("Disable-ScheduledTask"), 1)
        self.assertEqual(contain.count("Disable-NetFirewallRule"), 1)
        self.assertIn("Stop-ScheduledTask", contain)
        for destructive in (
            "Unregister-ScheduledTask",
            "Remove-NetFirewallRule",
            "Remove-Item",
        ):
            self.assertNotIn(destructive, contain)

    def test_rollback_error_maps_to_dedicated_exit_code_six(self) -> None:
        source = inspect.getsource(install_entry.main)
        self.assertIn("except RollbackError as error", source)
        rollback_handler = source[source.index("except RollbackError as error") :]
        self.assertIn("return 6", rollback_handler)

    def test_staging_is_private_before_any_transaction_identity_or_log(self) -> None:
        run_source = inspect.getsource(InstallTransaction.run)
        self.assertLess(run_source.index("staging.mkdir()"), run_source.index("secure_staging"))
        self.assertLess(run_source.index("secure_staging"), run_source.index("state_path ="))
        self.assertLess(run_source.index("secure_staging"), run_source.index("self.log.add_path"))
        secure = inspect.getsource(WindowsSystemController.secure_staging)
        self.assertIn("S-1-5-18:(OI)(CI)F", secure)
        self.assertIn("S-1-5-32-544:(OI)(CI)F", secure)
        self.assertNotIn("S-1-5-32-545", secure)

    def test_windows_acl_never_recursively_opens_private_tree_to_users(self) -> None:
        configure = inspect.getsource(WindowsSystemController.configure_disabled)
        verify = inspect.getsource(WindowsSystemController.verify_security)
        self.assertNotIn("$root /grant:r", configure)
        self.assertNotIn("$root /reset /T", configure)
        self.assertLess(
            configure.index("foreach ($private in $privateRoots)"),
            configure.index("if (-not $isPrivate) { Set-FileAcl $fullPath $true }"),
        )
        self.assertIn("Get-ChildItem -LiteralPath $root -Force -Recurse", verify)
        self.assertIn("-band (-bnot $allowed)", verify)
        self.assertIn("私有目录向 Users 暴露", verify)
        self.assertIn("受保护身份文件与当前 install_id 不一致", verify)

    def test_windows_tasks_use_one_daily_backup_bound_to_install_id(self) -> None:
        configure = inspect.getsource(WindowsSystemController.configure_disabled)
        verify = inspect.getsource(WindowsSystemController.verify_security)
        self.assertEqual(configure.count("New-ScheduledTaskTrigger -Daily"), 1)
        self.assertEqual(configure.count("New-ScheduledTaskTrigger -AtStartup"), 1)
        self.assertIn("-At '02:00'", configure)
        self.assertIn("-StartWhenAvailable -MultipleInstances IgnoreNew", configure)
        self.assertIn("--scheduled --expected-install-id", configure)
        self.assertNotIn("BACKUP_CATCHUP", configure)
        self.assertIn("MSFT_TaskDailyTrigger", verify)
        self.assertIn("StartWhenAvailable", verify)
        self.assertIn("MultipleInstances", verify)
        self.assertIn("expectedBackupArguments", verify)

    def test_production_entry_has_fixed_known_folder_and_no_test_env_bypass(self) -> None:
        root_source = inspect.getsource(production_install_root)
        install_source = inspect.getsource(install_entry)
        self.assertIn("SHGetKnownFolderPath", root_source)
        self.assertNotIn("ProgramFiles", root_source)
        self.assertNotIn("os.environ", root_source)
        self.assertNotIn("--install-root", install_source)
        for bypass in (
            "MEETING_ROOM_V2_INSTALL_TEST_MODE",
            "MEETING_ROOM_V2_INSTALL_SKIP_HEALTH",
            "MEETING_ROOM_V2_INSTALL_CONFIRM",
        ):
            self.assertNotIn(bypass, install_source)
        self.assertIn("_test_target", install_source)
        self.assertIn("assert_production_target", install_source)

    def test_windows_native_tools_and_port_probe_are_not_path_hijackable_or_destructive(self) -> None:
        system_source = inspect.getsource(windows_system_directory)
        powershell_source = inspect.getsource(WindowsSystemController._run_powershell)
        port_source = inspect.getsource(assert_service_port_available)
        self.assertIn("GetSystemDirectoryW", system_source)
        self.assertIn('system32 / "WindowsPowerShell"', powershell_source)
        self.assertIn('system32 / "icacls.exe"', powershell_source)
        self.assertIn('merged["PSModulePath"]', powershell_source)
        self.assertIn('merged["PATH"]', powershell_source)
        self.assertIn('merged["ComSpec"]', powershell_source)
        self.assertIn("untrusted_search", powershell_source)
        self.assertNotIn('"powershell.exe",', powershell_source)
        self.assertIn("不会结束未知进程", port_source)
        self.assertNotIn("taskkill", port_source.casefold())


if __name__ == "__main__":
    unittest.main()

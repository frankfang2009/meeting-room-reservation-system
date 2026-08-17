from __future__ import annotations

import json
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from v2.installer.installer_core import (
    INSTALLED_MANIFEST,
    INSTALL_INFO,
    VERSION_FILE,
    InstallTransaction,
    PassiveSystemController,
    records_for_tree,
    sha256_bytes,
    tree_digest,
)
from v2.installer.tests.helpers import load_fixture_bundle
from v2.installer.update_core import (
    EXPECTED_V2_TABLES,
    PassiveUpdateSystemController,
    UpdateBundle,
    UpdatePayload,
    UpdatePolicyError,
    UpdateResult,
    UpdateRollbackError,
    V2UpdateTransaction,
    V2UpdatePreflight,
    WindowsUpdateSystemController,
    assert_update_payload_safe,
    build_update_state,
    load_v2_identity,
    read_installed_version,
    resolve_install_root,
    snapshot_protected_data,
)


class UpdateCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        _, bundle = load_fixture_bundle(self.root)
        self.install_root = self.root / "installed"
        InstallTransaction(
            bundle,
            self.install_root,
            PassiveSystemController(),
            health_probe=None,
        ).run()
        self._downgrade_to_v210()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _downgrade_to_v210(self) -> None:
        info_path = self.install_root / INSTALL_INFO
        info = json.loads(info_path.read_text(encoding="utf-8"))
        info["installed_version"] = "2.1.0"
        info_path.write_text(
            json.dumps(info, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_path = self.install_root / INSTALLED_MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = "2.1.0"
        manifest["release"] = "V2.1.0"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (self.install_root / VERSION_FILE).write_text("2.1.0\n", encoding="ascii")

    def _update_bundle(self) -> UpdateBundle:
        files = {}
        for name in ("app", "runtime"):
            root = self.install_root / "_程序文件" / name
            for record in records_for_tree(root):
                relative = f"_程序文件/{name}/{record['path']}"
                files[relative] = root.joinpath(*str(record["path"]).split("/")).read_bytes()
        files["_程序文件/app/service.py"] = b"# V2.2.0 updated service\n"
        records = tuple(
            {
                "path": relative,
                "size": len(content),
                "sha256": sha256_bytes(content),
            }
            for relative, content in sorted(files.items())
        )
        payload = UpdatePayload(
            zip_path=self.root / "synthetic-update.zip",
            zip_sha256="b" * 64,
            tree_sha256=tree_digest(records),
            records=records,
            files=files,
        )
        manifest = {
            "schema": 1,
            "kind": "v2-cumulative-update",
            "product_generation": 2,
            "version": "2.2.0",
            "release": "V2.2.0",
        }
        return UpdateBundle(
            tool_root=self.root,
            manifest=manifest,
            manifest_sha256="c" * 64,
            payload=payload,
            supported_source_versions=frozenset({"2.1.0"}),
        )

    def _create_v2_database(self, *, setup_complete: bool, schema_version: int = 2) -> Path:
        database = self.install_root / "_程序文件" / "data" / "reservation.db"
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO app_meta VALUES (?, ?)",
            (
                ("product_generation", "2"),
                ("schema_version", str(schema_version)),
                ("setup_complete", "1" if setup_complete else "0"),
            ),
        )
        for table in sorted(EXPECTED_V2_TABLES - {"app_meta"}):
            connection.execute(f'CREATE TABLE "{table}" (id TEXT PRIMARY KEY)')
        if setup_complete:
            connection.execute("INSERT INTO users VALUES ('admin')")
            connection.execute("INSERT INTO rooms VALUES ('room-1')")
        connection.commit()
        connection.close()
        return database

    def test_explicit_v2_identity_is_accepted_before_setup(self) -> None:
        identity = load_v2_identity(self.install_root)
        self.assertEqual(identity.version, "2.1.0")
        self.assertFalse(identity.setup_complete)
        self.assertFalse(identity.database.exists())

    def test_read_installed_version_uses_public_file_and_rejects_pre_v2(self) -> None:
        self.assertEqual(read_installed_version(self.install_root), "2.1.0")
        (self.install_root / VERSION_FILE).write_text("1.0.2\n", encoding="ascii")
        with self.assertRaises(UpdatePolicyError):
            read_installed_version(self.install_root)

    def test_v2_database_generation_two_is_required_after_setup(self) -> None:
        self._create_v2_database(setup_complete=True)
        info_path = self.install_root / INSTALL_INFO
        info = json.loads(info_path.read_text(encoding="utf-8"))
        info["setup_complete"] = True
        info_path.write_text(json.dumps(info, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        identity = load_v2_identity(self.install_root)
        self.assertTrue(identity.setup_complete)

    def test_schema_v1_baseline_is_accepted_for_in_place_migration(self) -> None:
        self._create_v2_database(setup_complete=True, schema_version=1)
        info_path = self.install_root / INSTALL_INFO
        info = json.loads(info_path.read_text(encoding="utf-8"))
        info["setup_complete"] = True
        info_path.write_text(json.dumps(info, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        identity = load_v2_identity(self.install_root)
        self.assertTrue(identity.setup_complete)

    def test_noncanonical_schema_version_is_rejected(self) -> None:
        database = self._create_v2_database(setup_complete=True)
        connection = sqlite3.connect(database)
        connection.execute(
            "UPDATE app_meta SET value = '02' WHERE key = 'schema_version'"
        )
        connection.commit()
        connection.close()
        info_path = self.install_root / INSTALL_INFO
        info = json.loads(info_path.read_text(encoding="utf-8"))
        info["setup_complete"] = True
        info_path.write_text(
            json.dumps(info, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        with self.assertRaises(UpdatePolicyError):
            load_v2_identity(self.install_root)

    def test_setup_mirror_must_match_database_truth(self) -> None:
        self._create_v2_database(setup_complete=True)
        with self.assertRaises(UpdatePolicyError):
            load_v2_identity(self.install_root)

    def test_unconfigured_database_cannot_contain_business_rows(self) -> None:
        database = self._create_v2_database(setup_complete=False)
        connection = sqlite3.connect(database)
        connection.execute("INSERT INTO users VALUES ('unexpected-user')")
        connection.commit()
        connection.close()
        with self.assertRaises(UpdatePolicyError):
            load_v2_identity(self.install_root)

    def test_v1_or_unknown_database_is_rejected(self) -> None:
        data = self.install_root / "_程序文件" / "data"
        database = data / "reservation.db"
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO app_meta VALUES ('product_generation', '1')")
        connection.commit()
        connection.close()
        with self.assertRaises(UpdatePolicyError):
            load_v2_identity(self.install_root)

    def test_update_payload_cannot_touch_any_mutable_or_identity_file(self) -> None:
        forbidden = (
            "_程序文件/data/reservation.db",
            "_程序文件/data/unknown.bin",
            "_程序文件/backups/old.db",
            "_程序文件/logs/server.log",
            "_程序文件/版本.txt",
            "_程序文件/产品代际.txt",
            "_程序文件/release-manifest.json",
        )
        for relative in forbidden:
            with self.subTest(relative=relative):
                with self.assertRaises(UpdatePolicyError):
                    assert_update_payload_safe([{"path": relative}])
        assert_update_payload_safe([{"path": "_程序文件/app/routes.py"}])

    def test_snapshot_copies_unknown_data_and_verifies_tree(self) -> None:
        unknown = self.install_root / "_程序文件" / "data" / "客户附件" / "未知.bin"
        unknown.parent.mkdir()
        unknown.write_bytes(b"preserve-me")
        identity = load_v2_identity(self.install_root)
        snapshot = snapshot_protected_data(identity, self.root / "rollback" / "tx1")
        self.assertEqual((snapshot.data_root / "客户附件" / "未知.bin").read_bytes(), b"preserve-me")
        manifest = json.loads(snapshot.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["product_generation"], 2)
        self.assertEqual(manifest["tree_sha256"], snapshot.tree_sha256)

    def test_update_state_binds_root_identity_version_and_payload_hash(self) -> None:
        identity = load_v2_identity(self.install_root)
        state = build_update_state(identity, "2.1.1", "a" * 64)
        self.assertEqual(state["install_root"], str(self.install_root.resolve()))
        self.assertEqual(state["install_id"], identity.install_id)
        self.assertEqual(state["source_version"], "2.1.0")
        self.assertEqual(state["target_version"], "2.1.1")
        with self.assertRaises(UpdatePolicyError):
            build_update_state(identity, "2.1.0", "a" * 64)

    def test_preflight_snapshots_before_future_program_writes(self) -> None:
        preflight = V2UpdatePreflight(
            self.install_root,
            "2.1.1",
            "b" * 64,
            [{"path": "_程序文件/app/routes.py"}],
        )
        snapshot = preflight.snapshot(self.root / "rollback-preflight")
        self.assertGreater(snapshot.file_count, 0)

    def test_resolver_never_searches_when_no_explicit_source(self) -> None:
        old = self.root / "会议室预约系统"
        old.mkdir()
        sentinel = old / "reservation.db"
        sentinel.write_bytes(b"v1")
        if __import__("os").name != "nt":
            with self.assertRaises(UpdatePolicyError):
                resolve_install_root()
        self.assertEqual(sentinel.read_bytes(), b"v1")

    def test_cumulative_update_preserves_unknown_data_and_original_run_state(self) -> None:
        unknown = self.install_root / "_程序文件" / "data" / "现场附件.bin"
        unknown.write_bytes(b"customer-data")
        controller = PassiveUpdateSystemController(running=False)
        result = V2UpdateTransaction(
            self._update_bundle(),
            self.install_root,
            controller,
            online_backup=None,
            health_probe=None,
        ).run()
        self.assertEqual(result.source_version, "2.1.0")
        self.assertEqual(result.target_version, "2.2.0")
        self.assertEqual((self.install_root / VERSION_FILE).read_text().strip(), "2.2.0")
        self.assertEqual(unknown.read_bytes(), b"customer-data")
        self.assertEqual(
            (self.install_root / "_程序文件" / "app" / "service.py").read_bytes(),
            b"# V2.2.0 updated service\n",
        )
        self.assertFalse(controller.running)
        self.assertTrue(result.receipt_path.is_file())

    def test_successful_update_applies_program_security_before_verify(self) -> None:
        controller = PassiveUpdateSystemController(running=False)
        events: list[str] = []
        controller.apply_security = lambda identity: events.append("apply")  # type: ignore[method-assign]
        controller.verify = lambda identity: events.append("verify")  # type: ignore[method-assign]
        result = V2UpdateTransaction(
            self._update_bundle(),
            self.install_root,
            controller,
            online_backup=None,
            health_probe=None,
        ).run()
        self.assertEqual(result.target_version, "2.2.0")
        # Windows 上 app/runtime 由 os.replace 换入时只带继承 ACL，
        # 必须先重固化受保护 DACL 再进入 verify_security。
        self.assertEqual(events, ["apply", "verify"])
        self.assertFalse(list((self.install_root / "_程序文件").glob(".update-displaced-*")))

    def test_rollback_after_program_replace_reapplies_program_security(self) -> None:
        controller = PassiveUpdateSystemController(running=True)

        def fail(stage: str) -> None:
            if stage == "program_replaced":
                raise RuntimeError("injected failure")

        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            V2UpdateTransaction(
                self._update_bundle(),
                self.install_root,
                controller,
                online_backup=None,
                health_probe=None,
                fault_hook=fail,
            ).run()
        # copytree 重建的旧树同样要重新过安全策略，回滚后的安装仍是合规 ACL。
        self.assertGreaterEqual(controller.security_applications, 1)
        self.assertEqual(load_v2_identity(self.install_root).version, "2.1.0")

    def test_stale_displaced_dirs_are_cleaned_by_rollback_and_never_block_retry(self) -> None:
        program = self.install_root / "_程序文件"
        for name in ("app", "runtime"):
            stale = program / f".update-displaced-{name}"
            stale.mkdir()
            (stale / "stale.txt").write_bytes(b"leftover-from-power-loss")
        controller = PassiveUpdateSystemController(running=True)

        # 上一次断电残留会先触发一次安全拒绝；回滚必须清掉残渣。
        with self.assertRaisesRegex(UpdatePolicyError, "临时目录已存在"):
            V2UpdateTransaction(
                self._update_bundle(),
                self.install_root,
                controller,
                online_backup=None,
                health_probe=None,
            ).run()
        for name in ("app", "runtime"):
            self.assertFalse((program / f".update-displaced-{name}").exists())

        # 再次双击同一升级包可以安全完成，而不是被旧临时目录永久卡死。
        result = V2UpdateTransaction(
            self._update_bundle(),
            self.install_root,
            controller,
            online_backup=None,
            health_probe=None,
        ).run()
        self.assertEqual(result.target_version, "2.2.0")
        self.assertFalse(list(program.glob(".update-displaced-*")))

    def test_windows_controller_reapplies_protected_dacl_on_program_roots(self) -> None:
        controller = WindowsUpdateSystemController()
        captured: dict[str, str] = {}
        controller.base._run_powershell = (  # type: ignore[method-assign]
            lambda script, environment: captured.update(script=script) or ""
        )
        controller.apply_security(load_v2_identity(self.install_root))
        script = captured["script"]
        self.assertIn("SetAccessRuleProtection($true, $false)", script)
        for name in ("'app'", "'runtime'"):
            self.assertIn(name, script)
        self.assertIn("ReadAndExecute", script)
        # data/backups/logs 的私有 DACL 不归更新器重写。
        self.assertNotIn("Join-Path $program 'data'", script)

    def test_precommit_failure_restores_program_data_version_and_running_state(self) -> None:
        service = self.install_root / "_程序文件" / "app" / "service.py"
        original_service = service.read_bytes()
        unknown = self.install_root / "_程序文件" / "data" / "unknown.bin"
        unknown.write_bytes(b"before")
        controller = PassiveUpdateSystemController(running=True)

        def fail(stage: str) -> None:
            if stage == "program_replaced":
                unknown.write_bytes(b"during-update")
                raise RuntimeError("injected failure")

        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            V2UpdateTransaction(
                self._update_bundle(),
                self.install_root,
                controller,
                online_backup=None,
                health_probe=None,
                fault_hook=fail,
            ).run()
        self.assertEqual(service.read_bytes(), original_service)
        self.assertEqual(unknown.read_bytes(), b"before")
        self.assertEqual((self.install_root / VERSION_FILE).read_text().strip(), "2.1.0")
        self.assertTrue(controller.running)
        self.assertEqual(load_v2_identity(self.install_root).version, "2.1.0")

    def test_same_package_rerun_recovers_an_interrupted_precommit_transaction(self) -> None:
        bundle = self._update_bundle()
        controller = PassiveUpdateSystemController(running=True)

        def fail(stage: str) -> None:
            if stage == "program_replaced":
                raise RuntimeError("power loss")

        interrupted = V2UpdateTransaction(
            bundle,
            self.install_root,
            controller,
            online_backup=None,
            health_probe=None,
            fault_hook=fail,
        )

        def failed_rollback(*args, **kwargs):
            del args, kwargs
            raise OSError("rollback interrupted")

        interrupted._restore = failed_rollback
        with self.assertRaises(UpdateRollbackError):
            interrupted.run()
        result = V2UpdateTransaction(
            bundle,
            self.install_root,
            controller,
            online_backup=None,
            health_probe=None,
        ).run()
        self.assertEqual(result.target_version, "2.2.0")
        self.assertEqual(load_v2_identity(self.install_root).version, "2.2.0")
        self.assertTrue(controller.running)

    def test_interrupted_transaction_rejects_tampered_snapshot_binding(self) -> None:
        bundle = self._update_bundle()
        controller = PassiveUpdateSystemController(running=True)

        def fail(stage: str) -> None:
            if stage == "program_replaced":
                raise RuntimeError("power loss")

        interrupted = V2UpdateTransaction(
            bundle,
            self.install_root,
            controller,
            online_backup=None,
            health_probe=None,
            fault_hook=fail,
        )

        def failed_rollback(*args, **kwargs):
            del args, kwargs
            raise OSError("rollback interrupted")

        interrupted._restore = failed_rollback
        with self.assertRaises(UpdateRollbackError):
            interrupted.run()

        state_path = (
            self.install_root / "_程序文件" / "update-transaction.json"
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["data_snapshot_sha256"] = "d" * 64
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(UpdatePolicyError, "数据快照摘要不一致"):
            V2UpdateTransaction(
                bundle,
                self.install_root,
                controller,
                online_backup=None,
                health_probe=None,
            ).run()

    def test_same_package_recovers_a_partially_committed_identity(self) -> None:
        bundle = self._update_bundle()
        controller = PassiveUpdateSystemController(running=True)

        def fail(stage: str) -> None:
            if stage == "commit_install_info_written":
                raise RuntimeError("power loss during identity commit")

        interrupted = V2UpdateTransaction(
            bundle,
            self.install_root,
            controller,
            online_backup=None,
            health_probe=None,
            fault_hook=fail,
        )

        def failed_rollback(*args, **kwargs):
            del args, kwargs
            raise OSError("machine lost power before rollback")

        interrupted._restore = failed_rollback
        with self.assertRaises(UpdateRollbackError):
            interrupted.run()
        self.assertEqual(
            json.loads((self.install_root / INSTALL_INFO).read_text(encoding="utf-8"))[
                "installed_version"
            ],
            "2.2.0",
        )
        self.assertEqual((self.install_root / VERSION_FILE).read_text().strip(), "2.1.0")

        result = V2UpdateTransaction(
            bundle,
            self.install_root,
            controller,
            online_backup=None,
            health_probe=None,
        ).run()
        self.assertEqual(result.target_version, "2.2.0")
        self.assertEqual(load_v2_identity(self.install_root).version, "2.2.0")
        self.assertTrue(controller.running)

    def test_committed_rerun_never_rolls_back_new_data_and_restores_run_state(self) -> None:
        bundle = self._update_bundle()
        controller = PassiveUpdateSystemController(running=True)
        new_data = self.install_root / "_程序文件" / "data" / "after-commit.bin"

        def fail(stage: str) -> None:
            if stage == "commit_version_written":
                new_data.write_bytes(b"created-after-commit")
                raise RuntimeError("power loss after commit point")

        interrupted = V2UpdateTransaction(
            bundle,
            self.install_root,
            controller,
            online_backup=None,
            health_probe=None,
            fault_hook=fail,
        )

        def failed_rollback(*args, **kwargs):
            del args, kwargs
            raise OSError("machine lost power")

        interrupted._restore = failed_rollback
        with self.assertRaises(UpdateRollbackError):
            interrupted.run()
        self.assertEqual(load_v2_identity(self.install_root).version, "2.2.0")

        result = V2UpdateTransaction(
            bundle,
            self.install_root,
            controller,
            online_backup=None,
            health_probe=None,
        ).run()
        self.assertEqual(result.source_version, "2.2.0")
        self.assertEqual(new_data.read_bytes(), b"created-after-commit")
        self.assertTrue(controller.running)


class UpdateEntryElevationTests(unittest.TestCase):
    """update.py 双击入口：提权前不得读取仅管理员可读的 data/ 身份文件。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run_entry(self) -> dict[str, object]:
        import v2.installer.update as entry

        recorded: dict[str, object] = {}

        class StubBundle:
            manifest_sha256 = "c" * 64

            @classmethod
            def load(cls, tool_root: Path) -> "StubBundle":
                del tool_root
                return cls()

        def refuse_identity(root: Path) -> None:
            del root
            raise AssertionError("load_v2_identity ran before elevation")

        patches = [
            mock.patch.object(entry, "UpdateBundle", StubBundle),
            mock.patch.object(
                entry, "resolve_install_root", lambda explicit=None: self.root / "install"
            ),
            mock.patch.object(entry, "load_v2_identity", refuse_identity),
            mock.patch.object(entry, "read_installed_version", lambda root: "2.1.0"),
            mock.patch.object(entry, "is_admin", lambda: False),
            mock.patch.object(
                entry, "encode_elevation_context", lambda target, sha: "elevated-context"
            ),
            mock.patch.object(
                entry,
                "run_elevated",
                lambda tool_root, context, entrypoint="install.py": (
                    recorded.update(entrypoint=entrypoint, elevated=True) or 0
                ),
            ),
            # 只替换入口模块看到的 os.name；全局改 os.name 会让 pathlib
            # 在 POSIX 上尝试实例化 WindowsPath。
            mock.patch.object(entry, "os", types.SimpleNamespace(name="nt")),
            mock.patch("builtins.input", lambda prompt="": "YES"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        recorded["code"] = entry.main([])
        return recorded

    def test_double_click_elevates_before_touching_admin_only_identity(self) -> None:
        recorded = self._run_entry()
        # 未提权时：不读 data/ 身份、直接走 run_elevated UAC 入口。
        self.assertEqual(recorded["code"], 0)
        self.assertIs(recorded.get("elevated"), True)
        self.assertEqual(recorded["entrypoint"], "update.py")

    def test_admin_direct_run_confirms_without_elevation(self) -> None:
        import v2.installer.update as entry

        identity_calls: list[Path] = []

        class StubBundle:
            manifest_sha256 = "c" * 64

            @classmethod
            def load(cls, tool_root: Path) -> "StubBundle":
                del tool_root
                return cls()

        class StubTransaction:
            def __init__(self, bundle, install_root, controller, **kwargs) -> None:
                del bundle, controller, kwargs
                self.install_root = install_root

            def run(self) -> UpdateResult:
                return UpdateResult(
                    install_root=self.install_root,
                    source_version="2.1.0",
                    target_version="2.2.0",
                    receipt_path=self.install_root / "receipt.json",
                )

        def fail_if_elevated(tool_root, context, entrypoint="install.py") -> int:
            raise AssertionError("admin session must not re-elevate")

        patches = [
            mock.patch.object(entry, "UpdateBundle", StubBundle),
            mock.patch.object(
                entry, "resolve_install_root", lambda explicit=None: self.root / "install"
            ),
            mock.patch.object(
                entry,
                "load_v2_identity",
                lambda root: identity_calls.append(root)
                or types.SimpleNamespace(version="2.1.0"),
            ),
            mock.patch.object(entry, "is_admin", lambda: True),
            mock.patch.object(
                entry, "WindowsUpdateSystemController", PassiveUpdateSystemController
            ),
            mock.patch.object(entry, "V2UpdateTransaction", StubTransaction),
            mock.patch.object(entry, "run_elevated", fail_if_elevated),
            mock.patch.object(entry, "os", types.SimpleNamespace(name="nt")),
            mock.patch("builtins.input", lambda prompt="": "YES"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        with mock.patch.object(entry, "_confirm", lambda version: None):
            code = entry.main([])
        self.assertEqual(code, 0)
        self.assertEqual(len(identity_calls), 1)


if __name__ == "__main__":
    unittest.main()

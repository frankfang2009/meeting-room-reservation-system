from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from v2.installer.installer_core import (
    INSTALLED_MANIFEST,
    INSTALL_INFO,
    VERSION_FILE,
    InstallTransaction,
    PassiveSystemController,
    encode_elevation_context,
    records_for_tree,
    sha256_bytes,
    tree_digest,
)
from v2.installer.tests.helpers import load_fixture_bundle
import v2.installer.update_core as update_core
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
        files["_程序文件/app/service.py"] = b"# V2.4.0 updated service\n"
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
            "version": "2.4.0",
            "release": "V2.4.0",
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
        # 回执表按 schema 版本二选一：假库使用 v3 形态（notice_receipts）。
        connection.execute('CREATE TABLE "notice_receipts" (id TEXT PRIMARY KEY)')
        if setup_complete:
            connection.execute("INSERT INTO users VALUES ('admin')")
            connection.execute("INSERT INTO rooms VALUES ('room-1')")
        connection.commit()
        connection.close()
        return database

    def test_online_backup_reconciles_watermark_above_foreign_sidecars(self) -> None:
        """V241-B1 回归：跨版本 sidecar 残留时，重试更新不再序列碰撞。

        健康检查失败的更新会由换入的新版本运行时在 backups/ 留下当前
        安装（V2.1.0）无法解析的 sidecar；回滚恢复旧库后水位回落。更新
        器在执行更新前在线备份前必须把旧库水位抬到文件名下限，让旧版
        本代码的序列预留落到空闲序列。
        """

        database = self._create_v2_database(setup_complete=True)
        with closing(sqlite3.connect(database)) as db, db:
            db.executemany(
                "INSERT INTO app_meta VALUES (?, ?)",
                (("backup_sequence", "2"), ("data_sequence", "5")),
            )
        backup_dir = self.install_root / "_程序文件" / "backups"
        backup_dir.mkdir(exist_ok=True)
        foreign_db = backup_dir / "reservation-v2-backup-00000003.db"
        foreign_db.write_bytes(b"synthetic-new-runtime-backup")
        foreign_sidecar = {
            "schema": 1,
            "kind": "meeting-room-v2-backup",
            "installId": "0" * 36,
            "productGeneration": 2,
            "databaseSchemaVersion": 999,
            "setupComplete": True,
            "databaseSha256": "a" * 64,
            "databaseBytes": 28,
            "sequence": 3,
            "sourceDataSequence": 5,
            "createdAtUtc": "2026-08-27T06:00:00Z",
            "fileName": "reservation-v2-backup-00000003.db",
        }
        (backup_dir / "reservation-v2-backup-00000003.json").write_text(
            json.dumps(foreign_sidecar, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        info_path = self.install_root / INSTALL_INFO
        info = json.loads(info_path.read_text(encoding="utf-8"))
        info["setup_complete"] = True
        info_path.write_text(json.dumps(info, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        identity = load_v2_identity(self.install_root)

        update_core.reconcile_backup_sequence_floor(identity)
        with closing(sqlite3.connect(database)) as db:
            watermark = db.execute(
                "SELECT value FROM app_meta WHERE key = 'backup_sequence'"
            ).fetchone()[0]
        self.assertEqual(watermark, "3")
        self.assertEqual(foreign_db.read_bytes(), b"synthetic-new-runtime-backup")

        # 幂等：水位不低于下限时不得改写。
        update_core.reconcile_backup_sequence_floor(identity)
        with closing(sqlite3.connect(database)) as db:
            watermark = db.execute(
                "SELECT value FROM app_meta WHERE key = 'backup_sequence'"
            ).fetchone()[0]
        self.assertEqual(watermark, "3")

        # 更高的跨版本残留出现时继续上调（多次失败重试场景）。
        (backup_dir / "reservation-v2-backup-00000005.db").write_bytes(b"x")
        (backup_dir / "reservation-v2-backup-00000005.json").write_text("{}", encoding="utf-8")
        update_core.reconcile_backup_sequence_floor(identity)
        with closing(sqlite3.connect(database)) as db:
            watermark = db.execute(
                "SELECT value FROM app_meta WHERE key = 'backup_sequence'"
            ).fetchone()[0]
        self.assertEqual(watermark, "5")

    def test_online_backup_reconcile_is_noop_without_backup_files(self) -> None:
        database = self._create_v2_database(setup_complete=True)
        with closing(sqlite3.connect(database)) as db, db:
            db.executemany(
                "INSERT INTO app_meta VALUES (?, ?)",
                (("backup_sequence", "2"),),
            )
        info_path = self.install_root / INSTALL_INFO
        info = json.loads(info_path.read_text(encoding="utf-8"))
        info["setup_complete"] = True
        info_path.write_text(json.dumps(info, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        identity = load_v2_identity(self.install_root)
        (self.install_root / "_程序文件" / "backups").mkdir(exist_ok=True)
        update_core.reconcile_backup_sequence_floor(identity)
        with closing(sqlite3.connect(database)) as db:
            watermark = db.execute(
                "SELECT value FROM app_meta WHERE key = 'backup_sequence'"
            ).fetchone()[0]
        self.assertEqual(watermark, "2")

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

    def test_schema_v3_database_after_service_migration_is_accepted(self) -> None:
        # 升级器替换程序并启动新服务后，服务已把数据库迁移到当前 schema；
        # 终验（load_v2_identity）必须接受迁移后的 v3 数据库。
        self._create_v2_database(setup_complete=True, schema_version=3)
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

    def test_production_resolver_uses_registered_fixed_root_not_environment(self) -> None:
        environment_root = self.root / "environment-root"
        environment_root.mkdir()
        key = object()
        key_context = mock.MagicMock()
        key_context.__enter__.return_value = key
        winreg = types.SimpleNamespace(
            HKEY_LOCAL_MACHINE=object(),
            REG_SZ=1,
            OpenKey=mock.Mock(return_value=key_context),
            QueryValueEx=mock.Mock(return_value=(str(self.install_root), 1)),
        )
        windows_os = types.SimpleNamespace(
            name="nt",
            path=os.path,
            environ=os.environ,
        )
        with (
            mock.patch.dict(
                os.environ,
                {"MEETING_ROOM_V2_INSTALL_ROOT": str(environment_root)},
            ),
            mock.patch.dict("sys.modules", {"winreg": winreg}),
            mock.patch.object(update_core, "os", windows_os),
            mock.patch.object(
                update_core,
                "production_install_root",
                return_value=self.install_root,
            ),
        ):
            resolved = resolve_install_root()

        self.assertEqual(resolved, self.install_root.resolve())
        open_args = winreg.OpenKey.call_args.args
        self.assertEqual(open_args[:2], (winreg.HKEY_LOCAL_MACHINE, update_core.REGISTRY_SUBKEY))
        winreg.QueryValueEx.assert_called_once_with(key, "InstallRoot")

    def test_production_resolver_rejects_registered_root_outside_fixed_root(self) -> None:
        fixed_root = self.root / "fixed-root"
        fixed_root.mkdir()
        key_context = mock.MagicMock()
        key_context.__enter__.return_value = object()
        winreg = types.SimpleNamespace(
            HKEY_LOCAL_MACHINE=object(),
            REG_SZ=1,
            OpenKey=mock.Mock(return_value=key_context),
            QueryValueEx=mock.Mock(return_value=(str(self.install_root), 1)),
        )
        windows_os = types.SimpleNamespace(
            name="nt",
            path=os.path,
            environ=os.environ,
        )
        with (
            mock.patch.dict("sys.modules", {"winreg": winreg}),
            mock.patch.object(update_core, "os", windows_os),
            mock.patch.object(
                update_core,
                "production_install_root",
                return_value=fixed_root,
            ),
        ):
            with self.assertRaises(UpdatePolicyError):
                resolve_install_root()

    def test_explicit_root_remains_the_test_only_resolver_injection(self) -> None:
        other = self.root / "ignored-environment-root"
        with mock.patch.dict(
            os.environ,
            {"MEETING_ROOM_V2_INSTALL_ROOT": str(other)},
        ):
            self.assertEqual(resolve_install_root(self.install_root), self.install_root)

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
        self.assertEqual(result.target_version, "2.4.0")
        self.assertEqual((self.install_root / VERSION_FILE).read_text().strip(), "2.4.0")
        self.assertEqual(unknown.read_bytes(), b"customer-data")
        self.assertEqual(
            (self.install_root / "_程序文件" / "app" / "service.py").read_bytes(),
            b"# V2.4.0 updated service\n",
        )
        self.assertFalse(controller.running)
        self.assertTrue(result.receipt_path.is_file())

    def test_controller_verification_precedes_online_backup_and_stop(self) -> None:
        events: list[str] = []

        class RecordingController(PassiveUpdateSystemController):
            def verify(inner_self, identity):
                events.append("verify")
                return super().verify(identity)

            def capture_and_stop(inner_self, identity):
                events.append("stop")
                return super().capture_and_stop(identity)

        def online_backup(identity) -> None:
            del identity
            events.append("backup")

        V2UpdateTransaction(
            self._update_bundle(),
            self.install_root,
            RecordingController(running=True),
            online_backup=online_backup,
            health_probe=None,
        ).run()

        self.assertEqual(events[:3], ["verify", "backup", "stop"])

    def test_failed_controller_verification_blocks_online_backup_and_stop(self) -> None:
        events: list[str] = []

        class RejectFirstVerification(PassiveUpdateSystemController):
            def verify(inner_self, identity):
                del identity
                events.append("verify")
                if events.count("verify") == 1:
                    raise OSError("synthetic production verification failure")

            def capture_and_stop(inner_self, identity):
                events.append("stop")
                return super().capture_and_stop(identity)

        def online_backup(identity) -> None:
            del identity
            events.append("backup")

        with self.assertRaisesRegex(OSError, "production verification failure"):
            V2UpdateTransaction(
                self._update_bundle(),
                self.install_root,
                RejectFirstVerification(running=True),
                online_backup=online_backup,
                health_probe=None,
            ).run()

        self.assertEqual(events, ["verify"])

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
        self.assertEqual(result.target_version, "2.4.0")
        # Windows 上 app/runtime 由 os.replace 换入时只带继承 ACL，
        # 必须先重固化受保护 DACL 再进入 verify_security。
        self.assertEqual(events, ["verify", "apply", "verify", "verify"])
        self.assertFalse(list((self.install_root / "_程序文件").glob(".update-displaced-*")))

    def test_pre_snapshot_failure_reverifies_before_restoring_run_state(self) -> None:
        events: list[str] = []

        class RecordingController(PassiveUpdateSystemController):
            def verify(inner_self, identity):
                events.append("verify")
                return super().verify(identity)

            def capture_and_stop(inner_self, identity):
                events.append("stop")
                return super().capture_and_stop(identity)

            def restore(inner_self, identity, state):
                events.append("restore")
                return super().restore(identity, state)

        def fail(stage: str) -> None:
            if stage == "service_stopped_pre_snapshot":
                raise RuntimeError("synthetic pre-snapshot failure")

        with self.assertRaisesRegex(RuntimeError, "pre-snapshot failure"):
            V2UpdateTransaction(
                self._update_bundle(),
                self.install_root,
                RecordingController(running=True),
                online_backup=None,
                health_probe=None,
                fault_hook=fail,
            ).run()

        self.assertEqual(events, ["verify", "stop", "verify", "restore"])

    def test_rollback_reapplies_full_security_after_all_files_and_before_run_state(self) -> None:
        service = self.install_root / "_程序文件" / "app" / "service.py"
        original_service = service.read_bytes()
        unknown = self.install_root / "_程序文件" / "data" / "unknown.bin"
        unknown.write_bytes(b"before")
        events: list[tuple[str, bytes, bytes]] = []

        class RecordingController(PassiveUpdateSystemController):
            def apply_security(inner_self, identity):
                events.append(("apply", service.read_bytes(), unknown.read_bytes()))
                super().apply_security(identity)

            def verify(inner_self, identity):
                events.append(("verify", service.read_bytes(), unknown.read_bytes()))
                super().verify(identity)

            def restore(inner_self, identity, state):
                events.append(("restore", service.read_bytes(), unknown.read_bytes()))
                super().restore(identity, state)

        controller = RecordingController(running=True)

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
        self.assertEqual(
            events[-3:],
            [
                ("apply", original_service, b"before"),
                ("verify", original_service, b"before"),
                ("restore", original_service, b"before"),
            ],
        )
        self.assertGreaterEqual(controller.security_applications, 1)
        self.assertEqual(load_v2_identity(self.install_root).version, "2.1.0")

    def test_rollback_security_verification_failure_keeps_runtime_stopped(self) -> None:
        class VerificationFails(PassiveUpdateSystemController):
            def __init__(inner_self) -> None:
                super().__init__(running=True)
                inner_self.verify_calls = 0

            def verify(inner_self, identity):
                del identity
                inner_self.verify_calls += 1
                if inner_self.verify_calls == 2:
                    raise OSError("synthetic security verification failure")

        controller = VerificationFails()

        def fail(stage: str) -> None:
            if stage == "program_replaced":
                raise RuntimeError("injected failure")

        with self.assertRaisesRegex(UpdateRollbackError, "自动回滚未能验证"):
            V2UpdateTransaction(
                self._update_bundle(),
                self.install_root,
                controller,
                online_backup=None,
                health_probe=None,
                fault_hook=fail,
            ).run()

        self.assertFalse(controller.running)

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
        self.assertEqual(result.target_version, "2.4.0")
        self.assertFalse(list(program.glob(".update-displaced-*")))

    def test_windows_controller_reapplies_exact_policy_to_public_and_private_roots(self) -> None:
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
        for name in ("data", "backups", "logs"):
            self.assertIn(f"Join-Path $program '{name}'", script)
        self.assertIn("$privateRoots", script)
        self.assertIn("SetAccessRuleProtection($true, $false)", script)
        self.assertIn("SetOwner($adminSid)", script)

    def test_windows_fail_closed_recovery_stop_never_restores_resources_on_failure(self) -> None:
        controller = WindowsUpdateSystemController()
        captured: dict[str, str] = {}

        def fail(script, environment):
            del environment
            captured["script"] = script
            raise OSError("synthetic stop failure")

        controller.base._run_powershell = fail  # type: ignore[method-assign]
        fail_closed_stop = getattr(controller, "capture_and_stop_fail_closed", None)
        self.assertTrue(callable(fail_closed_stop))

        with self.assertRaisesRegex(OSError, "stop failure"):
            fail_closed_stop(load_v2_identity(self.install_root))

        script = captured["script"]
        self.assertNotIn("Start-ScheduledTask", script)
        self.assertNotIn("Enable-ScheduledTask", script)
        self.assertNotIn("Enable-NetFirewallRule", script)
        self.assertNotIn("-ErrorAction SilentlyContinue", script)
        strict_stop = "Stop-ScheduledTask -InputObject $task -ErrorAction Stop"
        strict_disable = "Disable-ScheduledTask -InputObject $task -ErrorAction Stop"
        main_postcondition = (
            "$stoppedMain=Get-ScheduledTask -TaskPath $env:MRV2_TASK_PATH "
            "-TaskName $env:MRV2_TASK_NAME -ErrorAction Stop"
        )
        backup_postcondition = (
            "$stoppedBackup=Get-ScheduledTask -TaskPath $env:MRV2_TASK_PATH "
            "-TaskName $env:MRV2_BACKUP_TASK_NAME -ErrorAction Stop"
        )
        stopped_assertion = (
            "if ([string]$stoppedMain.State -eq 'Running' -or "
            "[string]$stoppedBackup.State -eq 'Running')"
        )
        self.assertIn(strict_stop, script)
        self.assertIn(strict_disable, script)
        self.assertEqual(script.count(main_postcondition), 1)
        self.assertEqual(script.count(backup_postcondition), 1)
        self.assertIn(stopped_assertion, script)
        self.assertLess(script.index(strict_stop), script.index(strict_disable))
        self.assertLess(script.index(strict_disable), script.index(main_postcondition))
        self.assertLess(script.index(main_postcondition), script.index(backup_postcondition))
        self.assertLess(script.index(backup_postcondition), script.index(stopped_assertion))
        self.assertLess(script.index(stopped_assertion), script.index("ConvertTo-Json"))

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

    def test_health_probe_failure_stops_new_runtime_before_restore(self) -> None:
        events: list[str] = []

        class RecordingController(PassiveUpdateSystemController):
            def __init__(self) -> None:
                super().__init__(running=True)
                self.stop_calls = 0
                self.restored_state: dict[str, object] | None = None

            def capture_and_stop(self, identity):
                self.stop_calls += 1
                events.append(
                    "stop-initial"
                    if self.stop_calls == 1
                    else "stop-current-runtime"
                )
                captured = dict(super().capture_and_stop(identity))
                if self.stop_calls == 2:
                    # The health runtime is transient. Its state must not replace
                    # the original pre-update state restored after rollback.
                    captured["service_running"] = False
                return captured

            def start_for_health(self, identity):
                events.append("start-health")
                return super().start_for_health(identity)

            def restore(self, identity, state):
                self.restored_state = dict(state)
                events.append("restore-state")
                return super().restore(identity, state)

        controller = RecordingController()

        def fail_probe(identity):
            del identity
            events.append("probe-failed")
            raise RuntimeError("probe failed")

        transaction = V2UpdateTransaction(
            self._update_bundle(),
            self.install_root,
            controller,
            online_backup=None,
            health_probe=fail_probe,
        )
        original_restore = transaction._restore

        def record_restore(*args, **kwargs):
            events.append("restore-files")
            return original_restore(*args, **kwargs)

        transaction._restore = record_restore

        with self.assertRaisesRegex(RuntimeError, "probe failed"):
            transaction.run()

        self.assertEqual(
            events,
            [
                "stop-initial",
                "start-health",
                "probe-failed",
                "stop-current-runtime",
                "restore-files",
                "restore-state",
            ],
        )
        self.assertLess(
            events.index("stop-current-runtime"), events.index("restore-files")
        )
        self.assertIsNotNone(controller.restored_state)
        self.assertTrue(controller.restored_state["service_running"])
        self.assertFalse(
            list((self.install_root / "_程序文件").glob(".update-staging-*"))
        )

    def test_health_success_uses_fail_closed_stop_before_commit(self) -> None:
        events: list[str] = []

        class RecordingController(PassiveUpdateSystemController):
            def capture_and_stop(self, identity):
                events.append("initial-stop")
                return PassiveUpdateSystemController.capture_and_stop(self, identity)

            def capture_and_stop_fail_closed(self, identity):
                events.append("fail-closed-stop")
                return PassiveUpdateSystemController.capture_and_stop(self, identity)

            def start_for_health(self, identity):
                events.append("start-health")
                return super().start_for_health(identity)

            def restore(self, identity, state):
                events.append("restore-state")
                return super().restore(identity, state)

        controller = RecordingController(running=True)
        result = V2UpdateTransaction(
            self._update_bundle(),
            self.install_root,
            controller,
            online_backup=None,
            health_probe=lambda identity: events.append("health-passed"),
        ).run()

        self.assertEqual(result.target_version, "2.4.0")
        self.assertEqual(
            events,
            [
                "initial-stop",
                "start-health",
                "health-passed",
                "fail-closed-stop",
                "restore-state",
            ],
        )

    def test_health_failure_does_not_restore_files_when_second_stop_fails(self) -> None:
        class SecondStopFails(PassiveUpdateSystemController):
            def capture_and_stop_fail_closed(self, identity):
                del identity
                raise OSError("fail-closed stop failed")

        controller = SecondStopFails()
        transaction = V2UpdateTransaction(
            self._update_bundle(),
            self.install_root,
            controller,
            online_backup=None,
            health_probe=lambda identity: (_ for _ in ()).throw(
                RuntimeError("probe failed")
            ),
        )
        with mock.patch.object(transaction, "_restore") as restore:
            with self.assertRaisesRegex(UpdateRollbackError, "自动回滚未能验证"):
                transaction.run()

        restore.assert_not_called()
        self.assertEqual(
            (self.install_root / "_程序文件/app/service.py").read_bytes(),
            b"# V2.4.0 updated service\n",
        )

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

        # 模拟机器在回滚已于私有 backups 树构建好 data 候选目录、
        # 且已把现行 data 移开后断电。下次运行必须完成该原子窗口。
        state_path = self.install_root / "_程序文件" / "update-transaction.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        rollback = Path(state["rollback_root"])
        restored_data = rollback / "data-restore-candidate"
        displaced_data = rollback / "data-before-restore"
        shutil.copytree(rollback / "protected-data" / "data", restored_data)
        os.replace(self.install_root / "_程序文件" / "data", displaced_data)

        recovery_events: list[str] = []

        class RecoveryController(PassiveUpdateSystemController):
            def capture_and_stop(inner_self, identity):
                recovery_events.append("stop")
                return super().capture_and_stop(identity)

            def capture_and_stop_fail_closed(inner_self, identity):
                recovery_events.append("stop-fail-closed")
                return super().capture_and_stop(identity)

            def apply_security(inner_self, identity):
                recovery_events.append("apply")
                return super().apply_security(identity)

            def verify(inner_self, identity):
                recovery_events.append("verify")
                return super().verify(identity)

            def restore(inner_self, identity, state):
                recovery_events.append("restore")
                return super().restore(identity, state)

        recovery_controller = RecoveryController(running=True)
        result = V2UpdateTransaction(
            bundle,
            self.install_root,
            recovery_controller,
            online_backup=None,
            health_probe=None,
        ).run()
        self.assertEqual(
            recovery_events[:4],
            ["stop-fail-closed", "apply", "verify", "restore"],
        )
        self.assertEqual(result.target_version, "2.4.0")
        self.assertEqual(load_v2_identity(self.install_root).version, "2.4.0")
        self.assertTrue(recovery_controller.running)

    def test_service_stopped_recovery_reverifies_before_restoring_run_state(self) -> None:
        bundle = self._update_bundle()

        class FirstRestoreFails(PassiveUpdateSystemController):
            def restore(inner_self, identity, state):
                raise OSError("synthetic interrupted run-state restore")

        def fail(stage: str) -> None:
            if stage == "service_stopped_pre_snapshot":
                raise RuntimeError("synthetic interruption")

        with self.assertRaises(UpdateRollbackError):
            V2UpdateTransaction(
                bundle,
                self.install_root,
                FirstRestoreFails(running=True),
                online_backup=None,
                health_probe=None,
                fault_hook=fail,
            ).run()

        events: list[str] = []

        class RecoveryController(PassiveUpdateSystemController):
            def capture_and_stop(inner_self, identity):
                events.append("stop")
                return super().capture_and_stop(identity)

            def verify(inner_self, identity):
                events.append("verify")
                return super().verify(identity)

            def restore(inner_self, identity, state):
                events.append("restore")
                return super().restore(identity, state)

        V2UpdateTransaction(
            bundle,
            self.install_root,
            RecoveryController(running=True),
            online_backup=None,
            health_probe=None,
        ).run()

        self.assertEqual(events[:4], ["verify", "stop", "verify", "restore"])

    def test_partial_restore_candidate_is_rebuilt_after_cleanup_interruption(self) -> None:
        bundle = self._update_bundle()
        data = self.install_root / "_程序文件" / "data"
        install_id_before = (data / "install_id").read_bytes()
        unknown = data / "unknown.bin"
        unknown.write_bytes(b"before")

        def fail(stage: str) -> None:
            if stage == "program_replaced":
                unknown.write_bytes(b"during-update")
                raise RuntimeError("power loss")

        interrupted = V2UpdateTransaction(
            bundle,
            self.install_root,
            PassiveUpdateSystemController(running=True),
            online_backup=None,
            health_probe=None,
            fault_hook=fail,
        )

        def leave_partial_candidate(identity, rollback, run_state, *, restore_controller):
            del identity, run_state, restore_controller
            snapshot = rollback / "protected-data" / "data"
            candidate = rollback / "data-restore-candidate"
            candidate.mkdir()
            source = next(path for path in snapshot.rglob("*") if path.is_file())
            destination = candidate / source.relative_to(snapshot)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            raise OSError("copy interrupted")

        interrupted._restore = leave_partial_candidate
        with self.assertRaises(UpdateRollbackError):
            interrupted.run()

        state_path = self.install_root / "_程序文件" / "update-transaction.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        rollback = Path(state["rollback_root"])
        candidate = rollback / "data-restore-candidate"
        self.assertTrue(candidate.is_dir())

        controller = PassiveUpdateSystemController(running=False)
        real_rmtree = shutil.rmtree
        cleanup_attempted = False

        def interrupt_candidate_cleanup(path, *args, **kwargs):
            nonlocal cleanup_attempted
            if Path(path) == candidate and not cleanup_attempted:
                cleanup_attempted = True
                raise OSError("candidate cleanup interrupted")
            return real_rmtree(path, *args, **kwargs)

        with mock.patch(
            "v2.installer.update_core.shutil.rmtree",
            side_effect=interrupt_candidate_cleanup,
        ):
            with self.assertRaisesRegex(OSError, "candidate cleanup interrupted"):
                V2UpdateTransaction(
                    bundle,
                    self.install_root,
                    controller,
                    online_backup=None,
                    health_probe=None,
                ).run()
        self.assertTrue(cleanup_attempted)
        self.assertTrue(candidate.is_dir())
        self.assertFalse(controller.running)

        events: list[str] = []
        controller.capture_and_stop = lambda identity: (  # type: ignore[method-assign]
            events.append("stop") or PassiveUpdateSystemController.capture_and_stop(controller, identity)
        )
        controller.apply_security = lambda identity: events.append("apply")  # type: ignore[method-assign]
        controller.verify = lambda identity: events.append("verify")  # type: ignore[method-assign]
        controller.restore = lambda identity, run_state: (  # type: ignore[method-assign]
            events.append("restore") or PassiveUpdateSystemController.restore(controller, identity, run_state)
        )
        result = V2UpdateTransaction(
            bundle,
            self.install_root,
            controller,
            online_backup=None,
            health_probe=None,
        ).run()

        self.assertEqual(events[:4], ["stop", "apply", "verify", "restore"])
        self.assertEqual(result.target_version, "2.4.0")
        self.assertEqual(unknown.read_bytes(), b"before")
        self.assertEqual((data / "install_id").read_bytes(), install_id_before)
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
            "2.4.0",
        )
        self.assertEqual((self.install_root / VERSION_FILE).read_text().strip(), "2.1.0")

        result = V2UpdateTransaction(
            bundle,
            self.install_root,
            controller,
            online_backup=None,
            health_probe=None,
        ).run()
        self.assertEqual(result.target_version, "2.4.0")
        self.assertEqual(load_v2_identity(self.install_root).version, "2.4.0")
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
        self.assertEqual(load_v2_identity(self.install_root).version, "2.4.0")

        recovery_events: list[str] = []
        controller.capture_and_stop = lambda identity: (  # type: ignore[method-assign]
            recovery_events.append("stop")
            or PassiveUpdateSystemController.capture_and_stop(controller, identity)
        )
        controller.verify = lambda identity: (  # type: ignore[method-assign]
            recovery_events.append("verify")
            or PassiveUpdateSystemController.verify(controller, identity)
        )
        controller.restore = lambda identity, state: (  # type: ignore[method-assign]
            recovery_events.append("restore")
            or PassiveUpdateSystemController.restore(controller, identity, state)
        )

        result = V2UpdateTransaction(
            bundle,
            self.install_root,
            controller,
            online_backup=None,
            health_probe=None,
        ).run()
        self.assertEqual(result.source_version, "2.4.0")
        self.assertEqual(new_data.read_bytes(), b"created-after-commit")
        self.assertTrue(controller.running)
        self.assertEqual(
            recovery_events[:4], ["verify", "stop", "verify", "restore"]
        )


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

    def test_elevated_context_must_match_fresh_registered_root_before_identity_load(self) -> None:
        import v2.installer.update as entry

        registered_root = self.root / "registered-root"
        context_root = self.root / "context-root"
        registered_root.mkdir()
        context_root.mkdir()
        manifest_sha256 = "c" * 64
        context = encode_elevation_context(context_root, manifest_sha256)
        identity_loader = mock.Mock(
            return_value=types.SimpleNamespace(root=context_root.resolve())
        )

        with (
            mock.patch.object(
                entry,
                "resolve_install_root",
                return_value=registered_root.resolve(),
            ),
            mock.patch.object(entry, "load_v2_identity", identity_loader),
        ):
            with self.assertRaises(UpdatePolicyError):
                entry._decode_update_context(context, manifest_sha256)

        identity_loader.assert_not_called()

    def test_elevated_context_loads_identity_from_fresh_registered_root(self) -> None:
        import v2.installer.update as entry

        registered_root = self.root / "registered-root"
        registered_root.mkdir()
        manifest_sha256 = "c" * 64
        context = encode_elevation_context(registered_root, manifest_sha256)
        identity_loader = mock.Mock(
            return_value=types.SimpleNamespace(root=registered_root.resolve())
        )

        with (
            mock.patch.object(
                entry,
                "resolve_install_root",
                return_value=registered_root.resolve(),
            ) as resolver,
            mock.patch.object(entry, "load_v2_identity", identity_loader),
        ):
            resolved = entry._decode_update_context(context, manifest_sha256)

        self.assertEqual(resolved, registered_root.resolve())
        resolver.assert_called_once_with()
        identity_loader.assert_called_once_with(registered_root.resolve())

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
                    target_version="2.4.0",
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

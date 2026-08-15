from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from v2.installer.installer_core import INSTALL_INFO, InstallTransaction, PassiveSystemController
from v2.installer.tests.helpers import load_fixture_bundle
from v2.installer.update_core import (
    EXPECTED_V2_TABLES,
    UpdatePolicyError,
    V2UpdatePreflight,
    assert_update_payload_safe,
    build_update_state,
    load_v2_identity,
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

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create_v2_database(self, *, setup_complete: bool) -> Path:
        database = self.install_root / "_程序文件" / "data" / "reservation.db"
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO app_meta VALUES (?, ?)",
            (
                ("product_generation", "2"),
                ("schema_version", "1"),
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
        self.assertEqual(identity.version, "2.0.0")
        self.assertFalse(identity.setup_complete)
        self.assertFalse(identity.database.exists())

    def test_v2_database_generation_two_is_required_after_setup(self) -> None:
        self._create_v2_database(setup_complete=True)
        info_path = self.install_root / INSTALL_INFO
        info = json.loads(info_path.read_text(encoding="utf-8"))
        info["setup_complete"] = True
        info_path.write_text(json.dumps(info, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        identity = load_v2_identity(self.install_root)
        self.assertTrue(identity.setup_complete)

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
        state = build_update_state(identity, "2.0.1", "a" * 64)
        self.assertEqual(state["install_root"], str(self.install_root.resolve()))
        self.assertEqual(state["install_id"], identity.install_id)
        self.assertEqual(state["source_version"], "2.0.0")
        self.assertEqual(state["target_version"], "2.0.1")
        with self.assertRaises(UpdatePolicyError):
            build_update_state(identity, "2.0.0", "a" * 64)

    def test_preflight_snapshots_before_future_program_writes(self) -> None:
        preflight = V2UpdatePreflight(
            self.install_root,
            "2.0.1",
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


if __name__ == "__main__":
    unittest.main()

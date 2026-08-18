from __future__ import annotations

import hashlib
import errno
import io
import json
import logging
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import service as service_entrypoint
import server as server_entrypoint
import v2app.db as db_module
from v2app import create_app
from v2app.services import reservations as reservation_service
from server import determine_bind_host


INSTALL_ID = "00000000-0000-4000-8000-000000000001"


class BackendTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_dir = self.root / "data"
        self.database = self.data_dir / "reservation.db"
        self.now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone(timedelta(hours=8)))
        self.restart_event = threading.Event()
        self.app = create_app(
            {
                "TESTING": True,
                "DATA_DIR": str(self.data_dir),
                "DATABASE": str(self.database),
                "BACKUP_DIR": str(self.root / "backups"),
                "STATIC_DIR": str(self.root / "dist"),
                "SECRET_KEY": "test-secret",
                "INSTALL_ID": INSTALL_ID,
                "NOW_PROVIDER": lambda: self.now,
                "SETUP_COMPLETED_EVENT": self.restart_event,
                "LOGIN_RATE_MAX_ATTEMPTS": 50,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def csrf(self, client=None) -> str:
        client = client or self.client
        return client.get("/api/v1/session").get_json()["csrfToken"]

    def write(self, method: str, path: str, payload=None, client=None, token=None, **kwargs):
        client = client or self.client
        token = token or self.csrf(client)
        return client.open(
            path,
            method=method,
            json=payload if payload is not None else {},
            headers={"Host": "localhost:8080", "X-CSRF-Token": token},
            **kwargs,
        )

    def setup_system(self) -> dict:
        response = self.write(
            "POST",
            "/api/v1/setup/complete",
            {
                "admin": {
                    "username": "admin",
                    "password": "admin-pass-123",
                    "name": "系统管理员",
                    "department": "测试部门",
                },
                "rooms": [{"name": "笔录室 1"}, {"name": "笔录室 2"}],
                "workStart": "08:30",
                "workEnd": "17:30",
            },
        )
        payload = response.get_json()
        self.assertEqual(response.status_code, 201, payload)
        response.close()
        return payload

    def login(self, username="admin", password="admin-pass-123", client=None):
        client = client or self.client
        response = self.write(
            "POST",
            "/api/v1/session",
            {"username": username, "password": password},
            client=client,
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def bootstrap(self, client=None):
        client = client or self.client
        return client.get("/api/v1/bootstrap").get_json()

    def booking_payload(self, room_id: str, **overrides):
        payload = {
            "date": "2026-08-10",
            "roomId": room_id,
            "start": "09:00",
            "duration": 60,
            "partyName": "张晓燕",
            "caseNumber": "TEST-2026-001",
            "purpose": "工伤笔录",
            "notes": "合成测试备注",
            "tagId": "tag-1",
        }
        payload.update(overrides)
        return payload

    def add_employee(self, username="employee") -> dict:
        response = self.write(
            "POST",
            "/api/v1/users",
            {
                "username": username,
                "password": "employee-pass-123",
                "name": "普通员工",
                "department": "测试部门",
                "role": "employee",
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def write_install_json(
        self,
        data_dir: Path,
        *,
        setup_complete: bool,
        install_id: str = INSTALL_ID,
    ) -> Path:
        data_dir.mkdir(parents=True, exist_ok=True)
        target = data_dir / "install.json"
        target.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "product_generation": 2,
                    "install_id": install_id,
                    "installed_version": "2.0.0",
                    "installed_at_utc": "2026-08-09T00:00:00Z",
                    "port": 8080,
                    "setup_bind": "127.0.0.1",
                    "lan_bind": "0.0.0.0",
                    "setup_complete": setup_complete,
                }
            ),
            encoding="utf-8",
        )
        return target


class GenerationAndSetupTests(BackendTestCase):
    @staticmethod
    def downgrade_schema_to_v1(database: Path) -> None:
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("ALTER TABLE rooms DROP COLUMN show_on_display")
            db.execute("ALTER TABLE user_preferences DROP COLUMN default_tag_slot")
            db.execute(
                "ALTER TABLE user_preferences DROP COLUMN reminder_lead_minutes"
            )
            db.execute("ALTER TABLE user_preferences DROP COLUMN reminder_template")
            db.execute("ALTER TABLE user_preferences DROP COLUMN reminder_sound")
            db.execute("DROP TABLE notice_receipts")
            db.execute(
                """
                CREATE TABLE reminder_receipts (
                    reservation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    reservation_revision INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('change', 'upcoming')),
                    delivered_at TEXT NOT NULL DEFAULT
                        (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    acknowledged_at TEXT,
                    PRIMARY KEY (reservation_id, user_id, reservation_revision, kind),
                    FOREIGN KEY (reservation_id) REFERENCES reservations(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            db.execute(
                "UPDATE app_meta SET value = '1' WHERE key = 'schema_version'"
            )

    def test_new_database_has_generation_but_no_seed_account(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            meta = dict(db.execute("SELECT key, value FROM app_meta"))
            self.assertEqual(meta["product_generation"], "2")
            self.assertEqual(meta["schema_version"], "3")
            self.assertEqual(meta["setup_complete"], "0")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
            room_columns = {
                row[1] for row in db.execute("PRAGMA table_info(rooms)")
            }
            preference_columns = {
                row[1] for row in db.execute("PRAGMA table_info(user_preferences)")
            }
            self.assertIn("show_on_display", room_columns)
            self.assertTrue(
                {
                    "default_tag_slot",
                    "reminder_lead_minutes",
                    "reminder_sound",
                    "reminder_template",
                }.issubset(
                    preference_columns
                )
            )
            self.assertIn(
                "notice_receipts",
                {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                },
            )
        self.assertEqual(determine_bind_host(self.app), "127.0.0.1")

    def test_schema_v1_is_migrated_atomically_without_rewriting_business_data(self):
        self.setup_system()
        self.login()
        bootstrap = self.bootstrap()
        room_id = bootstrap["rooms"][0]["id"]
        booking = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(room_id),
        ).get_json()
        user_id = bootstrap["currentUser"]["id"]
        self.downgrade_schema_to_v1(self.database)

        migrated = create_app(
            {
                "TESTING": True,
                "DATA_DIR": str(self.data_dir),
                "DATABASE": str(self.database),
                "SECRET_KEY": "migration-secret",
                "INSTALL_ID": INSTALL_ID,
            }
        )
        self.assertTrue(migrated.config["SYSTEM_READY"])
        with closing(sqlite3.connect(self.database)) as db:
            meta = dict(db.execute("SELECT key, value FROM app_meta"))
            self.assertEqual(meta["schema_version"], "3")
            self.assertEqual(
                db.execute(
                    "SELECT party_name FROM reservations WHERE id = ?", (booking["id"],)
                ).fetchone()[0],
                "张晓燕",
            )
            self.assertEqual(
                db.execute(
                    "SELECT default_tag_slot, reminder_lead_minutes, reminder_sound, "
                    "reminder_template "
                    "FROM user_preferences WHERE user_id = ?",
                    (user_id,),
                ).fetchone(),
                (None, 30, 1, db_module.DEFAULT_REMINDER_TEMPLATE),
            )
            self.assertEqual(
                db.execute(
                    "SELECT show_on_display FROM rooms WHERE id = ?", (room_id,)
                ).fetchone()[0],
                1,
            )
            self.assertIn(
                "notice_receipts",
                {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                },
            )
        self.assertFalse(db_module.migrate_schema_v1_to_v2(self.database))
        self.assertFalse(db_module.migrate_schema_v2_to_v3(self.database))

    def test_schema_migration_failure_rolls_back_every_column_and_version(self):
        self.downgrade_schema_to_v1(self.database)
        original_add = db_module._add_column_if_missing
        calls = 0

        def fail_after_first_column(db, table, column, declaration):
            nonlocal calls
            original_add(db, table, column, declaration)
            calls += 1
            if calls == 1:
                raise RuntimeError("C0 migration failpoint")

        with mock.patch.object(
            db_module,
            "_add_column_if_missing",
            side_effect=fail_after_first_column,
        ):
            state = db_module.prepare_database(self.database)
        self.assertFalse(state.ready)
        self.assertEqual(state.code, "DATABASE_MIGRATION_FAILED")
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0],
                "1",
            )
            self.assertNotIn(
                "show_on_display",
                {row[1] for row in db.execute("PRAGMA table_info(rooms)")},
            )
            self.assertFalse(
                {"default_tag_slot", "reminder_lead_minutes", "reminder_template"}
                & {
                    row[1]
                    for row in db.execute("PRAGMA table_info(user_preferences)")
                }
            )

    @staticmethod
    def downgrade_schema_to_v2(database: Path) -> None:
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("ALTER TABLE user_preferences DROP COLUMN reminder_sound")
            db.execute("DROP TABLE notice_receipts")
            db.execute(
                """
                CREATE TABLE reminder_receipts (
                    reservation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    reservation_revision INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('change', 'upcoming')),
                    delivered_at TEXT NOT NULL DEFAULT
                        (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    acknowledged_at TEXT,
                    PRIMARY KEY (reservation_id, user_id, reservation_revision, kind),
                    FOREIGN KEY (reservation_id) REFERENCES reservations(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            db.execute(
                "UPDATE app_meta SET value = '2' WHERE key = 'schema_version'"
            )

    def test_schema_v2_to_v3_converts_acknowledged_change_receipts(self):
        self.setup_system()
        self.login()
        bootstrap = self.bootstrap()
        room_id = bootstrap["rooms"][0]["id"]
        employee_record = self.add_employee()
        employee = self.app.test_client()
        self.login("employee", "employee-pass-123", employee)
        self.now = datetime(2026, 8, 10, 8, 0, tzinfo=timezone(timedelta(hours=8)))
        created = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(room_id, start="09:00"),
            client=employee,
        ).get_json()
        first_edit = self.write(
            "PATCH",
            f"/api/v1/reservations/{created['id']}",
            self.booking_payload(
                room_id, start="10:00", expectedRevision=created["revision"]
            ),
        ).get_json()
        notice = employee.get("/api/v1/reminders/due").get_json()["items"][0]
        acknowledged = self.write(
            "POST",
            "/api/v1/reminders/ack",
            {"eventId": notice["eventId"]},
            client=employee,
        )
        self.assertEqual(acknowledged.status_code, 200, acknowledged.get_json())
        second_edit = self.write(
            "PATCH",
            f"/api/v1/reservations/{created['id']}",
            self.booking_payload(
                room_id, start="11:00", expectedRevision=first_edit["revision"]
            ),
        ).get_json()

        # 降级回 v2 形态，并把已确认回执写成 v2 的预约+版本维度。
        self.downgrade_schema_to_v2(self.database)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                """
                INSERT INTO reminder_receipts (
                    reservation_id, user_id, reservation_revision, kind, acknowledged_at
                ) VALUES (?, ?, ?, 'change', '2026-08-10T00:10:00.000Z')
                """,
                (created["id"], employee_record["id"], first_edit["revision"]),
            )

        self.assertTrue(db_module.migrate_schema_v2_to_v3(self.database))
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0],
                "3",
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM notice_receipts").fetchone()[0], 1
            )
            self.assertEqual(
                db.execute(
                    "SELECT reminder_sound FROM user_preferences WHERE user_id = ?",
                    (employee_record["id"],),
                ).fetchone()[0],
                1,
            )
        # 已确认的 rev2 变更不再出现；未确认的 rev3 变更仍然待确认。
        remaining = [
            item
            for item in employee.get("/api/v1/reminders/due").get_json()["items"]
            if item["kind"] == "change"
        ]
        self.assertEqual(
            [(item["changeType"], item["revision"]) for item in remaining],
            [("updated", second_edit["revision"])],
        )
        # 幂等：重复迁移直接返回 False。
        self.assertFalse(db_module.migrate_schema_v2_to_v3(self.database))

    def test_schema_v2_to_v3_failure_rolls_back_receipts_and_version(self):
        self.downgrade_schema_to_v2(self.database)
        original_add = db_module._add_column_if_missing

        def fail_first_column(db, table, column, declaration):
            original_add(db, table, column, declaration)
            raise RuntimeError("v3 migration failpoint")

        with mock.patch.object(
            db_module,
            "_add_column_if_missing",
            side_effect=fail_first_column,
        ):
            state = db_module.prepare_database(self.database)
        self.assertFalse(state.ready)
        self.assertEqual(state.code, "DATABASE_MIGRATION_FAILED")
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0],
                "2",
            )
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("reminder_receipts", tables)
            self.assertNotIn("notice_receipts", tables)

    def test_v1_database_is_rejected_without_mutation_or_identity_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            database = data / "reservation.db"
            with closing(sqlite3.connect(database)) as db, db:
                for table in ("users", "rooms", "reservations", "reservation_slots"):
                    db.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
                db.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                db.execute("INSERT INTO app_meta VALUES ('schema_version', '1')")
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            recovery = create_app({"DATA_DIR": str(data), "DATABASE": str(database)})
            self.assertFalse(recovery.config["SYSTEM_READY"])
            self.assertEqual(
                recovery.config["RECOVERY_STATE"]["code"],
                "DATABASE_GENERATION_INVALID",
            )
            self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)
            self.assertFalse((data / ".secret_key").exists())
            self.assertFalse((data / "install_id").exists())

    def test_unknown_database_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "unknown.db"
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("CREATE TABLE alien (id INTEGER)")
            recovery = create_app(
                {
                    "DATA_DIR": temporary,
                    "DATABASE": str(database),
                    "SECRET_KEY": "x",
                    "INSTALL_ID": INSTALL_ID,
                }
            )
            self.assertFalse(recovery.config["SYSTEM_READY"])

    def test_setup_requires_csrf_and_loopback(self):
        missing = self.client.post("/api/v1/setup/complete", json={})
        self.assertEqual(missing.status_code, 403)
        denied = self.write(
            "POST",
            "/api/v1/setup/complete",
            {},
            environ_base={"REMOTE_ADDR": "8.8.8.8"},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.get_json()["error"]["code"], "SETUP_LOOPBACK_ONLY")

        setup_payload = {
            "admin": {
                "username": "admin",
                "password": "admin-pass-123",
                "name": "系统管理员",
            },
            "rooms": [{"name": "笔录室 1"}],
        }
        attacker = self.app.test_client()
        attacker_base = "http://attacker.example:8080"
        attacker_token = attacker.get(
            "/api/v1/session", base_url=attacker_base
        ).get_json()["csrfToken"]
        rebound = attacker.post(
            "/api/v1/setup/complete",
            base_url=attacker_base,
            json=setup_payload,
            headers={"X-CSRF-Token": attacker_token},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(rebound.status_code, 403)
        self.assertEqual(rebound.get_json()["error"]["code"], "SETUP_HOST_INVALID")

        wrong_port = self.app.test_client()
        wrong_port_base = "http://127.0.0.1:8081"
        wrong_port_token = wrong_port.get(
            "/api/v1/session", base_url=wrong_port_base
        ).get_json()["csrfToken"]
        rejected_port = wrong_port.post(
            "/api/v1/setup/complete",
            base_url=wrong_port_base,
            json=setup_payload,
            headers={"X-CSRF-Token": wrong_port_token},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(rejected_port.status_code, 403)
        self.assertEqual(
            rejected_port.get_json()["error"]["code"], "SETUP_HOST_INVALID"
        )

        ipv6 = self.app.test_client()
        ipv6_base = "http://[::1]:8080"
        ipv6_token = ipv6.get(
            "/api/v1/session",
            base_url=ipv6_base,
            environ_base={"REMOTE_ADDR": "::1"},
        ).get_json()["csrfToken"]
        allowed = ipv6.post(
            "/api/v1/setup/complete",
            base_url=ipv6_base,
            json=setup_payload,
            headers={"X-CSRF-Token": ipv6_token},
            environ_base={"REMOTE_ADDR": "::1"},
        )
        self.assertEqual(allowed.status_code, 201, allowed.get_json())

        lan = self.app.test_client()
        lan_base = "http://192.168.50.10:8080"
        lan_token = lan.get(
            "/api/v1/session",
            base_url=lan_base,
            environ_base={"REMOTE_ADDR": "192.168.50.20"},
        ).get_json()["csrfToken"]
        normal_lan_login = lan.post(
            "/api/v1/session",
            base_url=lan_base,
            json={"username": "admin", "password": "admin-pass-123"},
            headers={"X-CSRF-Token": lan_token},
            environ_base={"REMOTE_ADDR": "192.168.50.20"},
        )
        self.assertEqual(normal_lan_login.status_code, 200, normal_lan_login.get_json())

    def test_setup_is_atomic_and_emits_restart_signal(self):
        self.app.config["SETUP_FAILPOINT"] = True
        failed = self.write(
                "POST",
                "/api/v1/setup/complete",
                {
                    "admin": {"username": "admin", "password": "password123", "name": "管理员"},
                    "rooms": [{"name": "笔录室"}],
                    "workStart": "08:30",
                    "workEnd": "17:30",
                },
            )
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(failed.get_json()["error"]["code"], "INTERNAL_ERROR")
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT value FROM app_meta WHERE key='setup_complete'").fetchone()[0], "0")
        self.app.config.pop("SETUP_FAILPOINT")
        result = self.setup_system()
        self.assertTrue(result["restartRequired"])
        self.assertTrue(self.restart_event.is_set())
        self.assertEqual(determine_bind_host(self.app), "0.0.0.0")

    def test_database_truth_repairs_install_json(self):
        install_json = self.data_dir / "install.json"
        install_json.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "product_generation": 2,
                    "install_id": INSTALL_ID,
                    "installed_version": "2.0.0",
                    "installed_at_utc": "2026-08-09T00:00:00Z",
                    "port": 8080,
                    "setup_bind": "127.0.0.1",
                    "lan_bind": "0.0.0.0",
                    "setup_complete": False,
                }
            ),
            encoding="utf-8",
        )
        self.setup_system()
        self.assertTrue(json.loads(install_json.read_text(encoding="utf-8"))["setup_complete"])
        # A stale false value is repaired from DB on the next app construction.
        value = json.loads(install_json.read_text(encoding="utf-8"))
        value["setup_complete"] = False
        install_json.write_text(json.dumps(value), encoding="utf-8")
        create_app(
            {
                "TESTING": True,
                "DATA_DIR": str(self.data_dir),
                "DATABASE": str(self.database),
                "SECRET_KEY": "test-secret",
                "INSTALL_ID": INSTALL_ID,
            }
        )
        self.assertTrue(json.loads(install_json.read_text(encoding="utf-8"))["setup_complete"])

    def test_health_identity_only_leaves_loopback(self):
        local = self.client.get("/healthz").get_json()
        self.assertEqual(
            {key: local[key] for key in ("ok", "product_generation", "install_id", "setup_complete", "bind_mode", "port")},
            {
                "ok": True,
                "product_generation": 2,
                "install_id": INSTALL_ID,
                "setup_complete": False,
                "bind_mode": "loopback",
                "port": 8080,
            },
        )
        remote = self.client.get("/healthz", environ_base={"REMOTE_ADDR": "192.168.1.20"}).get_json()
        self.assertNotIn("install_id", remote)

    def test_completed_install_never_initializes_missing_or_empty_database(self):
        for empty_file in (False, True):
            with self.subTest(empty_file=empty_file), tempfile.TemporaryDirectory() as temporary:
                data = Path(temporary) / "data"
                self.write_install_json(data, setup_complete=True)
                database = data / "reservation.db"
                if empty_file:
                    database.touch()
                app = create_app(
                    {
                        "TESTING": True,
                        "DATA_DIR": str(data),
                        "DATABASE": str(database),
                        "SECRET_KEY": "test-secret",
                        "INSTALL_ID": INSTALL_ID,
                    }
                )
                self.assertFalse(app.config["SYSTEM_READY"])
                self.assertEqual(
                    app.config["RECOVERY_STATE"]["code"],
                    "DATABASE_MISSING_AFTER_SETUP",
                )
                self.assertFalse(database.exists() and database.stat().st_size > 0)
                client = app.test_client()
                health = client.get("/healthz/").get_json()
                self.assertFalse(health["ok"])
                self.assertEqual(health["status"], "recovery")
                self.assertEqual(
                    health["recovery_code"], "DATABASE_MISSING_AFTER_SETUP"
                )
                remote_health = client.get(
                    "/healthz/", environ_base={"REMOTE_ADDR": "192.168.1.20"}
                ).get_json()
                self.assertNotIn("install_id", remote_health)
                self.assertNotIn("recovery_code", remote_health)
                blocked = client.get("/api/v1/session")
                self.assertEqual(blocked.status_code, 503)
                self.assertIn("requestId", blocked.get_json())
                blocked_write = client.post("/api/v1/session", json={})
                self.assertEqual(blocked_write.status_code, 503)
                self.assertEqual(
                    blocked_write.get_json()["error"]["code"],
                    "SYSTEM_RECOVERY_REQUIRED",
                )

    def test_mirror_true_database_false_is_fail_closed(self):
        self.write_install_json(self.data_dir, setup_complete=True)
        restarted = create_app(
            {
                "TESTING": True,
                "DATA_DIR": str(self.data_dir),
                "DATABASE": str(self.database),
                "SECRET_KEY": "test-secret",
                "INSTALL_ID": INSTALL_ID,
            }
        )
        self.assertFalse(restarted.config["SYSTEM_READY"])
        self.assertEqual(
            restarted.config["RECOVERY_STATE"]["code"], "SETUP_STATE_CONFLICT"
        )

    def test_corrupt_or_foreign_key_invalid_database_enters_recovery(self):
        self.setup_system()
        self.write_install_json(self.data_dir, setup_complete=True)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute(
                "INSERT INTO user_preferences (user_id) VALUES ('missing-user')"
            )
        invalid_fk = create_app(
            {
                "TESTING": True,
                "DATA_DIR": str(self.data_dir),
                "DATABASE": str(self.database),
                "SECRET_KEY": "test-secret",
                "INSTALL_ID": INSTALL_ID,
            }
        )
        self.assertEqual(
            invalid_fk.config["RECOVERY_STATE"]["code"],
            "DATABASE_FOREIGN_KEY_FAILED",
        )

        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "data"
            self.write_install_json(data, setup_complete=True)
            database = data / "reservation.db"
            database.write_bytes(b"not-a-sqlite-database")
            before = database.read_bytes()
            corrupt = create_app(
                {
                    "TESTING": True,
                    "DATA_DIR": str(data),
                    "DATABASE": str(database),
                    "SECRET_KEY": "test-secret",
                    "INSTALL_ID": INSTALL_ID,
                }
            )
            self.assertFalse(corrupt.config["SYSTEM_READY"])
            self.assertEqual(database.read_bytes(), before)

    def test_quick_check_failure_enters_recovery(self):
        with mock.patch(
            "v2app.db.database_health",
            return_value={
                "quickCheck": ["corrupt"],
                "quickCheckOk": False,
                "foreignKeyErrors": 0,
                "foreignKeysOk": True,
            },
        ):
            restarted = create_app(
                {
                    "TESTING": True,
                    "DATA_DIR": str(self.data_dir),
                    "DATABASE": str(self.database),
                    "SECRET_KEY": "test-secret",
                    "INSTALL_ID": INSTALL_ID,
                }
            )
        self.assertEqual(
            restarted.config["RECOVERY_STATE"]["code"],
            "DATABASE_INTEGRITY_FAILED",
        )

    def test_invalid_setup_metadata_never_reopens_first_setup(self):
        self.setup_system()
        self.write_install_json(self.data_dir, setup_complete=False)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("DELETE FROM app_meta WHERE key='setup_complete'")
        restarted = create_app(
            {
                "TESTING": True,
                "DATA_DIR": str(self.data_dir),
                "DATABASE": str(self.database),
                "SECRET_KEY": "test-secret",
                "INSTALL_ID": INSTALL_ID,
            }
        )
        self.assertFalse(restarted.config["SYSTEM_READY"])
        self.assertEqual(
            restarted.config["RECOVERY_STATE"]["code"],
            "DATABASE_GENERATION_INVALID",
        )
        response = restarted.test_client().get("/api/v1/setup")
        self.assertEqual(response.status_code, 503)


class AuthenticationAndAdministrationTests(BackendTestCase):
    def setUp(self):
        super().setUp()
        self.setup_system()
        self.login()

    def test_json_writes_require_csrf_and_bool_integer_is_validation_error(self):
        response = self.client.post("/api/v1/rooms", json={"name": "新房间"})
        self.assertEqual(response.status_code, 403)
        response = self.write(
            "POST", "/api/v1/rooms", {"name": "新房间", "sortOrder": True}
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["error"]["code"], "VALIDATION_ERROR")

    def test_admin_updates_work_hours_without_rewriting_existing_bookings(self):
        room_id = self.bootstrap()["rooms"][0]["id"]
        existing = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(room_id, start="09:00", caseNumber="C1-EXISTING"),
        )
        self.assertEqual(existing.status_code, 201, existing.get_json())
        token_response = self.write(
            "POST",
            "/api/v1/admin/tokens",
            {"name": "C1 可用时段", "scopes": ["availability:read"]},
        )
        raw_token = token_response.get_json()["token"]

        updated = self.write(
            "PUT",
            "/api/v1/admin/settings",
            {"workStart": "10:00", "workEnd": "16:00"},
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        self.assertEqual(updated.get_json()["workStart"], "10:00")
        self.assertEqual(updated.get_json()["workEnd"], "16:00")
        self.assertEqual(self.bootstrap()["settings"], updated.get_json())
        system = self.client.get("/api/v1/admin/system").get_json()
        self.assertEqual((system["workStart"], system["workEnd"]), ("10:00", "16:00"))
        availability = self.client.get(
            "/api/v1/integration/availability?date=2026-08-10",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        self.assertEqual(availability.status_code, 200, availability.get_json())
        slots = availability.get_json()["rooms"][0]["slots"]
        self.assertEqual(slots[0]["start"], "10:00")
        self.assertEqual(slots[-1]["start"], "15:30")
        calendar = self.client.get(
            "/api/v1/reservations?dateFrom=2026-08-10&dateTo=2026-08-10"
        ).get_json()["items"]
        self.assertEqual([item["caseNumber"] for item in calendar], ["C1-EXISTING"])
        with closing(sqlite3.connect(self.database)) as db:
            details = db.execute(
                "SELECT details_json FROM security_audit_log "
                "WHERE action = 'settings.updated' ORDER BY occurred_at DESC LIMIT 1"
            ).fetchone()[0]
        self.assertEqual(
            json.loads(details),
            {
                "before": {"workEnd": "17:30", "workStart": "08:30"},
                "after": {"workEnd": "16:00", "workStart": "10:00"},
            },
        )

    def test_work_hours_permissions_validation_and_transaction_rollback(self):
        self.add_employee()
        employee = self.app.test_client()
        self.login("employee", "employee-pass-123", employee)
        denied = self.write(
            "PUT",
            "/api/v1/admin/settings",
            {"workStart": "09:00", "workEnd": "17:00"},
            client=employee,
        )
        self.assertEqual(denied.status_code, 403, denied.get_json())

        invalid_cases = (
            ({"workStart": "8:30", "workEnd": "17:30"}, "workStart"),
            ({"workStart": "08:15", "workEnd": "17:30"}, "workStart"),
            ({"workStart": "18:00", "workEnd": "17:30"}, "workEnd"),
        )
        for payload, field in invalid_cases:
            with self.subTest(payload=payload):
                response = self.write("PUT", "/api/v1/admin/settings", payload)
                self.assertEqual(response.status_code, 422, response.get_json())
                self.assertIn(field, response.get_json()["error"]["fields"])

        with mock.patch(
            "v2app.api.admin.write_security_audit",
            side_effect=RuntimeError("C1 audit failpoint"),
        ):
            failed = self.write(
                "PUT",
                "/api/v1/admin/settings",
                {"workStart": "09:00", "workEnd": "17:00"},
            )
        self.assertEqual(failed.status_code, 500, failed.get_json())
        self.assertEqual(
            (self.bootstrap()["settings"]["workStart"], self.bootstrap()["settings"]["workEnd"]),
            ("08:30", "17:30"),
        )

    def test_create_room_and_user_preserve_explicit_disabled_state(self):
        room = self.write(
            "POST",
            "/api/v1/rooms",
            {"name": "暂停使用的笔录室", "sortOrder": 10, "isActive": False},
        )
        self.assertEqual(room.status_code, 201, room.get_json())
        self.assertFalse(room.get_json()["isActive"])

        user = self.write(
            "POST",
            "/api/v1/users",
            {
                "username": "disabled-user",
                "password": "employee-pass-123",
                "name": "待启用员工",
                "department": "测试部门",
                "role": "employee",
                "enabled": False,
            },
        )
        self.assertEqual(user.status_code, 201, user.get_json())
        self.assertFalse(user.get_json()["enabled"])
        disabled_client = self.app.test_client()
        login = self.write(
            "POST",
            "/api/v1/session",
            {"username": "disabled-user", "password": "employee-pass-123"},
            client=disabled_client,
        )
        self.assertEqual(login.status_code, 403)
        self.assertEqual(login.get_json()["error"]["code"], "ACCOUNT_DISABLED")
        bad_room = self.write(
            "POST",
            "/api/v1/rooms",
            {"name": "无效房间", "sortOrder": 11, "isActive": 0},
        )
        self.assertEqual(bad_room.status_code, 422)
        bad_user = self.write(
            "POST",
            "/api/v1/users",
            {
                "username": "bad-enabled",
                "password": "employee-pass-123",
                "name": "无效员工",
                "role": "employee",
                "enabled": 0,
            },
        )
        self.assertEqual(bad_user.status_code, 422)

    def test_admin_user_lifecycle_and_last_admin_guard(self):
        employee = self.add_employee()
        changed = self.write(
            "PATCH",
            f"/api/v1/users/{employee['id']}",
            {"role": "admin", "enabled": True},
        )
        self.assertEqual(changed.status_code, 200)
        current = self.bootstrap()["currentUser"]
        demoted = self.write(
            "PATCH",
            f"/api/v1/users/{current['id']}",
            {"role": "employee", "enabled": True},
        )
        self.assertEqual(demoted.status_code, 200)

        other_client = self.app.test_client()
        self.login("employee", "employee-pass-123", other_client)
        other_id = employee["id"]
        blocked = self.write(
            "PATCH",
            f"/api/v1/users/{other_id}",
            {"role": "employee", "enabled": True},
            client=other_client,
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.get_json()["error"]["code"], "LAST_ADMIN_REQUIRED")

    def test_employee_cannot_read_management_endpoints(self):
        self.add_employee()
        employee = self.app.test_client()
        self.login("employee", "employee-pass-123", employee)
        for path in ("/api/v1/users", "/api/v1/rooms", "/api/v1/admin/system"):
            self.assertEqual(employee.get(path).status_code, 403, path)

    def test_room_and_preferences_fields_match_frontend_contract(self):
        room = self.bootstrap()["rooms"][0]
        self.assertIn("isActive", room)
        self.assertTrue(room["showOnDisplay"])
        updated = self.write(
            "PATCH",
            f"/api/v1/rooms/{room['id']}",
            {"name": room["name"], "sortOrder": 9, "isActive": False},
        )
        self.assertFalse(updated.get_json()["isActive"])
        saved = self.write(
            "PUT",
            "/api/v1/preferences",
            {
                "defaultDuration": 90,
                "defaultRoomId": "",
                "bookingChangeNotifications": False,
                "bookingReminder": True,
                "personalTags": [
                    {"slot": 3, "label": "我的标签 A"},
                    {"slot": 4, "label": "我的标签 B"},
                ],
            },
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertIsNone(saved.get_json()["defaultRoomId"])

    def test_room_display_setting_is_admin_only_strict_and_audited(self):
        room = self.bootstrap()["rooms"][0]
        self.add_employee()
        employee = self.app.test_client()
        self.login("employee", "employee-pass-123", employee)
        employee_room = self.bootstrap(employee)["rooms"][0]
        self.assertNotIn("showOnDisplay", employee_room)
        denied = self.write(
            "PATCH",
            f"/api/v1/rooms/{room['id']}",
            {"showOnDisplay": False},
            client=employee,
        )
        self.assertEqual(denied.status_code, 403, denied.get_json())
        invalid = self.write(
            "PATCH",
            f"/api/v1/rooms/{room['id']}",
            {"showOnDisplay": 0},
        )
        self.assertEqual(invalid.status_code, 422, invalid.get_json())

        updated = self.write(
            "PATCH",
            f"/api/v1/rooms/{room['id']}",
            {"showOnDisplay": False},
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        self.assertFalse(updated.get_json()["showOnDisplay"])
        with closing(sqlite3.connect(self.database)) as db:
            details = json.loads(
                db.execute(
                    "SELECT details_json FROM security_audit_log "
                    "WHERE action = 'room.updated' "
                    "ORDER BY occurred_at DESC LIMIT 1"
                ).fetchone()[0]
            )
        self.assertFalse(details["showOnDisplay"])

    def test_default_tag_slot_validates_and_follows_global_slot_rename(self):
        for invalid in (0, 5, "1", -1, True):
            with self.subTest(defaultTagSlot=invalid):
                response = self.write(
                    "PUT",
                    "/api/v1/preferences",
                    {"defaultTagSlot": invalid},
                )
                self.assertEqual(response.status_code, 422, response.get_json())
                self.assertIn(
                    "defaultTagSlot", response.get_json()["error"].get("fields", {})
                )

        saved = self.write(
            "PUT",
            "/api/v1/preferences",
            {"defaultTagSlot": 1},
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(saved.get_json()["defaultTagSlot"], 1)
        renamed = self.write(
            "PUT",
            "/api/v1/tags/global",
            {
                "tags": [
                    {"slot": 1, "label": "新单位标签"},
                    {"slot": 2, "label": "单位标签二"},
                ]
            },
        )
        self.assertEqual(renamed.status_code, 200, renamed.get_json())
        bootstrap = self.bootstrap()
        self.assertEqual(bootstrap["preferences"]["defaultTagSlot"], 1)
        self.assertEqual(bootstrap["globalTags"][0]["label"], "新单位标签")
        cleared = self.write(
            "PUT", "/api/v1/preferences", {"defaultTagSlot": None}
        )
        self.assertIsNone(cleared.get_json()["defaultTagSlot"])
        with closing(sqlite3.connect(self.database)) as db:
            details = json.loads(
                db.execute(
                    "SELECT details_json FROM security_audit_log "
                    "WHERE action = 'preferences.updated' "
                    "ORDER BY occurred_at DESC LIMIT 1"
                ).fetchone()[0]
            )
        self.assertIn("defaultTagSlot", details)
        self.assertIsNone(details["defaultTagSlot"])

    def test_personal_default_tag_slot_is_isolated_per_employee(self):
        self.add_employee("employee-a")
        self.add_employee("employee-b")
        employee_a = self.app.test_client()
        employee_b = self.app.test_client()
        self.login("employee-a", "employee-pass-123", employee_a)
        self.login("employee-b", "employee-pass-123", employee_b)

        saved_a = self.write(
            "PUT",
            "/api/v1/preferences",
            {
                "defaultTagSlot": 3,
                "personalTags": [
                    {"slot": 3, "label": "A 的个人标签"},
                    {"slot": 4, "label": "A 的备用标签"},
                ],
            },
            client=employee_a,
        )
        self.assertEqual(saved_a.status_code, 200, saved_a.get_json())
        bootstrap_b = self.bootstrap(employee_b)
        self.assertIsNone(bootstrap_b["preferences"]["defaultTagSlot"])
        self.assertEqual(bootstrap_b["personalTags"][0]["label"], "标签 3")

        saved_b = self.write(
            "PUT",
            "/api/v1/preferences",
            {
                "defaultTagSlot": 3,
                "personalTags": [
                    {"slot": 3, "label": "B 的个人标签"},
                    {"slot": 4, "label": "B 的备用标签"},
                ],
            },
            client=employee_b,
        )
        self.assertEqual(saved_b.status_code, 200, saved_b.get_json())
        bootstrap_a = self.bootstrap(employee_a)
        self.assertEqual(bootstrap_a["preferences"]["defaultTagSlot"], 3)
        self.assertEqual(bootstrap_a["personalTags"][0]["label"], "A 的个人标签")

    def test_reminder_template_validation_default_audit_and_user_isolation(self):
        default_template = db_module.DEFAULT_REMINDER_TEMPLATE
        self.assertEqual(
            self.bootstrap()["preferences"]["reminderTemplate"], default_template
        )
        accepted = "请于{日期} {开始时间}到{笔录室}，{当事人姓名}。" + "好" * 172
        self.assertEqual(len(accepted), 200)
        saved = self.write(
            "PUT", "/api/v1/preferences", {"reminderTemplate": accepted}
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(saved.get_json()["reminderTemplate"], accepted)
        rejected = self.write(
            "PUT", "/api/v1/preferences", {"reminderTemplate": "字" * 201}
        )
        self.assertEqual(rejected.status_code, 422, rejected.get_json())
        self.assertIn(
            "reminderTemplate", rejected.get_json()["error"].get("fields", {})
        )
        with closing(sqlite3.connect(self.database)) as db:
            audit_json = db.execute(
                "SELECT details_json FROM security_audit_log "
                "WHERE action = 'preferences.updated' "
                "ORDER BY occurred_at DESC LIMIT 1"
            ).fetchone()[0]
        self.assertNotIn(accepted, audit_json)
        details = json.loads(audit_json)
        self.assertTrue(details["reminderTemplateUpdated"])
        self.assertEqual(details["reminderTemplateLength"], 200)

        reset = self.write(
            "PUT", "/api/v1/preferences", {"reminderTemplate": " \n\t "}
        )
        self.assertEqual(reset.status_code, 200, reset.get_json())
        self.assertEqual(reset.get_json()["reminderTemplate"], default_template)

        self.add_employee("employee-a")
        self.add_employee("employee-b")
        employee_a = self.app.test_client()
        employee_b = self.app.test_client()
        self.login("employee-a", "employee-pass-123", employee_a)
        self.login("employee-b", "employee-pass-123", employee_b)
        custom_a = "A 的提醒：{当事人姓名}，{日期} {开始时间}。"
        response_a = self.write(
            "PUT",
            "/api/v1/preferences",
            {"reminderTemplate": custom_a},
            client=employee_a,
        )
        self.assertEqual(response_a.status_code, 200, response_a.get_json())
        self.assertEqual(
            self.bootstrap(employee_a)["preferences"]["reminderTemplate"], custom_a
        )
        self.assertEqual(
            self.bootstrap(employee_b)["preferences"]["reminderTemplate"],
            default_template,
        )

    def test_admin_bootstrap_supplies_owner_personal_tags(self):
        employee = self.add_employee()
        selected = next(
            item for item in self.bootstrap()["users"] if item["id"] == employee["id"]
        )
        self.assertEqual(
            [(item["id"], item["slot"]) for item in selected["personalTags"]],
            [("tag-3", 3), ("tag-4", 4)],
        )

    def test_room_management_metrics_are_derived_from_active_bookings(self):
        room_id = self.bootstrap()["rooms"][0]["id"]
        for booking_date in ("2026-08-09", "2026-08-10"):
            response = self.write(
                "POST",
                "/api/v1/reservations",
                self.booking_payload(room_id, date=booking_date),
            )
            self.assertEqual(response.status_code, 201, response.get_json())
        room = next(
            item
            for item in self.client.get("/api/v1/rooms").get_json()["items"]
            if item["id"] == room_id
        )
        self.assertEqual(room["todayCount"], 1)
        self.assertEqual(room["futureCount"], 2)
        self.assertEqual(room["nextBooking"], "今天 09:00")

    def test_room_delete_conflict_lists_bookings_for_direct_admin_action(self):
        room_id = self.bootstrap()["rooms"][0]["id"]
        created = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(room_id, date="2026-08-10", start="09:00"),
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        impact = self.client.get(f"/api/v1/rooms/{room_id}/deletion-impact")
        self.assertEqual(impact.status_code, 200, impact.get_json())
        self.assertEqual(impact.get_json()["room"]["id"], room_id)
        self.assertEqual(impact.get_json()["total"], 1)
        self.assertEqual(impact.get_json()["items"][0]["id"], created.get_json()["id"])
        blocked = self.write("DELETE", f"/api/v1/rooms/{room_id}")
        payload = blocked.get_json()
        self.assertEqual(blocked.status_code, 409, payload)
        self.assertEqual(payload["error"]["code"], "ROOM_HAS_FUTURE_BOOKINGS")
        self.assertEqual(payload["error"]["current"]["roomId"], room_id)
        self.assertEqual(payload["error"]["current"]["total"], 1)
        self.assertEqual(len(payload["error"]["conflicts"]), 1)
        blocker = payload["error"]["conflicts"][0]
        self.assertEqual(blocker["id"], created.get_json()["id"])
        self.assertEqual(blocker["partyName"], "张晓燕")
        self.assertTrue(blocker["canEdit"])

        clear_room = self.write(
            "POST",
            "/api/v1/rooms",
            {"name": "无预约测试室", "sortOrder": 99, "isActive": True},
        )
        self.assertEqual(clear_room.status_code, 201, clear_room.get_json())
        clear_room_id = clear_room.get_json()["id"]
        clear_impact = self.client.get(
            f"/api/v1/rooms/{clear_room_id}/deletion-impact"
        )
        self.assertEqual(clear_impact.status_code, 200, clear_impact.get_json())
        self.assertEqual(clear_impact.get_json()["total"], 0)
        self.assertEqual(clear_impact.get_json()["items"], [])

    def test_personal_history_filter_requires_selected_owner(self):
        room_id = self.bootstrap()["rooms"][0]["id"]
        created = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(room_id, tagId="tag-3"),
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        missing_owner = self.client.get(
            "/api/v1/reservations/history?month=2026-08&tagId=tag-3"
        )
        self.assertEqual(missing_owner.status_code, 422)
        self.assertEqual(
            missing_owner.get_json()["error"]["code"],
            "PERSONAL_TAG_OWNER_REQUIRED",
        )
        owner_id = self.bootstrap()["currentUser"]["id"]
        selected = self.client.get(
            f"/api/v1/reservations/history?month=2026-08&ownerId={owner_id}&tagId=tag-3"
        )
        self.assertEqual(len(selected.get_json()["items"]), 1)


class ServiceEntrypointTests(BackendTestCase):
    def test_service_port_conflict_is_actionable_without_leaking_exception(self):
        with mock.patch.object(
            service_entrypoint,
            "load_install_identity",
            return_value={"install_id": INSTALL_ID},
        ), mock.patch.object(
            service_entrypoint,
            "run_service",
            side_effect=OSError(errno.EADDRINUSE, "Address already in use: private path"),
        ), mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(service_entrypoint.main([]), 1)
        message = stderr.getvalue()
        self.assertIn("8080 端口已被其他程序占用", message)
        self.assertIn("① 启动系统", message)
        # 失败提示按平台指向对应日志位置：Windows 是安装目录，macOS 是应用文件夹。
        if os.name == "nt":
            self.assertIn("_程序文件\\logs", message)
        else:
            self.assertIn("应用文件夹内的 logs", message)
        self.assertNotIn("private path", message)

    def test_listener_handoff_reports_actual_bind_and_closes_once(self):
        class FakeServer:
            def __init__(self):
                self.close_calls = 0
                self.closed = threading.Event()
                self.triggered = threading.Event()
                self.thunk = None
                self.run_thread_id = None
                self.close_thread_id = None
                server = self

                class FakeTrigger:
                    def pull_trigger(self, thunk):
                        server.thunk = thunk
                        server.triggered.set()

                self.trigger = FakeTrigger()

            def run(self):
                self.run_thread_id = threading.get_ident()
                self.triggered.wait(2)
                self.thunk()
                self.closed.wait(2)

            def close(self):
                self.close_calls += 1
                self.close_thread_id = threading.get_ident()
                self.closed.set()

        fake_server = FakeServer()
        captured_app = None

        def create_fake_server(app, **kwargs):
            nonlocal captured_app
            captured_app = app
            self.assertEqual(kwargs["host"], "127.0.0.1")
            return fake_server

        def complete_setup():
            while captured_app is None:
                threading.Event().wait(0.01)
            captured_app.config["SETUP_COMPLETED_EVENT"].set()

        signal = threading.Thread(target=complete_setup)
        signal.start()
        with mock.patch.object(server_entrypoint, "create_server", create_fake_server):
            restart = server_entrypoint.run_server_once(
                8080,
                app_config={
                    "TESTING": True,
                    "DATA_DIR": str(self.data_dir),
                    "DATABASE": str(self.database),
                    "SECRET_KEY": "test-secret",
                    "INSTALL_ID": INSTALL_ID,
                },
            )
        signal.join(timeout=1)
        self.assertTrue(restart)
        self.assertEqual(fake_server.close_calls, 1)
        self.assertEqual(fake_server.close_thread_id, fake_server.run_thread_id)
        with captured_app.test_client() as client:
            health = client.get("/healthz").get_json()
        self.assertEqual(health["bind_mode"], "loopback")

    def test_listener_handoff_failure_is_not_reported_as_success(self):
        with mock.patch.object(
            server_entrypoint,
            "run_server_once",
            side_effect=[True, OSError("new LAN listener failed")],
        ), mock.patch.object(server_entrypoint, "_port", return_value=8080):
            self.assertEqual(server_entrypoint.main(), 1)

    def test_service_control_is_loopback_token_and_process_identity_gated(self):
        stop_event = threading.Event()
        control = {
            "pid": 4321,
            "token": "a" * 64,
            "executable": "/runtime/pythonw.exe",
            "servicePath": "/program/service.py",
            "installId": INSTALL_ID,
        }
        controlled = create_app(
            {
                "TESTING": True,
                "DATA_DIR": str(self.data_dir),
                "DATABASE": str(self.database),
                "SECRET_KEY": "test-secret",
                "INSTALL_ID": INSTALL_ID,
                "SERVICE_CONTROL": control,
                "SERVICE_STOP_EVENT": stop_event,
            }
        ).test_client()
        body = {
            key: control[key]
            for key in ("pid", "executable", "servicePath", "installId")
        }
        self.assertEqual(
            controlled.post(
                "/_service/stop",
                json=body,
                headers={"X-Meeting-Room-Service-Token": control["token"]},
                environ_base={"REMOTE_ADDR": "192.168.1.20"},
            ).status_code,
            404,
        )
        self.assertEqual(controlled.post("/_service/stop", json=body).status_code, 403)
        accepted = controlled.post(
            "/_service/stop",
            json=body,
            headers={"X-Meeting-Room-Service-Token": control["token"]},
        )
        self.assertEqual(accepted.get_json(), {"stopping": True, "pid": 4321})
        self.assertTrue(stop_event.is_set())

    def test_windows_pid_probe_never_calls_os_kill(self):
        with mock.patch.object(service_entrypoint.os, "name", "nt"), mock.patch.object(
            service_entrypoint, "_windows_pid_exists", return_value=True
        ) as windows_probe, mock.patch.object(service_entrypoint.os, "kill") as kill:
            self.assertTrue(service_entrypoint._pid_exists(4321))
        windows_probe.assert_called_once_with(4321)
        kill.assert_not_called()

    def test_service_default_config_check_and_optional_browser_contract(self):
        identity = {"install_id": INSTALL_ID}
        record = {
            "pid": 4321,
            "token": "b" * 64,
            "executable": "/runtime/pythonw.exe",
            "servicePath": "/program/service.py",
            "installId": INSTALL_ID,
        }
        with mock.patch.object(
            service_entrypoint, "_claim_pid", return_value=record
        ), mock.patch.object(
            service_entrypoint, "run_server_once", return_value=False
        ) as run_once, mock.patch.object(
            service_entrypoint, "_remove_pid_if_token", return_value=True
        ), mock.patch.object(
            service_entrypoint,
            "discover_lan_address",
            return_value="http://192.168.1.20:8080",
        ), mock.patch.object(
            service_entrypoint,
            "_launch_backup_catch_up",
        ) as catch_up, mock.patch.object(
            service_entrypoint.threading, "Thread"
        ) as thread_type, mock.patch.dict(
            service_entrypoint.os.environ, {"MEETING_ROOM_OPEN_BROWSER": "1"}
        ):
            service_entrypoint.run_service(identity)
        config = run_once.call_args.kwargs["app_config"]
        self.assertEqual(config["STATIC_DIR"], str(service_entrypoint.SERVICE_DIR / "static"))
        self.assertEqual(config["LAN_ADDRESS"], "http://192.168.1.20:8080")
        self.assertEqual(run_once.call_args.args, (8080,))
        catch_up.assert_called_once_with(identity)
        thread_type.assert_called_once()

        with mock.patch.object(
            service_entrypoint, "load_install_identity", return_value=identity
        ), mock.patch.object(service_entrypoint, "check_service") as check, mock.patch.object(
            service_entrypoint, "configure_logging", return_value=mock.Mock()
        ):
            self.assertEqual(service_entrypoint.main(["--check"]), 0)
        check.assert_called_once_with(identity)
        self.assertEqual(service_entrypoint.main(["unexpected"]), 2)

    def test_lan_address_accepts_only_rfc1918_ipv4(self):
        self.assertEqual(
            service_entrypoint._private_lan_url(
                {
                    "127.0.0.1",
                    "169.254.1.1",
                    "8.8.8.8",
                    "224.0.0.1",
                    "192.168.50.7",
                },
                8080,
            ),
            "http://192.168.50.7:8080",
        )
        self.assertIsNone(
            service_entrypoint._private_lan_url(
                {"127.0.0.1", "169.254.1.1", "8.8.8.8"}, 8080
            )
        )

    def test_startup_failure_writes_rotating_log_without_identity_secrets(self):
        log_path = self.root / "logs" / "service.log"
        logger = service_entrypoint.configure_logging(log_path)
        identity = {"install_id": INSTALL_ID}
        with mock.patch.object(
            service_entrypoint, "configure_logging", return_value=logger
        ), mock.patch.object(
            service_entrypoint, "load_install_identity", return_value=identity
        ), mock.patch.object(
            service_entrypoint,
            "run_service",
            side_effect=OSError("[Errno 48] Address already in use: 8080"),
        ):
            self.assertEqual(service_entrypoint.main([]), 1)
        root_logger = logging.getLogger()
        for handler in list(root_logger.handlers):
            if getattr(handler, "_meeting_room_v2_service", False):
                handler.flush()
        contents = log_path.read_text(encoding="utf-8")
        self.assertIn("Address already in use", contents)
        self.assertIn("Traceback", contents)
        self.assertNotIn("a" * 64, contents)
        for handler in list(root_logger.handlers):
            if getattr(handler, "_meeting_room_v2_service", False):
                root_logger.removeHandler(handler)
                handler.close()


class AuthenticatedReservationTestCase(BackendTestCase):
    def setUp(self):
        super().setUp()
        self.setup_system()
        self.login()
        self.room_id = self.bootstrap()["rooms"][0]["id"]

    def create_booking(self, **overrides):
        response = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(self.room_id, **overrides),
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()


class ReservationTests(AuthenticatedReservationTestCase):

    def test_personal_activity_counts_only_completed_active_own_reservations(self):
        self.now = datetime(2025, 8, 31, 8, 0, tzinfo=timezone(timedelta(hours=8)))
        self.create_booking(date="2025-09-01", start="09:00", duration=60)

        self.now = datetime(2026, 8, 1, 8, 0, tzinfo=timezone(timedelta(hours=8)))
        self.create_booking(date="2026-08-01", start="09:00", duration=90)
        cancelled = self.create_booking(date="2026-08-02", start="09:00", duration=30)
        cancelled_response = self.write(
            "POST",
            f"/api/v1/reservations/{cancelled['id']}/cancel",
            {"expectedRevision": cancelled["revision"]},
        )
        self.assertEqual(cancelled_response.status_code, 200, cancelled_response.get_json())
        self.create_booking(date="2026-08-12", start="09:00", duration=60)

        self.add_employee()
        employee_client = self.app.test_client()
        self.login("employee", "employee-pass-123", employee_client)
        employee_booking = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(self.room_id, date="2026-08-03", start="09:00"),
            client=employee_client,
        )
        self.assertEqual(employee_booking.status_code, 201, employee_booking.get_json())

        self.now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone(timedelta(hours=8)))
        response = self.client.get("/api/v1/activity")
        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertEqual(set(payload), {"summary", "overview"})
        self.assertEqual(
            payload["summary"],
            {
                "currentMonthCompleted": 1,
                "totalCompleted": 2,
                "totalDurationMinutes": 150,
                "activeDays": 2,
            },
        )
        self.assertEqual(
            payload["overview"],
            {
                "averageDurationMinutes": 75,
                "favoriteRoom": "笔录室 1",
                "favoriteTag": "标签 1",
            },
        )
        unauthenticated = self.app.test_client().get("/api/v1/activity")
        self.assertEqual(unauthenticated.status_code, 401)

    def test_create_commits_record_slots_and_event_together(self):
        booking = self.create_booking()
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reservations").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reservation_slots").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reservation_events").fetchone()[0], 1)
        calendar = self.client.get(
            "/api/v1/reservations?dateFrom=2026-08-10&dateTo=2026-08-10"
        ).get_json()["items"]
        self.assertEqual(calendar[0]["notes"], "合成测试备注")
        self.assertEqual(calendar[0]["owner"]["name"], "系统管理员")
        self.assertEqual(booking["revision"], 1)

    def test_slot_conflict_and_revision_conflict(self):
        booking = self.create_booking()
        conflict = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(self.room_id, partyName="另一人"),
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["error"]["code"], "SLOT_CONFLICT")
        updated_payload = self.booking_payload(
            self.room_id, start="10:00", expectedRevision=booking["revision"]
        )
        updated = self.write(
            "PATCH", f"/api/v1/reservations/{booking['id']}", updated_payload
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        self.assertEqual(updated.get_json()["revision"], 2)
        stale = self.write(
            "PATCH", f"/api/v1/reservations/{booking['id']}", updated_payload
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.get_json()["error"]["code"], "REVISION_CONFLICT")
        self.assertEqual(stale.get_json()["error"]["current"]["revision"], 2)

    def test_slot_unique_constraint_fallback_maps_create_and_update_to_conflict(self):
        blocker = self.create_booking(start="09:00")
        original_conflicts = reservation_service._conflicts

        def hide_precheck_once():
            calls = 0

            def conflicts(db, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return []
                return original_conflicts(db, **kwargs)

            return conflicts

        with mock.patch.object(
            reservation_service, "_conflicts", side_effect=hide_precheck_once()
        ):
            create_conflict = self.write(
                "POST",
                "/api/v1/reservations",
                self.booking_payload(self.room_id, partyName="并发创建"),
            )
        self.assertEqual(create_conflict.status_code, 409, create_conflict.get_json())
        self.assertEqual(create_conflict.get_json()["error"]["code"], "SLOT_CONFLICT")
        self.assertEqual(
            create_conflict.get_json()["error"]["conflicts"][0]["id"], blocker["id"]
        )

        movable = self.create_booking(start="11:00")
        with mock.patch.object(
            reservation_service, "_conflicts", side_effect=hide_precheck_once()
        ):
            update_conflict = self.write(
                "PATCH",
                f"/api/v1/reservations/{movable['id']}",
                self.booking_payload(
                    self.room_id,
                    start="09:00",
                    expectedRevision=movable["revision"],
                ),
            )
        self.assertEqual(update_conflict.status_code, 409, update_conflict.get_json())
        self.assertEqual(update_conflict.get_json()["error"]["code"], "SLOT_CONFLICT")
        self.assertEqual(
            update_conflict.get_json()["error"]["conflicts"][0]["id"], blocker["id"]
        )

    def test_create_update_cancel_failpoints_roll_back_all_state(self):
        self.app.config["TRANSACTION_FAILPOINT"] = "create_after_slots"
        failed = self.write(
            "POST", "/api/v1/reservations", self.booking_payload(self.room_id)
        )
        self.assertEqual(failed.status_code, 500)
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reservations").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reservation_slots").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reservation_events").fetchone()[0], 0)

        self.app.config.pop("TRANSACTION_FAILPOINT")
        booking = self.create_booking()
        original_slots = None
        with closing(sqlite3.connect(self.database)) as db, db:
            original_slots = db.execute(
                "SELECT room_id, booking_date, slot_start FROM reservation_slots ORDER BY slot_start"
            ).fetchall()
        self.app.config["TRANSACTION_FAILPOINT"] = "update_after_slots"
        failed = self.write(
            "PATCH",
            f"/api/v1/reservations/{booking['id']}",
            self.booking_payload(self.room_id, start="10:00", expectedRevision=1),
        )
        self.assertEqual(failed.status_code, 500)
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(db.execute("SELECT revision FROM reservations").fetchone()[0], 1)
            self.assertEqual(
                db.execute("SELECT room_id, booking_date, slot_start FROM reservation_slots ORDER BY slot_start").fetchall(),
                original_slots,
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reservation_events").fetchone()[0], 1)

        self.app.config["TRANSACTION_FAILPOINT"] = "cancel_after_slots"
        failed = self.write(
            "POST",
            f"/api/v1/reservations/{booking['id']}/cancel",
            {"expectedRevision": 1},
        )
        self.assertEqual(failed.status_code, 500)
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(db.execute("SELECT status FROM reservations").fetchone()[0], "active")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reservation_slots").fetchone()[0], 2)

    def test_employee_sees_shared_details_but_cannot_mutate_other_booking(self):
        booking = self.create_booking()
        self.add_employee()
        employee = self.app.test_client()
        self.login("employee", "employee-pass-123", employee)
        shared = employee.get(
            "/api/v1/reservations?dateFrom=2026-08-10&dateTo=2026-08-10"
        ).get_json()["items"][0]
        self.assertEqual(shared["notes"], booking["notes"])
        self.assertFalse(shared["canEdit"])
        denied = self.write(
            "PATCH",
            f"/api/v1/reservations/{booking['id']}",
            self.booking_payload(self.room_id, expectedRevision=1),
            client=employee,
        )
        self.assertEqual(denied.status_code, 403)
        own_history = employee.get("/api/v1/reservations/history?month=2026-08").get_json()["items"]
        self.assertEqual(own_history, [])
        expanded = employee.get(
            f"/api/v1/reservations/history?month=2026-08&ownerId={booking['ownerId']}"
        )
        self.assertEqual(expanded.status_code, 403)

    def test_cancelled_details_are_limited_to_owner_or_admin(self):
        admin_booking = self.create_booking(start="09:00")
        self.add_employee()
        employee = self.app.test_client()
        self.login("employee", "employee-pass-123", employee)

        shared_active = employee.get(
            f"/api/v1/reservations/{admin_booking['id']}"
        )
        self.assertEqual(shared_active.status_code, 200, shared_active.get_json())
        self.assertEqual(shared_active.get_json()["notes"], admin_booking["notes"])

        cancelled_admin = self.write(
            "POST",
            f"/api/v1/reservations/{admin_booking['id']}/cancel",
            {"expectedRevision": admin_booking["revision"]},
        )
        self.assertEqual(cancelled_admin.status_code, 200, cancelled_admin.get_json())
        denied = employee.get(f"/api/v1/reservations/{admin_booking['id']}")
        self.assertEqual(denied.status_code, 403, denied.get_json())
        self.assertEqual(denied.get_json()["error"]["code"], "FORBIDDEN")

        employee_booking = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(self.room_id, start="10:00"),
            client=employee,
        )
        self.assertEqual(employee_booking.status_code, 201, employee_booking.get_json())
        employee_booking = employee_booking.get_json()
        cancelled_employee = self.write(
            "POST",
            f"/api/v1/reservations/{employee_booking['id']}/cancel",
            {"expectedRevision": employee_booking["revision"]},
            client=employee,
        )
        self.assertEqual(
            cancelled_employee.status_code, 200, cancelled_employee.get_json()
        )
        owner_view = employee.get(
            f"/api/v1/reservations/{employee_booking['id']}"
        )
        self.assertEqual(owner_view.status_code, 200, owner_view.get_json())
        admin_view = self.client.get(
            f"/api/v1/reservations/{employee_booking['id']}"
        )
        self.assertEqual(admin_view.status_code, 200, admin_view.get_json())

    def test_cancel_increments_revision_releases_slots_and_hides_from_calendar(self):
        booking = self.create_booking()
        cancelled = self.write(
            "POST",
            f"/api/v1/reservations/{booking['id']}/cancel",
            {"expectedRevision": 1},
        )
        self.assertEqual(cancelled.get_json()["revision"], 2)
        self.assertEqual(cancelled.get_json()["status"], "cancelled")
        calendar = self.client.get(
            "/api/v1/reservations?dateFrom=2026-08-10&dateTo=2026-08-10"
        ).get_json()["items"]
        self.assertEqual(calendar, [])
        history = self.client.get("/api/v1/reservations/history?month=2026-08").get_json()["items"]
        self.assertEqual(history[0]["status"], "cancelled")
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reservation_slots").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reservation_events").fetchone()[0], 2)
        active_booking = self.create_booking(start="10:00", caseNumber="ACTIVE-STATUS")
        cancelled_history = self.client.get(
            "/api/v1/reservations/history?month=2026-08&status=cancelled"
        ).get_json()["items"]
        self.assertEqual([item["id"] for item in cancelled_history], [booking["id"]])
        active_history = self.client.get(
            "/api/v1/reservations/history?month=2026-08&status=active"
        ).get_json()["items"]
        self.assertEqual([item["id"] for item in active_history], [active_booking["id"]])
        invalid_status = self.client.get(
            "/api/v1/reservations/history?month=2026-08&status=pending"
        )
        self.assertEqual(invalid_status.status_code, 422)
        self.assertEqual(invalid_status.get_json()["error"]["code"], "VALIDATION_ERROR")

    def test_tag_snapshot_survives_rename_and_history_room_filter(self):
        booking = self.create_booking(tagId="tag-1")
        changed = self.write(
            "PUT",
            "/api/v1/tags/global",
            {"tags": [{"slot": 1, "label": "新标签"}, {"slot": 2, "label": "标签 2"}]},
        )
        self.assertEqual(changed.status_code, 200)
        detail = self.client.get(f"/api/v1/reservations/{booking['id']}").get_json()
        self.assertEqual(detail["tagLabel"], "标签 1")
        filtered = self.client.get(
            f"/api/v1/reservations/history?month=2026-08&roomId={self.room_id}"
        ).get_json()["items"]
        self.assertEqual(len(filtered), 1)
        other_room = self.bootstrap()["rooms"][1]["id"]
        self.assertEqual(
            self.client.get(
                f"/api/v1/reservations/history?month=2026-08&roomId={other_room}"
            ).get_json()["items"],
            [],
        )


class PublicSystemAndStaticTests(AuthenticatedReservationTestCase):
    def test_public_projection_exact_allowlist_and_masking(self):
        self.now = datetime(2026, 8, 10, 8, 0, tzinfo=timezone(timedelta(hours=8)))
        self.create_booking(partyName="张晓燕", start="09:00", duration=60)
        self.now = datetime(2026, 8, 10, 9, 15, tzinfo=timezone(timedelta(hours=8)))
        payload = self.client.get("/api/v1/display/today").get_json()
        self.assertEqual(
            set(payload), {"serverDate", "serverTime", "lastUpdatedAt", "status", "rooms"}
        )
        room = payload["rooms"][0]
        self.assertEqual(set(room), {"id", "name", "current", "next"})
        self.assertEqual(room["current"], {"maskedPartyName": "张*燕", "start": "09:00", "end": "10:00"})
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in ("张晓燕", "TEST-2026-001", "合成测试备注", "系统管理员", "标签 1"):
            self.assertNotIn(forbidden, serialized)
        denied = self.client.get(
            "/api/v1/display/today", environ_base={"REMOTE_ADDR": "8.8.8.8"}
        )
        self.assertEqual(denied.status_code, 403)

    def test_hidden_display_rooms_are_filtered_without_affecting_shared_or_integration_data(self):
        self.now = datetime(2026, 8, 10, 8, 0, tzinfo=timezone(timedelta(hours=8)))
        hidden_booking = self.create_booking(
            partyName="隐藏人员甲", start="09:00", duration=60
        )
        second_room_id = self.bootstrap()["rooms"][1]["id"]
        visible_booking = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(
                second_room_id,
                partyName="公开人员乙",
                caseNumber="C3-VISIBLE",
                start="09:00",
                duration=60,
            ),
        )
        self.assertEqual(visible_booking.status_code, 201, visible_booking.get_json())
        hidden = self.write(
            "PATCH",
            f"/api/v1/rooms/{self.room_id}",
            {"showOnDisplay": False},
        )
        self.assertEqual(hidden.status_code, 200, hidden.get_json())

        self.now = datetime(2026, 8, 10, 9, 15, tzinfo=timezone(timedelta(hours=8)))
        display = self.client.get("/api/v1/display/today").get_json()
        self.assertEqual([room["id"] for room in display["rooms"]], [second_room_id])
        self.assertNotIn("隐藏人员甲", json.dumps(display, ensure_ascii=False))

        token = self.write(
            "POST",
            "/api/v1/admin/tokens",
            {"name": "C3 集成不变", "scopes": ["rooms:read", "availability:read"]},
        ).get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        integration_rooms = self.client.get(
            "/api/v1/integration/rooms", headers=headers
        ).get_json()["items"]
        self.assertIn(self.room_id, [room["id"] for room in integration_rooms])
        availability_rooms = self.client.get(
            "/api/v1/integration/availability?date=2026-08-10", headers=headers
        ).get_json()["rooms"]
        self.assertIn(self.room_id, [room["roomId"] for room in availability_rooms])

        self.add_employee()
        employee = self.app.test_client()
        self.login("employee", "employee-pass-123", employee)
        employee_rooms = self.bootstrap(employee)["rooms"]
        self.assertIn(self.room_id, [room["id"] for room in employee_rooms])
        self.assertTrue(all("showOnDisplay" not in room for room in employee_rooms))
        shared = employee.get(
            "/api/v1/reservations?dateFrom=2026-08-10&dateTo=2026-08-10"
        ).get_json()["items"]
        self.assertIn(hidden_booking["id"], [booking["id"] for booking in shared])

        empty = self.write(
            "PATCH",
            f"/api/v1/rooms/{second_room_id}",
            {"showOnDisplay": False},
        )
        self.assertEqual(empty.status_code, 200, empty.get_json())
        self.assertEqual(
            self.client.get("/api/v1/display/today").get_json()["rooms"], []
        )

    def test_upcoming_reminders_are_state_and_need_no_ack(self):
        self.now = datetime(2026, 8, 10, 8, 40, tzinfo=timezone(timedelta(hours=8)))
        booking = self.create_booking(start="09:00", duration=30)
        due = self.client.get("/api/v1/reminders/due").get_json()["items"]
        self.assertEqual(
            [(item["id"], item["kind"]) for item in due],
            [(booking["id"], "upcoming")],
        )
        # 临近提醒是状态而非待办：重复读取持续可见，不需要确认。
        repeated = self.client.get("/api/v1/reminders/due").get_json()["items"]
        self.assertEqual([item["id"] for item in repeated], [booking["id"]])
        # 窗口移出后条目自然消失。
        self.now = datetime(2026, 8, 10, 8, 20, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(
            self.client.get("/api/v1/reminders/due").get_json()["items"], []
        )

    def test_reminder_lead_preference_and_sound_drive_due_window(self):
        self.now = datetime(2026, 8, 10, 8, 10, tzinfo=timezone(timedelta(hours=8)))
        booking = self.create_booking(start="09:00", duration=30)
        self.assertEqual(self.bootstrap()["preferences"]["reminderLeadMinutes"], 30)
        self.assertTrue(self.bootstrap()["preferences"]["reminderSound"])
        self.assertEqual(
            self.client.get("/api/v1/reminders/due").get_json()["items"], []
        )

        for allowed in (15, 30, 60):
            response = self.write(
                "PUT",
                "/api/v1/preferences",
                {"reminderLeadMinutes": allowed},
            )
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.get_json()["reminderLeadMinutes"], allowed)
        for invalid in (0, 14, 45, 61, "30", True):
            with self.subTest(reminderLeadMinutes=invalid):
                response = self.write(
                    "PUT",
                    "/api/v1/preferences",
                    {"reminderLeadMinutes": invalid},
                )
                self.assertEqual(response.status_code, 422, response.get_json())
        for invalid_sound in ("1", 1, "yes"):
            with self.subTest(reminderSound=invalid_sound):
                response = self.write(
                    "PUT",
                    "/api/v1/preferences",
                    {"reminderSound": invalid_sound},
                )
                self.assertEqual(response.status_code, 422, response.get_json())

        due_with_larger_window = self.client.get(
            "/api/v1/reminders/due"
        ).get_json()["items"]
        self.assertEqual([item["id"] for item in due_with_larger_window], [booking["id"]])
        smaller = self.write(
            "PUT",
            "/api/v1/preferences",
            {"reminderLeadMinutes": 15},
        )
        self.assertEqual(smaller.status_code, 200, smaller.get_json())
        self.assertEqual(
            self.client.get("/api/v1/reminders/due").get_json()["items"], []
        )

        disabled = self.write(
            "PUT",
            "/api/v1/preferences",
            {"bookingReminder": False, "reminderSound": False, "reminderLeadMinutes": 60},
        )
        self.assertFalse(disabled.get_json()["bookingReminder"])
        self.assertFalse(disabled.get_json()["reminderSound"])
        self.assertEqual(disabled.get_json()["reminderLeadMinutes"], 60)
        self.assertEqual(
            self.client.get("/api/v1/reminders/due").get_json()["items"], []
        )
        enabled = self.write(
            "PUT",
            "/api/v1/preferences",
            {"bookingReminder": True, "reminderSound": True},
        )
        self.assertTrue(enabled.get_json()["bookingReminder"])
        self.assertTrue(enabled.get_json()["reminderSound"])
        self.assertEqual(enabled.get_json()["reminderLeadMinutes"], 60)
        self.assertEqual(
            [item["id"] for item in self.client.get("/api/v1/reminders/due").get_json()["items"]],
            [booking["id"]],
        )

    def test_change_notices_are_event_scoped_and_survive_later_edits(self):
        self.add_employee()
        employee = self.app.test_client()
        self.login("employee", "employee-pass-123", employee)
        self.now = datetime(2026, 8, 10, 8, 0, tzinfo=timezone(timedelta(hours=8)))
        created = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(self.room_id, start="09:00"),
            client=employee,
        ).get_json()
        changed = self.write(
            "PATCH",
            f"/api/v1/reservations/{created['id']}",
            self.booking_payload(
                self.room_id,
                start="10:00",
                expectedRevision=created["revision"],
            ),
        )
        self.assertEqual(changed.status_code, 200, changed.get_json())
        revision = changed.get_json()["revision"]

        # 本人随后又自行修改：旧的管理员变更通知必须仍然待确认（回归：按当前版本
        # 联接的旧实现会把它静默丢弃）。
        self_edited = self.write(
            "PATCH",
            f"/api/v1/reservations/{created['id']}",
            self.booking_payload(
                self.room_id,
                start="10:30",
                expectedRevision=revision,
            ),
            client=employee,
        )
        self.assertEqual(self_edited.status_code, 200, self_edited.get_json())

        notifications = employee.get("/api/v1/reminders/due").get_json()["items"]
        self.assertEqual(
            [(item["kind"], item["changeType"]) for item in notifications],
            [("change", "updated")],
        )
        notice = notifications[0]
        self.assertEqual(notice["revision"], self_edited.get_json()["revision"])
        self.assertEqual(notice["actorName"], "系统管理员")
        self.assertTrue(notice["eventId"])
        fields = {diff["field"]: diff for diff in notice["diffs"]}
        self.assertEqual(fields["start"], {"field": "start", "from": "09:00", "to": "10:00"})
        self.assertEqual(fields["end"], {"field": "end", "from": "10:00", "to": "11:00"})

        # 只有预约本人能按事件确认；管理员代确认被拒绝。
        denied = self.write(
            "POST",
            "/api/v1/reminders/ack",
            {"eventId": notice["eventId"]},
        )
        self.assertEqual(denied.status_code, 403, denied.get_json())
        acknowledged = self.write(
            "POST",
            "/api/v1/reminders/ack",
            {"eventId": notice["eventId"]},
            client=employee,
        )
        self.assertEqual(acknowledged.status_code, 200, acknowledged.get_json())
        # 事件维度确认幂等。
        again = self.write(
            "POST",
            "/api/v1/reminders/ack",
            {"eventId": notice["eventId"]},
            client=employee,
        )
        self.assertEqual(again.status_code, 200, again.get_json())
        self.assertEqual(
            [item for item in employee.get("/api/v1/reminders/due").get_json()["items"]
             if item["kind"] == "change"],
            [],
        )

        # 临近提醒与变更通知互相独立：变更已确认不影响临近条目出现。
        self.now = datetime(2026, 8, 10, 10, 10, tzinfo=timezone(timedelta(hours=8)))
        upcoming = employee.get("/api/v1/reminders/due").get_json()["items"]
        self.assertEqual(
            [(item["kind"], item["start"]) for item in upcoming],
            [("upcoming", "10:30")],
        )

    def test_change_notice_expiry_and_receipt_pruning(self):
        self.add_employee()
        employee = self.app.test_client()
        self.login("employee", "employee-pass-123", employee)
        self.now = datetime(2026, 8, 10, 8, 0, tzinfo=timezone(timedelta(hours=8)))
        created = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(self.room_id, start="09:00"),
            client=employee,
        ).get_json()
        self.write(
            "PATCH",
            f"/api/v1/reservations/{created['id']}",
            self.booking_payload(
                self.room_id,
                start="10:00",
                expectedRevision=created["revision"],
            ),
        )
        notice = employee.get("/api/v1/reminders/due").get_json()["items"][0]
        acknowledged = self.write(
            "POST",
            "/api/v1/reminders/ack",
            {"eventId": notice["eventId"]},
            client=employee,
        )
        self.assertEqual(acknowledged.status_code, 200, acknowledged.get_json())

        # 超过 45 天的未确认变更事件过期，不再出现。
        self.write(
            "PATCH",
            f"/api/v1/reservations/{created['id']}",
            self.booking_payload(
                self.room_id,
                start="11:00",
                expectedRevision=notice["revision"],
            ),
        )
        stale = employee.get("/api/v1/reminders/due").get_json()["items"]
        self.assertEqual([item["kind"] for item in stale], ["change"])
        stale_event_id = stale[0]["eventId"]
        with closing(
            sqlite3.connect(self.database)
        ) as db, db:
            db.execute(
                "UPDATE reservation_events SET occurred_at = '2026-06-01T00:00:00.000Z'"
                " WHERE id = ?",
                (stale_event_id,),
            )
        self.assertEqual(
            [item for item in employee.get("/api/v1/reminders/due").get_json()["items"]
             if item["kind"] == "change"],
            [],
        )

        # 确认动作顺带清理 90 天前的旧回执。
        with closing(
            sqlite3.connect(self.database)
        ) as db, db:
            db.execute(
                "UPDATE notice_receipts SET acknowledged_at = '2026-05-01T00:00:00.000Z'"
            )
            rows_before = db.execute("SELECT COUNT(*) FROM notice_receipts").fetchone()[0]
        self.assertEqual(rows_before, 1)
        self.write(
            "PATCH",
            f"/api/v1/reservations/{created['id']}",
            self.booking_payload(
                self.room_id,
                start="12:00",
                expectedRevision=stale[0]["revision"],
            ),
        )
        fresh = [
            item
            for item in employee.get("/api/v1/reminders/due").get_json()["items"]
            if item["kind"] == "change"
        ]
        self.assertEqual(len(fresh), 1)
        pruned = self.write(
            "POST",
            "/api/v1/reminders/ack",
            {"eventId": fresh[0]["eventId"]},
            client=employee,
        )
        self.assertEqual(pruned.status_code, 200, pruned.get_json())
        with closing(
            sqlite3.connect(self.database)
        ) as db:
            rows_after = db.execute("SELECT COUNT(*) FROM notice_receipts").fetchone()[0]
        self.assertEqual(rows_after, 1)

    def test_backup_diagnostics_and_token_read_scope(self):
        backup = self.write("POST", "/api/v1/admin/backups")
        self.assertEqual(backup.status_code, 201, backup.get_json())
        target = self.root / "backups" / backup.get_json()["fileName"]
        self.assertTrue(target.is_file())
        with closing(sqlite3.connect(target)) as db, db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        diagnostics = self.client.get("/api/v1/admin/diagnostics").get_json()
        text = json.dumps(diagnostics, ensure_ascii=False)
        self.assertNotIn("张晓燕", text)
        self.assertNotIn("TEST-2026", text)

        token_response = self.write(
            "POST",
            "/api/v1/admin/tokens",
            {"name": "只读测试", "scopes": ["rooms:read"]},
        )
        self.assertEqual(token_response.status_code, 201)
        raw = token_response.get_json()["token"]
        with closing(sqlite3.connect(self.database)) as db, db:
            stored = db.execute("SELECT token_hash FROM api_tokens").fetchone()[0]
        self.assertNotEqual(stored, raw)
        rooms = self.client.get(
            "/api/v1/integration/rooms", headers={"Authorization": f"Bearer {raw}"}
        )
        self.assertEqual(rooms.status_code, 200)
        forbidden = self.client.get(
            "/api/v1/integration/health", headers={"Authorization": f"Bearer {raw}"}
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_token_expiry_requires_timezone_and_is_enforced(self):
        for invalid in ("2026-08-10", "2026-08-10T12:00:00"):
            response = self.write(
                "POST",
                "/api/v1/admin/tokens",
                {
                    "name": "无效时效",
                    "scopes": ["health:read"],
                    "expiresAt": invalid,
                },
            )
            self.assertEqual(response.status_code, 422, invalid)

        created = self.write(
            "POST",
            "/api/v1/admin/tokens",
            {
                "name": "有时效令牌",
                "scopes": ["health:read"],
                "expiresAt": "2026-08-10T00:00:00+08:00",
            },
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        self.assertEqual(created.get_json()["expiresAt"], "2026-08-09T16:00:00Z")
        raw = created.get_json()["token"]
        headers = {"Authorization": f"Bearer {raw}"}
        self.assertEqual(
            self.client.get("/api/v1/integration/health", headers=headers).status_code,
            200,
        )
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "UPDATE api_tokens SET expires_at = '2026-08-08T00:00:00Z' WHERE id = ?",
                (created.get_json()["id"],),
            )
        expired = self.client.get("/api/v1/integration/health", headers=headers)
        self.assertEqual(expired.status_code, 401)
        self.assertEqual(expired.get_json()["error"]["code"], "TOKEN_EXPIRED")

    def test_system_shape_and_spa_fallback(self):
        status = self.client.get("/api/v1/admin/system").get_json()
        self.assertEqual(status["status"], "warning")
        self.assertEqual(status["health"], "warning")
        self.assertEqual(status["label"], "系统可用，但备份待处理")
        self.assertFalse(status["backupCaughtUp"])
        self.assertEqual(
            {service["id"] for service in status["services"]},
            {"api", "display", "database", "backup"},
        )
        backup = self.write("POST", "/api/v1/admin/backups")
        self.assertEqual(backup.status_code, 201, backup.get_json())
        protected = self.client.get("/api/v1/admin/system").get_json()
        self.assertEqual(protected["status"], "normal")
        self.assertEqual(protected["health"], "healthy")
        self.assertEqual(protected["label"], "系统运行正常")
        self.assertTrue(protected["backupCaughtUp"])
        dist = self.root / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<div id='root'>V2</div>", encoding="utf-8")
        (dist / "assets" / "app.js").write_text("console.log('v2')", encoding="utf-8")
        page = self.client.get("/history/deep-link")
        try:
            self.assertIn("V2", page.get_data(as_text=True))
        finally:
            page.close()
        asset = self.client.get("/assets/app.js")
        try:
            self.assertEqual(asset.status_code, 200)
            self.assertIn("immutable", asset.headers["Cache-Control"])
        finally:
            asset.close()
        api_missing = self.client.get("/api/v1/not-real")
        self.assertEqual(api_missing.status_code, 404)
        self.assertTrue(api_missing.is_json)


if __name__ == "__main__":
    unittest.main()

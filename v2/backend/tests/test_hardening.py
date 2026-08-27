from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from http.client import RemoteDisconnected
from pathlib import Path
from time import monotonic, sleep
from unittest import mock
from urllib.error import URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import backup as backup_entrypoint
import restore as restore_entrypoint
import service as service_entrypoint
import v2app.backup as backup_module
import v2app.db as db_module
from tests.test_backend import BackendTestCase, INSTALL_ID
from v2app.backup import (
    backup_records,
    create_backup,
    load_backup_sidecar,
    maintenance_lock,
    sha256_file,
)
from v2app.db import prepare_database
from v2app.errors import ApiError


class ApiAndAuthenticationHardeningTests(BackendTestCase):
    def test_api_413_and_500_have_stable_json_request_ids(self):
        self.setup_system()
        self.login()
        token = self.csrf()
        oversized = json.dumps(
            {"username": "admin", "password": "x" * (300 * 1024)}
        ).encode("utf-8")
        too_large = self.client.post(
            "/api/v1/session",
            data=oversized,
            content_type="application/json",
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(too_large.status_code, 413)
        payload = too_large.get_json()
        self.assertEqual(payload["error"]["code"], "PAYLOAD_TOO_LARGE")
        self.assertEqual(payload["requestId"], payload["error"]["requestId"])
        self.assertEqual(payload["requestId"], too_large.headers["X-Request-Id"])

        room_id = self.bootstrap()["rooms"][0]["id"]
        self.app.config["TRANSACTION_FAILPOINT"] = "create_after_slots"
        failed = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(room_id),
        )
        self.assertEqual(failed.status_code, 500)
        body = failed.get_json()
        self.assertEqual(body["error"]["code"], "INTERNAL_ERROR")
        self.assertEqual(body["requestId"], body["error"]["requestId"])
        self.assertNotIn("transaction failpoint", failed.get_data(as_text=True))

    def test_passive_polling_does_not_renew_idle_and_absolute_session(self):
        self.setup_system()
        clock = [1_000.0]
        self.app.config["SESSION_TIME_PROVIDER"] = lambda: clock[0]
        self.login()

        clock[0] = 2_690.0
        self.assertEqual(self.client.get("/api/v1/admin/system").status_code, 200)
        clock[0] = 2_700.0
        self.assertEqual(self.client.get("/api/v1/admin/audit").status_code, 200)
        clock[0] = 2_750.0
        self.assertEqual(self.client.get("/api/v1/admin/tokens").status_code, 200)
        clock[0] = 2_801.0
        expired = self.client.get("/api/v1/bootstrap")
        self.assertEqual(expired.status_code, 401)
        self.assertEqual(expired.get_json()["error"]["code"], "SESSION_EXPIRED")

        clock[0] = 3_000.0
        self.login()
        clock[0] = 46_201.0
        with self.client.session_transaction() as stored:
            stored["_issued_at"] = 3_000.0
            stored["_last_active_at"] = clock[0]
        absolute = self.client.get("/api/v1/bootstrap")
        self.assertEqual(absolute.status_code, 401)
        with closing(sqlite3.connect(self.database)) as db, db:
            reasons = [
                json.loads(row[0])["reason"]
                for row in db.execute(
                    "SELECT details_json FROM security_audit_log "
                    "WHERE action='auth.session_expired'"
                )
            ]
        self.assertIn("idle_timeout", reasons)
        self.assertIn("absolute_timeout", reasons)

    def test_room_poll_is_passive_while_calendar_read_renews_idle_session(self):
        self.setup_system()
        clock = [1_000.0]
        self.app.config["SESSION_TIME_PROVIDER"] = lambda: clock[0]
        self.login()

        clock[0] = 1_100.0
        rooms = self.client.get("/api/v1/rooms")
        self.assertEqual(rooms.status_code, 200, rooms.get_json())
        with self.client.session_transaction() as stored:
            self.assertEqual(stored["_last_active_at"], 1_000.0)

        clock[0] = 1_200.0
        reservations = self.client.get(
            "/api/v1/reservations?dateFrom=2026-08-10&dateTo=2026-08-10"
        )
        self.assertEqual(reservations.status_code, 200, reservations.get_json())
        with self.client.session_transaction() as stored:
            self.assertEqual(stored["_last_active_at"], 1_200.0)

    def test_login_uses_dummy_hash_ip_limit_and_hmac_audit_fingerprints(self):
        self.setup_system()
        self.app.config["LOGIN_RATE_MAX_ATTEMPTS"] = 10
        self.app.config["LOGIN_RATE_IP_MAX_ATTEMPTS"] = 2
        missing_client = self.app.test_client()
        with mock.patch(
            "v2app.api.core.check_password_hash", return_value=False
        ) as password_check:
            missing = self.write(
                "POST",
                "/api/v1/session",
                {"username": "does-not-exist", "password": "bad-pass-123"},
                client=missing_client,
                environ_base={"REMOTE_ADDR": "10.77.88.90"},
            )
        self.assertEqual(missing.status_code, 401)
        self.assertTrue(password_check.call_args.args[0].startswith("scrypt:"))

        client = self.app.test_client()
        address = "10.77.88.91"
        for username in ("rotating-one", "rotating-two"):
            response = self.write(
                "POST",
                "/api/v1/session",
                {"username": username, "password": "bad-pass-123"},
                client=client,
                environ_base={"REMOTE_ADDR": address},
            )
            self.assertEqual(response.status_code, 401)
        limited = self.write(
            "POST",
            "/api/v1/session",
            {"username": "rotating-three", "password": "x" * 257},
            client=client,
            environ_base={"REMOTE_ADDR": address},
        )
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.get_json()["error"]["code"], "LOGIN_RATE_LIMITED")
        with closing(sqlite3.connect(self.database)) as db, db:
            audit_text = "\n".join(
                row[0]
                for row in db.execute(
                    "SELECT details_json FROM security_audit_log "
                    "WHERE action='auth.login_failed'"
                )
            )
        self.assertNotIn(address, audit_text)
        self.assertNotIn("rotating-three", audit_text)
        self.assertIn("ipFingerprint", audit_text)


class ApplicationInvariantHardeningTests(BackendTestCase):
    def test_bootstrap_exposes_server_business_clock(self):
        self.setup_system()
        self.login()
        payload = self.bootstrap()
        self.assertEqual(payload["serverDate"], "2026-08-09")
        self.assertEqual(payload["serverTime"], "08:00:00")

    def test_missing_required_user_preference_fails_closed(self):
        self.setup_system()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("DELETE FROM user_preferences")
            db.commit()

        state = prepare_database(self.database, mirror_setup_complete=True)
        self.assertFalse(state.ready)
        self.assertEqual(state.code, "DATABASE_APPLICATION_INVARIANT_FAILED")

    def test_missing_global_tag_fails_closed(self):
        self.setup_system()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("DELETE FROM global_tags WHERE slot = 2")
            db.commit()
        state = prepare_database(self.database, mirror_setup_complete=True)
        self.assertFalse(state.ready)
        self.assertEqual(state.code, "DATABASE_APPLICATION_INVARIANT_FAILED")

    def test_missing_settings_fails_closed(self):
        self.setup_system()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("DELETE FROM system_settings WHERE id = 1")
            db.commit()
        state = prepare_database(self.database, mirror_setup_complete=True)
        self.assertFalse(state.ready)
        self.assertEqual(state.code, "DATABASE_APPLICATION_INVARIANT_FAILED")

    def test_backup_rejects_readable_database_with_missing_preference(self):
        self.setup_system()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("DELETE FROM user_preferences")
            db.commit()
        with self.assertRaisesRegex(RuntimeError, "业务完整性"):
            create_backup(
                self.database,
                self.root / "backups",
                install_id=INSTALL_ID,
                sequence=1,
            )

    @unittest.skipUnless(os.name == "posix", "fcntl 仅在 POSIX 上可检查句柄访问模式")
    def test_backup_fsyncs_only_writable_handles(self):
        # 真实 Windows 服务中备份以 Errno 9 失败：os.fsync 在 Windows 上要求
        # 句柄可写，而 POSIX 允许只读 fsync。用 fcntl 记录每次 fsync 的访问
        # 模式，锁定备份管道不会回退到只读句柄。
        import fcntl

        self.setup_system()
        access_modes = []
        original_fsync = os.fsync

        def spy(descriptor):
            access_modes.append(fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE)
            return original_fsync(descriptor)

        with mock.patch("os.fsync", side_effect=spy):
            created_path, _metadata = create_backup(
                self.database,
                self.root / "backups",
                install_id=INSTALL_ID,
                sequence=1,
            )
        self.assertTrue(created_path.is_file())
        self.assertTrue(access_modes)
        self.assertTrue(
            all(mode in (os.O_WRONLY, os.O_RDWR) for mode in access_modes),
            f"backup pipeline fsynced read-only handle(s): {access_modes}",
        )

    def test_login_account_limit_applies_across_rotating_ip_addresses(self):
        self.setup_system()
        self.app.config["LOGIN_RATE_MAX_ATTEMPTS"] = 2
        self.app.config["LOGIN_RATE_IP_MAX_ATTEMPTS"] = 100
        username = "admin"
        for address in ("10.20.30.1", "10.20.30.2"):
            client = self.app.test_client()
            failed = self.write(
                "POST",
                "/api/v1/session",
                {"username": username, "password": "bad-pass-123"},
                client=client,
                environ_base={"REMOTE_ADDR": address},
            )
            self.assertEqual(failed.status_code, 401)
        third = self.app.test_client()
        limited = self.write(
            "POST",
            "/api/v1/session",
            {"username": username, "password": "admin-pass-123"},
            client=third,
            environ_base={"REMOTE_ADDR": "10.20.30.3"},
        )
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.get_json()["error"]["code"], "LOGIN_RATE_LIMITED")


class PaginationAndReservationHardeningTests(BackendTestCase):
    def setUp(self) -> None:
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

    def test_signed_pagination_totals_filters_and_strict_json_integers(self):
        for index, start in enumerate(("09:00", "10:00", "11:00"), 1):
            self.create_booking(
                start=start,
                duration=30,
                partyName=f"分页用户{index}",
                caseNumber=f"PAGE-{index}",
            )
        first = self.client.get(
            "/api/v1/reservations?date=2026-08-10&pageSize=2"
        ).get_json()
        self.assertEqual(first["total"], 3)
        self.assertEqual(len(first["items"]), 2)
        self.assertTrue(first["nextCursor"])
        second = self.client.get(
            "/api/v1/reservations?date=2026-08-10&pageSize=2&cursor="
            + first["nextCursor"]
        ).get_json()
        self.assertEqual(second["total"], 3)
        self.assertEqual(len(second["items"]), 1)
        tampered = first["nextCursor"][:-1] + (
            "0" if first["nextCursor"][-1] != "0" else "1"
        )
        invalid = self.client.get(
            "/api/v1/reservations?date=2026-08-10&cursor=" + tampered
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.get_json()["error"]["code"], "INVALID_CURSOR")

        float_duration = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(self.room_id, start="12:00", duration=60.0),
        )
        self.assertEqual(float_duration.status_code, 422)
        revision_string = self.write(
            "PATCH",
            f"/api/v1/reservations/{first['items'][0]['id']}",
            self.booking_payload(
                self.room_id,
                start="13:00",
                duration=30,
                expectedRevision="1",
            ),
        )
        self.assertEqual(revision_string.status_code, 422)

        filtered = self.client.get(
            "/api/v1/admin/audit?action=auth.login_succeeded&outcome=succeeded&pageSize=1"
        ).get_json()
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["items"][0]["action"], "auth.login_succeeded")
        invalid_date = self.client.get(
            "/api/v1/admin/audit?dateFrom=2026-08-09T00:00:00"
        )
        self.assertEqual(invalid_date.status_code, 422)

    def test_date_month_extremes_and_filter_bound_cursors_return_stable_json(self):
        for path in (
            "/api/v1/reservations?dateFrom=0001-01-01&dateTo=0001-01-01",
            "/api/v1/reservations?dateFrom=9999-12-31&dateTo=9999-12-31",
            "/api/v1/reservations/history?month=0001-01",
            "/api/v1/reservations/history?month=9999-11",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.get_json())

        for month in ("9999-12", "0000-01", "2026-13", "2026-8", "not-a-month"):
            with self.subTest(month=month):
                response = self.client.get(
                    "/api/v1/reservations/history?month=" + month
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.content_type, "application/json")
                body = response.get_json()
                self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
                self.assertEqual(body["requestId"], response.headers["X-Request-Id"])

        for path in (
            "/api/v1/reservations?dateFrom=2026-02-30&dateTo=2026-03-01",
            "/api/v1/reservations?dateFrom=2026-08-11&dateTo=2026-08-10",
            "/api/v1/reservations?dateFrom=0001-01-01&dateTo=0002-01-03",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.get_json()["error"]["code"], "VALIDATION_ERROR")

        for index, start in enumerate(("09:00", "10:00"), 1):
            self.create_booking(start=start, duration=30, caseNumber=f"CURSOR-{index}")
        reservation_page = self.client.get(
            "/api/v1/reservations?date=2026-08-10&pageSize=1"
        ).get_json()
        mismatched_dates = self.client.get(
            "/api/v1/reservations?dateFrom=2026-08-09&dateTo=2026-08-10&pageSize=1&cursor="
            + reservation_page["nextCursor"]
        )
        self.assertEqual(mismatched_dates.status_code, 422)
        self.assertEqual(mismatched_dates.get_json()["error"]["code"], "INVALID_CURSOR")

        history_page = self.client.get(
            "/api/v1/reservations/history?month=2026-08&pageSize=1"
        ).get_json()
        owner_id = self.bootstrap()["currentUser"]["id"]
        mismatched_filter = self.client.get(
            f"/api/v1/reservations/history?month=2026-08&ownerId={owner_id}&pageSize=1&cursor="
            + history_page["nextCursor"]
        )
        self.assertEqual(mismatched_filter.status_code, 422)
        self.assertEqual(mismatched_filter.get_json()["error"]["code"], "INVALID_CURSOR")
        mismatched_status = self.client.get(
            "/api/v1/reservations/history?month=2026-08&status=active&pageSize=1&cursor="
            + history_page["nextCursor"]
        )
        self.assertEqual(mismatched_status.status_code, 422)
        self.assertEqual(mismatched_status.get_json()["error"]["code"], "INVALID_CURSOR")

        for path in (
            "/api/v1/admin/audit?dateFrom=0001-01-01T00:00:00Z&dateTo=0001-01-01T00:00:00Z",
            "/api/v1/admin/audit?dateFrom=9999-12-31T23:59:59Z&dateTo=9999-12-31T23:59:59Z",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.get_json())
        for path in (
            "/api/v1/admin/audit?dateFrom=0001-01-01T00:00:00%2B14:00",
            "/api/v1/admin/audit?dateTo=9999-12-31T23:59:59-12:00",
            "/api/v1/admin/audit?dateFrom=2026-08-11T00:00:00Z&dateTo=2026-08-10T00:00:00Z",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.get_json()["error"]["code"], "VALIDATION_ERROR")
        audit_page = self.client.get("/api/v1/admin/audit?pageSize=1").get_json()
        self.assertTrue(audit_page["nextCursor"])
        audit_mismatch = self.client.get(
            "/api/v1/admin/audit?pageSize=1&action=auth.login_succeeded&cursor="
            + audit_page["nextCursor"]
        )
        self.assertEqual(audit_mismatch.status_code, 422)
        self.assertEqual(audit_mismatch.get_json()["error"]["code"], "INVALID_CURSOR")

    def test_reservation_purpose_is_required_by_the_api(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            purpose_column = next(
                row for row in db.execute("PRAGMA table_info(reservations)")
                if row[1] == "purpose"
            )
        self.assertIsNone(purpose_column[4])
        for purpose in (None, "", "   "):
            with self.subTest(purpose=purpose):
                payload = self.booking_payload(self.room_id, start="09:00")
                if purpose is None:
                    payload.pop("purpose")
                else:
                    payload["purpose"] = purpose
                response = self.write("POST", "/api/v1/reservations", payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.get_json()["error"]["fields"]["purpose"],
                    "请输入事项",
                )

        booking = self.create_booking(start="10:00")
        for purpose in (None, "", "   "):
            with self.subTest(method="PATCH", purpose=purpose):
                payload = self.booking_payload(
                    self.room_id,
                    start="10:30",
                    duration=30,
                    expectedRevision=booking["revision"],
                )
                if purpose is None:
                    payload.pop("purpose")
                else:
                    payload["purpose"] = purpose
                response = self.write(
                    "PATCH",
                    f"/api/v1/reservations/{booking['id']}",
                    payload,
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.get_json()["error"]["fields"]["purpose"],
                    "请输入事项",
                )

    def test_owner_snapshot_username_immutability_and_started_cancel_rule(self):
        self.now = datetime(
            2026, 8, 10, 8, 0, tzinfo=timezone(timedelta(hours=8))
        )
        booking = self.create_booking(start="09:00", duration=60)
        preferences = self.write(
            "PUT",
            "/api/v1/preferences",
            {"name": "管理员新姓名"},
        )
        self.assertEqual(preferences.status_code, 200)
        detail = self.client.get(
            f"/api/v1/reservations/{booking['id']}"
        ).get_json()
        self.assertEqual(detail["owner"]["name"], "系统管理员")

        user_id = self.bootstrap()["currentUser"]["id"]
        immutable = self.write(
            "PATCH",
            f"/api/v1/users/{user_id}",
            {"username": "renamed-admin"},
        )
        self.assertEqual(immutable.status_code, 422)
        self.assertEqual(immutable.get_json()["error"]["code"], "USERNAME_IMMUTABLE")

        self.now = datetime(
            2026, 8, 10, 9, 15, tzinfo=timezone(timedelta(hours=8))
        )
        update = self.write(
            "PATCH",
            f"/api/v1/reservations/{booking['id']}",
            self.booking_payload(
                self.room_id,
                start="10:00",
                duration=30,
                expectedRevision=1,
            ),
        )
        self.assertEqual(update.status_code, 409)
        self.assertEqual(update.get_json()["error"]["code"], "BOOKING_STARTED")
        cancelled = self.write(
            "POST",
            f"/api/v1/reservations/{booking['id']}/cancel",
            {"expectedRevision": 1},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.get_json()["status"], "cancelled")


class BackupRestoreAndServiceHardeningTests(BackendTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.setup_system()
        self.login()

    @staticmethod
    def downgrade_backup_to_schema_v1(backup: Path) -> None:
        with closing(sqlite3.connect(backup)) as db, db:
            db.execute("ALTER TABLE rooms DROP COLUMN show_on_display")
            db.execute("ALTER TABLE user_preferences DROP COLUMN default_tag_slot")
            db.execute(
                "ALTER TABLE user_preferences DROP COLUMN reminder_lead_minutes"
            )
            db.execute("ALTER TABLE user_preferences DROP COLUMN reminder_template")
            db.execute("ALTER TABLE user_preferences DROP COLUMN reminder_sound")
            db.execute("DROP TABLE IF EXISTS handover_requests")
            db.execute(
                """
                CREATE TABLE reservation_events_new (
                    id TEXT PRIMARY KEY,
                    reservation_id TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL
                        CHECK (event_type IN ('created', 'updated', 'cancelled')),
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    before_json TEXT,
                    after_json TEXT,
                    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    FOREIGN KEY (reservation_id) REFERENCES reservations(id),
                    FOREIGN KEY (actor_user_id) REFERENCES users(id)
                )
                """
            )
            db.execute(
                """
                INSERT INTO reservation_events_new (
                    id, reservation_id, actor_user_id, event_type, revision,
                    before_json, after_json, occurred_at
                )
                SELECT id, reservation_id, actor_user_id, event_type, revision,
                       before_json, after_json, occurred_at
                FROM reservation_events
                """
            )
            db.execute("DROP TABLE reservation_events")
            db.execute(
                "ALTER TABLE reservation_events_new RENAME TO reservation_events"
            )
            db.execute(
                "CREATE INDEX idx_events_reservation "
                "ON reservation_events(reservation_id, occurred_at)"
            )
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
        sidecar_path = backup.with_suffix(".json")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar["databaseSchemaVersion"] = 1
        sidecar["databaseBytes"] = backup.stat().st_size
        sidecar["databaseSha256"] = sha256_file(backup)
        sidecar_path.write_text(
            json.dumps(sidecar, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    def test_backup_sidecar_monotonic_sequence_rotation_and_failure_audit(self):
        first = self.write("POST", "/api/v1/admin/backups")
        self.assertEqual(first.status_code, 201, first.get_json())
        backup_dir = self.root / "backups"
        first_path = backup_dir / first.get_json()["fileName"]
        sidecar = load_backup_sidecar(
            first_path.with_suffix(".json"),
            expected_install_id=INSTALL_ID,
            verify_hash=True,
        )
        self.assertEqual(sidecar["databaseSha256"], sha256_file(first_path))
        self.assertEqual(sidecar["sequence"], 1)
        self.assertEqual(sidecar["databaseSchemaVersion"], 4)
        self.assertEqual(sidecar["sourceDataSequence"], first.get_json()["sourceDataSequence"])
        self.assertEqual(list(backup_dir.glob(".*.part-*")), [])
        self.assertFalse(Path(str(first_path) + "-wal").exists())
        self.assertFalse(Path(str(first_path) + "-shm").exists())
        with closing(sqlite3.connect(first_path)) as backup_db:
            self.assertEqual(backup_db.execute("PRAGMA journal_mode").fetchone()[0], "delete")

        room = self.write(
            "POST",
            "/api/v1/rooms",
            {"name": "备份序列房间", "sortOrder": 99},
        )
        self.assertEqual(room.status_code, 201)
        self.assertFalse(
            self.client.get("/api/v1/admin/system").get_json()["backupCaughtUp"]
        )
        second = self.write("POST", "/api/v1/admin/backups")
        self.assertEqual(second.get_json()["sequence"], 2)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE app_meta SET value='0' WHERE key='backup_sequence'")
        third = self.write("POST", "/api/v1/admin/backups")
        self.assertEqual(third.get_json()["sequence"], 3)

        retired_path = None
        for sequence in (10, 11, 12):
            created_path, _metadata = create_backup(
                self.database,
                backup_dir,
                install_id=INSTALL_ID,
                sequence=sequence,
                keep_count=2,
            )
            if sequence == 10:
                retired_path = created_path
                Path(str(created_path) + "-wal").write_bytes(b"")
                Path(str(created_path) + "-shm").write_bytes(b"stale")
                (backup_dir / f".{created_path.name}.old.part-shm").write_bytes(b"stale")
        records = backup_records(backup_dir, expected_install_id=INSTALL_ID)
        self.assertEqual([value["sequence"] for _, value in records], [12, 11])
        self.assertIsNotNone(retired_path)
        self.assertFalse(Path(str(retired_path) + "-wal").exists())
        self.assertFalse(Path(str(retired_path) + "-shm").exists())
        self.assertEqual(list(backup_dir.glob(f".{retired_path.name}.*.part-*")), [])
        records[0][0].write_bytes(b"corrupted-backup")
        status = self.client.get("/api/v1/admin/system").get_json()
        self.assertEqual(status["backupSequence"], 11)

        with maintenance_lock(
            self.data_dir / "maintenance.lock",
            operation="restore",
            install_id=INSTALL_ID,
        ):
            failed = self.write("POST", "/api/v1/admin/backups")
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(failed.get_json()["error"]["code"], "BACKUP_FAILED")
        with closing(sqlite3.connect(self.database)) as db, db:
            actions = {
                row[0]
                for row in db.execute(
                    "SELECT action FROM security_audit_log WHERE action LIKE 'backup.%'"
                )
            }
        self.assertTrue(
            {"backup.requested", "backup.succeeded", "backup.failed"}.issubset(actions)
        )

    def test_backup_sequence_skips_foreign_sidecar_after_rollback_retry(self):
        """V241-B1 回归：失败更新回滚后重试，跨版本 sidecar 也要占住序列。

        故障注入的健康失败更新会由新版本运行时留下当前版本无法解析的
        sidecar（例如更高的 databaseSchemaVersion）；回滚恢复旧数据库
        （水位回落）后，重试更新的更新前在线备份必须避开该序列，而不
        是瞄准已存在的文件触发拒绝覆盖。
        """

        first = self.write("POST", "/api/v1/admin/backups")
        self.assertEqual(first.status_code, 201, first.get_json())
        room = self.write(
            "POST", "/api/v1/rooms", {"name": "跨版本序列房间", "sortOrder": 97}
        )
        self.assertEqual(room.status_code, 201)
        second = self.write("POST", "/api/v1/admin/backups")
        self.assertEqual(second.status_code, 201, second.get_json())
        self.assertEqual(second.get_json()["sequence"], 2)
        backup_dir = self.root / "backups"
        # 模拟更新器回滚：data 树回到在线备份之前的快照（水位 2），
        # 但 backups/ 中残留更新版本运行时写出的 seq3 备份，其
        # sidecar 带当前版本不支持的 databaseSchemaVersion。
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE app_meta SET value='2' WHERE key='backup_sequence'")
        second_path = backup_dir / second.get_json()["fileName"]
        foreign_db = backup_dir / "reservation-v2-backup-00000003.db"
        foreign_db.write_bytes(second_path.read_bytes())
        foreign_sidecar_path = foreign_db.with_suffix(".json")
        foreign_sidecar = json.loads(
            second_path.with_suffix(".json").read_text(encoding="utf-8")
        )
        foreign_sidecar["sequence"] = 3
        foreign_sidecar["fileName"] = foreign_db.name
        foreign_sidecar["databaseSchemaVersion"] = (
            foreign_sidecar["databaseSchemaVersion"] + 1
        )
        foreign_sidecar_path.write_text(
            json.dumps(foreign_sidecar, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        with self.assertRaises(RuntimeError):
            load_backup_sidecar(foreign_sidecar_path, expected_install_id=INSTALL_ID)
        self.assertEqual(
            [
                value["sequence"]
                for _, value in backup_records(
                    backup_dir, expected_install_id=INSTALL_ID
                )
            ],
            [2, 1],
        )
        retry = self.write("POST", "/api/v1/admin/backups")
        self.assertEqual(retry.status_code, 201, retry.get_json())
        self.assertEqual(retry.get_json()["sequence"], 4)
        # 拒绝覆盖语义保持：他版本备份文件未被触碰或改写。
        self.assertEqual(foreign_db.read_bytes(), second_path.read_bytes())
        self.assertTrue((backup_dir / "reservation-v2-backup-00000004.db").is_file())

    def test_backup_preserves_product_defined_api_errors(self):
        denied = ApiError(403, "FORBIDDEN", "当前账户无权执行备份")
        with mock.patch("v2app.api.system.locked_actor", side_effect=denied):
            response = self.write("POST", "/api/v1/admin/backups")
        self.assertEqual(response.status_code, 403)
        body = response.get_json()
        self.assertEqual(body["error"]["code"], "FORBIDDEN")
        self.assertNotEqual(body["error"]["code"], "BACKUP_FAILED")

    def test_backup_failure_code_survives_failure_audit_error(self):
        with mock.patch(
            "v2app.api.system.maintenance_lock",
            side_effect=RuntimeError("backup pipeline failed"),
        ), mock.patch(
            "v2app.api.system.write_security_audit",
            side_effect=RuntimeError("failure audit failed"),
        ):
            response = self.write("POST", "/api/v1/admin/backups")
        self.assertEqual(response.status_code, 500, response.get_json())
        self.assertEqual(response.get_json()["error"]["code"], "BACKUP_FAILED")

    def test_scheduled_backup_is_due_on_new_local_day_before_exact_24_hours(self):
        backup_dir = self.root / "backups"
        local_zone = timezone(timedelta(hours=8))
        yesterday = datetime(2026, 8, 9, 2, 0, 30, tzinfo=local_zone)
        today = datetime(2026, 8, 10, 2, 0, 0, tzinfo=local_zone)
        create_backup(
            self.database,
            backup_dir,
            install_id=INSTALL_ID,
            sequence=1,
            now=yesterday,
        )
        self.assertTrue(
            backup_module.scheduled_backup_due(
                backup_dir,
                install_id=INSTALL_ID,
                now=today,
                catch_up=False,
            )
        )
        self.assertFalse(
            backup_module.scheduled_backup_due(
                backup_dir,
                install_id=INSTALL_ID,
                now=today,
                catch_up=True,
            )
        )

    def test_current_scheduled_backup_skips_before_contending_for_maintenance_lock(self):
        backup_dir = self.root / "backups"
        create_backup(
            self.database,
            backup_dir,
            install_id=INSTALL_ID,
            sequence=1,
        )
        self.write_install_json(self.data_dir, setup_complete=True)
        (self.data_dir / "install_id").write_text(
            INSTALL_ID + "\n", encoding="utf-8"
        )
        paths = backup_entrypoint._CliPaths(
            program_dir=self.root,
            data_dir=self.data_dir,
            backup_dir=backup_dir,
        )
        args = backup_entrypoint.argparse.Namespace(
            scheduled=True,
            catch_up=False,
            expected_install_id=INSTALL_ID,
        )
        with mock.patch.object(
            backup_entrypoint,
            "maintenance_lock",
            side_effect=RuntimeError("another verified maintenance task is active"),
        ) as lock, mock.patch.object(
            backup_entrypoint, "_logger", return_value=mock.Mock()
        ):
            self.assertEqual(backup_entrypoint._run_backup(args, paths), 0)
        lock.assert_not_called()
        status = json.loads(
            (self.data_dir / "backup-status.json").read_text(encoding="utf-8")
        )
        self.assertEqual(status["status"], "current")

    def test_maintenance_lock_reclaims_dead_and_reused_pid_but_rejects_live(self):
        lock_path = self.data_dir / "maintenance.lock"
        with maintenance_lock(
            lock_path,
            operation="backup",
            install_id=INSTALL_ID,
        ):
            active = json.loads(lock_path.read_text(encoding="utf-8"))
            self.assertEqual(active["pid"], os.getpid())
            self.assertEqual(active["operation"], "backup")
            self.assertEqual(active["installId"], INSTALL_ID)
            with self.assertRaisesRegex(RuntimeError, "正在执行"):
                with maintenance_lock(
                    lock_path,
                    operation="restore",
                    install_id=INSTALL_ID,
                ):
                    self.fail("active maintenance lock must not be entered")
        self.assertFalse(lock_path.exists())

        active["pid"] = 0xFFFFFFFF
        lock_path.write_text(json.dumps(active), encoding="utf-8")
        with maintenance_lock(
            lock_path,
            operation="restore",
            install_id=INSTALL_ID,
        ):
            reclaimed = json.loads(lock_path.read_text(encoding="utf-8"))
            self.assertEqual(reclaimed["pid"], os.getpid())
            self.assertNotEqual(reclaimed["token"], active["token"])
        self.assertFalse(lock_path.exists())

        active["pid"] = os.getpid()
        active["processIdentity"] = {
            "method": active["processIdentity"]["method"],
            "fingerprint": "0" * 64,
        }
        lock_path.write_text(json.dumps(active), encoding="utf-8")
        with maintenance_lock(
            lock_path,
            operation="backup",
            install_id=INSTALL_ID,
        ):
            reused = json.loads(lock_path.read_text(encoding="utf-8"))
            self.assertNotEqual(reused["token"], active["token"])
        self.assertFalse(lock_path.exists())

    def test_maintenance_lock_unknown_format_or_install_identity_fails_closed(self):
        lock_path = self.data_dir / "maintenance.lock"
        lock_path.write_text("not-json", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "不可识别"):
            with maintenance_lock(
                lock_path,
                operation="backup",
                install_id=INSTALL_ID,
            ):
                self.fail("unknown lock must remain closed")
        self.assertEqual(lock_path.read_text(encoding="utf-8"), "not-json")

        lock_path.unlink()
        with maintenance_lock(
            lock_path,
            operation="backup",
            install_id=INSTALL_ID,
        ):
            foreign = json.loads(lock_path.read_text(encoding="utf-8"))
        foreign["installId"] = "00000000-0000-4000-8000-000000000002"
        lock_path.write_text(json.dumps(foreign), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "安装身份不一致"):
            with maintenance_lock(
                lock_path,
                operation="backup",
                install_id=INSTALL_ID,
            ):
                self.fail("foreign lock must remain closed")
        self.assertTrue(lock_path.exists())

    def test_restore_allows_missing_database_and_rolls_back_failed_replacement(self):
        created = self.write("POST", "/api/v1/admin/backups").get_json()
        backup = self.root / "backups" / created["fileName"]
        for path in (
            self.database,
            Path(str(self.database) + "-wal"),
            Path(str(self.database) + "-shm"),
        ):
            if path.exists():
                path.unlink()

        with mock.patch.object(
            restore_entrypoint,
            "_record_restore_audit",
            side_effect=RuntimeError("forced post-replace failure"),
        ):
            with self.assertRaises(RuntimeError):
                restore_entrypoint.restore_backup(
                    database=self.database,
                    backup=backup,
                    backup_dir=self.root / "backups",
                    data_dir=self.data_dir,
                    install_id=INSTALL_ID,
                )
        self.assertFalse(self.database.exists())

        restored = restore_entrypoint.restore_backup(
            database=self.database,
            backup=backup,
            backup_dir=self.root / "backups",
            data_dir=self.data_dir,
            install_id=INSTALL_ID,
        )
        self.assertTrue(restored["restored"])
        state = prepare_database(self.database, mirror_setup_complete=True)
        self.assertTrue(state.ready)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        with mock.patch.object(
            restore_entrypoint,
            "_record_restore_audit",
            side_effect=RuntimeError("forced rollback"),
        ):
            with self.assertRaises(RuntimeError):
                restore_entrypoint.restore_backup(
                    database=self.database,
                    backup=backup,
                    backup_dir=self.root / "backups",
                    data_dir=self.data_dir,
                    install_id=INSTALL_ID,
                )
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)

    def test_restore_invalidates_sessions_above_the_backup_session_version(self):
        employee = self.add_employee()
        employee_client = self.app.test_client()
        self.login("employee", "employee-pass-123", client=employee_client)
        self.assertEqual(employee_client.get("/api/v1/bootstrap").status_code, 200)

        created = self.write("POST", "/api/v1/admin/backups").get_json()
        backup = self.root / "backups" / created["fileName"]
        reset = self.write(
            "POST",
            f"/api/v1/users/{employee['id']}/reset-password",
            {"password": "employee-new-pass-123"},
        )
        self.assertEqual(reset.status_code, 200, reset.get_json())
        fresh_pre_restore_client = self.app.test_client()
        self.login(
            "employee",
            "employee-new-pass-123",
            client=fresh_pre_restore_client,
        )
        self.assertEqual(
            fresh_pre_restore_client.get("/api/v1/bootstrap").status_code,
            200,
        )
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT session_version FROM users WHERE id = ?",
                    (employee["id"],),
                ).fetchone()[0],
                2,
            )

        restored = restore_entrypoint.restore_backup(
            database=self.database,
            backup=backup,
            backup_dir=self.root / "backups",
            data_dir=self.data_dir,
            install_id=INSTALL_ID,
        )
        self.assertTrue(restored["restored"])
        with closing(sqlite3.connect(self.database)) as db:
            restored_versions = [
                row[0]
                for row in db.execute(
                    "SELECT session_version FROM users ORDER BY id"
                )
            ]
        self.assertTrue(restored_versions)
        self.assertTrue(all(version > 2 for version in restored_versions))
        self.assertEqual(employee_client.get("/api/v1/bootstrap").status_code, 401)
        self.assertEqual(
            fresh_pre_restore_client.get("/api/v1/bootstrap").status_code,
            401,
        )
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM security_audit_log "
                    "WHERE action = 'restore.succeeded'"
                ).fetchone()[0],
                1,
            )

    def test_restore_recovers_corrupt_live_database_without_reusing_unknown_sessions(self):
        employee = self.add_employee()
        backup_session = self.app.test_client()
        self.login("employee", "employee-pass-123", client=backup_session)
        created = self.write("POST", "/api/v1/admin/backups").get_json()
        backup = self.root / "backups" / created["fileName"]

        reset = self.write(
            "POST",
            f"/api/v1/users/{employee['id']}/reset-password",
            {"password": "employee-new-pass-123"},
        )
        self.assertEqual(reset.status_code, 200, reset.get_json())
        live_session = self.app.test_client()
        self.login(
            "employee",
            "employee-new-pass-123",
            client=live_session,
        )
        self.assertEqual(live_session.get("/api/v1/bootstrap").status_code, 200)
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(
                db.execute(
                    "SELECT session_version FROM users WHERE id = ?",
                    (employee["id"],),
                ).fetchone()[0],
                2,
            )
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()

        self.database.write_bytes(b"corrupt-live-database")
        for side_file in (
            Path(str(self.database) + "-wal"),
            Path(str(self.database) + "-shm"),
        ):
            if side_file.exists():
                side_file.unlink()

        try:
            restored = restore_entrypoint.restore_backup(
                database=self.database,
                backup=backup,
                backup_dir=self.root / "backups",
                data_dir=self.data_dir,
                install_id=INSTALL_ID,
            )
        except sqlite3.DatabaseError as error:
            self.fail(f"valid backup must recover a corrupt live database: {error}")

        self.assertTrue(restored["restored"])
        self.assertTrue(prepare_database(self.database).ready)
        self.assertEqual(backup_session.get("/api/v1/bootstrap").status_code, 401)
        self.assertEqual(live_session.get("/api/v1/bootstrap").status_code, 401)
        with closing(sqlite3.connect(self.database)) as db:
            restored_versions = {
                row[0] for row in db.execute("SELECT session_version FROM users")
            }
        self.assertTrue(restored_versions)
        self.assertTrue(restored_versions.isdisjoint({1, 2}))

    def test_restore_fails_closed_before_snapshot_when_live_database_is_locked(self):
        created = self.write("POST", "/api/v1/admin/backups").get_json()
        backup = self.root / "backups" / created["fileName"]
        live_room = self.write(
            "POST",
            "/api/v1/rooms",
            {"name": "锁态现场", "sortOrder": 99},
        ).get_json()
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            self.assertEqual(db.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete")

        before_hash = hashlib.sha256(self.database.read_bytes()).hexdigest()
        snapshots_before = set((self.root / "backups").glob("pre-restore-*"))
        real_connect = sqlite3.connect

        def connect_without_wait(database, *args, **kwargs):
            kwargs["timeout"] = 0.05
            return real_connect(database, *args, **kwargs)

        locker = real_connect(self.database, timeout=0, isolation_level=None)
        locker.execute("BEGIN EXCLUSIVE")
        try:
            with mock.patch.object(
                restore_entrypoint.sqlite3,
                "connect",
                side_effect=connect_without_wait,
            ):
                with self.assertRaises(sqlite3.OperationalError) as raised:
                    restore_entrypoint.restore_backup(
                        database=self.database,
                        backup=backup,
                        backup_dir=self.root / "backups",
                        data_dir=self.data_dir,
                        install_id=INSTALL_ID,
                    )
        finally:
            locker.execute("ROLLBACK")
            locker.close()

        self.assertIn(
            raised.exception.sqlite_errorcode & 0xFF,
            {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED},
        )
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before_hash)
        self.assertEqual(
            set((self.root / "backups").glob("pre-restore-*")),
            snapshots_before,
        )
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT name FROM rooms WHERE id = ?", (live_room["id"],)
                ).fetchone()[0],
                "锁态现场",
            )

    def test_schema_v1_backup_migrates_and_failed_migration_restores_live_database(self):
        created = self.write("POST", "/api/v1/admin/backups").get_json()
        backup = self.root / "backups" / created["fileName"]
        self.downgrade_backup_to_schema_v1(backup)
        legacy_sidecar = load_backup_sidecar(
            backup.with_suffix(".json"),
            expected_install_id=INSTALL_ID,
            verify_hash=True,
        )
        self.assertEqual(legacy_sidecar["databaseSchemaVersion"], 1)

        live_room = self.write(
            "POST",
            "/api/v1/rooms",
            {"name": "恢复前现场", "sortOrder": 99},
        ).get_json()
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        original_add = db_module._add_column_if_missing
        calls = 0

        def fail_after_first_column(db, table, column, declaration):
            nonlocal calls
            original_add(db, table, column, declaration)
            calls += 1
            if calls == 1:
                raise RuntimeError("C0 restore migration failpoint")

        with mock.patch.object(
            db_module,
            "_add_column_if_missing",
            side_effect=fail_after_first_column,
        ):
            with self.assertRaisesRegex(RuntimeError, "恢复后数据库复检失败"):
                restore_entrypoint.restore_backup(
                    database=self.database,
                    backup=backup,
                    backup_dir=self.root / "backups",
                    data_dir=self.data_dir,
                    install_id=INSTALL_ID,
                )
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT name FROM rooms WHERE id = ?", (live_room["id"],)
                ).fetchone()[0],
                "恢复前现场",
            )

        result = restore_entrypoint.restore_backup(
            database=self.database,
            backup=backup,
            backup_dir=self.root / "backups",
            data_dir=self.data_dir,
            install_id=INSTALL_ID,
        )
        self.assertTrue(result["restored"])
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT value FROM app_meta WHERE key = 'schema_version'"
                ).fetchone()[0],
                "4",
            )
            self.assertIsNone(
                db.execute(
                    "SELECT name FROM rooms WHERE id = ?", (live_room["id"],)
                ).fetchone()
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM rooms WHERE show_on_display = 1"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                {
                    row[0]
                    for row in db.execute(
                        "SELECT DISTINCT reminder_template FROM user_preferences"
                    )
                },
                {db_module.DEFAULT_REMINDER_TEMPLATE},
            )

    def test_production_cli_layout_catch_up_and_expected_identity_contract(self):
        self.assertEqual(backup_entrypoint.PROGRAM_DIR, backup_entrypoint.APP_DIR.parent)
        self.assertEqual(restore_entrypoint.PROGRAM_DIR, restore_entrypoint.APP_DIR.parent)
        with mock.patch.object(service_entrypoint.subprocess, "Popen") as popen:
            service_entrypoint._launch_backup_catch_up({"install_id": INSTALL_ID})
        command = popen.call_args.args[0]
        self.assertEqual(command[1], str(service_entrypoint.APP_DIR / "backup.py"))
        self.assertEqual(
            command[2:],
            ["--catch-up", "--expected-install-id", INSTALL_ID],
        )
        self.assertEqual(popen.call_args.kwargs["env"]["PYTHONUTF8"], "1")

    def test_backup_cli_reporting_survives_pythonw_and_narrow_codepages(self):
        # 真实 Windows：补跑 worker 在 cp1252 重定向句柄上打印中文会以
        # UnicodeEncodeError 失败；pythonw 计划任务下 stdout/stderr 为 None。
        # 备份结果必须只由状态文件与审计决定，控制台报告不能破坏成功路径。
        self.write_install_json(self.data_dir, setup_complete=True)
        (self.data_dir / "install_id").write_text(INSTALL_ID + "\n", encoding="utf-8")
        paths = backup_entrypoint._CliPaths(
            program_dir=self.root,
            data_dir=self.data_dir,
            backup_dir=self.root / "backups",
        )
        with mock.patch.object(backup_entrypoint, "_logger", return_value=mock.Mock()):
            with mock.patch.object(backup_entrypoint.sys, "stdout", None), mock.patch.object(
                backup_entrypoint.sys, "stderr", None
            ):
                pythonw_args = backup_entrypoint.argparse.Namespace(
                    scheduled=False,
                    catch_up=False,
                    expected_install_id=None,
                )
                self.assertEqual(backup_entrypoint._run_backup(pythonw_args, paths), 0)
            status = json.loads(
                (self.data_dir / "backup-status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["status"], "succeeded")

            narrow_console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
            with mock.patch.object(
                backup_entrypoint.sys, "stdout", narrow_console
            ), mock.patch.object(backup_entrypoint.sys, "stderr", narrow_console):
                manual_args = backup_entrypoint.argparse.Namespace(
                    scheduled=False,
                    catch_up=False,
                    expected_install_id=None,
                )
                self.assertEqual(backup_entrypoint._run_backup(manual_args, paths), 0)
            second_status = json.loads(
                (self.data_dir / "backup-status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(second_status["status"], "succeeded")
        test_paths = backup_entrypoint._CliPaths(
            program_dir=self.root,
            data_dir=self.data_dir,
            backup_dir=self.root / "backups",
        )
        test_args = backup_entrypoint.argparse.Namespace(
            scheduled=True,
            catch_up=False,
            expected_install_id=None,
        )
        with mock.patch.object(
            backup_entrypoint, "_logger", return_value=mock.Mock()
        ):
            self.assertEqual(
                backup_entrypoint._run_backup(test_args, test_paths),
                1,
            )

    def test_maintenance_clis_ignore_environment_path_redirection(self):
        controlled_app = self.root / "app"
        controlled_app.mkdir()
        self.write_install_json(self.data_dir, setup_complete=True)
        (self.data_dir / "install_id").write_text(
            INSTALL_ID + "\n", encoding="utf-8"
        )
        malicious_program = self.root / "attacker-program"
        malicious_data = malicious_program / "attacker-data"
        malicious_backups = malicious_program / "backups"
        malicious_data.mkdir(parents=True)
        malicious_backups.mkdir()
        sentinel = malicious_data / "install.json"
        sentinel.write_text("attacker-controlled", encoding="utf-8")
        environment = {
            "MEETING_ROOM_V2_PROGRAM_DIR": str(malicious_program),
            "MEETING_ROOM_V2_DATA_DIR": str(malicious_data),
        }

        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(
            backup_entrypoint, "APP_DIR", controlled_app
        ), mock.patch.object(
            backup_entrypoint, "_logger", return_value=mock.Mock()
        ), mock.patch("builtins.print"):
            self.assertEqual(
                backup_entrypoint.main(
                    ["--scheduled", "--expected-install-id", INSTALL_ID]
                ),
                0,
            )
        created_backups = sorted((self.root / "backups").glob("*.db"))
        self.assertEqual(len(created_backups), 1)

        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(
            restore_entrypoint, "APP_DIR", controlled_app
        ), mock.patch.object(
            restore_entrypoint, "_logger", return_value=mock.Mock()
        ), mock.patch("builtins.print"):
            self.assertEqual(
                restore_entrypoint.main(
                    [
                        "--backup",
                        str(created_backups[0]),
                        "--expected-install-id",
                        INSTALL_ID,
                    ]
                ),
                0,
            )
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "attacker-controlled")
        self.assertEqual(list(malicious_backups.iterdir()), [])


class SetupListenerProcessTests(unittest.TestCase):
    def test_real_waitress_setup_handoff_has_no_bad_file_descriptor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            config = {
                "DATA_DIR": str(root / "data"),
                "DATABASE": str(root / "data" / "reservation.db"),
                "BACKUP_DIR": str(root / "backups"),
                "STATIC_DIR": str(root / "dist"),
                "INSTALL_ID": INSTALL_ID,
                "SERVICE_PORT": port,
            }
            program = (
                "from server import run_server_once\n"
                f"config = {config!r}\n"
                f"while run_server_once({port}, app_config=config):\n"
                "    pass\n"
            )
            environment = dict(os.environ)
            environment["PYTHONUNBUFFERED"] = "1"
            process = subprocess.Popen(
                [sys.executable, "-c", program],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            output = ""
            try:
                opener = build_opener(HTTPCookieProcessor(CookieJar()))
                base_url = f"http://127.0.0.1:{port}"
                deadline = monotonic() + 15
                while monotonic() < deadline:
                    if process.poll() is not None:
                        output = process.communicate(timeout=1)[0]
                        self.fail(f"setup listener exited before readiness:\n{output}")
                    try:
                        with opener.open(base_url + "/healthz", timeout=1) as response:
                            health = json.load(response)
                        if health.get("bind_mode") == "loopback":
                            break
                    except (URLError, TimeoutError, json.JSONDecodeError):
                        pass
                    sleep(0.05)
                else:
                    self.fail("setup listener did not become ready")

                with opener.open(base_url + "/api/v1/session", timeout=2) as response:
                    csrf = json.load(response)["csrfToken"]
                setup_body = json.dumps({
                    "admin": {
                        "username": "admin",
                        "password": "admin-pass-123",
                        "name": "系统管理员",
                        "department": "测试部门",
                    },
                    "rooms": [{"name": "笔录室 1"}],
                    "workStart": "08:30",
                    "workEnd": "17:30",
                }).encode("utf-8")
                request = Request(
                    base_url + "/api/v1/setup/complete",
                    data=setup_body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-CSRF-Token": csrf,
                        "Host": f"localhost:{port}",
                    },
                )
                with opener.open(request, timeout=5) as response:
                    self.assertEqual(response.status, 201)
                    self.assertTrue(json.load(response)["restartRequired"])

                stable = 0
                deadline = monotonic() + 15
                while monotonic() < deadline and stable < 2:
                    if process.poll() is not None:
                        output = process.communicate(timeout=1)[0]
                        self.fail(f"listener handoff failed:\n{output}")
                    try:
                        with opener.open(base_url + "/healthz", timeout=1) as response:
                            health = json.load(response)
                        ready = (
                            health.get("ok") is True
                            and health.get("setup_complete") is True
                            and health.get("bind_mode") == "lan"
                        )
                        stable = stable + 1 if ready else 0
                    except (
                        URLError,
                        TimeoutError,
                        RemoteDisconnected,
                        ConnectionResetError,
                        json.JSONDecodeError,
                    ):
                        stable = 0
                    sleep(0.05)
                self.assertEqual(stable, 2, "replacement LAN listener never stabilized")
            finally:
                if process.poll() is None:
                    process.terminate()
                remaining, _ = process.communicate(timeout=5)
                output += remaining
            self.assertNotIn("Bad file descriptor", output)
            self.assertNotIn("Errno 9", output)


if __name__ == "__main__":
    unittest.main()

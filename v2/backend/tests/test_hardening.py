from __future__ import annotations

import hashlib
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
        self.assertEqual(sidecar["sourceDataSequence"], first.get_json()["sourceDataSequence"])

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

        for sequence in (10, 11, 12):
            create_backup(
                self.database,
                backup_dir,
                install_id=INSTALL_ID,
                sequence=sequence,
                keep_count=2,
            )
        records = backup_records(backup_dir, expected_install_id=INSTALL_ID)
        self.assertEqual([value["sequence"] for _, value in records], [12, 11])
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

    def test_backup_preserves_product_defined_api_errors(self):
        denied = ApiError(403, "FORBIDDEN", "当前账户无权执行备份")
        with mock.patch("v2app.api.system.locked_actor", side_effect=denied):
            response = self.write("POST", "/api/v1/admin/backups")
        self.assertEqual(response.status_code, 403)
        body = response.get_json()
        self.assertEqual(body["error"]["code"], "FORBIDDEN")
        self.assertNotEqual(body["error"]["code"], "BACKUP_FAILED")

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

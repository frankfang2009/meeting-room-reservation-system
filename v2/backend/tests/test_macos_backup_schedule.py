from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, time, timedelta
from unittest import mock

import service
from tests.test_backend import BackendTestCase, INSTALL_ID
from v2app.backup import backup_records, create_backup


class MacBackupScheduleTests(BackendTestCase):
    def setUp(self):
        super().setUp()
        self.setup_system()
        self.login()
        self.identity = {"install_id": INSTALL_ID}
        self.write_install_json(self.data_dir, setup_complete=True)
        (self.data_dir / "install_id").write_text(INSTALL_ID, encoding="utf-8")
        self.backups = self.root / "backups"
        self.app_dir = self.root / "app"
        self.app_dir.mkdir()
        for name in ("backup.py", "restore.py", "service.py", "server.py"):
            shutil.copy2(service.APP_DIR / name, self.app_dir)
        shutil.copytree(
            service.APP_DIR / "v2app",
            self.app_dir / "v2app",
            ignore=shutil.ignore_patterns("__pycache__"),
        )

    def seed_reservation(self):
        payload = self.booking_payload(self.bootstrap()["rooms"][0]["id"])
        response = self.write("POST", "/api/v1/reservations", payload)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["id"]

    def run_clock(self, started, readings):
        event = mock.Mock()
        event.is_set.side_effect = [False] * len(readings) + [True]
        with mock.patch.object(service, "APP_DIR", self.app_dir):
            service._run_macos_backup_schedule(
                self.identity, event, started, now=mock.Mock(side_effect=readings)
            )
        return event

    def restore_and_check(self, reservation_id, expected_count):
        records = backup_records(self.backups, expected_install_id=INSTALL_ID, verify_hash=True)
        self.assertEqual(len(records), expected_count)
        backup, metadata = records[0]
        with closing(sqlite3.connect(self.database)) as live:
            sequence = live.execute(
                "SELECT value FROM app_meta WHERE key='data_sequence'"
            ).fetchone()[0]
        self.assertEqual(metadata["sourceDataSequence"], int(sequence))
        # A blank recovery installation cannot return a row from the live database.
        recovery = self.root / "recovery"
        shutil.copytree(self.app_dir, recovery / "app")
        (recovery / "data").mkdir()
        for name in ("install_id", "install.json"):
            shutil.copy2(self.data_dir / name, recovery / "data")
        shutil.copytree(self.backups, recovery / "backups")
        result = subprocess.run(
            [
                sys.executable,
                str(recovery / "app/restore.py"),
                "--backup",
                str(recovery / "backups" / backup.name),
                "--expected-install-id",
                INSTALL_ID,
            ],
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with closing(sqlite3.connect(recovery / "data/reservation.db")) as db:
            self.assertEqual(
                db.execute(
                    "SELECT case_number, start_time, end_time FROM reservations WHERE id=?",
                    (reservation_id,),
                ).fetchone(),
                ("TEST-2026-001", "09:00", "10:00"),
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM reservation_slots WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                db.execute(
                    "SELECT event_type FROM reservation_events WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchall(),
                [("created",)],
            )

    def check_crossing(self, wake_delay):
        # CLI workers use the real local date. Only the scheduler's wall clock is advanced.
        due = datetime.combine(datetime.now().date(), time(2))
        create_backup(
            self.database,
            self.backups,
            install_id=INSTALL_ID,
            sequence=1,
            now=(due - timedelta(hours=23)).astimezone(),
        )
        booking_id = self.seed_reservation()
        started = due - timedelta(seconds=10)
        wake = due + wake_delay
        with mock.patch.object(
            service, "_launch_backup_worker", wraps=service._launch_backup_worker
        ) as launch:
            self.run_clock(started, [started, wake, wake + timedelta(minutes=1)])
        launch.assert_called_once_with(self.identity, "--scheduled")
        self.restore_and_check(booking_id, 2)

    def test_running_across_two_am_creates_recoverable_backup(self):
        self.check_crossing(timedelta())

    def test_resume_after_two_am_creates_recoverable_backup(self):
        self.check_crossing(timedelta(hours=6))

    def check_startup(self, stale):
        if stale:
            create_backup(
                self.database,
                self.backups,
                install_id=INSTALL_ID,
                sequence=1,
                now=datetime.now().astimezone() - timedelta(days=2),
            )
        booking_id = self.seed_reservation()
        with mock.patch.object(service, "APP_DIR", self.app_dir):
            for _ in range(2):
                worker = service._launch_backup_worker(self.identity, "--catch-up")
                self.assertEqual(worker.wait(timeout=30), 0)
        self.restore_and_check(booking_id, 2 if stale else 1)

    def test_first_backup_is_recoverable(self):
        self.check_startup(stale=False)

    def test_restart_after_two_days_creates_one_recoverable_backup(self):
        self.check_startup(stale=True)

    def test_failure_retries_and_multiple_missed_days_coalesce(self):
        due = datetime(2026, 8, 9, 2)
        wake = due + timedelta(days=3, hours=5)
        worker = mock.Mock()
        worker.wait.side_effect = [1, 0, 0]
        with mock.patch.object(
            service, "_launch_backup_worker", side_effect=[None, worker, worker, worker]
        ) as launch:
            event = self.run_clock(
                due, [wake, wake, wake, wake, wake - timedelta(hours=1), wake + timedelta(days=1)]
            )
        self.assertEqual(launch.call_count, 4)
        self.assertEqual(event.wait.call_args_list.count(mock.call(900)), 2)

    def test_service_owns_one_schedule_across_setup_listener_restart(self):
        record = {"pid": 1234, "token": "test"}
        with (
            mock.patch.object(service, "_claim_pid", return_value=record),
            mock.patch.object(service, "_remove_pid_if_token"),
            mock.patch.object(service, "_edition_is_macos", return_value=True),
            mock.patch.object(service, "_launch_backup_worker") as launch,
            mock.patch.object(service, "run_server_once", side_effect=[True, False]) as listener,
            mock.patch.object(service.threading, "Thread") as thread,
        ):
            service.run_service(self.identity)
        schedules = [
            call.kwargs
            for call in thread.call_args_list
            if call.kwargs["target"] is service._run_macos_backup_schedule
        ]
        self.assertEqual(len(schedules), 1)
        identity, stop, _ = schedules[0]["args"]
        self.assertEqual(identity, self.identity)
        self.assertIs(stop, listener.call_args.kwargs["service_stop_event"])
        self.assertTrue(stop.is_set())
        self.assertEqual(launch.call_args_list, [mock.call(self.identity, "--catch-up")] * 2)

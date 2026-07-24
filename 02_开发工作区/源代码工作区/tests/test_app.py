from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing, redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import app as app_module
import server as server_module
from app import SCHEMA_VERSION, _migrate_schema, create_app, get_db, init_db
from backup import main as backup_database
from werkzeug.security import generate_password_hash


class MeetingRoomSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": str(self.database),
                "SECRET_KEY": "test-secret",
                "INITIAL_ADMIN_PASSWORD": "admin123",
            }
        )
        with self.app.app_context():
            init_db()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def csrf(self, client=None) -> str:
        client = client or self.client
        with client.session_transaction() as session:
            return session["_csrf_token"]

    def login(self, username="admin", password="admin123", client=None):
        client = client or self.client
        client.get("/login")
        return client.post(
            "/login",
            data={
                "_csrf_token": self.csrf(client),
                "username": username,
                "password": password,
            },
            follow_redirects=True,
        )

    def post(self, path, data=None, client=None, follow_redirects=True):
        client = client or self.client
        payload = dict(data or {})
        payload["_csrf_token"] = self.csrf(client)
        return client.post(
            path, data=payload, follow_redirects=follow_redirects
        )

    def add_user(self, username="user1", password="pass123", is_admin=0):
        with self.app.app_context():
            get_db().execute(
                """
                INSERT INTO users (username, password_hash, display_name, is_admin)
                VALUES (?, ?, ?, ?)
                """,
                (username, generate_password_hash(password), username, is_admin),
            )

    def insert_reservation(
        self,
        reserve_date,
        start_time="09:00",
        end_time="09:30",
        user_id=1,
        room_id=1,
    ):
        with self.app.app_context():
            db = get_db()
            room_name = db.execute(
                "SELECT name FROM rooms WHERE id = ?", (room_id,)
            ).fetchone()[0]
            cursor = db.execute(
                """
                INSERT INTO reservations (
                    room_id, room_name_snapshot, reserve_date, start_time,
                    end_time, user_id, party_name, case_number, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    room_id,
                    room_name,
                    reserve_date,
                    start_time,
                    end_time,
                    user_id,
                    "测试单位",
                    "测试案号",
                    "测试备注",
                ),
            )
            db.executemany(
                """
                INSERT INTO reservation_slots
                    (reservation_id, room_id, reserve_date, slot_time)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (cursor.lastrowid, room_id, reserve_date, slot)
                    for slot in app_module._reservation_slot_times(
                        start_time, end_time
                    )
                ],
            )
            return cursor.lastrowid

    def reservation_payload(
        self,
        room_id=1,
        reserve_date=None,
        start_time="09:00",
        end_time="09:30",
    ):
        return {
            "room_id": str(room_id),
            "reserve_date": reserve_date or (date.today() + timedelta(days=1)).isoformat(),
            "start_time": start_time,
            "end_time": end_time,
            "party_name": "测试单位",
            "case_number": "测试案号",
            "notes": "测试备注",
        }

    def test_initial_data_is_seeded_only_once(self):
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0], 3)
            db.execute("UPDATE users SET username = 'renamed' WHERE username = 'admin'")
            db.execute("DELETE FROM rooms")
            init_db()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0], 0)

    def test_new_database_records_current_schema_version(self):
        with self.app.app_context():
            version = get_db().execute(
                "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        self.assertEqual(int(version), SCHEMA_VERSION)

    def test_login_and_admin_authorization(self):
        response = self.login(password="wrong")
        self.assertIn("用户名或密码错误", response.get_data(as_text=True))
        response = self.login()
        self.assertIn("预约日历", response.get_data(as_text=True))

        self.add_user()
        ordinary = self.app.test_client()
        self.login("user1", "pass123", ordinary)
        response = ordinary.get("/admin/users", follow_redirects=True)
        self.assertIn("需要管理员权限", response.get_data(as_text=True))

    def test_healthz_reports_stable_install_identity_and_mode(self):
        first = self.client.get("/healthz")
        second = self.client.get("/healthz")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["X-Meeting-Room-System"], "1")
        payload = first.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "normal")
        self.assertIsNone(payload["lan_url"])
        self.assertEqual(payload["install_id"], second.get_json()["install_id"])
        self.assertEqual(
            payload["install_id"],
            Path(self.app.config["INSTALL_ID_FILE"]).read_text(
                encoding="utf-8"
            ).strip(),
        )
        self.app.config["CURRENT_LAN_URL"] = "http://192.168.1.10:8080"
        self.assertEqual(
            self.client.get("/healthz").get_json()["lan_url"],
            "http://192.168.1.10:8080",
        )

    def test_only_admin_can_see_and_acknowledge_network_change(self):
        state_path = Path(self.app.config["NETWORK_STATE_FILE"])
        app_module._write_network_state(
            state_path,
            {
                "schema": 1,
                "last_acknowledged_url": "http://192.168.1.10:8080",
                "last_observed_url": "http://192.168.1.11:8080",
                "pending": {
                    "kind": "changed",
                    "old_url": "http://192.168.1.10:8080",
                    "new_url": "http://192.168.1.11:8080",
                    "detected_at": "2026-07-24T10:00:00+08:00",
                },
            },
        )
        self.app.config["CURRENT_LAN_URL"] = "http://192.168.1.11:8080"

        admin_html = self.login().get_data(as_text=True)
        self.assertIn("同事访问网址已发生变化", admin_html)
        self.assertIn("预约数据没有变化", admin_html)
        self.assertIn("http://192.168.1.10:8080", admin_html)
        self.assertIn("http://192.168.1.11:8080", admin_html)
        self.assertIn("data-copy-network-url", admin_html)
        self.assertNotIn('class="network-address-current"', admin_html)

        self.add_user()
        ordinary = self.app.test_client()
        ordinary_html = self.login("user1", "pass123", ordinary).get_data(as_text=True)
        self.assertNotIn("同事访问网址已发生变化", ordinary_html)
        denied = self.post(
            "/admin/network-address/acknowledge",
            client=ordinary,
        )
        self.assertIn("需要管理员权限", denied.get_data(as_text=True))
        self.assertIsNotNone(app_module.pending_network_change(state_path))

        acknowledged = self.post(
            "/admin/network-address/acknowledge",
            {"expected_url": "http://192.168.1.11:8080"},
        )
        acknowledged_html = acknowledged.get_data(as_text=True)
        self.assertIn("新网址已确认", acknowledged_html)
        self.assertNotIn("同事访问网址已发生变化", acknowledged_html)
        self.assertIsNone(app_module.pending_network_change(state_path))

    def test_admin_sees_current_address_strip_without_pending_notice(self):
        state_path = Path(self.app.config["NETWORK_STATE_FILE"])
        current_url = "http://192.168.1.10:8080"
        app_module.observe_network_url(state_path, current_url)
        self.app.config["CURRENT_LAN_URL"] = current_url

        admin_html = self.login().get_data(as_text=True)
        self.assertIn('class="network-address-current"', admin_html)
        self.assertIn("同事当前访问", admin_html)
        self.assertIn(current_url, admin_html)
        self.assertIn('data-copy-label="复制网址"', admin_html)
        self.assertNotIn("同事访问网址已发生变化", admin_html)

        self.add_user()
        ordinary = self.app.test_client()
        ordinary_html = self.login(
            "user1",
            "pass123",
            ordinary,
        ).get_data(as_text=True)
        self.assertNotIn('class="network-address-current"', ordinary_html)
        self.assertNotIn("同事当前访问", ordinary_html)
        css_response = self.client.get("/static/app.css")
        css = css_response.get_data(as_text=True)
        css_response.close()
        self.assertIn(
            ".site-header, .network-address-current, .network-address-alert",
            css,
        )

    def test_admin_sees_unknown_hint_instead_of_stale_observed_url(self):
        state_path = Path(self.app.config["NETWORK_STATE_FILE"])
        stale_url = "http://192.168.1.10:8080"
        app_module.observe_network_url(state_path, stale_url)
        self.app.config["CURRENT_LAN_URL"] = None

        html = self.login().get_data(as_text=True)
        self.assertIn('class="network-address-current"', html)
        self.assertIn("暂时无法可靠识别同事访问地址", html)
        self.assertNotIn(stale_url, html)

    def test_admin_context_reads_network_state_only_once(self):
        state_path = Path(self.app.config["NETWORK_STATE_FILE"])
        current_url = "http://192.168.1.10:8080"
        app_module.observe_network_url(state_path, current_url)
        self.app.config["CURRENT_LAN_URL"] = current_url
        self.login()

        with mock.patch.object(
            app_module,
            "_read_network_state",
            wraps=app_module._read_network_state,
        ) as read_state:
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(read_state.call_count, 1)

    def test_in_memory_write_failure_fallback_keeps_admin_warning_visible(self):
        state_path = Path(self.app.config["NETWORK_STATE_FILE"])
        address_a = "http://192.168.1.10:8080"
        address_b = "http://192.168.1.11:8080"
        app_module.observe_network_url(state_path, address_a)
        self.app.config.update(
            CURRENT_LAN_URL=address_b,
            NETWORK_WARNING_FALLBACK={
                "kind": "changed",
                "old_url": address_a,
                "new_url": address_b,
                "detected_at": "2026-07-24T12:00:00+08:00",
            },
            NETWORK_WARNING_PERSIST_FAILED=True,
        )

        html = self.login().get_data(as_text=True)
        self.assertIn("同事访问网址已发生变化", html)
        self.assertIn("本次变化提醒暂时无法保存确认", html)
        self.assertIn("重新尝试保存确认", html)
        self.assertNotIn('class="network-address-current"', html)
        self.assertIsNone(app_module.pending_network_change(state_path))

        with self.assertLogs(level="ERROR"), mock.patch.object(
            app_module,
            "_write_network_state",
            side_effect=OSError("read only"),
        ):
            response = self.post(
                "/admin/network-address/acknowledge",
                {"expected_url": address_b},
            )
        failed_html = response.get_data(as_text=True)
        self.assertIn("暂时无法保存确认", failed_html)
        self.assertIn("同事访问网址已发生变化", failed_html)
        self.assertIn(address_b, failed_html)
        self.assertTrue(
            self.app.config["NETWORK_WARNING_PERSIST_FAILED"]
        )

    def test_in_memory_warning_can_be_confirmed_after_storage_recovers(self):
        state_path = Path(self.app.config["NETWORK_STATE_FILE"])
        address_a = "http://192.168.1.10:8080"
        address_b = "http://192.168.1.11:8080"
        app_module.observe_network_url(state_path, address_a)
        self.app.config.update(
            CURRENT_LAN_URL=address_b,
            NETWORK_WARNING_FALLBACK={
                "kind": "changed",
                "old_url": address_a,
                "new_url": address_b,
                "detected_at": "2026-07-24T12:00:00+08:00",
            },
            NETWORK_WARNING_PERSIST_FAILED=True,
        )
        self.login()

        response = self.post(
            "/admin/network-address/acknowledge",
            {"expected_url": address_b},
        )
        html = response.get_data(as_text=True)
        self.assertIn("新网址已确认", html)
        self.assertNotIn("同事访问网址已发生变化", html)
        self.assertFalse(
            self.app.config["NETWORK_WARNING_PERSIST_FAILED"]
        )
        state = app_module._read_network_state(state_path)
        self.assertEqual(state["last_acknowledged_url"], address_b)
        self.assertIsNone(state["pending"])

    def test_network_change_acknowledgement_requires_csrf(self):
        state_path = Path(self.app.config["NETWORK_STATE_FILE"])
        app_module.observe_network_url(
            state_path,
            "http://192.168.1.10:8080",
            detected_at="2026-07-24T10:00:00+08:00",
        )
        app_module.observe_network_url(
            state_path,
            "http://192.168.1.11:8080",
            detected_at="2026-07-24T10:01:00+08:00",
        )
        self.login()
        response = self.client.post("/admin/network-address/acknowledge")
        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(app_module.pending_network_change(state_path))

    def test_admin_can_review_generic_recovery_warning_without_fake_old_url(self):
        state_path = Path(self.app.config["NETWORK_STATE_FILE"])
        current_url = "http://192.168.1.11:8080"
        state_path.write_text("{damaged", encoding="utf-8")
        result = app_module.observe_network_url(state_path, current_url)
        self.assertEqual(result["pending"]["kind"], "verify")
        self.assertNotIn("old_url", result["pending"])

        html = self.login().get_data(as_text=True)
        self.assertIn("请重新核对同事访问网址", html)
        self.assertIn("无法可靠确认原网址", html)
        self.assertIn(current_url, html)
        self.assertNotIn("原网址</dt>", html)

        acknowledged = self.post(
            "/admin/network-address/acknowledge",
            {"expected_url": current_url},
        )
        self.assertIn("新网址已确认", acknowledged.get_data(as_text=True))
        self.assertIsNone(app_module.pending_network_change(state_path))

    def test_stale_network_acknowledgement_keeps_newest_notice(self):
        state_path = Path(self.app.config["NETWORK_STATE_FILE"])
        address_a = "http://192.168.1.10:8080"
        address_b = "http://192.168.1.11:8080"
        address_c = "http://192.168.1.12:8080"
        app_module.observe_network_url(state_path, address_a)
        app_module.observe_network_url(state_path, address_b)
        self.login()
        app_module.observe_network_url(state_path, address_c)

        response = self.post(
            "/admin/network-address/acknowledge",
            {"expected_url": address_b},
        )
        html = response.get_data(as_text=True)
        self.assertIn("网址刚刚再次变化", html)
        self.assertIn(address_c, html)
        self.assertEqual(
            app_module.pending_network_change(state_path)["new_url"],
            address_c,
        )

    def test_network_copy_script_falls_back_and_reports_failure(self):
        script = self.client.get(
            "/network-address-notice.js"
        ).get_data(as_text=True)
        self.assertIn(".catch(fallbackCopy)", script)
        self.assertIn('document.execCommand("copy")', script)
        self.assertIn("复制失败，请手动复制", script)
        self.assertIn("button.dataset.copyLabel", script)
        self.assertIn("button.textContent = defaultLabel", script)

    def test_exact_half_hour_and_overlap_rules(self):
        self.login()
        target = (date.today() + timedelta(days=2)).isoformat()

        first = self.post(
            "/reserve",
            self.reservation_payload(reserve_date=target),
        )
        self.assertIn("预约成功", first.get_data(as_text=True))

        adjacent = self.post(
            "/reserve",
            self.reservation_payload(
                room_id=1,
                reserve_date=target,
                start_time="09:30",
                end_time="10:00",
            ),
        )
        self.assertIn("预约成功", adjacent.get_data(as_text=True))

        overlap = self.post(
            "/reserve",
            self.reservation_payload(
                room_id=1,
                reserve_date=target,
                start_time="09:00",
                end_time="10:00",
            ),
        )
        self.assertIn("该时段已被预约", overlap.get_data(as_text=True))

        other_room = self.post(
            "/reserve",
            self.reservation_payload(
                room_id=2,
                reserve_date=target,
                start_time="09:00",
                end_time="10:00",
            ),
        )
        self.assertIn("预约成功", other_room.get_data(as_text=True))

    def test_default_end_time_includes_0930(self):
        self.login()
        target = (date.today() + timedelta(days=1)).isoformat()
        response = self.client.get(
            f"/reserve?room_id=1&date={target}&start_time=09:00"
        )
        html = response.get_data(as_text=True)
        self.assertIn('<option value="09:30" selected>09:30</option>', html)

    def test_past_and_started_reservations_are_rejected(self):
        fixed_now = datetime(2026, 7, 24, 9, 27)
        self.app.config["NOW_PROVIDER"] = lambda: fixed_now
        self.login()

        past_response = self.post(
            "/reserve",
            self.reservation_payload(reserve_date="2026-07-23"),
        )
        self.assertIn(
            "不能预约已经开始或过去的时段",
            past_response.get_data(as_text=True),
        )
        started_response = self.post(
            "/reserve",
            self.reservation_payload(reserve_date="2026-07-24"),
        )
        self.assertIn(
            "不能预约已经开始或过去的时段",
            started_response.get_data(as_text=True),
        )
        future_response = self.post(
            "/reserve",
            self.reservation_payload(
                reserve_date="2026-07-24",
                start_time="09:30",
                end_time="10:00",
            ),
        )
        self.assertIn("预约成功", future_response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(
                get_db().execute("SELECT COUNT(*) FROM reservations").fetchone()[0],
                1,
            )

    def test_booking_rechecks_start_time_inside_write_lock(self):
        clock = {"now": datetime(2026, 7, 24, 9, 29)}
        self.app.config["NOW_PROVIDER"] = lambda: clock["now"]
        self.app.config["BEFORE_RESERVATION_WRITE"] = lambda: clock.update(
            now=datetime(2026, 7, 24, 9, 30)
        )
        self.login()
        response = self.post(
            "/reserve",
            self.reservation_payload(
                reserve_date="2026-07-24",
                start_time="09:30",
                end_time="10:00",
            ),
        )
        self.assertIn("该时段已经开始，请重新选择", response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(
                get_db().execute("SELECT COUNT(*) FROM reservations").fetchone()[0],
                0,
            )
            self.assertFalse(get_db().in_transaction)

    def test_calendar_marks_elapsed_slots_and_current_time(self):
        fixed_now = datetime(2026, 7, 24, 9, 27)
        self.app.config["NOW_PROVIDER"] = lambda: fixed_now
        self.login()
        html = self.client.get("/?date=2026-07-24").get_data(as_text=True)

        self.assertIn("星期五", html)
        self.assertIn("current-time-row", html)
        self.assertIn("data-calendar-focus", html)
        self.assertIn(">现在</small>", html)
        self.assertEqual(html.count("expired-slot"), 6)
        self.assertEqual(html.count(">已开始</span>"), 3)
        self.assertEqual(html.count(">已过期</span>"), 3)
        self.assertNotIn(
            "/reserve?room_id=1&amp;date=2026-07-24&amp;start_time=09:00",
            html,
        )
        self.assertIn(
            "/reserve?room_id=1&amp;date=2026-07-24&amp;start_time=09:30",
            html,
        )

        past_html = self.client.get(
            "/?date=2026-07-23"
        ).get_data(as_text=True)
        self.assertNotIn("/reserve?", past_html)
        self.assertNotIn("data-calendar-focus", past_html)

        future_html = self.client.get(
            "/?date=2026-07-25"
        ).get_data(as_text=True)
        self.assertIn("/reserve?", future_html)
        self.assertNotIn("data-calendar-focus", future_html)

    def test_calendar_focuses_business_boundaries_without_false_now_label(self):
        self.app.config["NOW_PROVIDER"] = lambda: datetime(2026, 7, 24, 7, 45)
        self.login()
        before_open = self.client.get("/").get_data(as_text=True)
        self.assertEqual(before_open.count("data-calendar-focus"), 1)
        self.assertNotIn(">现在</small>", before_open)

        self.app.config["NOW_PROVIDER"] = lambda: datetime(2026, 7, 24, 18, 0)
        after_close = self.client.get("/").get_data(as_text=True)
        self.assertEqual(after_close.count("data-calendar-focus"), 1)
        self.assertNotIn(">现在</small>", after_close)
        self.assertNotIn("/reserve?", after_close)

    def test_reservation_form_and_flash_include_comfort_guards(self):
        fixed_now = datetime(2026, 7, 24, 9, 27)
        self.app.config["NOW_PROVIDER"] = lambda: fixed_now
        login_html = self.login().get_data(as_text=True)
        self.assertIn('data-auto-dismiss="4500"', login_html)

        form_html = self.client.get(
            "/reserve?room_id=1&date=2026-07-24&start_time=09:30"
        ).get_data(as_text=True)
        self.assertIn('min="2026-07-24"', form_html)
        self.assertIn('data-today="2026-07-24"', form_html)
        self.assertIn('data-now-minutes="567"', form_html)

        other_client = self.app.test_client()
        danger_html = self.login(
            password="wrong", client=other_client
        ).get_data(as_text=True)
        self.assertIn("flash-danger", danger_html)
        self.assertNotIn("data-auto-dismiss", danger_html)

    def test_cancel_is_post_only_and_releases_slot(self):
        self.login()
        target = (date.today() + timedelta(days=3)).isoformat()
        self.post("/reserve", self.reservation_payload(reserve_date=target))
        with self.app.app_context():
            reservation_id = get_db().execute(
                "SELECT id FROM reservations ORDER BY id DESC"
            ).fetchone()[0]

        self.assertEqual(self.client.get(f"/cancel/{reservation_id}").status_code, 405)
        response = self.post(f"/cancel/{reservation_id}")
        self.assertIn("已取消预约", response.get_data(as_text=True))
        response = self.post(
            "/reserve", self.reservation_payload(reserve_date=target)
        )
        self.assertIn("预约成功", response.get_data(as_text=True))

    def test_all_destructive_get_routes_are_rejected(self):
        self.login()
        for path in (
            "/admin/users/delete/999",
            "/admin/rooms/delete/999",
            "/admin/cancel/999",
        ):
            self.assertEqual(self.client.get(path).status_code, 405, path)

    def test_missing_or_wrong_csrf_is_rejected(self):
        self.client.get("/login")
        self.assertEqual(
            self.client.post(
                "/login", data={"username": "admin", "password": "admin123"}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/login",
                data={
                    "_csrf_token": "wrong",
                    "username": "admin",
                    "password": "admin123",
                },
            ).status_code,
            400,
        )

    def test_disabled_user_session_is_revoked_immediately(self):
        self.add_user()
        user_client = self.app.test_client()
        self.login("user1", "pass123", user_client)
        self.assertEqual(user_client.get("/").status_code, 200)

        self.login()
        with self.app.app_context():
            user_id = get_db().execute(
                "SELECT id FROM users WHERE username = 'user1'"
            ).fetchone()[0]
        self.post(f"/admin/users/delete/{user_id}")

        response = user_client.get("/", follow_redirects=True)
        self.assertIn("请先登录", response.get_data(as_text=True))

    def test_admin_demotion_revokes_old_admin_access(self):
        self.add_user("admin2", "pass123", is_admin=1)
        second_admin = self.app.test_client()
        self.login("admin2", "pass123", second_admin)

        with self.app.app_context():
            admin2_id = get_db().execute(
                "SELECT id FROM users WHERE username = 'admin2'"
            ).fetchone()[0]
        self.login()
        self.post(
            f"/admin/users/edit/{admin2_id}",
            {
                "username": "admin2",
                "display_name": "admin2",
                "is_active": "1",
            },
        )

        response = second_admin.get("/admin/users", follow_redirects=True)
        self.assertIn("请先登录", response.get_data(as_text=True))

    def test_concurrent_booking_creates_only_one_reservation(self):
        target = (date.today() + timedelta(days=4)).isoformat()
        barrier = threading.Barrier(2)
        results = []

        def book() -> None:
            client = self.app.test_client()
            self.login(client=client)
            token = self.csrf(client)
            barrier.wait()
            response = client.post(
                "/reserve",
                data={"_csrf_token": token, **self.reservation_payload(reserve_date=target)},
                follow_redirects=True,
            )
            results.append(response.get_data(as_text=True))

        threads = [threading.Thread(target=book) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertEqual(len(results), 2)
        self.assertEqual(sum("预约成功" in result for result in results), 1)
        self.assertEqual(sum("该时段已被预约" in result for result in results), 1)
        with closing(sqlite3.connect(self.database)) as db:
            count = db.execute(
                """
                SELECT COUNT(*) FROM reservations
                WHERE room_id = 1 AND reserve_date = ? AND status = 'pending'
                """,
                (target,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_room_deletion_keeps_history_visible(self):
        self.login()
        target = (date.today() + timedelta(days=5)).isoformat()
        self.post("/reserve", self.reservation_payload(room_id=3, reserve_date=target))
        with self.app.app_context():
            reservation_id = get_db().execute(
                "SELECT id FROM reservations ORDER BY id DESC"
            ).fetchone()[0]
        self.post(f"/admin/cancel/{reservation_id}")
        self.post("/admin/rooms/delete/3")
        response = self.client.get("/my")
        self.assertIn("笔录室3", response.get_data(as_text=True))

    def test_room_rename_then_delete_keeps_latest_name_in_history(self):
        self.login()
        target = (date.today() + timedelta(days=5)).isoformat()
        self.post("/reserve", self.reservation_payload(room_id=3, reserve_date=target))
        with self.app.app_context():
            reservation_id = get_db().execute(
                "SELECT id FROM reservations ORDER BY id DESC"
            ).fetchone()[0]
        self.post(
            "/admin/rooms/edit/3",
            {"name": "新笔录室", "sort_order": "3", "is_active": "1"},
        )
        self.post(f"/admin/cancel/{reservation_id}")
        self.post("/admin/rooms/delete/3")

        my_html = self.client.get("/my").get_data(as_text=True)
        admin_html = self.client.get(
            f"/admin/reservations?start_date={target}&end_date={target}"
        ).get_data(as_text=True)
        self.assertIn("新笔录室", my_html)
        self.assertIn("新笔录室", admin_html)
        self.assertNotIn("笔录室3", my_html)

    def test_booking_rechecks_room_after_concurrent_deactivation(self):
        self.login()
        booking_client = self.app.test_client()
        self.login(client=booking_client)
        ready = threading.Event()
        continue_booking = threading.Event()
        target = (date.today() + timedelta(days=8)).isoformat()
        results = []

        def pause_before_write() -> None:
            ready.set()
            self.assertTrue(continue_booking.wait(timeout=10))

        self.app.config["BEFORE_RESERVATION_WRITE"] = pause_before_write

        def book() -> None:
            results.append(
                self.post(
                    "/reserve",
                    self.reservation_payload(room_id=2, reserve_date=target),
                    client=booking_client,
                ).get_data(as_text=True)
            )

        thread = threading.Thread(target=book)
        thread.start()
        self.assertTrue(ready.wait(timeout=10))
        self.post(
            "/admin/rooms/edit/2",
            {"name": "笔录室2", "sort_order": "2"},
        )
        continue_booking.set()
        thread.join(timeout=15)
        self.app.config.pop("BEFORE_RESERVATION_WRITE", None)

        self.assertEqual(len(results), 1)
        self.assertIn("会议室状态已改变", results[0])
        with self.app.app_context():
            count = get_db().execute(
                "SELECT COUNT(*) FROM reservations WHERE room_id = 2 AND reserve_date = ?",
                (target,),
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_invalid_reservation_input_returns_friendly_error(self):
        self.login()
        payload = self.reservation_payload()
        payload["start_time"] = "09:17"
        response = self.post("/reserve", payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn("请选择正确的预约时间", response.get_data(as_text=True))

    def test_validation_keeps_fields_out_of_url(self):
        self.login()
        payload = self.reservation_payload()
        payload["end_time"] = payload["start_time"]
        payload["party_name"] = "不应进入网址的单位"
        response = self.post("/reserve", payload, follow_redirects=False)
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("不应进入网址的单位", html)
        self.assertNotIn("不应进入网址的单位", response.request.path)
        self.assertNotIn("不应进入网址的单位", response.request.query_string.decode())

    def test_admin_user_management_full_lifecycle(self):
        self.login()
        response = self.post(
            "/admin/users/add",
            {
                "username": "newuser",
                "display_name": "新用户",
                "password": "oldpass",
            },
        )
        self.assertIn("用户添加成功", response.get_data(as_text=True))
        with self.app.app_context():
            user_id = get_db().execute(
                "SELECT id FROM users WHERE username = 'newuser'"
            ).fetchone()[0]

        response = self.post(
            f"/admin/users/edit/{user_id}",
            {
                "username": "renameduser",
                "display_name": "改名用户",
                "new_password": "newpass",
                "is_admin": "1",
                "is_active": "1",
            },
        )
        self.assertIn("用户信息已更新", response.get_data(as_text=True))

        new_client = self.app.test_client()
        response = self.login("renameduser", "newpass", new_client)
        self.assertIn("管理后台", response.get_data(as_text=True))

        self.post(f"/admin/users/delete/{user_id}")
        disabled_client = self.app.test_client()
        response = self.login("renameduser", "newpass", disabled_client)
        self.assertIn("用户名或密码错误", response.get_data(as_text=True))

        response = self.post(
            f"/admin/users/edit/{user_id}",
            {
                "username": "renameduser",
                "display_name": "改名用户",
                "is_active": "1",
            },
        )
        self.assertIn("用户信息已更新", response.get_data(as_text=True))
        response = self.login("renameduser", "newpass", self.app.test_client())
        self.assertIn("预约日历", response.get_data(as_text=True))

    def test_last_admin_cannot_be_demoted_or_disabled(self):
        self.login()
        demote = self.post(
            "/admin/users/edit/1",
            {
                "username": "admin",
                "display_name": "管理员",
                "is_active": "1",
            },
        )
        self.assertIn("至少需要保留一名启用的管理员", demote.get_data(as_text=True))

        disable = self.post(
            "/admin/users/edit/1",
            {
                "username": "admin",
                "display_name": "管理员",
                "is_admin": "1",
            },
        )
        self.assertIn("不能停用当前登录账号", disable.get_data(as_text=True))
        with self.app.app_context():
            state = get_db().execute(
                "SELECT is_admin, is_active FROM users WHERE id = 1"
            ).fetchone()
        self.assertEqual(tuple(state), (1, 1))

    def test_self_password_change_gives_clear_relogin_flow(self):
        self.login()
        response = self.post(
            "/admin/users/edit/1",
            {
                "username": "admin",
                "display_name": "管理员",
                "new_password": "safer-password",
                "is_admin": "1",
                "is_active": "1",
            },
        )
        html = response.get_data(as_text=True)
        self.assertIn("密码已修改，请使用新密码重新登录", html)
        self.assertIn("请使用单位分配的账号登录", html)
        self.assertFalse((self.database.parent / "首次登录账号密码.txt").exists())
        self.assertIn(
            "预约日历",
            self.login("admin", "safer-password").get_data(as_text=True),
        )

    def test_ordinary_user_cannot_post_admin_mutations(self):
        self.add_user()
        ordinary = self.app.test_client()
        self.login("user1", "pass123", ordinary)
        attempts = (
            ("/admin/users/add", {"username": "bad", "password": "x", "display_name": "x"}),
            ("/admin/users/edit/1", {"username": "hacked", "display_name": "x"}),
            ("/admin/users/delete/1", {}),
            ("/admin/rooms/add", {"name": "badroom", "sort_order": "0"}),
            ("/admin/rooms/edit/1", {"name": "hacked", "sort_order": "0"}),
            ("/admin/rooms/delete/1", {}),
            ("/admin/cancel/999", {}),
        )
        for path, payload in attempts:
            response = self.post(path, payload, client=ordinary)
            self.assertIn("需要管理员权限", response.get_data(as_text=True), path)
        with self.app.app_context():
            db = get_db()
            self.assertIsNone(
                db.execute("SELECT id FROM users WHERE username = 'bad'").fetchone()
            )
            self.assertEqual(
                db.execute("SELECT name FROM rooms WHERE id = 1").fetchone()[0],
                "笔录室1",
            )

    def test_future_reservation_blocks_room_deletion(self):
        self.login()
        target = (date.today() + timedelta(days=9)).isoformat()
        self.post("/reserve", self.reservation_payload(room_id=2, reserve_date=target))
        response = self.post("/admin/rooms/delete/2")
        self.assertIn("有未完成的预约，无法删除", response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNotNone(
                get_db().execute("SELECT id FROM rooms WHERE id = 2").fetchone()
            )

    def test_admin_room_management_full_lifecycle(self):
        self.login()
        response = self.post(
            "/admin/rooms/add", {"name": "测试室", "sort_order": "0"}
        )
        self.assertIn("会议室添加成功", response.get_data(as_text=True))
        with self.app.app_context():
            room_id = get_db().execute(
                "SELECT id FROM rooms WHERE name = '测试室'"
            ).fetchone()[0]

        response = self.post(
            f"/admin/rooms/edit/{room_id}",
            {"name": "改名测试室", "sort_order": "9"},
        )
        self.assertIn("会议室信息已更新", response.get_data(as_text=True))
        index_html = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("改名测试室", index_html)

        self.post(
            f"/admin/rooms/edit/{room_id}",
            {"name": "改名测试室", "sort_order": "9", "is_active": "1"},
        )
        self.assertIn("改名测试室", self.client.get("/").get_data(as_text=True))
        response = self.post(f"/admin/rooms/delete/{room_id}")
        self.assertIn("会议室已删除", response.get_data(as_text=True))

    def test_admin_reservation_filter_and_cancel(self):
        self.login()
        target = (date.today() + timedelta(days=6)).isoformat()
        self.post("/reserve", self.reservation_payload(reserve_date=target))
        with self.app.app_context():
            reservation_id = get_db().execute(
                "SELECT id FROM reservations ORDER BY id DESC"
            ).fetchone()[0]

        response = self.client.get(
            f"/admin/reservations?start_date={target}&end_date={target}"
        )
        html = response.get_data(as_text=True)
        self.assertIn("测试单位", html)
        self.assertIn("共 1 条记录", html)

        response = self.post(
            f"/admin/cancel/{reservation_id}",
            {"start_date": target, "end_date": target},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"start_date={target}", response.headers["Location"])
        self.assertIn(f"end_date={target}", response.headers["Location"])
        follow = self.client.get(response.headers["Location"])
        self.assertIn("预约已取消", follow.get_data(as_text=True))
        with self.app.app_context():
            status = get_db().execute(
                "SELECT status FROM reservations WHERE id = ?", (reservation_id,)
            ).fetchone()[0]
        self.assertEqual(status, "cancelled")

    def test_admin_and_user_pages_agree_on_completed_status(self):
        self.login()
        past = (date.today() - timedelta(days=1)).isoformat()
        self.insert_reservation(past)
        self.assertIn("已完成", self.client.get("/my").get_data(as_text=True))
        admin_html = self.client.get(
            f"/admin/reservations?start_date={past}&end_date={past}"
        ).get_data(as_text=True)
        self.assertIn("已完成", admin_html)
        self.assertNotIn(">取消</button>", admin_html)
        with self.app.app_context():
            reservation_id = get_db().execute(
                "SELECT id FROM reservations ORDER BY id DESC"
            ).fetchone()[0]
        rejected = self.post(f"/admin/cancel/{reservation_id}")
        self.assertIn("预约已结束、已取消或不存在", rejected.get_data(as_text=True))
        with self.app.app_context():
            status = get_db().execute(
                "SELECT status FROM reservations WHERE id = ?", (reservation_id,)
            ).fetchone()[0]
        self.assertEqual(status, "pending")

    def test_reservation_is_completed_at_exact_end_time(self):
        fixed_now = datetime(2026, 7, 24, 9, 30)
        self.app.config["NOW_PROVIDER"] = lambda: fixed_now
        self.login()
        reservation_id = self.insert_reservation(
            "2026-07-24", start_time="09:00", end_time="09:30"
        )

        self.assertIn("已完成", self.client.get("/my").get_data(as_text=True))
        admin_html = self.client.get(
            "/admin/reservations?start_date=2026-07-24&end_date=2026-07-24"
        ).get_data(as_text=True)
        self.assertIn("已完成", admin_html)
        self.assertNotIn(">取消</button>", admin_html)
        response = self.post(f"/admin/cancel/{reservation_id}")
        self.assertIn(
            "预约已结束、已取消或不存在",
            response.get_data(as_text=True),
        )

    def test_extreme_calendar_dates_do_not_crash(self):
        self.login()
        for value in ("0001-01-01", "9999-12-31"):
            response = self.client.get(f"/?date={value}")
            self.assertEqual(response.status_code, 200, value)

    def test_production_initial_password_is_generated_and_usable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "generated.db"
            generated_app = create_app(
                {
                    "TESTING": True,
                    "DATABASE": str(database),
                    "SECRET_KEY": "generated-secret",
                    "INITIAL_ADMIN_PASSWORD": None,
                }
            )
            with generated_app.app_context():
                init_db()
            credential_file = Path(temp_dir) / "首次登录账号密码.txt"
            self.assertTrue(credential_file.exists())
            password_line = next(
                line
                for line in credential_file.read_text(encoding="utf-8").splitlines()
                if line.startswith("密码：")
            )
            password = password_line.split("：", 1)[1]
            generated_client = generated_app.test_client()
            response = self.login("admin", password, generated_client)
            self.assertIn("预约日历", response.get_data(as_text=True))

    def test_live_backup_is_single_file_and_self_contained(self):
        self.login()
        self.post("/reserve", self.reservation_payload())
        backup_dir = self.database.parent / "backups"
        self.assertEqual(backup_database(self.database, backup_dir), 0)
        backups = list(backup_dir.glob("reservation_*.db"))
        self.assertEqual(len(backups), 1)
        self.assertFalse(list(backup_dir.glob("*.db-wal")))
        self.assertFalse(list(backup_dir.glob("*.db-shm")))
        with closing(sqlite3.connect(backups[0])) as backup_db:
            self.assertEqual(
                backup_db.execute("PRAGMA journal_mode").fetchone()[0], "delete"
            )
            self.assertEqual(
                backup_db.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
            self.assertEqual(
                backup_db.execute("SELECT COUNT(*) FROM reservations").fetchone()[0],
                1,
            )

    def test_user_content_is_html_escaped(self):
        self.login()
        target = (date.today() + timedelta(days=7)).isoformat()
        payload = self.reservation_payload(reserve_date=target)
        payload["party_name"] = "<script>alert(1)</script>"
        self.post("/reserve", payload)
        html = self.client.get(f"/?date={target}").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_local_assets_and_security_headers(self):
        response = self.client.get("/login")
        html = response.get_data(as_text=True)
        self.assertNotIn("cdn.jsdelivr", html)
        self.assertNotIn("https://", html)
        self.assertIn(
            f"/static/app.css?v={app_module.STATIC_REVISION}", html
        )
        self.assertIn(
            f"/static/app.js?v={app_module.STATIC_REVISION}", html
        )
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])


class NetworkStateTests(unittest.TestCase):
    class FakeHealthResponse:
        def __init__(self, payload, system_header="1"):
            self.headers = {"X-Meeting-Room-System": system_header}
            self.body = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.body

    def test_install_id_is_created_once_and_strictly_validated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "install_id"
            install_id = app_module._load_or_create_install_id(path)
            self.assertEqual(install_id, app_module._load_or_create_install_id(path))
            self.assertEqual(install_id, str(app_module.uuid.UUID(install_id)))
            self.assertEqual(path.read_text(encoding="utf-8"), install_id + "\n")

            path.write_text(install_id.upper() + "\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "格式无效"):
                app_module._load_or_create_install_id(path)

            path.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "安装标识为空"):
                app_module._load_or_create_install_id(path)

            path.write_text("not-an-install-id", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "安装标识已损坏"):
                app_module._load_or_create_install_id(path)

    def test_install_identity_accepts_only_canonical_rfc4122_uuid4(self):
        valid_uuid4 = "168efe19-ff50-40fb-bd86-ecc7995bf11f"
        invalid_values = (
            "00000000-0000-0000-0000-000000000000",
            "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            "1ec9414c-232a-6b00-b3c8-9e6bdeced846",
            "168efe19-ff50-40fb-3d86-ecc7995bf11f",
            valid_uuid4.upper(),
        )
        self.assertEqual(
            app_module._canonical_uuid4(valid_uuid4),
            valid_uuid4,
        )
        self.assertEqual(
            server_module._canonical_uuid4(valid_uuid4),
            valid_uuid4,
        )
        for value in invalid_values:
            with self.subTest(value=value):
                self.assertIsNone(app_module._canonical_uuid4(value))
                self.assertIsNone(server_module._canonical_uuid4(value))

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            install_path = data_dir / "install_id"
            for value in invalid_values:
                with self.subTest(file_value=value):
                    install_path.write_text(value + "\n", encoding="utf-8")
                    with self.assertRaises(RuntimeError):
                        app_module._load_or_create_install_id(install_path)
                    with mock.patch.dict(
                        os.environ,
                        {"MEETING_ROOM_DATA_DIR": str(data_dir)},
                    ):
                        self.assertEqual(
                            server_module._read_local_install_id(),
                            (None, "invalid"),
                        )

    def test_health_probe_rejects_non_uuid4_remote_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            local_id = "168efe19-ff50-40fb-bd86-ecc7995bf11f"
            (data_dir / "install_id").write_text(
                local_id + "\n",
                encoding="utf-8",
            )
            response = self.FakeHealthResponse(
                {
                    "ok": True,
                    "install_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "mode": "normal",
                }
            )
            with mock.patch.dict(
                os.environ,
                {"MEETING_ROOM_DATA_DIR": str(data_dir)},
            ), mock.patch.object(
                server_module.urllib.request,
                "urlopen",
                return_value=response,
            ):
                probe = server_module._probe_app(8080)
            self.assertEqual(probe["kind"], "meeting-room-unverified")
            self.assertIsNone(probe["remote_install_id"])

    def test_first_run_identity_creation_reuses_concurrent_winner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_path = root / "install_id"
            winning_install_id = "168efe19-ff50-40fb-bd86-ecc7995bf11f"

            def install_race(path, _value):
                path.write_text(winning_install_id + "\n", encoding="utf-8")
                raise FileExistsError

            with mock.patch.object(
                app_module,
                "_write_text_exclusive",
                side_effect=install_race,
            ):
                self.assertEqual(
                    app_module._load_or_create_install_id(install_path),
                    winning_install_id,
                )

            secret_path = root / ".secret_key"
            winning_secret = "a" * 64

            def secret_race(path, _value):
                path.write_text(winning_secret, encoding="utf-8")
                raise FileExistsError

            with mock.patch.object(
                app_module,
                "_write_text_exclusive",
                side_effect=secret_race,
            ):
                self.assertEqual(
                    app_module._load_or_create_secret(secret_path),
                    winning_secret,
                )

    def test_identity_race_waits_for_winner_to_finish_writing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_path = root / "install_id"
            winning_install_id = "168efe19-ff50-40fb-bd86-ecc7995bf11f"
            writer_done = threading.Event()

            def incomplete_install_race(path, _value):
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                os.close(descriptor)

                def finish_write():
                    app_module.time.sleep(0.06)
                    path.write_text(
                        winning_install_id + "\n",
                        encoding="utf-8",
                    )
                    writer_done.set()

                threading.Thread(target=finish_write, daemon=True).start()
                raise FileExistsError

            with mock.patch.object(
                app_module,
                "_write_text_exclusive",
                side_effect=incomplete_install_race,
            ):
                self.assertEqual(
                    app_module._load_or_create_install_id(install_path),
                    winning_install_id,
                )
            self.assertTrue(writer_done.wait(1))

            secret_path = root / ".secret_key"
            winning_secret = "b" * 64
            secret_writer_done = threading.Event()

            def incomplete_secret_race(path, _value):
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                os.close(descriptor)

                def finish_write():
                    app_module.time.sleep(0.06)
                    path.write_text(winning_secret, encoding="utf-8")
                    secret_writer_done.set()

                threading.Thread(target=finish_write, daemon=True).start()
                raise FileExistsError

            with mock.patch.object(
                app_module,
                "_write_text_exclusive",
                side_effect=incomplete_secret_race,
            ):
                self.assertEqual(
                    app_module._load_or_create_secret(secret_path),
                    winning_secret,
                )
            self.assertTrue(secret_writer_done.wait(1))

    def test_first_run_identity_files_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "identity"
            path.write_text("existing", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                app_module._write_text_exclusive(path, "replacement")
            self.assertEqual(path.read_text(encoding="utf-8"), "existing")

            empty_secret = root / ".secret_key"
            empty_secret.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "会话密钥为空"):
                app_module._load_or_create_secret(empty_secret)

    def test_custom_data_dir_owns_database_install_id_and_network_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "custom-data"
            custom_app = create_app(
                {
                    "DATA_DIR": str(data_dir),
                    "SECRET_KEY": "custom-secret",
                    "INITIAL_ADMIN_PASSWORD": "test-password",
                }
            )
            self.assertEqual(
                Path(custom_app.config["DATABASE"]),
                data_dir / "reservation.db",
            )
            self.assertEqual(
                Path(custom_app.config["INSTALL_ID_FILE"]),
                data_dir / "install_id",
            )
            self.assertEqual(
                Path(custom_app.config["NETWORK_STATE_FILE"]),
                data_dir / app_module.NETWORK_STATE_FILENAME,
            )
            self.assertTrue((data_dir / "install_id").is_file())

    def test_healthz_uses_upgrade_check_mode_from_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {"MEETING_ROOM_UPGRADE_CHECK": "1"},
        ):
            custom_app = create_app(
                {
                    "DATA_DIR": temp_dir,
                    "SECRET_KEY": "upgrade-secret",
                    "INITIAL_ADMIN_PASSWORD": "test-password",
                    "TESTING": True,
                    "CURRENT_LAN_URL": "http://192.168.1.10:8080",
                }
            )
            payload = custom_app.test_client().get("/healthz").get_json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["mode"], "upgrade-check")
            self.assertIsNone(payload["lan_url"])
            self.assertEqual(
                payload["install_id"],
                (Path(temp_dir) / "install_id").read_text(
                    encoding="utf-8"
                ).strip(),
            )

    def test_upgrade_check_mode_uses_loopback_only_binding(self):
        with mock.patch.dict(
            os.environ,
            {"MEETING_ROOM_UPGRADE_CHECK": "1"},
        ):
            self.assertEqual(server_module._bind_host(), "127.0.0.1")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(server_module._bind_host(), "0.0.0.0")

    def test_running_check_requires_exact_local_identity_and_normal_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            local_id = "168efe19-ff50-40fb-bd86-ecc7995bf11f"
            (data_dir / "install_id").write_text(
                local_id + "\n",
                encoding="utf-8",
            )
            normal = self.FakeHealthResponse(
                {
                    "ok": True,
                    "install_id": local_id,
                    "mode": "normal",
                }
            )
            with mock.patch.dict(
                os.environ,
                {"MEETING_ROOM_DATA_DIR": str(data_dir)},
            ), mock.patch.object(
                server_module.urllib.request,
                "urlopen",
                return_value=normal,
            ):
                self.assertTrue(server_module._app_is_running(8080))
                self.assertEqual(
                    server_module._probe_app(8080)["kind"],
                    "same-installation",
                )

            different = self.FakeHealthResponse(
                {
                    "ok": True,
                    "install_id": "7721a579-7c2d-41ff-a3ca-8b62176b1e23",
                    "mode": "normal",
                }
            )
            with mock.patch.dict(
                os.environ,
                {"MEETING_ROOM_DATA_DIR": str(data_dir)},
            ), mock.patch.object(
                server_module.urllib.request,
                "urlopen",
                return_value=different,
            ):
                self.assertFalse(server_module._app_is_running(8080))
                self.assertEqual(
                    server_module._probe_app(8080)["kind"],
                    "other-installation",
                )

            upgrade_check = self.FakeHealthResponse(
                {
                    "ok": True,
                    "install_id": local_id,
                    "mode": "upgrade-check",
                }
            )
            with mock.patch.dict(
                os.environ,
                {"MEETING_ROOM_DATA_DIR": str(data_dir)},
            ), mock.patch.object(
                server_module.urllib.request,
                "urlopen",
                return_value=upgrade_check,
            ):
                self.assertFalse(server_module._app_is_running(8080))
                self.assertEqual(
                    server_module._probe_app(8080)["kind"],
                    "upgrade-check",
                )

    def test_running_check_refuses_matching_service_without_local_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            remote_id = "168efe19-ff50-40fb-bd86-ecc7995bf11f"
            response = self.FakeHealthResponse(
                {
                    "ok": True,
                    "install_id": remote_id,
                    "mode": "normal",
                }
            )
            with mock.patch.dict(
                os.environ,
                {"MEETING_ROOM_DATA_DIR": temp_dir},
            ), mock.patch.object(
                server_module.urllib.request,
                "urlopen",
                return_value=response,
            ):
                probe = server_module._probe_app(8080)
                self.assertFalse(server_module._app_is_running(8080))
            self.assertEqual(probe["kind"], "local-identity-problem")
            self.assertFalse((Path(temp_dir) / "install_id").exists())

    def test_main_never_opens_browser_for_another_installation(self):
        probe = {
            "kind": "other-installation",
            "local_identity_state": "ok",
            "local_install_id": "168efe19-ff50-40fb-bd86-ecc7995bf11f",
            "remote_install_id": "7721a579-7c2d-41ff-a3ca-8b62176b1e23",
            "remote_mode": "normal",
        }
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["server.py"]), mock.patch.object(
            server_module, "_configure_logging"
        ), mock.patch.object(
            server_module, "_probe_app", return_value=probe
        ), mock.patch.object(
            server_module, "_open_browser"
        ) as open_browser, redirect_stdout(output):
            result = server_module.main()
        self.assertEqual(result, 1)
        open_browser.assert_not_called()
        self.assertIn("另一套会议室预约系统", output.getvalue())
        self.assertIn("不会连接或打开", output.getvalue())

    def test_main_stops_clearly_for_corrupt_local_install_identity(self):
        probe = {
            "kind": "none",
            "local_identity_state": "invalid",
            "local_install_id": None,
            "remote_install_id": None,
            "remote_mode": None,
        }
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["server.py"]), mock.patch.object(
            server_module, "_configure_logging"
        ), mock.patch.object(
            server_module, "_probe_app", return_value=probe
        ), redirect_stdout(output):
            result = server_module.main()
        self.assertEqual(result, 1)
        self.assertIn("install_id 已损坏", output.getvalue())
        self.assertIn("不会自动更换", output.getvalue())

    def test_windows_ip_selection_excludes_vpn_and_prefers_physical_lan(self):
        output = """
以太网适配器 Ethernet 2:
   描述. . . . . . . . . . . . . . . : TAP-Windows Adapter V9
   IPv4 地址 . . . . . . . . . . . . : 10.8.0.2

无线局域网适配器 WLAN:
   IPv4 地址 . . . . . . . . . . . . : 192.168.1.20
"""
        candidates = server_module._extract_ip_candidates("win32", output)
        self.assertEqual(candidates, [("192.168.1.20", 2)])
        self.assertEqual(
            server_module._select_local_ip(
                "10.8.0.2",
                candidates,
                "192.168.1.20",
            ),
            "192.168.1.20",
        )

    def test_ip_selection_is_conservative_for_ambiguous_physical_adapters(self):
        output = """
以太网适配器 Ethernet:
   IPv4 Address. . . . . . . . . . . : 192.168.10.20

无线局域网适配器 WLAN:
   IPv4 Address. . . . . . . . . . . : 192.168.20.20
"""
        candidates = server_module._extract_ip_candidates("win32", output)
        self.assertEqual(
            server_module._select_local_ip(
                "10.8.0.2",
                candidates,
                "192.168.10.20",
            ),
            "本机IP",
        )
        self.assertEqual(
            server_module._select_local_ip(
                "192.168.20.20",
                candidates,
                "192.168.10.20",
            ),
            "192.168.20.20",
        )
        self.assertEqual(
            server_module._select_local_ip("10.8.0.2", [], ""),
            "本机IP",
        )

    def test_ip_selection_prefers_route_when_it_is_a_physical_candidate(self):
        self.assertEqual(
            server_module._select_local_ip(
                "10.0.0.3",
                [
                    ("10.0.0.3", 2),
                    ("172.20.0.3", 2),
                    ("192.168.50.3", 2),
                ],
                "10.0.0.3",
            ),
            "10.0.0.3",
        )
        self.assertEqual(
            server_module._select_local_ip(
                "10.8.0.2",
                [
                    ("172.20.0.3", 2),
                    ("192.168.50.3", 2),
                ],
                "192.168.50.3",
            ),
            "本机IP",
        )

    def test_first_observation_without_history_establishes_quiet_baseline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / app_module.NETWORK_STATE_FILENAME
            result = app_module.observe_network_url(
                state_path,
                "http://192.168.1.10:8080",
                log_dir=Path(temp_dir) / "logs",
                detected_at="2026-07-24T10:00:00+08:00",
            )
            state = app_module._read_network_state(state_path)
            self.assertIsNone(result["pending"])
            self.assertEqual(
                state["last_acknowledged_url"],
                "http://192.168.1.10:8080",
            )
            self.assertEqual(
                state["last_observed_url"],
                "http://192.168.1.10:8080",
            )

    def test_missing_state_uses_latest_log_without_old_history_false_alarm(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log_dir = root / "logs"
            log_dir.mkdir()
            (log_dir / "server.log").write_text(
                "2026-07-23 INFO root: 会议室预约系统启动，"
                "本机地址=http://127.0.0.1:8080，"
                "局域网地址=http://192.168.1.10:8080\n"
                "2026-07-24 INFO root: 会议室预约系统启动，"
                "本机地址=http://127.0.0.1:8080，"
                "局域网地址=http://192.168.1.11:8080\n",
                encoding="utf-8",
            )
            state_path = root / app_module.NETWORK_STATE_FILENAME
            result = app_module.observe_network_url(
                state_path,
                "http://192.168.1.11:8080",
                log_dir=log_dir,
                detected_at="2026-07-24T10:00:00+08:00",
            )
            self.assertIsNone(result["pending"])

    def test_missing_state_uses_latest_different_log_as_real_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log_dir = root / "logs"
            log_dir.mkdir()
            (log_dir / "server.log").write_text(
                "局域网地址=http://192.168.1.10:8080\n",
                encoding="utf-8",
            )
            result = app_module.observe_network_url(
                root / app_module.NETWORK_STATE_FILENAME,
                "http://192.168.1.11:8080",
                log_dir=log_dir,
                detected_at="2026-07-24T10:00:00+08:00",
            )
            self.assertEqual(
                result["pending"],
                {
                    "kind": "changed",
                    "old_url": "http://192.168.1.10:8080",
                    "new_url": "http://192.168.1.11:8080",
                    "detected_at": "2026-07-24T10:00:00+08:00",
                },
            )

    def test_pending_notice_persists_and_collapses_a_to_c(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / app_module.NETWORK_STATE_FILENAME
            app_module.observe_network_url(
                state_path,
                "http://192.168.1.10:8080",
                detected_at="2026-07-24T10:00:00+08:00",
            )
            app_module.observe_network_url(
                state_path,
                "http://192.168.1.11:8080",
                detected_at="2026-07-24T10:01:00+08:00",
            )
            repeated = app_module.observe_network_url(
                state_path,
                "http://192.168.1.11:8080",
                detected_at="2026-07-24T10:02:00+08:00",
            )
            self.assertEqual(
                repeated["pending"]["detected_at"],
                "2026-07-24T10:01:00+08:00",
            )

            changed_again = app_module.observe_network_url(
                state_path,
                "http://192.168.1.12:9090",
                detected_at="2026-07-24T10:03:00+08:00",
            )
            self.assertEqual(
                changed_again["pending"],
                {
                    "kind": "changed",
                    "old_url": "http://192.168.1.10:8080",
                    "new_url": "http://192.168.1.12:9090",
                    "detected_at": "2026-07-24T10:03:00+08:00",
                },
            )
            self.assertEqual(
                app_module.pending_network_change(state_path),
                changed_again["pending"],
            )

    def test_observe_and_stale_acknowledgement_are_one_locked_transaction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / app_module.NETWORK_STATE_FILENAME
            address_a = "http://192.168.1.10:8080"
            address_b = "http://192.168.1.11:8080"
            address_c = "http://192.168.1.12:8080"
            app_module.observe_network_url(state_path, address_a)
            app_module.observe_network_url(state_path, address_b)

            original_write = app_module._write_network_state
            observer_inside_write = threading.Event()
            release_observer = threading.Event()
            acknowledgement_finished = threading.Event()
            result = {}

            def controlled_write(path, state):
                if threading.current_thread().name == "network-observer":
                    observer_inside_write.set()
                    self.assertTrue(release_observer.wait(2))
                return original_write(path, state)

            def observe():
                result["observe"] = app_module.observe_network_url(
                    state_path,
                    address_c,
                )

            def acknowledge():
                result["ack"] = app_module.acknowledge_network_change(
                    state_path,
                    expected_url=address_b,
                )
                acknowledgement_finished.set()

            with mock.patch.object(
                app_module,
                "_write_network_state",
                side_effect=controlled_write,
            ):
                observer = threading.Thread(
                    target=observe,
                    name="network-observer",
                )
                observer.start()
                self.assertTrue(observer_inside_write.wait(2))
                acknowledger = threading.Thread(
                    target=acknowledge,
                    name="network-acknowledger",
                )
                acknowledger.start()
                self.assertFalse(acknowledgement_finished.wait(0.05))
                release_observer.set()
                observer.join(2)
                acknowledger.join(2)

            self.assertFalse(observer.is_alive())
            self.assertFalse(acknowledger.is_alive())
            self.assertTrue(result["observe"]["updated"])
            self.assertFalse(result["ack"])
            self.assertEqual(
                app_module.pending_network_change(state_path),
                {
                    "kind": "changed",
                    "old_url": address_a,
                    "new_url": address_c,
                    "detected_at": result["observe"]["pending"]["detected_at"],
                },
            )

    def test_port_change_is_a_real_address_change_and_revert_clears_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / app_module.NETWORK_STATE_FILENAME
            app_module.observe_network_url(
                state_path, "http://10.0.0.8:8080"
            )
            changed = app_module.observe_network_url(
                state_path, "http://10.0.0.8:8081"
            )
            self.assertIsNotNone(changed["pending"])
            reverted = app_module.observe_network_url(
                state_path, "http://10.0.0.8:8080"
            )
            self.assertIsNone(reverted["pending"])

    def test_invalid_current_url_never_overwrites_valid_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / app_module.NETWORK_STATE_FILENAME
            app_module.observe_network_url(
                state_path, "http://192.168.1.10:8080"
            )
            original = state_path.read_bytes()
            for invalid in (
                "http://本机IP:8080",
                "http://127.0.0.1:8080",
                "http://198.18.0.2:8080",
                "http://192.168.1.10:8080/<script>",
            ):
                result = app_module.observe_network_url(state_path, invalid)
                self.assertEqual(result["error"], "invalid-current-url")
                self.assertEqual(state_path.read_bytes(), original)

    def test_corrupt_or_oversized_state_is_recovered_without_blocking(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / app_module.NETWORK_STATE_FILENAME
            state_path.write_text("{bad json", encoding="utf-8")
            recovered = app_module.observe_network_url(
                state_path, "http://192.168.1.10:8080"
            )
            self.assertEqual(recovered["pending"]["kind"], "verify")
            self.assertNotIn("old_url", recovered["pending"])
            self.assertEqual(
                app_module._read_network_state(state_path)["pending"],
                recovered["pending"],
            )
            repeated = app_module.observe_network_url(
                state_path,
                "http://192.168.1.10:8080",
                detected_at="2026-07-24T11:00:00+08:00",
            )
            self.assertEqual(repeated["pending"], recovered["pending"])

            state_path.write_bytes(
                b"x" * (app_module.NETWORK_STATE_MAX_BYTES + 1)
            )
            recovered = app_module.observe_network_url(
                state_path, "http://192.168.1.11:8080"
            )
            self.assertEqual(recovered["pending"]["kind"], "verify")
            self.assertEqual(
                app_module._read_network_state(state_path)[
                    "last_acknowledged_url"
                ],
                "http://192.168.1.11:8080",
            )

    def test_corrupt_state_recovers_last_different_logged_address(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            log_dir = root / "logs"
            log_dir.mkdir()
            (log_dir / "server.log").write_text(
                "局域网地址=http://192.168.1.10:8080\n"
                "局域网地址=http://192.168.1.11:8080\n",
                encoding="utf-8",
            )
            state_path = root / app_module.NETWORK_STATE_FILENAME
            state_path.write_text("{damaged", encoding="utf-8")
            result = app_module.observe_network_url(
                state_path,
                "http://192.168.1.11:8080",
                log_dir=log_dir,
                detected_at="2026-07-24T10:00:00+08:00",
            )
            self.assertEqual(
                result["pending"],
                {
                    "kind": "changed",
                    "old_url": "http://192.168.1.10:8080",
                    "new_url": "http://192.168.1.11:8080",
                    "detected_at": "2026-07-24T10:00:00+08:00",
                },
            )

    def test_verify_warning_becomes_known_change_if_address_moves_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / app_module.NETWORK_STATE_FILENAME
            address_b = "http://192.168.1.11:8080"
            address_c = "http://192.168.1.12:8080"
            state_path.write_text("{damaged", encoding="utf-8")
            verify = app_module.observe_network_url(state_path, address_b)
            self.assertEqual(verify["pending"]["kind"], "verify")
            changed = app_module.observe_network_url(
                state_path,
                address_c,
                detected_at="2026-07-24T12:00:00+08:00",
            )
            self.assertEqual(
                changed["pending"],
                {
                    "kind": "changed",
                    "old_url": address_b,
                    "new_url": address_c,
                    "detected_at": "2026-07-24T12:00:00+08:00",
                },
            )

    def test_network_state_write_failure_does_not_block_startup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / app_module.NETWORK_STATE_FILENAME
            app_module.observe_network_url(
                state_path, "http://192.168.1.10:8080"
            )
            original = state_path.read_bytes()
            with mock.patch.object(
                app_module,
                "_atomic_write_text",
                side_effect=OSError("read only"),
            ):
                result = app_module.observe_network_url(
                    state_path, "http://192.168.1.11:8080"
                )
            self.assertEqual(result["error"], "write-failed")
            self.assertEqual(state_path.read_bytes(), original)

    def test_check_mode_does_not_create_runtime_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "must-not-exist"
            environment = dict(os.environ)
            environment.update(
                {
                    "MEETING_ROOM_DATA_DIR": str(data_dir),
                    "MEETING_ROOM_PORT": "65534",
                }
            )
            result = subprocess.run(
                [sys.executable, str(Path(server_module.__file__)), "--check"],
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            )
            self.assertEqual(result.returncode, 1)
            self.assertFalse(data_dir.exists())


class DatabaseMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "migration.db"
        self.db = sqlite3.connect(self.database, isolation_level=None)
        self.db.execute(
            "CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_v100_database_gets_baseline_version_without_changing_data(self):
        self.db.execute("CREATE TABLE legacy_data (value TEXT NOT NULL)")
        self.db.execute("INSERT INTO legacy_data (value) VALUES ('保留内容')")

        _migrate_schema(self.db)

        self.assertEqual(
            self.db.execute(
                "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).fetchone()[0],
            "1",
        )
        self.assertEqual(
            self.db.execute("SELECT value FROM legacy_data").fetchone()[0],
            "保留内容",
        )

    def test_multiple_migrations_run_in_order_and_only_once(self):
        self.db.execute("CREATE TABLE migration_events (target INTEGER NOT NULL)")
        calls = []

        def migrate_to_2(db):
            calls.append(2)
            db.execute("INSERT INTO migration_events (target) VALUES (2)")

        def migrate_to_3(db):
            calls.append(3)
            db.execute("INSERT INTO migration_events (target) VALUES (3)")

        with mock.patch.object(app_module, "SCHEMA_VERSION", 3), mock.patch.object(
            app_module,
            "MIGRATIONS",
            [(2, migrate_to_2), (3, migrate_to_3)],
        ):
            _migrate_schema(self.db)
            _migrate_schema(self.db)

        self.assertEqual(calls, [2, 3])
        self.assertEqual(
            [row[0] for row in self.db.execute(
                "SELECT target FROM migration_events ORDER BY rowid"
            )],
            [2, 3],
        )
        self.assertEqual(
            self.db.execute(
                "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).fetchone()[0],
            "3",
        )

    def test_failed_migration_rolls_back_data_and_version_together(self):
        self.db.execute("CREATE TABLE migration_events (value TEXT NOT NULL)")
        self.db.execute(
            "INSERT INTO app_meta (key, value) VALUES ('schema_version', '1')"
        )

        def failing_migration(db):
            db.execute("INSERT INTO migration_events (value) VALUES ('不应保留')")
            raise RuntimeError("模拟迁移失败")

        with mock.patch.object(app_module, "SCHEMA_VERSION", 2), mock.patch.object(
            app_module, "MIGRATIONS", [(2, failing_migration)]
        ):
            with self.assertRaisesRegex(RuntimeError, "模拟迁移失败"):
                _migrate_schema(self.db)

        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM migration_events").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).fetchone()[0],
            "1",
        )

    def test_invalid_migration_registries_are_rejected(self):
        noop = lambda _db: None
        invalid_registries = (
            (3, [(2, noop), (2, noop)], "duplicate"),
            (4, [(2, noop), (4, noop)], "gap"),
            (3, [(3, noop), (2, noop)], "out_of_order"),
            (3, [(2, noop)], "highest_mismatch"),
            (1, [(2, noop)], "registered_above_schema_version"),
        )

        for schema_version, migrations, label in invalid_registries:
            with self.subTest(label=label), mock.patch.object(
                app_module, "SCHEMA_VERSION", schema_version
            ), mock.patch.object(app_module, "MIGRATIONS", migrations):
                with self.assertRaisesRegex(RuntimeError, "连续、唯一且严格递增"):
                    _migrate_schema(self.db)

        self.assertIsNone(
            self.db.execute(
                "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).fetchone()
        )

    def test_database_newer_than_program_is_rejected(self):
        self.db.execute(
            "INSERT INTO app_meta (key, value) VALUES ('schema_version', '2')"
        )
        with self.assertRaisesRegex(RuntimeError, "高于当前程序版本"):
            _migrate_schema(self.db)


class MigrateCheckCommandTests(unittest.TestCase):
    SCRIPT = Path(__file__).resolve().parents[1] / "migrate_check.py"

    def run_command(self, *arguments, environment=None):
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), *map(str, arguments)],
            cwd=str(self.SCRIPT.parent),
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_precheck_success_and_failure_exit_codes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            healthy = Path(temp_dir) / "healthy.db"
            with closing(sqlite3.connect(healthy)) as db:
                db.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
                db.commit()

            success = self.run_command("--precheck", healthy)
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertIn("数据库预检通过", success.stdout)

            broken = Path(temp_dir) / "broken.db"
            with closing(sqlite3.connect(broken)) as db:
                db.execute("PRAGMA foreign_keys = OFF")
                db.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
                db.execute(
                    "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))"
                )
                db.execute("INSERT INTO child (parent_id) VALUES (999)")
                db.commit()

            log_path = Path(temp_dir) / "upgrade.log"
            environment = dict(os.environ)
            environment["MEETING_ROOM_UPGRADE_LOG"] = str(log_path)
            failure = self.run_command(
                "--precheck", broken, environment=environment
            )
            self.assertEqual(failure.returncode, 1)
            self.assertIn("外键错误", failure.stderr)
            self.assertIn("外键错误", log_path.read_text(encoding="utf-8"))

    def test_migrate_success_and_failure_exit_codes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "healthy-data"
            data_dir.mkdir()
            # --migrate 只服务于已有安装，不能负责新建数据库；这里先放置一个
            # 可打开的既有数据库文件，再验证 init_db/迁移与自检。
            with closing(sqlite3.connect(data_dir / "reservation.db")):
                pass
            log_path = Path(temp_dir) / "upgrade.log"
            environment = dict(os.environ)
            environment.update(
                {
                    "MEETING_ROOM_DATA_DIR": str(data_dir),
                    "MEETING_ROOM_INITIAL_ADMIN_PASSWORD": "test-password",
                    "MEETING_ROOM_UPGRADE_LOG": str(log_path),
                }
            )

            success = self.run_command("--migrate", environment=environment)
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertIn("结构版本 1", success.stdout)
            with closing(sqlite3.connect(data_dir / "reservation.db")) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT value FROM app_meta WHERE key = 'schema_version'"
                    ).fetchone()[0],
                    "1",
                )

            future_data_dir = Path(temp_dir) / "future-data"
            future_data_dir.mkdir()
            future_database = future_data_dir / "reservation.db"
            with closing(sqlite3.connect(future_database)) as db:
                db.execute(
                    "CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                db.executemany(
                    "INSERT INTO app_meta (key, value) VALUES (?, ?)",
                    (("initial_seed_complete", "1"), ("schema_version", "2")),
                )
                db.commit()

            environment["MEETING_ROOM_DATA_DIR"] = str(future_data_dir)
            failure = self.run_command("--migrate", environment=environment)
            self.assertEqual(failure.returncode, 1)
            self.assertIn("高于当前程序版本", failure.stderr)
            self.assertIn(
                "高于当前程序版本", log_path.read_text(encoding="utf-8")
            )

    def test_migrate_rejects_missing_database_without_creating_one(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "missing-data"
            log_path = Path(temp_dir) / "upgrade.log"
            environment = dict(os.environ)
            environment.update(
                {
                    "MEETING_ROOM_DATA_DIR": str(data_dir),
                    "MEETING_ROOM_INITIAL_ADMIN_PASSWORD": "test-password",
                    "MEETING_ROOM_UPGRADE_LOG": str(log_path),
                }
            )

            result = self.run_command("--migrate", environment=environment)

            self.assertEqual(result.returncode, 1)
            self.assertIn("数据库文件不存在", result.stderr)
            self.assertIn("疑似数据丢失", result.stderr)
            self.assertFalse((data_dir / "reservation.db").exists())
            self.assertIn(
                "数据库文件不存在", log_path.read_text(encoding="utf-8")
            )

    def test_version_file_is_v102(self):
        version_file = self.SCRIPT.parent / "版本.txt"
        self.assertEqual(version_file.read_bytes(), b"1.0.2\n")


if __name__ == "__main__":
    unittest.main()

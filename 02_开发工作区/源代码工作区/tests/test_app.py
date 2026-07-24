from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import app as app_module
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

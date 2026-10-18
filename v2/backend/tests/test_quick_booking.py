"""一句话预约的编排、准备和确认。模型只用替身，不访问网络。"""

from __future__ import annotations

import json
import re
import socket
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest import mock

import v2app.db as db_module
from tests.test_backend import BackendTestCase
from v2app.db import get_db
from v2app.services.deid import deidentify
from v2app.services.model_provider import FakeProvider
from v2app.services.quick_booking import deid_context_for_user


OTHER_PARTY = "钱保密"
OTHER_CASE = "SECRET-9981"
OTHER_PURPOSE = "他人事项秘密"
OWN_PARTY = "孙某"
OWN_CASE = "2026-087"
OWN_PURPOSE = "询问笔录"
_SPOKEN_DIGITS = str.maketrans(
    {
        "〇": "0",
        "零": "0",
        "洞": "0",
        "一": "1",
        "幺": "1",
        "二": "2",
        "两": "2",
        "三": "3",
        "四": "4",
        "五": "5",
        "六": "6",
        "七": "7",
        "八": "8",
        "九": "9",
    }
)


def _benign(text, *, date, part, duration, start=None, end=None, room=None, date_body=None):
    return {
        "text": text,
        "date": date,
        "part": part,
        "duration": duration,
        "start": start,
        "end": end,
        "room": room,
        "date_body": date_body or {"relative_days": (date and 0) or 0},
    }


_BENIGN_CORPUS = (
    _benign(
        "帮我约明天下午一小时",
        date="2026-08-10",
        part="afternoon",
        duration=60,
        date_body={"relative_days": 1},
    ),
    _benign(
        "明天上午九点三十分，笔录室1，一小时",
        date="2026-08-10",
        part="exact",
        duration=60,
        start="09:30",
        room="笔录室 1",
        date_body={"relative_days": 1},
    ),
    _benign(
        "明天下午一小时",
        date="2026-08-10",
        part="afternoon",
        duration=60,
        date_body={"relative_days": 1},
    ),
    _benign(
        "后天上午笔录室 1 两小时",
        date="2026-08-11",
        part="morning",
        duration=120,
        room="笔录室 1",
        date_body={"relative_days": 2},
    ),
    _benign(
        "下周一上午两小时",
        date="2026-08-10",
        part="morning",
        duration=120,
        date_body={"week_offset": 1, "weekday": 1},
    ),
    _benign(
        "明天14:00到15:00",
        date="2026-08-10",
        part="exact",
        duration=60,
        start="14:00",
        end="15:00",
        date_body={"relative_days": 1},
    ),
    _benign(
        "明天14:00到14:30",
        date="2026-08-10",
        part="exact",
        duration=30,
        start="14:00",
        end="14:30",
        date_body={"relative_days": 1},
    ),
    _benign(
        "明天上午90分钟 2号笔录室",
        date="2026-08-10",
        part="morning",
        duration=90,
        room="笔录室 2",
        date_body={"relative_days": 1},
    ),
    _benign(
        "周五下午开会一小时",
        date="2026-08-14",
        part="afternoon",
        duration=60,
        date_body={"week_offset": 1, "weekday": 5},
    ),
    _benign(
        "明天晚上谈话半小时",
        date="2026-08-10",
        part="afternoon",
        duration=30,
        date_body={"relative_days": 1},
    ),
    _benign(
        "明天上午会见一小时",
        date="2026-08-10",
        part="morning",
        duration=60,
        date_body={"relative_days": 1},
    ),
    _benign(
        "笔录室1 明天上午一小时",
        date="2026-08-10",
        part="morning",
        duration=60,
        room="笔录室 1",
        date_body={"relative_days": 1},
    ),
    _benign(
        "帮我预约明天下午开会一小时",
        date="2026-08-10",
        part="afternoon",
        duration=60,
        date_body={"relative_days": 1},
    ),
    _benign(
        "明天上午九点半，笔录室1，一小时",
        date="2026-08-10",
        part="exact",
        duration=60,
        start="09:30",
        room="笔录室 1",
        date_body={"relative_days": 1},
    ),
    _benign(
        "明天下午三点到四点，笔录室 2",
        date="2026-08-10",
        part="exact",
        duration=60,
        start="15:00",
        end="16:00",
        room="笔录室 2",
        date_body={"relative_days": 1},
    ),
    _benign(
        "后天中午一小时",
        date="2026-08-11",
        part="afternoon",
        duration=60,
        date_body={"relative_days": 2},
    ),
    _benign(
        "下周三下午两点，谈话一小时",
        date="2026-08-12",
        part="exact",
        duration=60,
        start="14:00",
        date_body={"week_offset": 1, "weekday": 3},
    ),
    _benign(
        "今天上午十点，笔录室1，一小时",
        date="2026-08-09",
        part="exact",
        duration=60,
        start="10:00",
        room="笔录室 1",
        date_body={"relative_days": 0},
    ),
    _benign(
        "明天 9:30 笔录室1 一小时",
        date="2026-08-10",
        part="exact",
        duration=60,
        start="09:30",
        room="笔录室 1",
        date_body={"relative_days": 1},
    ),
    _benign(
        "下周一下午三点半，半小时",
        date="2026-08-10",
        part="exact",
        duration=30,
        start="15:30",
        date_body={"week_offset": 1, "weekday": 1},
    ),
    _benign(
        "帮我订后天上午笔录室2，三十分钟",
        date="2026-08-11",
        part="morning",
        duration=30,
        room="笔录室 2",
        date_body={"relative_days": 2},
    ),
    _benign(
        "大后天上午一小时",
        date="2026-08-12",
        part="morning",
        duration=60,
        date_body={"relative_days": 3},
    ),
    _benign(
        "明天上午九点，笔录室1，两小时",
        date="2026-08-10",
        part="exact",
        duration=120,
        start="09:00",
        room="笔录室 1",
        date_body={"relative_days": 1},
    ),
    _benign(
        "后天14:00笔录室1一小时",
        date="2026-08-11",
        part="exact",
        duration=60,
        start="14:00",
        room="笔录室 1",
        date_body={"relative_days": 2},
    ),
    _benign(
        "下周五下午两点半，笔录室 2，一小时",
        date="2026-08-14",
        part="exact",
        duration=60,
        start="14:30",
        room="笔录室 2",
        date_body={"week_offset": 1, "weekday": 5},
    ),
    _benign(
        "明天中午十二点，一小时",
        date="2026-08-10",
        part="exact",
        duration=60,
        start="12:00",
        date_body={"relative_days": 1},
    ),
    _benign(
        "周六上午十点，笔录室1，90分钟",
        date="2026-08-15",
        part="exact",
        duration=90,
        start="10:00",
        room="笔录室 1",
        date_body={"week_offset": 1, "weekday": 6},
    ),
    _benign(
        "明天上午8:30到10:00",
        date="2026-08-10",
        part="exact",
        duration=90,
        start="08:30",
        end="10:00",
        date_body={"relative_days": 1},
    ),
    _benign(
        "帮我约下周一上午九点，笔录室1，一小时",
        date="2026-08-10",
        part="exact",
        duration=60,
        start="09:00",
        room="笔录室 1",
        date_body={"week_offset": 1, "weekday": 1},
    ),
    _benign(
        "明天晚上一小时",
        date="2026-08-10",
        part="afternoon",
        duration=60,
        date_body={"relative_days": 1},
    ),
    _benign(
        "后天上午九点三十分，2号笔录室，半小时",
        date="2026-08-11",
        part="exact",
        duration=30,
        start="09:30",
        room="笔录室 2",
        date_body={"relative_days": 2},
    ),
    _benign(
        "明天下午两点一小时",
        date="2026-08-10",
        part="exact",
        duration=60,
        start="14:00",
        date_body={"relative_days": 1},
    ),
    _benign(
        "周五上午开会两小时",
        date="2026-08-14",
        part="morning",
        duration=120,
        date_body={"week_offset": 1, "weekday": 5},
    ),
    _benign(
        "帮我订明天下午",
        date="2026-08-10",
        part="afternoon",
        duration=None,
        date_body={"relative_days": 1},
    ),
    _benign(
        "明天晚上谈话",
        date="2026-08-10",
        part="afternoon",
        duration=None,
        date_body={"relative_days": 1},
    ),
    _benign(
        "明天上午会见",
        date="2026-08-10",
        part="morning",
        duration=None,
        date_body={"relative_days": 1},
    ),
    _benign(
        "明天上午九点三十分",
        date="2026-08-10",
        part="exact",
        duration=None,
        start="09:30",
        date_body={"relative_days": 1},
    ),
    _benign(
        "约明天下午开会",
        date="2026-08-10",
        part="afternoon",
        duration=None,
        date_body={"relative_days": 1},
    ),
    _benign(
        "周五下午开会",
        date="2026-08-14",
        part="afternoon",
        duration=None,
        date_body={"week_offset": 1, "weekday": 5},
    ),
    _benign(
        "下周一上午笔录",
        date="2026-08-10",
        part="morning",
        duration=None,
        date_body={"week_offset": 1, "weekday": 1},
    ),
    _benign(
        "后天中午开会",
        date="2026-08-11",
        part="afternoon",
        duration=None,
        date_body={"relative_days": 2},
    ),
)


def _intent(**overrides) -> dict:
    value = {
        "intent": "book",
        "date": {"relative_days": 1},
        "time": {"part": "afternoon", "start": None, "end": None},
        "duration_minutes": 60,
        "room": {"name": None, "kind": None},
        "party_ref": None,
        "case_ref": None,
        "purpose_hint": None,
        "notes": None,
        "need_clarification": False,
        "missing": [],
    }
    value.update(overrides)
    return value


class _NoNetwork(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("测试不应访问网络"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class QuickBookingTests(_NoNetwork, BackendTestCase):
    def begin(self):
        self.setup_system()
        self.login()
        user = self.add_employee()
        client = self.app.test_client()
        self.login("employee", "employee-pass-123", client)
        rooms = self.bootstrap()["rooms"]
        return user, client, rooms

    def enable(self, provider: FakeProvider) -> None:
        self.app.config["QUICK_BOOKING_PROVIDER"] = provider
        with self.app.app_context():
            get_db().execute(
                """
                UPDATE conversation_settings
                SET enabled = 1, mode = 'external', model = 'fake-model'
                """
            )

    def provider_for(self, user_id: str, text: str, **overrides) -> FakeProvider:
        with self.app.app_context():
            context = deid_context_for_user(get_db(), user_id)
        masked = deidentify(text, context)
        intent = _intent()
        for key, label in (("party_ref", ("当事人", "同事")), ("case_ref", ("案号",))):
            match = next(
                (
                    placeholder
                    for placeholder in masked.mapping
                    if placeholder.startswith(tuple(f"[{item}" for item in label))
                ),
                None,
            )
            if match is not None:
                intent[key] = match
        intent.update(overrides)
        return FakeProvider([json.dumps(intent, ensure_ascii=False)])

    def book_past(self, client, room_id: str, **overrides) -> dict:
        saved = self.now
        self.now = datetime(2026, 8, 8, 8, 0, tzinfo=timezone(timedelta(hours=8)))
        try:
            response = self.write(
                "POST",
                "/api/v1/reservations",
                self.booking_payload(room_id, date="2026-08-08", **overrides),
                client=client,
            )
            self.assertEqual(response.status_code, 201, response.get_json())
            return response.get_json()
        finally:
            self.now = saved

    def own_history(self, client, rooms) -> None:
        room_one = next(room for room in rooms if room["name"] == "笔录室 1")
        room_two = next(room for room in rooms if room["name"] == "笔录室 2")
        fields = {
            "partyName": OWN_PARTY,
            "caseNumber": OWN_CASE,
            "purpose": OWN_PURPOSE,
        }
        self.book_past(client, room_one["id"], start="08:30", **fields)
        self.book_past(client, room_one["id"], start="10:00", **fields)
        self.book_past(client, room_two["id"], start="08:30", **fields)

    def ask(self, client, user_id: str, text: str, **overrides):
        provider = self.provider_for(user_id, text, **overrides)
        self.enable(provider)
        response = self.write(
            "POST",
            "/api/v1/conversations/new/messages",
            {"text": text},
            client=client,
        )
        return provider, response

    def test_feature_off_does_not_call_the_provider(self) -> None:
        self.begin()
        provider = FakeProvider(["{}"])
        self.app.config["QUICK_BOOKING_PROVIDER"] = provider
        for method, path, payload in (
            ("POST", "/api/v1/conversations/new/messages", {"text": "明天下午做笔录"}),
            ("POST", "/api/v1/quick-booking/prepare", {"draftId": "missing"}),
            ("POST", "/api/v1/quick-booking/commit", {"token": "qb_unused"}),
        ):
            response = self.write(method, path, payload)
            body = response.get_json()
            self.assertEqual(response.status_code, 403, body)
            self.assertEqual(body["error"]["code"], "FEATURE_DISABLED")
            self.assertEqual(body["error"]["message"], "对话预约未开启")
        self.assertEqual(provider.calls, [])
        self.assertIs(self.bootstrap()["permissions"]["useConversation"], False)
        self.assertEqual(self.client.get("/api/v1/conversations").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/admin/conversation-settings").status_code, 404)

    def test_happy_path_prefers_the_most_used_room_and_own_history(self) -> None:
        user, client, rooms = self.begin()
        self.own_history(client, rooms)
        sentence = "明天下午给孙某做笔录，案号2026-087"
        provider, response = self.ask(client, user["id"], sentence)
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        reply = body["reply"]
        self.assertEqual(reply["kind"], "prepared")
        booking = reply["prepared"]["booking"]
        room_one = next(room for room in rooms if room["name"] == "笔录室 1")
        self.assertEqual(booking["roomId"], room_one["id"])
        self.assertEqual(booking["date"], "2026-08-10")
        self.assertEqual(booking["start"], "12:00")
        self.assertEqual(booking["duration"], 60)
        self.assertEqual(booking["partyName"], OWN_PARTY)
        self.assertEqual(booking["caseNumber"], OWN_CASE)
        self.assertEqual(booking["purpose"], OWN_PURPOSE)
        self.assertEqual(
            reply["prepared"]["inferred"],
            ["start", "roomId", "caseNumber", "purpose"],
        )
        self.assertEqual(reply["prepared"]["missing"], [])
        self.assertEqual(reply["prepared"]["relativeDay"], "明天")
        self.assertNotIn(OWN_CASE, reply["text"])
        self.assertIn("8 月 8 日", reply["text"])
        self.assertTrue(reply["prepared"]["token"].startswith("qb_"))
        sent = "\n".join(message.content for message in provider.calls[0])
        self.assertNotIn(OWN_PARTY, sent)
        self.assertNotIn(OWN_CASE, sent)
        self.assertNotIn(sentence, sent)
        permissions = client.get("/api/v1/bootstrap").get_json()["permissions"]
        self.assertIs(permissions["useConversation"], True)

        committed = self.write(
            "POST",
            "/api/v1/quick-booking/commit",
            {"token": reply["prepared"]["token"]},
            client=client,
        )
        created = committed.get_json()
        self.assertEqual(committed.status_code, 201, created)
        self.assertEqual(created["purpose"], OWN_PURPOSE)
        self.assertEqual(created["conversationId"], body["conversation"]["id"])
        events = client.get(f"/api/v1/reservations/{created['id']}/events").get_json()
        self.assertEqual(events["items"][0]["type"], "created")
        again = self.write(
            "POST",
            "/api/v1/quick-booking/commit",
            {"token": reply["prepared"]["token"]},
            client=client,
        )
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.get_json()["error"]["code"], "PREPARED_USED")

        with self.app.app_context():
            db = get_db()
            draft = db.execute("SELECT draft_json FROM conversations").fetchone()[0]
            stored = db.execute("SELECT token_hash, payload_json FROM prepared_bookings").fetchall()
        self.assertNotIn(sentence, draft)
        self.assertNotIn(
            reply["prepared"]["token"],
            json.dumps([tuple(row) for row in stored], ensure_ascii=False),
        )
        audit = self.client.get("/api/v1/admin/audit?action=model.request").get_json()
        self.assertNotIn(OWN_PARTY, json.dumps(audit, ensure_ascii=False))
        self.assertNotIn(sentence, json.dumps(audit, ensure_ascii=False))

    def test_two_free_rooms_and_ambiguous_duration_ask_for_a_choice(self) -> None:
        user, client, rooms = self.begin()
        sentence = "明天下午做笔录"
        _provider, response = self.ask(
            client,
            user["id"],
            sentence,
            duration_minutes=None,
            need_clarification=True,
            missing=["duration"],
            party_ref=None,
            case_ref=None,
        )
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        reply = body["reply"]
        self.assertEqual(reply["kind"], "clarify")
        self.assertNotIn("prepared", reply)
        self.assertNotIn("qb_", json.dumps(body, ensure_ascii=False))
        self.assertEqual(
            [option["roomName"] for option in reply["options"]],
            ["笔录室 1", "笔录室 2"],
        )
        for option in reply["options"]:
            self.assertEqual(option["type"], "room")
            self.assertEqual(option["date"], "2026-08-10")
            self.assertEqual(option["windowStart"], "12:00")
            self.assertEqual(option["windowEnd"], "17:30")
            self.assertEqual(option["maxDuration"], 180)
        self.assertEqual(reply["duration"]["choices"], [60, 120, "window"])
        self.assertEqual(reply["duration"]["default"], 60)
        self.assertIn("两间", reply["text"])

    def test_missing_purpose_blocks_commit_until_the_user_supplies_it(self) -> None:
        user, client, rooms = self.begin()
        self.write(
            "POST",
            "/api/v1/users",
            {
                "username": "sunmou",
                "password": "employee-pass-123",
                "name": OWN_PARTY,
                "department": "测试部门",
                "role": "employee",
            },
        )
        room = next(item for item in rooms if item["name"] == "笔录室 1")
        sentence = "明天下午在笔录室 1给孙某做笔录，案号2026-087"
        _provider, response = self.ask(
            client,
            user["id"],
            sentence,
            room={"name": room["name"], "kind": None},
            purpose_hint="笔录",
        )
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        prepared = body["reply"]["prepared"]
        self.assertEqual(prepared["booking"]["partyName"], OWN_PARTY)
        self.assertEqual(prepared["booking"]["caseNumber"], OWN_CASE)
        self.assertEqual(prepared["booking"]["purpose"], "")
        self.assertEqual(prepared["booking"]["roomId"], room["id"])
        self.assertEqual(prepared["missing"], ["purpose"])
        self.assertNotIn("purpose", prepared["inferred"])
        self.assertNotIn("笔录", prepared["booking"]["purpose"])
        blocked = self.write(
            "POST",
            "/api/v1/quick-booking/commit",
            {"token": prepared["token"]},
            client=client,
        )
        error = blocked.get_json()
        self.assertEqual(blocked.status_code, 422, error)
        self.assertEqual(error["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(error["error"]["fields"]["purpose"], "请输入事项")
        with self.app.app_context():
            count = (
                get_db()
                .execute(
                    "SELECT COUNT(*) FROM reservations WHERE owner_user_id = ?",
                    (user["id"],),
                )
                .fetchone()[0]
            )
            used = get_db().execute("SELECT used_at FROM prepared_bookings").fetchone()[0]
        self.assertEqual(count, 0)
        self.assertIsNone(used)

        supplied = self.write(
            "POST",
            "/api/v1/quick-booking/prepare",
            {"draftId": body["conversation"]["id"], "purpose": OWN_PURPOSE},
            client=client,
        )
        card = supplied.get_json()
        self.assertEqual(supplied.status_code, 200, card)
        self.assertEqual(card["reply"]["prepared"]["booking"]["purpose"], OWN_PURPOSE)
        self.assertNotIn("purpose", card["reply"]["prepared"]["inferred"])
        created = self.write(
            "POST",
            "/api/v1/quick-booking/commit",
            {"token": card["reply"]["prepared"]["token"]},
            client=client,
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        self.assertEqual(created.get_json()["purpose"], OWN_PURPOSE)

    def test_token_expiry_reuse_and_other_user(self) -> None:
        user, client, rooms = self.begin()
        self.own_history(client, rooms)
        sentence = "明天下午给孙某做笔录，案号2026-087"
        _provider, response = self.ask(client, user["id"], sentence)
        token = response.get_json()["reply"]["prepared"]["token"]
        self.now += timedelta(minutes=11)
        expired = self.write(
            "POST", "/api/v1/quick-booking/commit", {"token": token}, client=client
        )
        self.assertEqual(expired.status_code, 410)
        self.assertEqual(expired.get_json()["error"]["code"], "PREPARED_EXPIRED")
        self.now -= timedelta(minutes=11)
        with self.app.app_context():
            get_db().execute(
                "UPDATE users SET session_version = session_version + 1 WHERE id = ?",
                (user["id"],),
            )
        self.login("employee", "employee-pass-123", client)
        mismatched = self.write(
            "POST", "/api/v1/quick-booking/commit", {"token": token}, client=client
        )
        self.assertEqual(mismatched.status_code, 401)
        self.assertEqual(mismatched.get_json()["error"]["code"], "SESSION_EXPIRED")
        stranger = self.add_employee("stranger")
        other = self.app.test_client()
        self.login("stranger", "employee-pass-123", other)
        hidden = self.write("POST", "/api/v1/quick-booking/commit", {"token": token}, client=other)
        self.assertEqual(hidden.status_code, 404, hidden.get_json())
        self.assertNotIn(OWN_PARTY, json.dumps(hidden.get_json(), ensure_ascii=False))
        self.assertNotEqual(stranger["id"], user["id"])
        with self.app.app_context():
            used = get_db().execute("SELECT used_at FROM prepared_bookings").fetchone()[0]
        self.assertIsNone(used)

    def test_conflict_between_prepare_and_commit_keeps_the_token(self) -> None:
        user, client, rooms = self.begin()
        self.own_history(client, rooms)
        room = next(item for item in rooms if item["name"] == "笔录室 1")
        _provider, response = self.ask(
            client,
            user["id"],
            "明天下午在笔录室 1给孙某做笔录，案号2026-087",
            room={"name": room["name"], "kind": None},
        )
        prepared = response.get_json()["reply"]["prepared"]
        booking = prepared["booking"]
        blocker = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(
                booking["roomId"],
                date=booking["date"],
                start=booking["start"],
                duration=booking["duration"],
                partyName="旁人姓名甲",
                caseNumber="OTHER-1",
                purpose="占位事项",
            ),
        )
        self.assertEqual(blocker.status_code, 201, blocker.get_json())
        conflicted = self.write(
            "POST",
            "/api/v1/quick-booking/commit",
            {"token": prepared["token"]},
            client=client,
        )
        error = conflicted.get_json()
        self.assertEqual(conflicted.status_code, 409, error)
        self.assertEqual(error["error"]["code"], "SLOT_CONFLICT")
        self.assertEqual(
            set(error["error"]["conflicts"][0]),
            {"id", "roomId", "date", "start", "end"},
        )
        serialized = json.dumps(error, ensure_ascii=False)
        self.assertNotIn("旁人姓名甲", serialized)
        self.assertNotIn("占位事项", serialized)
        with self.app.app_context():
            db = get_db()
            used = db.execute("SELECT used_at FROM prepared_bookings").fetchone()[0]
            count = db.execute(
                """
                SELECT COUNT(*) FROM reservations
                WHERE booking_date = ? AND start_time = ? AND room_id = ?
                """,
                (booking["date"], booking["start"], booking["roomId"]),
            ).fetchone()[0]
        self.assertIsNone(used)
        self.assertEqual(count, 1)

    def test_commit_failpoint_rolls_back_the_token_and_the_booking(self) -> None:
        user, client, rooms = self.begin()
        self.own_history(client, rooms)
        _provider, response = self.ask(client, user["id"], "明天下午给孙某做笔录，案号2026-087")
        token = response.get_json()["reply"]["prepared"]["token"]
        before = self._reservation_count()
        self.app.config["TRANSACTION_FAILPOINT"] = "create_after_slots"
        failed = self.write("POST", "/api/v1/quick-booking/commit", {"token": token}, client=client)
        self.assertEqual(failed.status_code, 500, failed.get_json())
        self.app.config["TRANSACTION_FAILPOINT"] = None
        self.assertEqual(self._reservation_count(), before)
        with self.app.app_context():
            used = get_db().execute("SELECT used_at FROM prepared_bookings").fetchone()[0]
        self.assertIsNone(used)
        created = self.write(
            "POST", "/api/v1/quick-booking/commit", {"token": token}, client=client
        )
        self.assertEqual(created.status_code, 201, created.get_json())

    def _reservation_count(self) -> int:
        with self.app.app_context():
            return int(get_db().execute("SELECT COUNT(*) FROM reservations").fetchone()[0])

    def test_other_users_bookings_never_enter_the_prompt_or_the_response(self) -> None:
        user, client, rooms = self.begin()
        self.own_history(client, rooms)
        other = self.add_employee("other")
        other_client = self.app.test_client()
        self.login("other", "employee-pass-123", other_client)
        room = rooms[0]
        saved = self.now
        self.now = datetime(2026, 8, 8, 8, 0, tzinfo=timezone(timedelta(hours=8)))
        created = self.write(
            "POST",
            "/api/v1/reservations",
            self.booking_payload(
                room["id"],
                date="2026-08-08",
                start="14:00",
                partyName=OTHER_PARTY,
                caseNumber=OTHER_CASE,
                purpose=OTHER_PURPOSE,
            ),
            client=other_client,
        )
        self.now = saved
        self.assertEqual(created.status_code, 201, created.get_json())
        with self.app.app_context():
            context = deid_context_for_user(get_db(), user["id"])
        self.assertNotIn(OTHER_CASE, context.case_numbers)
        self.assertNotIn(OTHER_PARTY, context.party_names)
        self.assertIn(OWN_CASE, context.case_numbers)
        sentence = "明天下午给孙某做笔录，案号2026-087"
        provider, response = self.ask(client, user["id"], sentence)
        payload = json.dumps(response.get_json(), ensure_ascii=False)
        sent = "\n".join(message.content for call in provider.calls for message in call)
        for secret in (OTHER_PARTY, OTHER_CASE, OTHER_PURPOSE):
            self.assertNotIn(secret, sent)
            self.assertNotIn(secret, payload)
        draft_id = response.get_json()["conversation"]["id"]
        switched = self.write(
            "POST",
            "/api/v1/quick-booking/prepare",
            {
                "draftId": draft_id,
                "partyName": OTHER_PARTY,
                "caseNumber": OTHER_CASE,
            },
            client=client,
        )
        switched_body = json.dumps(switched.get_json(), ensure_ascii=False)
        self.assertEqual(switched.status_code, 200, switched.get_json())
        self.assertEqual(switched.get_json()["reply"]["prepared"]["booking"]["purpose"], "")
        self.assertNotIn(OTHER_PURPOSE, switched_body)
        self.assertNotEqual(other["id"], user["id"])

    def test_week_offset_is_resolved_from_the_server_monday(self) -> None:
        user, client, _rooms = self.begin()
        _provider, response = self.ask(
            client,
            user["id"],
            "明天上午做笔录",
            date={"week_offset": 1, "weekday": 1},
            time={"part": "morning", "start": None, "end": None},
            duration_minutes=None,
            need_clarification=True,
            missing=["duration"],
            party_ref=None,
            case_ref=None,
        )
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        option = body["reply"]["options"][0]
        self.assertEqual(option["date"], "2026-08-10")
        self.assertEqual(option["windowStart"], "08:30")
        self.assertEqual(option["windowEnd"], "12:00")

    def test_current_schema_missing_table_or_column_refuses_startup(self) -> None:
        self.setup_system()

        def snapshot(name: str):
            target = self.database.with_name(name)
            with (
                closing(sqlite3.connect(self.database)) as source,
                closing(sqlite3.connect(target)) as copied,
            ):
                source.backup(copied)
            return target

        missing_table_path = snapshot("missing-prepared-bookings.db")
        with closing(sqlite3.connect(missing_table_path)) as db:
            db.execute("DROP TABLE prepared_bookings")
            db.commit()
        with self.assertRaises(db_module.DatabaseGenerationError):
            db_module.classify_existing_database(missing_table_path)
        self.assertFalse(db_module.prepare_database(missing_table_path).ready)

        missing_column_path = snapshot("missing-show-on-display.db")
        with closing(sqlite3.connect(missing_column_path)) as db:
            db.execute("ALTER TABLE rooms DROP COLUMN show_on_display")
            db.commit()
        with self.assertRaises(db_module.DatabaseGenerationError):
            db_module.classify_existing_database(missing_column_path)
        self.assertFalse(db_module.prepare_database(missing_column_path).ready)

    def test_clarification_revokes_the_previous_prepared_token(self) -> None:
        user, client, rooms = self.begin()
        self.own_history(client, rooms)
        room = next(item for item in rooms if item["name"] == "笔录室 1")
        _provider, first = self.ask(
            client,
            user["id"],
            "明天下午给孙某做笔录，案号2026-087",
            room={"name": room["name"], "kind": None},
        )
        first_body = first.get_json()
        self.assertEqual(first.status_code, 200, first_body)
        token = first_body["reply"]["prepared"]["token"]
        conversation_id = first_body["conversation"]["id"]
        follow = "明天下午做笔录"
        provider = self.provider_for(
            user["id"],
            follow,
            duration_minutes=None,
            need_clarification=True,
            missing=["duration"],
            room={"name": room["name"], "kind": None},
        )
        self.enable(provider)
        second = self.write(
            "POST",
            f"/api/v1/conversations/{conversation_id}/messages",
            {"text": follow},
            client=client,
        )
        second_body = second.get_json()
        self.assertEqual(second.status_code, 200, second_body)
        self.assertEqual(second_body["reply"]["kind"], "clarify")
        self.assertNotIn("token", second_body["reply"])
        commit = self.write(
            "POST",
            "/api/v1/quick-booking/commit",
            {"token": token},
            client=client,
        )
        self.assertEqual(commit.status_code, 409, commit.get_json())
        self.assertEqual(commit.get_json()["error"]["code"], "PREPARED_USED")
        with self.app.app_context():
            used = get_db().execute("SELECT used_at FROM prepared_bookings").fetchone()[0]
        self.assertIsNotNone(used)

    def test_explicit_end_time_sets_duration_and_conflict_clarifies(self) -> None:
        user, client, rooms = self.begin()
        self.own_history(client, rooms)
        room = next(item for item in rooms if item["name"] == "笔录室 1")
        sentence = "明天14:00到14:30给孙某做笔录，案号2026-087"
        _provider, response = self.ask(
            client,
            user["id"],
            sentence,
            time={"part": "exact", "start": "14:00", "end": "14:30"},
            duration_minutes=None,
            room={"name": room["name"], "kind": None},
        )
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        booking = body["reply"]["prepared"]["booking"]
        self.assertEqual(booking["start"], "14:00")
        self.assertEqual(booking["end"], "14:30")
        self.assertEqual(booking["duration"], 30)
        conflict = self.provider_for(
            user["id"],
            sentence,
            time={"part": "exact", "start": "14:00", "end": "14:30"},
            duration_minutes=60,
            room={"name": room["name"], "kind": None},
        )
        self.enable(conflict)
        clarified = self.write(
            "POST",
            "/api/v1/conversations/new/messages",
            {"text": sentence},
            client=client,
        )
        clarified_body = clarified.get_json()
        self.assertEqual(clarified.status_code, 200, clarified_body)
        self.assertEqual(clarified_body["reply"]["kind"], "clarify")
        self.assertNotIn("token", clarified_body["reply"])

    def test_end_only_deadline_never_overruns_and_start_only_holds(self) -> None:
        user, client, rooms = self.begin()
        room = next(item for item in rooms if item["name"] == "笔录室 1")
        room_body = {"name": room["name"], "kind": None}
        too_early = self.ask(
            client,
            user["id"],
            "明天09:00前结束",
            time={"part": "exact", "start": None, "end": "09:00"},
            duration_minutes=None,
            room=room_body,
        )[1]
        early_body = too_early.get_json()
        self.assertEqual(too_early.status_code, 200, early_body)
        self.assertEqual(early_body["reply"]["kind"], "clarify")
        self.assertNotIn("prepared", early_body["reply"])
        fits = self.ask(
            client,
            user["id"],
            "明天10:00前结束",
            time={"part": "exact", "start": None, "end": "10:00"},
            duration_minutes=None,
            room=room_body,
        )[1]
        fit_body = fits.get_json()
        self.assertEqual(fits.status_code, 200, fit_body)
        booking = fit_body["reply"]["prepared"]["booking"]
        self.assertEqual(booking["start"], "09:00")
        self.assertEqual(booking["end"], "10:00")
        self.assertLessEqual(booking["end"], "10:00")
        start_only = self.ask(
            client,
            user["id"],
            "明天14:00以后",
            time={"part": "exact", "start": "14:00", "end": None},
            duration_minutes=None,
            room=room_body,
        )[1]
        start_body = start_only.get_json()
        self.assertEqual(start_only.status_code, 200, start_body)
        started = start_body["reply"]["prepared"]["booking"]
        self.assertEqual(started["start"], "14:00")
        self.assertEqual(started["end"], "15:00")
        short = self.ask(
            client,
            user["id"],
            "明天10:00前结束，半小时",
            time={"part": "exact", "start": None, "end": "10:00"},
            duration_minutes=30,
            room=room_body,
        )[1]
        short_booking = short.get_json()["reply"]["prepared"]["booking"]
        self.assertEqual(short_booking["start"], "09:30")
        self.assertEqual(short_booking["end"], "10:00")

    def test_benign_booking_corpus_is_fully_usable(self) -> None:
        user, client, rooms = self.begin()
        room_ids = {room["name"]: room["id"] for room in rooms}
        local = 0
        model = 0
        self.assertGreaterEqual(len(_BENIGN_CORPUS), 30)
        for sample in _BENIGN_CORPUS:
            intent = _intent(
                date=sample["date_body"],
                time={"part": sample["part"], "start": sample["start"], "end": sample["end"]},
                duration_minutes=sample["duration"],
                room={"name": sample["room"], "kind": None},
                need_clarification=sample["duration"] is None,
                missing=["duration"] if sample["duration"] is None else [],
            )
            provider = FakeProvider([json.dumps(intent, ensure_ascii=False)])
            self.enable(provider)
            response = self.write(
                "POST",
                "/api/v1/conversations/new/messages",
                {"text": sample["text"]},
                client=client,
            )
            body = response.get_json()
            self.assertEqual(response.status_code, 200, body)
            reply = body["reply"]
            self.assertIn(reply["kind"], {"prepared", "clarify"}, sample["text"])
            with self.app.app_context():
                raw = (
                    get_db()
                    .execute(
                        "SELECT draft_json FROM conversations WHERE id = ?",
                        (body["conversation"]["id"],),
                    )
                    .fetchone()[0]
                )
            draft = json.loads(raw)
            self.assertEqual(draft["date"], sample["date"], sample["text"])
            self.assertEqual(draft["timePart"], sample["part"], sample["text"])
            self.assertEqual(draft.get("timeStart"), sample["start"], sample["text"])
            self.assertEqual(draft.get("timeEnd"), sample["end"], sample["text"])
            self.assertEqual(draft.get("duration"), sample["duration"], sample["text"])
            if sample["duration"] is None:
                self.assertIn("duration", reply, sample["text"])
            if sample["room"] is None:
                self.assertEqual(reply["kind"], "clarify", sample["text"])
            else:
                self.assertEqual(reply["kind"], "prepared", sample["text"])
                booking = reply["prepared"]["booking"]
                self.assertEqual(booking["roomId"], room_ids[sample["room"]], sample["text"])
                self.assertEqual(booking["roomName"], sample["room"], sample["text"])
                self.assertEqual(booking["date"], sample["date"], sample["text"])
                self.assertEqual(booking["duration"], sample["duration"], sample["text"])
                if sample["start"]:
                    self.assertEqual(booking["start"], sample["start"], sample["text"])
            if provider.calls:
                model += 1
                with self.app.app_context():
                    context = deid_context_for_user(get_db(), user["id"])
                masked = deidentify(sample["text"], context)
                self.assertTrue(masked.confident, (sample["text"], masked.reasons, masked.text))
                self.assertEqual(provider.calls[0][1].content, masked.text)
            else:
                local += 1
        self.assertEqual(local + model, len(_BENIGN_CORPUS))
        self.assertGreater(local, 0)
        self.assertGreater(model, 0)
        self.assertEqual((local, model), (33, 8))

    def test_sensitive_repros_do_not_reach_provider_or_audit(self) -> None:
        user, client, _rooms = self.begin()
        created = self.write(
            "POST",
            "/api/v1/users",
            {
                "username": "liming",
                "password": "employee-pass-123",
                "name": "李明",
                "department": "测试部门",
                "role": "employee",
            },
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        samples = (
            ("明天和李/明做笔录", ("李/明", "李明", "liming")),
            ("明天和李_/明做笔录", ("李_/明", "李明")),
            ("明天 L i M i n g 做笔录", ("L i M i n g", "liming", "LiMing")),
            ("明天和Иван做笔录", ("Иван", "иван")),
            ("明天和محمد做笔录", ("محمد",)),
            ("明天和李月做笔录", ("李月",)),
            ("明天和明月华做笔录", ("明月华",)),
            ("联系电 话1/3/8/0/0/1/3/8/0/0/0", ("13800138000", "1/3/8")),
            (
                "案 号 二 〇 二 六 沪 零 一 零 一 民 初 一 二 三 号",
                ("二〇二六", "零一零一", "一二三", "2026"),
            ),
            ("电话幺三八洞洞幺三八洞洞洞", ("幺三八洞洞幺三八洞洞洞", "13800138000")),
        )
        for text, needles in samples:
            with self.app.app_context():
                db = get_db()
                db.execute("DELETE FROM security_audit_log WHERE action = 'model.request'")
                db.commit()
            provider = FakeProvider([json.dumps(_intent(), ensure_ascii=False)])
            self.enable(provider)
            response = self.write(
                "POST",
                "/api/v1/conversations/new/messages",
                {"text": text},
                client=client,
            )
            body = response.get_json()
            self.assertEqual(response.status_code, 200, body)
            payload = "\n".join(
                message.content
                for call in provider.calls
                for message in call
                if message.role == "user"
            )
            with self.app.app_context():
                rows = (
                    get_db()
                    .execute(
                        """
                    SELECT details_json FROM security_audit_log
                    WHERE action = 'model.request'
                    """
                    )
                    .fetchall()
                )
            self.assertEqual(len(rows), 1, text)
            stored = rows[0][0]
            details = json.loads(stored)
            sent = details.get("sentText") or ""
            blob = "\n".join((payload, sent, stored))
            compact = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", blob.casefold())
            digits = re.sub(r"\D", "", blob.translate(_SPOKEN_DIGITS))
            for needle in needles:
                with self.subTest(text=text, needle=needle):
                    self.assertNotIn(needle.casefold(), blob.casefold())
                    folded = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", needle.casefold())
                    if len(folded) >= 2:
                        self.assertNotIn(folded, compact)
                    needle_digits = re.sub(r"\D", "", needle.translate(_SPOKEN_DIGITS))
                    if len(needle_digits) >= 3:
                        self.assertNotIn(needle_digits, digits)

    def test_unhashable_model_json_falls_back_and_audits(self) -> None:
        _user, client, _rooms = self.begin()
        content = json.dumps({**_intent(), "intent": []}, ensure_ascii=False)
        self.enable(FakeProvider([content]))
        response = self.write(
            "POST",
            "/api/v1/conversations/new/messages",
            {"text": "明天下午做笔录"},
            client=client,
        )
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["reply"]["kind"], "fallback")
        with self.app.app_context():
            stored = (
                get_db()
                .execute(
                    """
                SELECT details_json FROM security_audit_log
                WHERE action = 'model.request'
                """
                )
                .fetchone()[0]
            )
        details = json.loads(stored)
        self.assertEqual(details["outcome"], "invalid_json")
        self.assertIsInstance(details["sentText"], str)
        self.assertNotIn(content, stored)
        self.assertNotIn("普通员工", stored)

    def test_v4_database_gains_disabled_quick_booking_tables(self) -> None:
        self.setup_system()
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute("DROP TABLE prepared_bookings")
            db.execute("DROP TABLE conversations")
            db.execute("DROP TABLE conversation_settings")
            db.execute("UPDATE app_meta SET value = '4' WHERE key = 'schema_version'")
            db.commit()
        self.assertTrue(db_module.migrate_schema_v4_to_v5(self.database))
        with closing(sqlite3.connect(self.database)) as db:
            version = db.execute(
                "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            enabled = db.execute(
                "SELECT enabled FROM conversation_settings WHERE id = 1"
            ).fetchone()[0]
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        self.assertEqual(version, "5")
        self.assertEqual(enabled, 0)
        self.assertTrue({"conversations", "prepared_bookings", "conversation_settings"} <= tables)
        self.assertFalse(db_module.migrate_schema_v4_to_v5(self.database))


if __name__ == "__main__":
    unittest.main()

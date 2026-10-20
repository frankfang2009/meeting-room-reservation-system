"""一句话预约的编排、准备和确认。模型只用替身，不访问网络。"""

from __future__ import annotations

import json
import re
import socket
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import v2app.db as db_module
from tests.test_backend import BackendTestCase
from v2app.db import get_db
from v2app.services.deid import allowed_surface, deidentify, satisfies_outbound_invariant
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


def _intent(**overrides) -> dict:
    """旧调用点用的最小对象。真正发给解析器的形状由 ``_script`` 生成。"""

    value = {
        "intent": "book",
        "day": {"offset": 1},
        "start": {"period": "afternoon", "time": None},
        "duration": {"minutes": None},
        "room": None,
        "purpose": None,
        "refs": [],
        "evidence": {"day": [0], "start": [1]},
        "unknown": [],
        "missing": [],
        "need_clarification": False,
    }
    value.update(overrides)
    return value


def _script(encoded, **overrides) -> dict:
    """把测试里的旧字段翻译成带证据下标的引用 JSON。"""

    legacy = {
        "date": {"relative_days": 1},
        "time": {"part": "afternoon", "start": None, "end": None},
        "duration_minutes": 60,
        "room": None,
        "need_clarification": False,
        "missing": [],
    }
    legacy.update(overrides)
    tokens = encoded.tokens

    def indexes(*categories: str) -> list[int]:
        return [index for index, token in enumerate(tokens) if token.category in categories]

    date_body = legacy["date"] or {}
    if "relative_days" in date_body:
        day: dict | None = {"offset": date_body["relative_days"]}
    elif "week_offset" in date_body:
        day = {"week_offset": date_body["week_offset"], "weekday": date_body["weekday"]}
    elif "month" in date_body:
        day = {"month": date_body["month"], "day": date_body["day"]}
    else:
        day = None
    day_evidence = indexes("day")[:1]
    if day is not None and not day_evidence:
        day = None

    part = (legacy["time"] or {}).get("part") or "any"
    period = {
        "afternoon": "afternoon",
        "morning": "morning",
        "noon": "noon",
        "night": "evening",
        "dawn": "morning",
        "early": "morning",
        "dusk": "evening",
        "exact": "any",
        "any": "any",
    }.get(part, "any")
    clock = (legacy["time"] or {}).get("start")
    start_evidence = indexes("period", "clock")[:1]
    if (period != "any" or clock) and not start_evidence:
        period = "any"
        clock = None
    if period == "any" and not clock:
        start_evidence = []

    duration = legacy["duration_minutes"]
    duration_evidence = indexes("duration", "clock")[:1]
    if duration is not None and not duration_evidence:
        duration = None

    room_body = legacy.get("room")
    room_name = room_body.get("name") if isinstance(room_body, dict) else None
    room_evidence = indexes("room")[:1]
    if room_name and not room_evidence:
        room_name = None

    evidence: dict[str, list[int]] = {}
    if day is not None and day_evidence:
        evidence["day"] = day_evidence
    if start_evidence and (period != "any" or clock):
        evidence["start"] = start_evidence
    if duration is not None and duration_evidence:
        evidence["duration"] = duration_evidence
    if room_name and room_evidence:
        evidence["room"] = room_evidence

    want_party = overrides.get("party_ref", "auto") is not None
    want_case = overrides.get("case_ref", "auto") is not None
    refs = []
    digit_spans = [
        placeholder
        for placeholder, original in encoded.mapping.items()
        if any(char.isdigit() for char in original)
    ]
    for placeholder, original in encoded.mapping.items():
        if want_party and _looks_like_party(original):
            refs.append({"ph": placeholder, "role": "当事人"})
        elif want_case and _looks_like_case(original):
            refs.append({"ph": placeholder, "role": "案号"})
    if want_case and not any(item["role"] == "案号" for item in refs) and len(digit_spans) == 1:
        refs.append({"ph": digit_spans[0], "role": "案号"})

    missing = [
        item
        for item in legacy["missing"]
        if item in {"day", "start", "duration", "room", "party", "case", "purpose"}
    ]
    return {
        "intent": "book",
        "day": day,
        "start": {"period": period, "time": clock},
        "duration": {"minutes": duration},
        "room": room_name,
        "purpose": None,
        "refs": refs,
        "evidence": evidence,
        "unknown": [],
        "missing": missing,
        "need_clarification": bool(legacy["need_clarification"]),
    }


def _looks_like_party(value: str) -> bool:
    if not value or any(char.isdigit() for char in value) or len(value) > 20:
        return False
    return any("\u4e00" <= char <= "\u9fff" for char in value) and value not in {
        "案号",
        "电话",
        "那个",
        "这个",
    }


def _looks_like_case(value: str) -> bool:
    digits = sum(char.isdigit() for char in value)
    return digits >= 3 and len(value) <= 40


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
        intent = _script(masked, **overrides)
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
            ["start", "roomId", "duration", "caseNumber", "purpose"],
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
        self.assertEqual(option["windowEnd"], "17:30")

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

    def test_clock_phrase_sets_duration_and_conflict_clarifies(self) -> None:
        user, client, rooms = self.begin()
        self.own_history(client, rooms)
        room = next(item for item in rooms if item["name"] == "笔录室 1")
        sentence = "明天下午两点给孙某做笔录，案号2026-087，笔录室1，半小时"
        _provider, response = self.ask(
            client,
            user["id"],
            sentence,
            time={"part": "afternoon", "start": "14:00", "end": None},
            duration_minutes=30,
            room={"name": room["name"], "kind": None},
        )
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        booking = body["reply"]["prepared"]["booking"]
        self.assertEqual(booking["start"], "14:00")
        self.assertEqual(booking["end"], "14:30")
        self.assertEqual(booking["duration"], 30)
        created = self.write(
            "POST",
            "/api/v1/quick-booking/commit",
            {"token": body["reply"]["prepared"]["token"]},
            client=client,
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        conflict = self.provider_for(
            user["id"],
            sentence,
            time={"part": "afternoon", "start": "14:00", "end": None},
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

    def test_evening_outside_work_hours_clarifies_until_the_user_picks(self) -> None:
        user, client, rooms = self.begin()
        room = next(item for item in rooms if item["name"] == "笔录室 1")
        _provider, response = self.ask(
            client,
            user["id"],
            "明天晚上笔录室1半小时",
            time={"part": "night", "start": None, "end": None},
            duration_minutes=30,
            room={"name": room["name"], "kind": None},
        )
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        reply = body["reply"]
        self.assertEqual(reply["kind"], "clarify", body)
        self.assertNotIn("prepared", reply)
        self.assertNotIn("token", reply)
        self.assertIn("晚上", reply["text"])
        self.assertIn("工作时间", reply["text"])
        option = next(item for item in reply["options"] if item["roomId"] == room["id"])
        self.assertEqual(option["date"], "2026-08-10")
        self.assertEqual(option["windowStart"], "17:00")
        self.assertEqual(option["windowEnd"], "17:30")
        self.assertGreaterEqual(option["windowStart"], "08:30")
        self.assertLessEqual(option["windowEnd"], "17:30")
        picked = self.write(
            "POST",
            "/api/v1/quick-booking/prepare",
            {
                "draftId": body["conversation"]["id"],
                "roomId": room["id"],
                "purpose": "询问笔录",
                "partyName": "孙某",
                "caseNumber": "2026-087",
            },
            client=client,
        )
        prepared_body = picked.get_json()
        self.assertEqual(picked.status_code, 200, prepared_body)
        prepared = prepared_body["reply"]
        self.assertEqual(prepared["kind"], "prepared", prepared)
        booking = prepared["prepared"]["booking"]
        self.assertEqual(booking["date"], "2026-08-10")
        self.assertEqual(booking["start"], "17:00")
        self.assertEqual(booking["end"], "17:30")
        self.assertEqual(booking["roomId"], room["id"])
        created = self.write(
            "POST",
            "/api/v1/quick-booking/commit",
            {"token": prepared["prepared"]["token"]},
            client=client,
        )
        saved = created.get_json()
        self.assertEqual(created.status_code, 201, saved)
        self.assertEqual(saved["start"], "17:00")
        self.assertEqual(saved["end"], "17:30")

    def test_tomorrow_morning_compound_commits_the_next_day(self) -> None:
        user, client, rooms = self.begin()
        room = next(item for item in rooms if item["name"] == "笔录室 1")
        provider, response = self.ask(
            client,
            user["id"],
            "明早十点笔录室1用半小时",
            time={"part": "morning", "start": "10:00", "end": None},
            duration_minutes=30,
            room={"name": room["name"], "kind": None},
        )
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(len(provider.calls), 1)
        prepared = body["reply"]["prepared"]["booking"]
        self.assertEqual(prepared["date"], "2026-08-10")
        self.assertEqual(prepared["start"], "10:00")
        self.assertEqual(prepared["end"], "10:30")
        self.assertEqual(prepared["roomId"], room["id"])
        supplied = self.write(
            "POST",
            "/api/v1/quick-booking/prepare",
            {
                "draftId": body["conversation"]["id"],
                "purpose": "询问笔录",
                "partyName": "孙某",
                "caseNumber": "2026-087",
            },
            client=client,
        )
        self.assertEqual(supplied.status_code, 200, supplied.get_json())
        created = self.write(
            "POST",
            "/api/v1/quick-booking/commit",
            {"token": supplied.get_json()["reply"]["prepared"]["token"]},
            client=client,
        )
        saved = created.get_json()
        self.assertEqual(created.status_code, 201, saved)
        self.assertEqual(saved["date"], "2026-08-10")
        self.assertEqual(saved["start"], "10:00")
        self.assertEqual(saved["end"], "10:30")
        self.assertEqual(saved["roomId"], room["id"])

    def test_period_outside_work_hours_offers_the_nearest_legal_start(self) -> None:
        user, client, _rooms = self.begin()
        clarify_samples = (
            (
                "明天晚上笔录室2用一个小时",
                {"relative_days": 1},
                "night",
                60,
                "2026-08-10",
                "16:30",
                "17:30",
                "笔录室 2",
            ),
            (
                "后天夜里笔录室1半小时",
                {"relative_days": 2},
                "night",
                30,
                "2026-08-11",
                "17:00",
                "17:30",
                "笔录室 1",
            ),
        )
        for text, date_body, part, duration, day, start, end, room_name in clarify_samples:
            provider, response = self.ask(
                client,
                user["id"],
                text,
                date=date_body,
                time={"part": part, "start": None, "end": None},
                duration_minutes=duration,
                room={"name": room_name, "kind": None},
            )
            body = response.get_json()
            self.assertEqual(response.status_code, 200, body)
            self.assertEqual(len(provider.calls), 1, text)
            reply = body["reply"]
            self.assertEqual(reply["kind"], "clarify", (text, reply))
            self.assertNotIn("prepared", reply)
            self.assertNotIn("token", reply)
            option = next(item for item in reply["options"] if item["roomName"] == room_name)
            self.assertEqual(option["date"], day, text)
            self.assertEqual(option["windowStart"], start, text)
            self.assertEqual(option["windowEnd"], end, text)
            self.assertGreaterEqual(option["windowStart"], "08:30", text)
            self.assertLessEqual(option["windowEnd"], "17:30", text)
        provider, response = self.ask(
            client,
            user["id"],
            "下周四凌晨笔录室1一小时",
            date={"week_offset": 1, "weekday": 4},
            time={"part": "dawn", "start": None, "end": None},
            duration_minutes=60,
            room={"name": "笔录室 1", "kind": None},
        )
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        booking = body["reply"]["prepared"]["booking"]
        self.assertEqual(booking["date"], "2026-08-13")
        self.assertEqual(booking["start"], "08:30")
        self.assertEqual(booking["end"], "09:30")
        self.assertGreaterEqual(booking["start"], "08:30")
        self.assertLessEqual(booking["end"], "17:30")

    def test_clock_before_its_period_clarifies_instead_of_moving(self) -> None:
        user, client, rooms = self.begin()
        room = next(item for item in rooms if item["name"] == "笔录室 1")
        provider, response = self.ask(
            client,
            user["id"],
            "明天下午十点笔录室1一小时",
            time={"part": "afternoon", "start": "10:00", "end": None},
            duration_minutes=60,
            room={"name": room["name"], "kind": None},
        )
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(len(provider.calls), 1)
        reply = body["reply"]
        self.assertEqual(reply["kind"], "clarify", body)
        self.assertNotIn("prepared", reply)
        self.assertNotIn("token", reply)
        option = next(item for item in reply["options"] if item["roomId"] == room["id"])
        self.assertEqual(option["windowStart"], "12:00")
        self.assertEqual(option["windowEnd"], "13:00")
        self.assertGreaterEqual(option["windowStart"], "08:30")
        self.assertLessEqual(option["windowEnd"], "17:30")

    def test_stated_clock_outside_hours_clarifies_without_a_token(self) -> None:
        user, client, rooms = self.begin()
        room = next(item for item in rooms if item["name"] == "笔录室 1")
        samples = (
            ("明天18:30笔录室1一小时", "18:30", 60, "16:30", "17:30"),
            ("明天07:00笔录室1半小时", "07:00", 30, "08:30", "09:00"),
            ("明天14:15笔录室1一小时", "14:15", 60, "14:00", "15:00"),
        )
        for text, clock, duration, start, end in samples:
            _provider, response = self.ask(
                client,
                user["id"],
                text,
                time={"part": "any", "start": clock, "end": None},
                duration_minutes=duration,
                room={"name": room["name"], "kind": None},
            )
            body = response.get_json()
            self.assertEqual(response.status_code, 200, body)
            reply = body["reply"]
            self.assertEqual(reply["kind"], "clarify", (text, reply))
            self.assertNotIn("prepared", reply)
            self.assertNotIn("token", reply)
            self.assertIn(clock, reply["text"])
            option = next(item for item in reply["options"] if item["roomId"] == room["id"])
            self.assertEqual(option["date"], "2026-08-10", text)
            self.assertEqual(option["windowStart"], start, text)
            self.assertEqual(option["windowEnd"], end, text)
        picked = self.write(
            "POST",
            "/api/v1/quick-booking/prepare",
            {
                "draftId": body["conversation"]["id"],
                "roomId": room["id"],
                "purpose": "询问笔录",
                "partyName": "孙某",
                "caseNumber": "2026-087",
            },
            client=client,
        )
        prepared_body = picked.get_json()
        self.assertEqual(picked.status_code, 200, prepared_body)
        booking = prepared_body["reply"]["prepared"]["booking"]
        self.assertEqual(booking["start"], "14:00")
        self.assertEqual(booking["end"], "15:00")
        self.assertEqual(booking["duration"], 60)
        self.assertIn("token", prepared_body["reply"]["prepared"])

    def test_hard_constraints_reject_mutated_model_fields(self) -> None:
        """S3：说出的日期、钟点或时长被改动时不发凭证。"""

        import random
        from datetime import date, timedelta

        user, client, rooms = self.begin()
        room = rooms[0]
        today = date(2026, 8, 9)
        rng = random.Random(20261019)
        for _index in range(40):
            offset = rng.choice([-3, -1, 0, 1, 2, 80, 400])
            period = rng.choice(["morning", "noon", "afternoon", "evening", "any"])
            clock = rng.choice([None, "07:00", "10:00", "14:00", "18:30", "23:00"])
            minutes = rng.choice([None, 15, 30, 45, 60, 200, 90])
            recorded = {
                "intent": "book",
                "day": {"offset": offset},
                "start": {"period": period, "time": clock},
                "duration": {"minutes": minutes},
                "room": room["name"],
                "purpose": None,
                "refs": [],
                "evidence": {"day": [0], "start": [1], "room": [2]},
                "unknown": [],
                "missing": [],
                "need_clarification": False,
            }
            if minutes is not None:
                recorded["evidence"]["duration"] = [3]
            self.enable(FakeProvider([json.dumps(recorded, ensure_ascii=False)]))
            response = self.write(
                "POST",
                "/api/v1/conversations/new/messages",
                {"text": "明天下午笔录室1一小时"},
                client=client,
            )
            body = response.get_json()
            self.assertEqual(response.status_code, 200, body)
            reply = body["reply"]
            stated_date = None
            if 0 <= offset <= 60:
                stated_date = (today + timedelta(days=offset)).isoformat()
            prepared = reply["kind"] == "prepared"
            if prepared and clock is not None:
                prepared = reply["prepared"]["booking"]["start"] == clock
            if prepared and minutes is not None:
                prepared = reply["prepared"]["booking"]["duration"] == minutes
            if prepared and stated_date is not None:
                prepared = reply["prepared"]["booking"]["date"] == stated_date
            if prepared and stated_date is None:
                prepared = False
            if not prepared:
                self.assertNotIn("token", reply, body)
                self.assertNotIn("prepared", reply, body)
                for option in reply.get("options") or []:
                    if "windowStart" in option:
                        self.assertGreaterEqual(option["windowStart"], "08:30")
                        self.assertLessEqual(option["windowEnd"], "17:30")
                continue
            booking = reply["prepared"]["booking"]
            self.assertEqual(booking["date"], stated_date)
            if clock is not None:
                self.assertEqual(booking["start"], clock)
            if minutes is not None:
                self.assertEqual(booking["duration"], minutes)
            self.assertGreaterEqual(booking["start"], "08:30")
            self.assertLessEqual(booking["end"], "17:30")
            self.assertIn(booking["duration"], {30, 60, 90, 120, 150, 180})

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

    def test_frozen_acceptance_corpus_reports_s1_and_q(self) -> None:
        """S1 必须全过。Q 是录制稿回放后的整卡一致率，只设健全底线。"""

        user, client, rooms = self.begin()
        room_ids = {room["name"]: room["id"] for room in rooms}
        path = Path(__file__).resolve().parent / "fixtures" / "booking-acceptance.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload["cases"]
        self.assertGreaterEqual(len(cases), 150)
        matched = 0
        mismatches: list[str] = []
        for case in cases:
            provider = FakeProvider([json.dumps(case["recorded"], ensure_ascii=False)])
            self.enable(provider)
            response = self.write(
                "POST",
                "/api/v1/conversations/new/messages",
                {"text": case["text"]},
                client=client,
            )
            body = response.get_json()
            self.assertEqual(response.status_code, 200, (case["id"], body))
            reply = body["reply"]
            if provider.calls:
                sent = provider.calls[0][1].content
                with self.app.app_context():
                    context = deid_context_for_user(get_db(), user["id"])
                    allowed = allowed_surface(context)
                self.assertTrue(
                    satisfies_outbound_invariant(sent, allowed),
                    (case["id"], sent),
                )
                self.assertEqual(sent, deidentify(case["text"], context).text)
                for original in deidentify(case["text"], context).mapping.values():
                    if len(original) >= 2:
                        self.assertNotIn(original, sent, case["id"])
            expect = case["expect"]
            ok = reply["kind"] == expect["kind"]
            if ok and expect["kind"] == "prepared":
                booking = reply["prepared"]["booking"]
                ok = (
                    booking["date"] == expect["date"]
                    and booking["start"] == expect["start"]
                    and booking["end"] == expect["end"]
                    and booking["duration"] == expect["duration"]
                    and booking["roomId"] == room_ids[expect["room"]]
                    and booking["start"] >= "08:30"
                    and booking["end"] <= "17:30"
                )
            elif ok and expect["kind"] == "clarify" and expect.get("date"):
                ok = (
                    bool(reply.get("options")) and reply["options"][0].get("date") == expect["date"]
                )
                for option in reply.get("options") or []:
                    if "windowStart" in option and (
                        option["windowStart"] < "08:30" or option["windowEnd"] > "17:30"
                    ):
                        ok = False
            if ok:
                matched += 1
            elif len(mismatches) < 12:
                got = reply.get("prepared", {}).get("booking", {})
                mismatches.append(
                    f"{case['id']} {case['text']} expect={expect} got={reply['kind']} {got}"
                )
        ratio = matched / len(cases)
        # Q 用假模型回放录制稿，衡量的是本机编排是否按稿还原卡片，不是 DeepSeek 的理解率。
        print(f"Q={matched}/{len(cases)} ({ratio:.1%}) fake-provider replay")
        for line in mismatches:
            print(line)
        self.assertGreaterEqual(ratio, 0.5)

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

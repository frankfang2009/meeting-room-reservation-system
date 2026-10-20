"""模型供应商与引用式意图。全部使用替身，不访问网络。

锁定：原文里的姓名和案号不能进入供应商载荷或审计；证据下标必须对上词元类别；
非法 JSON 降级为手动预约。没有本地规则快路径。
"""

from __future__ import annotations

import ast
import json
import socket
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from urllib.request import HTTPRedirectHandler, ProxyHandler

from tests.test_backend import BackendTestCase
from v2app.db import get_db
from v2app.services.deid import (
    DeidContext,
    DeidResult,
    allowed_surface,
    deidentify,
    satisfies_outbound_invariant,
)
from v2app.services.model_intent import (
    FALLBACK_UNAVAILABLE,
    FALLBACK_UNCERTAIN,
    ModelSettings,
    interpret_booking_sentence,
    model_request_audit,
    parse_booking_intent,
)
from v2app.services.model_provider import (
    MODEL_API_KEY_ENV,
    ChatMessage,
    DeepSeekProvider,
    FakeProvider,
    ModelCallError,
    chat_completions_url,
    open_configured_provider,
    read_model_api_key,
)


ROOT = Path(__file__).resolve().parents[3]
RAW = "明天下午帮孙某做笔录，案号2026-087，要笔录室 1"
PARTY = "孙某"
CASE = "2026-087"
ROOM = "笔录室 1"
KEY = "sk-test-key"
NOW = datetime(2026, 10, 19, 9, 30)
CONTEXT = DeidContext(party_names=(PARTY,), case_numbers=(CASE,), room_names=(ROOM,))
ROOMS = (ROOM, "笔录室 2")
PURPOSES = ("询问笔录", "谈话笔录")


def _settings(**overrides) -> ModelSettings:
    values = {
        "enabled": True,
        "provider_name": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "timeout_seconds": 10,
    }
    values.update(overrides)
    return ModelSettings(**values)


def _intent_for(encoded, **overrides) -> dict:
    tokens = encoded.tokens

    def indexes(*categories: str) -> list[int]:
        return [index for index, token in enumerate(tokens) if token.category in categories][:1]

    value = {
        "intent": "book",
        "day": {"offset": 1},
        "start": {"period": "afternoon", "time": None},
        "duration": {"minutes": None},
        "room": ROOM,
        "purpose": None,
        "refs": [],
        "evidence": {
            "day": indexes("day"),
            "start": indexes("period", "clock"),
            "room": indexes("room"),
        },
        "unknown": [],
        "missing": [],
        "need_clarification": False,
    }
    value.update(overrides)
    if not value["evidence"].get("room"):
        value["room"] = None
    if value["room"] is None:
        value["evidence"].pop("room", None)
    if value["duration"]["minutes"] is None:
        value["evidence"].pop("duration", None)
    else:
        value["evidence"]["duration"] = indexes("duration", "clock")
    if value["purpose"] is not None:
        value["evidence"]["purpose"] = indexes("purpose", "verb")
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


class ModelIntentTests(_NoNetwork):
    def test_default_settings_do_not_encode_call_or_audit(self) -> None:
        audits: list[dict] = []

        def explode(*_args, **_kwargs):
            raise AssertionError("功能关闭时不应编码")

        with mock.patch("v2app.services.model_intent.deidentify", side_effect=explode):
            result = interpret_booking_sentence(
                RAW,
                settings=ModelSettings(),
                provider=FakeProvider(["{}"]),
                now=NOW,
                work_start="08:30",
                work_end="17:30",
                record_audit=audits.append,
            )
        self.assertEqual(result.kind, "disabled")
        self.assertEqual(audits, [])

    def test_structured_sentence_still_calls_the_provider(self) -> None:
        encoded = deidentify("明天下午笔录室1一小时", DeidContext(room_names=ROOMS))
        provider = FakeProvider([json.dumps(_intent_for(encoded), ensure_ascii=False)])
        result = interpret_booking_sentence(
            "明天下午笔录室1一小时",
            context=DeidContext(room_names=ROOMS),
            settings=_settings(),
            provider=provider,
            now=NOW,
            work_start="08:30",
            work_end="17:30",
            room_names=ROOMS,
            purpose_names=PURPOSES,
        )
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(result.kind, "intent")
        self.assertEqual(result.outcome, "ok")
        self.assertIsNone(getattr(result.intent, "local", None))

    def test_raw_sentence_never_reaches_the_provider_payload(self) -> None:
        masked = deidentify(RAW, CONTEXT)
        self.assertTrue(masked.confident, masked.reasons)
        self.assertNotIn(PARTY, masked.text)
        self.assertNotIn(CASE, masked.text)
        self.assertTrue(any(token.category == "placeholder" for token in masked.tokens))
        provider = FakeProvider([json.dumps(_intent_for(masked), ensure_ascii=False)])
        audits: list[dict] = []
        result = interpret_booking_sentence(
            RAW,
            context=CONTEXT,
            settings=_settings(),
            provider=provider,
            now=NOW,
            work_start="08:30",
            work_end="17:30",
            room_names=ROOMS,
            purpose_names=PURPOSES,
            record_audit=audits.append,
        )
        self.assertEqual(result.kind, "intent", result.outcome)
        self.assertEqual(result.sent_text, masked.text)
        payload = "\n".join(message.content for message in provider.calls[0])
        user = provider.calls[0][1].content
        self.assertNotIn(RAW, payload)
        self.assertNotIn(PARTY, payload)
        self.assertNotIn(CASE, payload)
        for original in masked.mapping.values():
            if len(original) >= 2:
                self.assertNotIn(original, user)
        system = provider.calls[0][0].content
        self.assertIn("JSON", system)
        self.assertIn("2026-10-19", system)
        self.assertIn("星期一", system)
        self.assertIn("09:30", system)
        self.assertIn("08:30", system)
        self.assertIn("17:30", system)
        self.assertIn(ROOM, system)
        self.assertEqual(audits[0]["sentText"], masked.text)
        self.assertEqual(audits[0]["outcome"], "ok")
        self.assertEqual(audits[0]["placeholderCounts"]["P"], len(masked.mapping))
        self.assertNotIn(PARTY, json.dumps(audits, ensure_ascii=False))

    def test_history_purpose_text_stays_off_the_provider_payload(self) -> None:
        secret = "询问孙某，电话13812345678，案号CASE-2026-087"
        masked = deidentify(RAW, CONTEXT)
        provider = FakeProvider([json.dumps(_intent_for(masked), ensure_ascii=False)])
        result = interpret_booking_sentence(
            RAW,
            context=CONTEXT,
            settings=_settings(),
            provider=provider,
            now=NOW,
            work_start="08:30",
            work_end="17:30",
            room_names=ROOMS,
            purpose_names=(*PURPOSES, secret),
        )
        self.assertEqual(result.kind, "intent", result.outcome)
        payload = "\n".join(message.content for message in provider.calls[0])
        self.assertNotIn(secret, payload)
        self.assertNotIn("13812345678", payload)
        self.assertNotIn("CASE-2026-087", payload)
        self.assertIn("询问笔录", payload)
        self.assertIn("谈话笔录", payload)
        user = provider.calls[0][1].content
        system = provider.calls[0][0].content
        self.assertTrue(satisfies_outbound_invariant(user, allowed_surface(CONTEXT)))
        for original in masked.mapping.values():
            if len(original) >= 2:
                self.assertNotIn(original, payload)
        self.assertNotIn(secret, system)

    def test_unknown_name_is_a_placeholder_and_still_sent(self) -> None:
        raw = "明天下午找赵倩做笔录"
        provider = FakeProvider([json.dumps(_intent_for(deidentify(raw)), ensure_ascii=False)])
        result = interpret_booking_sentence(
            raw,
            settings=_settings(),
            provider=provider,
            now=NOW,
            work_start="08:30",
            work_end="17:30",
            room_names=ROOMS,
        )
        self.assertEqual(len(provider.calls), 1)
        self.assertNotIn("赵倩", provider.calls[0][1].content)
        self.assertIn("[P", provider.calls[0][1].content)
        self.assertEqual(result.kind, "intent")

    def test_gate_blocks_a_leaked_mapping_value_before_the_call(self) -> None:
        leaked = DeidResult(
            text="明天下午孙某",
            mapping={"[P1]": PARTY},
            confident=True,
            reasons=[],
        )
        provider = FakeProvider(["{}"])
        with mock.patch("v2app.services.model_intent.deidentify", return_value=leaked):
            result = interpret_booking_sentence(
                "明天下午孙某",
                settings=_settings(),
                provider=provider,
                now=NOW,
                work_start="08:30",
                work_end="17:30",
                room_names=ROOMS,
            )
        self.assertEqual(provider.calls, [])
        self.assertEqual(result.outcome, "blocked_invariant")

    def test_evidence_must_point_at_a_time_token_and_refs_must_exist(self) -> None:
        encoded = deidentify(RAW, CONTEXT)
        good = _intent_for(encoded)
        parsed = parse_booking_intent(
            json.dumps(good, ensure_ascii=False),
            room_names=ROOMS,
            purpose_names=PURPOSES,
            tokens=encoded.tokens,
        )
        self.assertEqual(parsed.period, "afternoon")
        self.assertEqual(parsed.date.relative_days, 1)
        bad_evidence = json.loads(json.dumps(good))
        bad_evidence["evidence"]["day"] = [
            index for index, token in enumerate(encoded.tokens) if token.category == "placeholder"
        ][:1]
        with self.assertRaises(Exception):
            parse_booking_intent(
                json.dumps(bad_evidence),
                room_names=ROOMS,
                purpose_names=PURPOSES,
                tokens=encoded.tokens,
            )
        invented = json.loads(json.dumps(good))
        invented["refs"] = [{"ph": "P9", "role": "当事人"}, {"ph": "赵倩", "role": "当事人"}]
        with self.assertRaises(Exception):
            parse_booking_intent(
                json.dumps(invented),
                room_names=ROOMS,
                purpose_names=PURPOSES,
                tokens=encoded.tokens,
            )
        dropped = json.loads(json.dumps(good))
        dropped["refs"] = [{"ph": "P9", "role": "当事人"}]
        dropped["purpose"] = "赵倩"
        kept = parse_booking_intent(
            json.dumps(dropped),
            room_names=ROOMS,
            purpose_names=PURPOSES,
            tokens=encoded.tokens,
        )
        self.assertEqual(kept.refs, ())
        self.assertIsNone(kept.purpose)

    def test_colon_clock_date_and_long_duration_can_be_cited(self) -> None:
        sentence = "2026-08-20 14:30笔录室1用90分钟"
        encoded = deidentify(sentence, DeidContext(room_names=ROOMS))
        indexes = {token.text: index for index, token in enumerate(encoded.tokens)}
        self.assertEqual(encoded.tokens[indexes["2026-08-20"]].category, "day")
        self.assertEqual(encoded.tokens[indexes["14:30"]].category, "clock")
        self.assertEqual(encoded.tokens[indexes["90分钟"]].category, "duration")
        body = {
            "intent": "book",
            "day": {"month": 8, "day": 20},
            "start": {"period": "any", "time": "14:30"},
            "duration": {"minutes": 90},
            "room": "笔录室 1",
            "purpose": None,
            "refs": [],
            "evidence": {
                "day": [indexes["2026-08-20"]],
                "start": [indexes["14:30"]],
                "duration": [indexes["90分钟"]],
                "room": [indexes["笔录室1"]],
            },
            "unknown": [],
            "missing": [],
            "need_clarification": False,
        }
        parsed = parse_booking_intent(
            json.dumps(body, ensure_ascii=False),
            room_names=ROOMS,
            purpose_names=PURPOSES,
            tokens=encoded.tokens,
        )
        self.assertEqual(parsed.date.month, 8)
        self.assertEqual(parsed.date.day, 20)
        self.assertEqual(parsed.time_start, "14:30")
        self.assertEqual(parsed.duration_minutes, 90)
        body["evidence"]["duration"] = [indexes["14:30"]]
        body["evidence"]["start"] = [indexes["90分钟"]]
        with self.assertRaises(Exception):
            parse_booking_intent(
                json.dumps(body, ensure_ascii=False),
                room_names=ROOMS,
                purpose_names=PURPOSES,
                tokens=encoded.tokens,
            )

    def test_invalid_json_falls_back_and_audits_the_encoded_text(self) -> None:
        encoded = deidentify(RAW, CONTEXT)
        cases = [
            "不是 json",
            json.dumps(["book"]),
            json.dumps({**_intent_for(encoded), "notes": "孙某"}),
        ]
        for content in cases:
            provider = FakeProvider([content])
            audits: list[dict] = []
            result = interpret_booking_sentence(
                RAW,
                context=CONTEXT,
                settings=_settings(),
                provider=provider,
                now=NOW,
                work_start="08:30",
                work_end="17:30",
                room_names=ROOMS,
                record_audit=audits.append,
            )
            self.assertEqual(result.kind, "fallback")
            self.assertEqual(result.outcome, "invalid_json")
            self.assertEqual(audits[0]["sentText"], encoded.text)
            self.assertNotIn(PARTY, json.dumps(audits, ensure_ascii=False))

    def test_missing_key_and_timeout_do_not_send_the_raw_sentence(self) -> None:
        audits: list[dict] = []
        result = interpret_booking_sentence(
            RAW,
            context=CONTEXT,
            settings=_settings(),
            now=NOW,
            work_start="08:30",
            work_end="17:30",
            room_names=ROOMS,
            record_audit=audits.append,
            environ={},
        )
        self.assertEqual(result.outcome, "skipped_unconfigured")
        self.assertIsNone(result.sent_text)
        self.assertNotIn(PARTY, json.dumps(audits, ensure_ascii=False))
        provider = FakeProvider([ModelCallError("timeout")])
        result = interpret_booking_sentence(
            RAW,
            context=CONTEXT,
            settings=_settings(),
            provider=provider,
            now=NOW,
            work_start="08:30",
            work_end="17:30",
            room_names=ROOMS,
        )
        self.assertEqual(result.outcome, "timeout")
        self.assertNotIn(PARTY, provider.calls[0][1].content)
        self.assertEqual(result.text, FALLBACK_UNAVAILABLE)

    def test_intent_module_does_not_import_the_http_client(self) -> None:
        source = Path(interpret_booking_sentence.__code__.co_filename).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        self.assertNotIn("urllib.request", imported)
        self.assertNotIn("local_booking_intent", source)
        root = Path(__file__).resolve().parents[1] / "v2app"
        http_importers = sorted(
            path.name
            for path in root.rglob("*.py")
            if "urllib.request" in path.read_text(encoding="utf-8")
        )
        self.assertEqual(http_importers, ["model_provider.py", "update_check.py"])

    def test_overlong_text_is_not_sent(self) -> None:
        provider = FakeProvider(["{}"])
        result = interpret_booking_sentence(
            "明天" * 300,
            settings=_settings(),
            provider=provider,
            now=NOW,
            work_start="08:30",
            work_end="17:30",
        )
        self.assertEqual(provider.calls, [])
        self.assertEqual(result.outcome, "skipped_uncertain")
        self.assertEqual(result.text, FALLBACK_UNCERTAIN)


class ModelProviderTests(_NoNetwork):
    def test_https_request_carries_the_key_only_in_the_header(self) -> None:
        opener = _RecordingOpener(_json_body('{"ok":true}'))
        with mock.patch("v2app.services.model_provider._opener", return_value=opener):
            provider = DeepSeekProvider(
                api_key=KEY,
                base_url="https://api.deepseek.com",
                model="deepseek-chat",
                timeout_seconds=10,
            )
            content = provider.complete(
                (
                    ChatMessage("system", "只输出 json"),
                    ChatMessage("user", "明天下午 [P1]"),
                )
            )
        self.assertEqual(content, '{"ok":true}')
        request = opener.requests[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {KEY}")
        self.assertEqual(opener.timeout, 10)
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["temperature"], 0)
        self.assertNotIn(KEY, request.data.decode("utf-8"))
        self.assertNotIn(KEY, repr(provider))

    def test_v1_base_url_is_accepted_and_secrets_in_the_url_are_not(self) -> None:
        self.assertEqual(
            chat_completions_url("https://api.deepseek.com/v1"),
            "https://api.deepseek.com/v1/chat/completions",
        )
        for base in (
            "http://api.deepseek.com",
            f"https://{KEY}@api.deepseek.com/v1",
            f"https://api.deepseek.com/v1?api_key={KEY}",
            "https://api.deepseek.com/v1/chat/completions",
        ):
            with self.subTest(base=base):
                with self.assertRaises(ValueError):
                    chat_completions_url(base)

    def test_timeout_error_does_not_keep_the_key_or_url(self) -> None:
        def explode(_request, timeout=None):  # noqa: ARG001
            raise TimeoutError(f"timed out {KEY} https://api.deepseek.com")

        opener = _RecordingOpener(b"")
        opener.open = explode  # type: ignore[method-assign]
        with mock.patch("v2app.services.model_provider._opener", return_value=opener):
            provider = DeepSeekProvider(
                api_key=KEY,
                base_url="https://api.deepseek.com/v1",
                model="deepseek-chat",
                timeout_seconds=10,
            )
            with self.assertRaises(ModelCallError) as caught:
                provider.complete((ChatMessage("user", "明天下午"),))
        self.assertEqual(caught.exception.outcome, "timeout")
        self.assertNotIn(KEY, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_redirects_are_refused_and_proxies_are_disabled(self) -> None:
        from urllib.request import build_opener

        provider_module = __import__("v2app.services.model_provider", fromlist=["_opener"])
        built = provider_module._opener()
        handlers = list(built.handlers)
        self.assertTrue(any(isinstance(handler, HTTPRedirectHandler) for handler in handlers))
        with mock.patch("urllib.request.getproxies", return_value={"https": "http://127.0.0.1:9"}):
            default = build_opener()
            ours = provider_module._opener()
        self.assertTrue(
            any(
                isinstance(handler, ProxyHandler) and handler.proxies
                for handler in default.handlers
            )
        )
        self.assertFalse(
            any(isinstance(handler, ProxyHandler) and handler.proxies for handler in ours.handlers)
        )

    def test_key_file_and_environment_stay_server_side(self) -> None:
        file_key = "file-key-1234"
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / ".model_api_key").write_text(file_key + "\n", encoding="utf-8")
            self.assertEqual(read_model_api_key(data_dir=data_dir, environ={}), file_key)
            self.assertEqual(
                read_model_api_key(data_dir=data_dir, environ={MODEL_API_KEY_ENV: KEY}),
                KEY,
            )
            self.assertIsNone(
                open_configured_provider(
                    enabled=False,
                    provider_name="deepseek",
                    base_url="https://api.deepseek.com",
                    model="deepseek-chat",
                    timeout_seconds=10,
                    data_dir=data_dir,
                    environ={},
                )
            )
        self.assertIn(".model_api_key", (ROOT / ".gitignore").read_text(encoding="utf-8"))


class ModelAuditRouteTests(_NoNetwork, BackendTestCase):
    def test_audit_row_and_admin_api_omit_raw_text_and_the_key(self) -> None:
        self.setup_system()
        self.login()
        (self.data_dir / ".model_api_key").write_text(KEY, encoding="utf-8")
        masked = deidentify(RAW, CONTEXT)
        provider = FakeProvider([json.dumps(_intent_for(masked), ensure_ascii=False)])
        with self.app.app_context():
            actor_id = get_db().execute("SELECT id FROM users LIMIT 1").fetchone()[0]
            result = interpret_booking_sentence(
                RAW,
                context=CONTEXT,
                settings=_settings(),
                provider=provider,
                now=NOW,
                work_start="08:30",
                work_end="17:30",
                room_names=ROOMS,
                purpose_names=PURPOSES,
                record_audit=model_request_audit(get_db(), actor_user_id=actor_id),
                data_dir=self.data_dir,
                environ={MODEL_API_KEY_ENV: KEY},
            )
        self.assertEqual(result.kind, "intent", result.outcome)
        self.assertNotIn(PARTY, provider.calls[0][1].content)
        response = self.client.get("/api/v1/admin/audit?action=model.request&pageSize=20")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200, payload)
        blob = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(PARTY, blob)
        self.assertNotIn(CASE, blob)
        self.assertNotIn(RAW, blob)
        self.assertNotIn(KEY, blob)
        self.assertIn(masked.text, blob)
        item = payload["items"][0]
        self.assertEqual(item["details"]["outcome"], "ok")
        self.assertEqual(item["details"]["sentText"], masked.text)


class _RecordingOpener:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.requests = []
        self.timeout = None

    def open(self, request, timeout=None):
        self.requests.append(request)
        self.timeout = timeout
        return _Body(self.payload)


class _Body:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self, size: int) -> bytes:
        return self.payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False


def _json_body(content) -> bytes:
    if isinstance(content, str):
        payload = {"choices": [{"message": {"content": content}}]}
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return json.dumps(content).encode("utf-8")


if __name__ == "__main__":
    unittest.main()

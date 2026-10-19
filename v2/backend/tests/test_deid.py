"""出站编码器回归。

固定词表、38 条旧夹具的输入、以及随机拼接都要满足不变式 I1。
夹具不再逐字比对黑名单输出；敏感原文不能以非词表子串出现。
"""

from __future__ import annotations

import json
import random
import re
import socket
import unittest
from pathlib import Path
from unittest import mock

from tests.test_backend import BackendTestCase
from v2app.services.deid import (
    MAX_RELEASED_DIGITS,
    SURNAMES,
    DeidContext,
    Mention,
    OutboundBlocked,
    allowed_surface,
    deidentify,
    encode_preview,
    lexicon_categories,
    release_outbound,
    satisfies_outbound_invariant,
)
from v2app.services.deid import _NUM_TOKEN_RE


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "deid-cases.json"
V2_ROOT = Path(__file__).resolve().parents[2]
APPROVED_FEATURE = (
    "对话预约（默认关闭）：管理员开启后，用户可用一句话准备预约并一键确认。"
    "句子先在本机按预约词表白名单编码，只有词表词和占位符能离开本机；"
    "编码文本与日期、工作时段、房间名、事项名可发送至管理员配置的外部模型服务；"
    "模型只返回引用和证据，日期计算、空闲、权限、冲突与写入均由服务端完成并复用现有预约事务。"
    "对话记录按用户私有、本机保存、默认 30 天自动清除，管理员不可读取；每次外发的编码文本记录在安全审计。"
)
DESIGN_SENTENCE = (
    "This remains an operational appointment product. One restrained 「对话」 page is allowed "
    "as an alternative way to create a booking: a centered message column, user text in a soft "
    "tinted block, system replies as plain text, booking results as the existing quiet card/row "
    "vocabulary, and a bottom text composer (no voice input)."
)
RAIL_SENTENCE = (
    "Administrators see all permitted business entries (eight, plus 「对话」 when the conversation "
    "feature is enabled) and employees see the shared entries (five, plus 「对话」 when enabled); "
    "「对话」 is the first business entry and uses the Phosphor `ChatCircle` regular icon."
)
_LEAKS = (
    "明天和李明做笔录",
    "明天和李 明做笔录",
    "明天和李/明做笔录",
    "明天和李_/明做笔录",
    "明天 L i M i n g 做笔录",
    "明天和Иван做笔录",
    "明天和محمد做笔录",
    "明天和李月做笔录",
    "明天和明月华做笔录",
    "明天和彤做笔录",
    "联系电话 13812345678，明天下午",
    "联系电 话1/3/8/0/0/1/3/8/0/0/0",
    "电话幺三八洞洞幺三八洞洞洞",
    "电话１　３　８　０　０　１　３　８　０　０　０",
    "身份证 31010119900307123X 明天",
    "案号二〇二六沪零一零一民初一二三号",
    "案 号 二 〇 二 六 沪 零 一 零 一 民 初 一 二 三 号",
    "（2026）沪0101民初1234号明天",
    "案号２０２６－０８７，明天",
    "护照号 E12345678，明天",
    "邮箱 a@b.com，明天",
    "明天和赵倩做笔录",
    "周五下午开会",
    "1甲3乙8丙明天下午",
)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _context_from_fixture(payload: dict) -> DeidContext:
    raw = payload["context"]
    return DeidContext(
        case_numbers=tuple(raw["case_numbers"]),
        party_names=tuple(raw["party_names"]),
        directory_names=tuple(raw["directory_names"]),
        room_names=tuple(raw["room_names"]),
        purpose_names=("询问笔录", "谈话笔录"),
    )


def _assert_secret_hidden(
    test: unittest.TestCase, secret: str, encoded: str, allowed: set[str]
) -> None:
    if len(secret) < 2:
        return
    for size in range(2, len(secret) + 1):
        for start in range(0, len(secret) - size + 1):
            piece = secret[start : start + size]
            if piece in allowed or piece.isspace():
                continue
            test.assertNotIn(piece, encoded, piece)


class EncoderInvariantTests(unittest.TestCase):
    def test_lexicon_has_no_surname_singles_or_long_digit_words(self) -> None:
        lexicon = lexicon_categories()
        singles = {word for word in lexicon if len(word) == 1}
        self.assertFalse(singles & SURNAMES)
        for word in lexicon:
            self.assertIsNone(re.search(r"\d{3,}", word), word)

    def test_spec_example_keeps_only_lexicon_and_placeholders(self) -> None:
        context = DeidContext(room_names=("笔录室 2",), purpose_names=("询问笔录",))
        sentence = "后儿晚上给孙某做个笔录，(2024)沪0101民初1234号那个案子，笔录室2，半个钟头"
        result = deidentify(sentence, context)
        self.assertTrue(result.confident, result.reasons)
        self.assertTrue(satisfies_outbound_invariant(result.text, allowed_surface(context)))
        self.assertNotIn("孙某", result.text)
        self.assertNotIn("2024", result.text)
        self.assertNotIn("1234", result.text)
        self.assertIn("后儿", result.text)
        self.assertIn("晚上", result.text)
        self.assertIn("半个钟头", result.text)
        self.assertIn("笔录室2", result.text)
        self.assertEqual(len(set(result.mapping)), len(result.mapping))
        again = deidentify(sentence, context)
        self.assertEqual(again.text, result.text)
        self.assertEqual(again.mapping, result.mapping)

    def test_mentions_restore_canonical_values_and_do_not_send_them(self) -> None:
        sentence = "后儿晚上给 @孙某 做个笔录，#(2024)沪0101民初1234号 那个案子"
        mentions = (
            Mention(
                start=sentence.index("孙某"),
                end=sentence.index("孙某") + 2,
                kind="party",
                value="孙某",
            ),
            Mention(
                start=sentence.index("(2024)"),
                end=sentence.index("号") + 1,
                kind="case",
                value="(2024)沪0101民初1234号",
            ),
        )
        result = deidentify(sentence, mentions=mentions)
        self.assertTrue(result.confident, result.reasons)
        self.assertNotIn("孙某", result.text)
        self.assertNotIn("2024", result.text)
        self.assertNotIn("@", result.text)
        self.assertNotIn("#", result.text)
        self.assertEqual(set(result.mention_roles.values()), {"party", "case"})
        self.assertIn("孙某", result.mapping.values())
        self.assertIn("(2024)沪0101民初1234号", result.mapping.values())

    def test_same_span_reuses_one_placeholder_across_a_prior_map(self) -> None:
        context = DeidContext(party_names=("孙某",))
        first = deidentify("明天约孙某", context)
        second = deidentify("后天还是孙某", context, prior=first.mapping)
        self.assertEqual(set(first.mapping.values()), {"孙某"})
        self.assertEqual(second.mapping, first.mapping)
        self.assertIn(next(iter(first.mapping)), second.text)

    def test_three_digit_runs_and_digit_cap_stay_masked(self) -> None:
        context = DeidContext()
        long = deidentify("明天 1234号 笔录", context)
        self.assertNotIn("1234", long.text)
        self.assertNotIn("号", long.text.replace("[P1]", "").replace("[P2]", ""))
        for token in long.text.split(" "):
            if token.startswith("[P"):
                continue
            self.assertIsNone(re.search(r"\d{3,}", token), token)
        capped = deidentify("1点2点3点4点5点6点7点8点9点", context)
        released = sum(
            sum(char.isdigit() for char in token)
            for token in capped.text.split(" ")
            if not token.startswith("[P")
        )
        self.assertLessEqual(released, MAX_RELEASED_DIGITS)
        self.assertNotIn("9点", capped.text)
        self.assertTrue(satisfies_outbound_invariant(capped.text, allowed_surface(context)))

    def test_clock_numbers_next_to_units_are_released(self) -> None:
        result = deidentify("明天10月20日14点30分，笔录室1", DeidContext(room_names=("笔录室 1",)))
        self.assertIn("10月", result.text)
        self.assertIn("20日", result.text)
        self.assertIn("14点", result.text)
        self.assertIn("30分", result.text)
        self.assertIn("笔录室1", result.text)
        self.assertIn(
            "1号笔录室", deidentify("明天1号笔录室", DeidContext(room_names=("笔录室 1",))).text
        )

    def test_gate_blocks_a_forged_name_and_accepts_encoder_output(self) -> None:
        allowed = allowed_surface(DeidContext(room_names=("笔录室 1",)))
        encoded = deidentify("明天下午孙某", DeidContext()).text
        self.assertEqual(release_outbound(encoded, allowed), encoded)
        with self.assertRaises(OutboundBlocked):
            release_outbound("明天下午 孙某", allowed)
        with self.assertRaises(OutboundBlocked):
            release_outbound("明天 13812345678", allowed)

    def test_preview_uses_the_same_text_and_hides_mapping_values(self) -> None:
        sentence = "明天约孙某做笔录"
        preview = encode_preview(sentence, DeidContext(party_names=("孙某",)))
        encoded = deidentify(sentence, DeidContext(party_names=("孙某",)))
        self.assertEqual(preview["text"], encoded.text)
        self.assertGreaterEqual(preview["localCount"], 1)
        self.assertNotIn("孙某", json.dumps(preview, ensure_ascii=False))

    def test_property_random_mixtures_satisfy_the_invariant(self) -> None:
        context = DeidContext(
            party_names=("孙某", "王芳"),
            case_numbers=("2026-087",),
            directory_names=("李明", "彤"),
            room_names=("笔录室 1", "笔录室 2"),
            purpose_names=("询问笔录",),
        )
        allowed = allowed_surface(context)
        lexicon = list(lexicon_categories())
        noise = list(
            "孙某王芳李明赵倩张伟周强高明"
            "甲乙丙丁戊己庚辛"
            "0123456789０１２３"
            "@#/\\-_.·（）()［］"
            "😀\u200b\u200c\ufeff"
            "Иванمحمد"
        )
        rng = random.Random(20261019)
        for _ in range(100_000):
            parts = []
            for _piece in range(rng.randint(1, 8)):
                parts.append(rng.choice(lexicon) if rng.random() < 0.45 else rng.choice(noise))
            text = "".join(parts)[:500]
            result = deidentify(text, context)
            if not result.confident:
                self.assertIn(result.reasons[0], {"empty", "source_too_long", "invariant"})
                continue
            self.assertTrue(satisfies_outbound_invariant(result.text, allowed), result.text)
            self.assertEqual(release_outbound(result.text, allowed), result.text)
            for secret in ("孙某", "王芳", "李明", "赵倩", "2026-087", "13800138000"):
                if secret in text:
                    _assert_secret_hidden(self, secret, result.text, allowed)

    def test_previous_leak_repros_and_canaries_stay_inside(self) -> None:
        context = DeidContext(
            party_names=("孙某", "彤"),
            directory_names=("李明",),
            case_numbers=("2026-087", "（2025）沪0101民初5521号"),
            room_names=("笔录室 1",),
        )
        allowed = allowed_surface(context)
        secrets = (
            "李明",
            "孙某",
            "赵倩",
            "明月华",
            "李月",
            "13812345678",
            "31010119900307123X",
            "E12345678",
        )
        for text in _LEAKS:
            result = deidentify(text, context)
            self.assertTrue(result.confident, (text, result.reasons, result.text))
            self.assertTrue(satisfies_outbound_invariant(result.text, allowed), (text, result.text))
            for secret in secrets:
                if secret in text or secret.replace(" ", "") in text.replace(" ", ""):
                    _assert_secret_hidden(self, secret, result.text, set(allowed))
            for original in result.mapping.values():
                if len(original) >= 2 and original not in allowed:
                    self.assertNotIn(original, result.text, text)


class DeidFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = _fixture()
        cls.context = _context_from_fixture(cls.payload)

    def test_fixture_inputs_remain_canaries(self) -> None:
        self.assertEqual(len(self.payload["cases"]), 38)
        allowed = set(allowed_surface(self.context))
        secrets = (
            *self.payload["context"]["case_numbers"],
            *self.payload["context"]["party_names"],
            *self.payload["context"]["directory_names"],
        )
        for case in self.payload["cases"]:
            result = deidentify(case["input"], self.context)
            self.assertTrue(result.confident, (case["id"], result.reasons, result.text))
            self.assertTrue(
                satisfies_outbound_invariant(result.text, allowed),
                (case["id"], result.text),
            )
            for secret in secrets:
                if secret and secret in case["input"]:
                    _assert_secret_hidden(self, secret, result.text, allowed)
            for needle in case.get("mustNotContain", []):
                if needle in allowed or (needle.isdigit() and len(needle) <= 2):
                    continue
                tokens = result.text.split(" ")
                if any(
                    needle in token and (token in allowed or _NUM_TOKEN_RE.fullmatch(token))
                    for token in tokens
                ):
                    continue
                self.assertNotIn(needle, result.text, case["id"])


class EncoderAppTests(BackendTestCase):
    def test_active_room_variants_come_from_the_database(self) -> None:
        self.setup_system()
        self.login()
        rooms = self.client.get("/api/v1/rooms").get_json()["items"]
        active = tuple(room["name"] for room in rooms if room["isActive"])
        context = DeidContext(room_names=active)
        result = deidentify("明天去笔录室2，不要410室", context)
        self.assertIn("笔录室2", result.text)
        self.assertNotIn("410", result.text)
        self.assertTrue(result.confident, result.reasons)

    def test_encoder_does_not_open_a_socket(self) -> None:
        with mock.patch.object(
            socket, "create_connection", side_effect=AssertionError("编码器不应联网")
        ):
            result = deidentify("明天下午找个笔录室，一小时", DeidContext(room_names=("笔录室 1",)))
        self.assertTrue(result.confident)


class DeidContractAmendmentTests(unittest.TestCase):
    def test_approved_v26_boundaries_are_written_down(self) -> None:
        product = (V2_ROOT / "docs" / "PRODUCT-CONTRACT.md").read_text(encoding="utf-8")
        design = (V2_ROOT / "frontend" / "DESIGN-CONTRACT.md").read_text(encoding="utf-8")
        api = (V2_ROOT / "docs" / "API-CONTRACT.md").read_text(encoding="utf-8")
        architecture = (V2_ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")

        self.assertNotIn("- AI 功能；", product)
        self.assertIn(APPROVED_FEATURE, product)
        self.assertIn("当前仓库发布版本仍是", product)
        self.assertIn("V2.5.1", product)
        self.assertIn("第二个出站连接", product)
        self.assertIn("第一期没有语音", product)
        self.assertNotIn("唯一例外", product)

        self.assertIn(DESIGN_SENTENCE, design)
        self.assertIn(RAIL_SENTENCE, design)
        self.assertNotIn("non-AI operational appointment product", design)
        self.assertIn("no voice input", design)

        self.assertIn("本增量未暴露接口", api)
        self.assertIn("confident=false", api)
        self.assertIn("第一期没有语音", api)
        self.assertIn("services/deid.py", architecture)
        self.assertIn("不联网", architecture)
        self.assertIn("第一期没有语音", architecture)
        self.assertIn("白名单", architecture)
        self.assertIn("S1", architecture)


if __name__ == "__main__":
    unittest.main()

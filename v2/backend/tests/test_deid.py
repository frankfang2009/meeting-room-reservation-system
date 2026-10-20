"""出站编码器回归。

固定词表、38 条旧夹具的输入、以及随机拼接都要满足不变式 I1。
夹具不再逐字比对黑名单输出；敏感原文不能以非词表子串出现。
"""

from __future__ import annotations

import json
import math
import random
import re
import socket
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from tests.test_backend import BackendTestCase
from v2app.services.deid import (
    MAX_RELEASED_DIGITS,
    PURPOSE_CATEGORIES,
    SURNAMES,
    DeidContext,
    Mention,
    OutboundBlocked,
    allowed_surface,
    deidentify,
    encode_preview,
    lexicon_categories,
    is_grammar_token,
    numeric_capacity_bits,
    release_outbound,
    released_number_digits,
    satisfies_outbound_invariant,
    validate_server_context,
)


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


def _looks_like_clock(token: str) -> bool:
    return ":" in token


def _looks_like_date(token: str) -> bool:
    return "-" in token or "/" in token


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

    def test_colon_clocks_and_dates_are_structured_tokens(self) -> None:
        context = DeidContext(room_names=("笔录室 1",))
        allowed = allowed_surface(context)
        clock = deidentify("明天14:00到15:30，笔录室1", context)
        self.assertIn("14:00", clock.text)
        self.assertIn("15:30", clock.text)
        self.assertEqual(
            [token.category for token in clock.tokens if token.text == "14:00"],
            ["clock"],
        )
        wide = deidentify("明天１４：３０笔录室1", context)
        self.assertIn("14:30", wide.text)
        self.assertNotIn("１４", wide.text)
        dated = deidentify("2026-08-20 09:30笔录室1", context)
        self.assertIn("2026-08-20", dated.text)
        self.assertIn("09:30", dated.text)
        self.assertEqual(
            [token.category for token in dated.tokens if token.text == "2026-08-20"],
            ["day"],
        )
        self.assertIn("10/28", deidentify("10/28上午", context).text)
        self.assertIn("10-28", deidentify("10-28上午", context).text)
        self.assertIn("10月", deidentify("10月28日", context).text)
        self.assertIn("28日", deidentify("10月28日", context).text)
        for result in (clock, wide, dated):
            self.assertTrue(satisfies_outbound_invariant(result.text, allowed), result.text)

    def test_invalid_clocks_and_extra_times_stay_masked(self) -> None:
        context = DeidContext()
        allowed = allowed_surface(context)
        smuggled = deidentify("13:81 23:45 6:7", context)
        self.assertIn("23:45", smuggled.text)
        self.assertNotIn("13:81", smuggled.text)
        self.assertNotIn("6:7", smuggled.text)
        self.assertTrue(satisfies_outbound_invariant(smuggled.text, allowed))
        with self.assertRaises(OutboundBlocked):
            release_outbound("13:81 23:45 6:7", allowed)
        capped = deidentify("08:30 09:00 10:15 11:20", context)
        released_clocks = [token for token in capped.text.split(" ") if ":" in token]
        self.assertEqual(released_clocks, ["08:30", "09:00"])
        self.assertNotIn("10:15", capped.text)
        self.assertNotIn("11:20", capped.text)
        both = deidentify("2026-10-28 14:00 15:30", context)
        self.assertIn("2026-10-28", both.text)
        self.assertIn("14:00", both.text)
        self.assertIn("15:30", both.text)
        self.assertNotIn("14:00:30", deidentify("14:00:30", context).text)
        self.assertNotIn("10/28", deidentify("10/28/2026", context).text)
        self.assertNotIn("1123", deidentify("2026-1123 那个", context).text)
        self.assertNotIn("64321234", deidentify("021-64321234", context).text)
        with self.assertRaises(OutboundBlocked):
            release_outbound("08:30 09:00 10:00", allowed)
        self.assertEqual(
            release_outbound("2026-10-28 14:00", allowed),
            "2026-10-28 14:00",
        )

    def test_durations_up_to_the_business_max_are_released(self) -> None:
        context = DeidContext()
        allowed = allowed_surface(context)
        samples = {
            "90分钟": "duration",
            "120分钟": "duration",
            "180分钟": "duration",
            "1.5小时": "duration",
            "１．５小时": "duration",
            "一个半小时": "clock",
            "两个小时": "clock",
            "两个半小时": "clock",
        }
        for text, category in samples.items():
            result = deidentify(f"明天上午{text}", context)
            self.assertIn(text.replace("１．５小时", "1.5小时"), result.text, text)
            found = [token for token in result.tokens if token.text in {text, "1.5小时"}]
            self.assertTrue(found, result.text)
            self.assertEqual(found[0].category, category, text)
            self.assertTrue(satisfies_outbound_invariant(result.text, allowed), result.text)
        for blocked in ("200分钟", "181分钟", "90点", "1234号", "3.5小时", "999分钟"):
            result = deidentify(f"明天{blocked}", context)
            self.assertNotIn(blocked, result.text, blocked)
            self.assertTrue(result.confident, result.reasons)
        self.assertNotIn("5小时", deidentify("3.5小时", context).text)
        self.assertEqual(release_outbound("90分钟", allowed), "90分钟")
        self.assertEqual(release_outbound("120分钟", allowed), "120分钟")
        self.assertEqual(release_outbound("1.5小时", allowed), "1.5小时")
        with self.assertRaises(OutboundBlocked):
            release_outbound("200分钟", allowed)
        shared = deidentify("2026-10-28 1点2点3点4点", context)
        self.assertIn("2026-10-28", shared.text)
        self.assertIn("1点", shared.text)
        self.assertIn("2点", shared.text)
        self.assertNotIn("4点", shared.text)
        self.assertLessEqual(
            sum(char.isdigit() for char in released_number_digits(shared.text, allowed)),
            MAX_RELEASED_DIGITS,
        )

    def test_separator_splits_cannot_smuggle_a_phone(self) -> None:
        context = DeidContext()
        allowed = allowed_surface(context)
        rng = random.Random(20261019)
        phones = ("13812345678", "13900001111", "02164321234")
        separators = (":", "/", "-", "")
        for _ in range(10_000):
            phone = rng.choice(phones)
            pieces: list[str] = []
            cursor = 0
            while cursor < len(phone):
                take = rng.randint(1, 4)
                pieces.append(phone[cursor : cursor + take])
                cursor += take
                if cursor < len(phone):
                    pieces.append(rng.choice(separators))
            text = "明天" + "".join(pieces)
            result = deidentify(text, context)
            self.assertTrue(result.confident, (text, result.reasons))
            self.assertTrue(satisfies_outbound_invariant(result.text, allowed), (text, result.text))
            self.assertNotIn(phone, result.text, text)
            clocks = [token.text for token in result.tokens if _looks_like_clock(token.text)]
            dates = [token.text for token in result.tokens if _looks_like_date(token.text)]
            self.assertLessEqual(len(clocks), 2, text)
            self.assertLessEqual(len(dates), 1, text)
            structured = sum(sum(char.isdigit() for char in token) for token in clocks + dates)
            self.assertLessEqual(structured, MAX_RELEASED_DIGITS, text)
            self.assertNotIn(phone, released_number_digits(result.text, allowed), text)

    def test_mixed_script_numbers_cannot_rebuild_a_long_secret(self) -> None:
        """Codex round 6：电话不能拆成钟点、点号和中文数字再拼回去。"""

        context = DeidContext(room_names=("笔录室 1", "笔录室 2"))
        allowed = allowed_surface(context)
        repros = (
            ("13:38 12:34 5点6点7点", "13381234567"),
            ("一号三号八号一号二号三号四号五号六号七号八号", "13812345678"),
            ("１３：３８　１２：３４　５点６点７点", "13381234567"),
        )
        for text, secret in repros:
            result = deidentify(text, context)
            self.assertTrue(result.confident, (text, result.reasons))
            digits = released_number_digits(result.text, allowed)
            self.assertNotIn(secret, digits, (text, result.text, digits))
            self.assertLess(len(digits), len(secret), (text, digits))
            self.assertTrue(satisfies_outbound_invariant(result.text, allowed), result.text)
        self._assert_capacity_is_a_fragment_budget(context, allowed)
        cn = "零一二三四五六七八九"
        spoken = str.maketrans("0123456789", "洞幺两三四五六七八九")
        rng = random.Random(20261020)
        for _ in range(10_000):
            width = rng.randint(7, 18)
            secret = "".join(rng.choice("0123456789") for _ in range(width))
            if secret[0] == "0":
                secret = "1" + secret[1:]
            mode = rng.randrange(6)
            if mode == 0:
                text = ":".join(secret[i : i + 2] for i in range(0, len(secret), 2))
            elif mode == 1:
                text = "".join(f"{digit}点" for digit in secret)
            elif mode == 2:
                text = "".join(f"{cn[int(digit)]}号" for digit in secret)
            elif mode == 3:
                text = "/".join(secret[i : i + 2] for i in range(0, len(secret), 2))
            elif mode == 4:
                text = secret.translate(spoken)
            else:
                text = "".join(
                    digit.translate(str.maketrans("0123456789", "０１２３４５６７８９"))
                    + rng.choice(("点", "号", "分", ":", "-", "/"))
                    for digit in secret
                )
            result = deidentify("明天" + text, context)
            self.assertTrue(result.confident, (text, result.reasons))
            digits = released_number_digits(result.text, allowed)
            self.assertNotIn(secret, digits, (text, result.text, digits))
            self.assertTrue(satisfies_outbound_invariant(result.text, allowed), (text, result.text))

    def _assert_capacity_is_a_fragment_budget(self, context: DeidContext, allowed: set[str]) -> None:
        """一句话的数字文法有上界；下一句和词表选词不在这笔里。"""

        iso_dates = 0
        year = date.today().year
        for candidate in (year, year + 1):
            for month in range(1, 13):
                for day in range(1, 32):
                    try:
                        date(candidate, month, day)
                    except ValueError:
                        continue
                    iso_dates += 1
        expected = (
            math.log2(max(iso_dates, 12 * 31))
            + 2 * math.log2(24 * 4)
            + math.log2(12)
        )
        self.assertAlmostEqual(numeric_capacity_bits(), expected, places=6)
        first = deidentify("2026-08-20 10:15", context)
        second = deidentify("2026-08-21 11:30", context)
        combined = released_number_digits(first.text, allowed) + released_number_digits(
            second.text, allowed
        )
        self.assertGreater(len(combined), MAX_RELEASED_DIGITS)
        self.assertLessEqual(
            len(released_number_digits(first.text, allowed)), MAX_RELEASED_DIGITS
        )
        lexicon = deidentify(" ".join(("明天", "下午", "笔录") * 40), context)
        self.assertNotIn("[P", lexicon.text)
        self.assertEqual(released_number_digits(lexicon.text, allowed), "")
        smuggle = deidentify("5678-7-18 明天 6:45 到 11:00 15分钟", context)
        self.assertNotIn("5678", smuggle.text)
        self.assertNotIn("13812345678", released_number_digits(smuggle.text, allowed))

    def test_case_number_tails_stay_inside_the_placeholder(self) -> None:
        context = DeidContext()
        folded = deidentify("(2026)京0105民初3号", context)
        self.assertEqual(folded.text, "[P1]")
        self.assertNotIn("3号", folded.text)
        case_tail = deidentify("案号12号", context)
        self.assertNotIn("12号", case_tail.text)
        self.assertIn("案号", case_tail.text)
        kept = deidentify("明天3号", context)
        self.assertIn("3号", kept.text)
        month_day = deidentify("10月20日", context)
        self.assertIn("10月", month_day.text)
        self.assertIn("20日", month_day.text)

    def test_iso_years_must_be_current_or_next_and_real(self) -> None:
        context = DeidContext()
        for text in ("1990-1-1", "1990-01-01", "2026-02-31", "1381-2-3", "5678-7-18"):
            result = deidentify(text, context)
            self.assertNotIn(text.replace(" ", ""), result.text.replace(" ", ""), text)
            self.assertIn("[P1]", result.text, text)
        kept = deidentify("2026-08-20", context)
        self.assertIn("2026-08-20", kept.text)
        next_year = deidentify("2027-01-15", context)
        self.assertIn("2027-01-15", next_year.text)

    def test_value_annotations_cover_released_time_tokens(self) -> None:
        context = DeidContext()
        result = deidentify("明天十点一小时14:15下周四", context)
        by_text = {token.text: token.values for token in result.tokens}
        self.assertEqual(by_text["明天"], ("offset:1",))
        self.assertEqual(by_text["十点"], ("time:10:00", "time:22:00"))
        self.assertEqual(by_text["一小时"], ("minutes:60",))
        self.assertEqual(by_text["14:15"], ("time:14:15",))
        self.assertEqual(by_text["下周四"], ("week_offset:1", "weekday:4"))
        compound = deidentify("明早十点明晚", context)
        compound_values = {token.text: token.values for token in compound.tokens}
        self.assertEqual(compound_values["明早"], ("offset:1", "period:morning"))
        self.assertEqual(compound_values["明晚"], ("offset:1", "period:evening"))
        minute = deidentify("14点30分", context)
        minute_values = {token.text: token.values for token in minute.tokens}
        self.assertEqual(minute_values["14点"], ("time:14:00",))
        self.assertEqual(minute_values["30分"], ("minute:30",))

    def test_unicode_digits_do_not_raise_or_echo(self) -> None:
        context = DeidContext(room_names=("笔录室 1",))
        samples = ("明天١٢٣٤٥", "٢٠٢٦-٠٨-٢٠", "３点", "案号１２号")
        for text in samples:
            result = deidentify(text, context)
            self.assertTrue(result.confident, (text, result.reasons))
            self.assertNotIn("١٢٣", result.text)

    def test_preview_returns_the_dynamic_outbound_payload(self) -> None:
        context = DeidContext(room_names=("笔录室 1",), purpose_names=PURPOSE_CATEGORIES)
        sentence = "明天14:00笔录室1一小时"
        server = "今天是2026-08-09（星期日），当前时间08:00。可用房间：笔录室 1。事项：询问笔录、谈话。"
        preview = encode_preview(sentence, context, server_context=server)
        self.assertEqual(preview["text"], deidentify(sentence, context).text)
        self.assertIn(preview["text"], preview["outbound"])
        self.assertIn("询问笔录", preview["outbound"])
        self.assertEqual(validate_server_context(server), server)
        leaked = "今天是2026-08-09（星期日），当前时间08:00。可用房间：笔录室 1。事项：询问孙某，电话13812345678。"
        blocked = encode_preview(sentence, context, server_context=leaked)
        self.assertFalse(blocked["confident"])
        self.assertNotIn("13812345678", blocked["outbound"])

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
                    needle in token and (token in allowed or is_grammar_token(token))
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

        self.assertIn("FEATURE_DISABLED", api)
        self.assertIn("对话预约未开启", api)
        self.assertIn("不采用模型的 `purpose_hint`", api)
        self.assertIn("confident=false", api)
        self.assertIn("第一期没有语音", api)
        self.assertIn("services/deid.py", architecture)
        self.assertIn("不联网", architecture)
        self.assertIn("第一期没有语音", architecture)
        self.assertIn("白名单", architecture)
        self.assertIn("S1", architecture)


if __name__ == "__main__":
    unittest.main()

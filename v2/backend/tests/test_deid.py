"""本机脱敏网关回归。

38 条设计夹具是验收基线。另外锁定：原文里的敏感值不能留在脱敏结果里，
房间名白名单必须来自调用方交来的真实房间记录，拿不准时不得外发。
"""

from __future__ import annotations

import ast
import json
import re
import socket
import unittest
from pathlib import Path
from unittest import mock

from tests.test_backend import BackendTestCase
from v2app.common import mask_party_name
from v2app.services.deid import DeidContext, deidentify


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "deid-cases.json"
_SPOKEN_DIGITS = str.maketrans(
    {
        "〇": "0",
        "零": "0",
        "洞": "0",
        "一": "1",
        "幺": "1",
        "壹": "1",
        "二": "2",
        "两": "2",
        "贰": "2",
        "貳": "2",
        "三": "3",
        "叁": "3",
        "參": "3",
        "四": "4",
        "肆": "4",
        "五": "5",
        "伍": "5",
        "六": "6",
        "陆": "6",
        "陸": "6",
        "七": "7",
        "柒": "7",
        "八": "8",
        "捌": "8",
        "九": "9",
        "玖": "9",
    }
)


def _digits_of(text: str) -> str:
    return "".join(char for char in text.translate(_SPOKEN_DIGITS) if char.isdigit())


def _assert_no_secret(test: unittest.TestCase, text: str, secret: str) -> None:
    folded = text.casefold()
    test.assertNotIn(secret.casefold(), folded)
    compact_text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", folded)
    compact_secret = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", secret.casefold())
    if len(compact_secret) >= 2:
        test.assertNotIn(compact_secret, compact_text)
    secret_digits = _digits_of(secret)
    if len(secret_digits) >= 3:
        test.assertNotIn(secret_digits, _digits_of(text))


V2_ROOT = Path(__file__).resolve().parents[2]
APPROVED_FEATURE = (
    "对话预约（默认关闭）：管理员开启后，用户可用一句话准备预约并一键确认。"
    "句子先在本机脱敏，仅脱敏文本与日期、工作时段、房间名上下文可发送至管理员配置的外部模型服务；"
    "模型输出只允许固定意图 JSON，日期解析、空闲计算、权限、冲突与写入均由服务端完成并复用现有预约事务。"
    "对话记录按用户私有、本机保存、默认 30 天自动清除，管理员不可读取；每次外发的脱敏文本记录在安全审计。"
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
_PHONE_RE = re.compile(r"(?:\+?86[-\s]?)?1[3-9]\d{9}|0\d{2,3}-?\d{7,8}")
_ID_RE = re.compile(r"\d{17}[\dXx]|\d{15}")


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _context_from_fixture(payload: dict) -> DeidContext:
    raw = payload["context"]
    return DeidContext(
        case_numbers=tuple(raw["case_numbers"]),
        party_names=tuple(raw["party_names"]),
        directory_names=tuple(raw["directory_names"]),
        room_names=tuple(raw["room_names"]),
    )


class DeidFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = _fixture()
        cls.context = _context_from_fixture(cls.payload)

    def test_fixture_has_the_approved_thirty_eight_cases(self) -> None:
        self.assertEqual(len(self.payload["cases"]), 38)

    def test_every_fixture_case_masks_without_leaking(self) -> None:
        secrets = (
            *self.payload["context"]["case_numbers"],
            *self.payload["context"]["party_names"],
            *self.payload["context"]["directory_names"],
        )
        for case in self.payload["cases"]:
            with self.subTest(case=case["id"]):
                result = deidentify(case["input"], self.context)
                self.assertEqual(result.text, case["expected"])
                self.assertTrue(result.confident, (case["id"], result.reasons, result.text))
                self.assertNotIn("unknown_digit_run", result.reasons)
                for needle in case.get("mustNotContain", []):
                    self.assertNotIn(needle, result.text)
                for secret in secrets:
                    if secret and secret in case["input"]:
                        self.assertNotIn(secret, result.text)
                for pattern in (_PHONE_RE, _ID_RE):
                    for found in pattern.findall(case["input"]):
                        self.assertNotIn(found, result.text)
                for original in result.mapping.values():
                    self.assertNotIn(original, result.text)
                    self.assertTrue(original)

    def test_exact_case_mapping_restores_the_caller_supplied_value(self) -> None:
        result = deidentify("（2025）沪0101民初5521号明天要用笔录室 2", self.context)
        self.assertEqual(result.mapping, {"[案号1]": "（2025）沪0101民初5521号"})

    def test_same_value_reuses_one_placeholder(self) -> None:
        result = deidentify("案号2026-087，再约一次2026-087", self.context)
        self.assertEqual(result.mapping, {"[案号1]": "2026-087"})
        self.assertEqual(result.text.count("[案号1]"), 2)


class DeidRuleTests(unittest.TestCase):
    def test_unknown_personal_name_is_masked_and_not_sent(self) -> None:
        result = deidentify("明天下午和赵倩一起做笔录", DeidContext(room_names=("笔录室 1",)))
        self.assertFalse(result.confident)
        self.assertIn("unknown_name", result.reasons)
        self.assertNotIn("赵倩", result.text)
        self.assertIn("[当事人1]", result.text)
        self.assertIn("明天下午", result.text)

    def test_compound_surname_outside_the_lists_is_not_sent(self) -> None:
        result = deidentify("请约欧阳娜，明天下午", DeidContext())
        self.assertFalse(result.confident)
        self.assertNotIn("欧阳娜", result.text)
        self.assertEqual(result.mapping["[当事人1]"], "欧阳娜")

    def test_title_pattern_is_masked_and_still_sendable(self) -> None:
        result = deidentify("赵女士明天下午", DeidContext())
        self.assertEqual(result.text, "[当事人1]明天下午")
        self.assertTrue(result.confident)
        self.assertEqual(result.mapping["[当事人1]"], "赵女士")

    def test_xiao_prefix_masks_a_surname_but_not_duration(self) -> None:
        masked = deidentify("小王明天下午", DeidContext())
        self.assertEqual(masked.text, "[当事人1]明天下午")
        self.assertTrue(masked.confident)
        kept = deidentify("明天上午 90 分钟，一小时", DeidContext())
        self.assertEqual(kept.text, "明天上午 90 分钟，一小时")
        self.assertTrue(kept.confident)

    def test_overlapping_party_and_directory_name_stays_on_the_party_side(self) -> None:
        result = deidentify(
            "李明明天下午",
            DeidContext(party_names=("李明",), directory_names=("李明", "周宁")),
        )
        self.assertEqual(result.text, "[当事人1]明天下午")
        self.assertNotIn("[同事", result.text)
        self.assertIn("ambiguous_person", result.reasons)
        self.assertTrue(result.confident)
        self.assertEqual(result.mapping["[当事人1]"], "李明")

    def test_masked_party_form_maps_back_to_the_original_name(self) -> None:
        original = "张晓燕"
        masked = mask_party_name(original)
        self.assertEqual(masked, "张*燕")
        result = deidentify(f"还是{masked}那个案子，明天", DeidContext(party_names=(original,)))
        self.assertEqual(result.text, "还是[当事人1]那个案子，明天")
        self.assertEqual(result.mapping["[当事人1]"], original)
        self.assertNotIn(original, result.text)
        self.assertNotIn(masked, result.text)
        self.assertTrue(result.confident)

    def test_case_number_not_in_context_is_still_masked(self) -> None:
        supplied = deidentify("约甲乙丙丁号外，明天", DeidContext(case_numbers=("甲乙丙丁号外",)))
        self.assertIn("[案号1]", supplied.text)
        self.assertNotIn("甲乙丙丁号外", supplied.text)
        omitted = deidentify("约甲乙丙丁号外，明天", DeidContext())
        self.assertNotIn("甲乙丙丁号外", omitted.text)
        self.assertFalse(omitted.confident)

    def test_email_marker_blocks_sending_and_drops_the_address(self) -> None:
        result = deidentify("邮箱 a@b.com，明天", DeidContext())
        self.assertFalse(result.confident)
        self.assertIn("email_marker", result.reasons)
        self.assertNotIn("a@b.com", result.text)
        self.assertNotIn("@", result.text)

    def test_document_keyword_with_leftover_text_is_not_sent(self) -> None:
        result = deidentify("护照放在抽屉里，明天", DeidContext())
        self.assertFalse(result.confident)
        self.assertIn("document_keyword", result.reasons)
        self.assertNotIn("抽屉", result.text)
        self.assertTrue(result.text.startswith("护照"))

    def test_address_digits_do_not_survive_when_the_keyword_is_uncertain(self) -> None:
        result = deidentify("住址在幸福路18弄，明天下午", DeidContext())
        self.assertFalse(result.confident)
        self.assertNotIn("幸福路", result.text)
        self.assertNotIn("18", result.text)

    def test_fully_masked_passport_number_does_not_leak(self) -> None:
        result = deidentify("护照号 E12345678，明天", DeidContext())
        self.assertNotIn("E12345678", result.text)
        self.assertNotIn("12345678", result.text)
        self.assertIn("[编号1]", result.text)

    def test_too_many_replacements_block_sending(self) -> None:
        numbers = [f"1380000000{index}" for index in range(7)]
        result = deidentify(" ".join(numbers), DeidContext())
        self.assertFalse(result.confident)
        self.assertIn("too_many_replacements", result.reasons)
        for number in numbers:
            self.assertNotIn(number, result.text)

    def test_overlong_source_is_not_sent(self) -> None:
        result = deidentify("明天" * 251, DeidContext())
        self.assertGreater(len("明天" * 251), 500)
        self.assertFalse(result.confident)
        self.assertIn("source_too_long", result.reasons)

    def test_overlong_masked_text_is_not_sent(self) -> None:
        text = "明天上午" * 60
        result = deidentify(text, DeidContext())
        self.assertLessEqual(len(text), 500)
        self.assertGreater(len(result.text), 200)
        self.assertFalse(result.confident)
        self.assertIn("masked_too_long", result.reasons)

    def test_spaced_known_name_variants_do_not_leak(self) -> None:
        context = DeidContext(directory_names=("李明",))
        samples = (
            "李 明明天下午",
            "李\u3000明明天下午",
            "李\u200b明明天下午",
            "李\u00a0明明天下午",
            "李·明明天下午",
            "李\u2060明明天下午",
        )
        for raw in samples:
            with self.subTest(raw=raw):
                result = deidentify(raw, context)
                self.assertTrue(result.confident, (result.reasons, result.text))
                self.assertIn("[同事1]", result.text)
                self.assertNotIn("李", result.text)
                self.assertNotRegex(result.text, r"李\s*明")
                self.assertEqual(result.mapping["[同事1]"], "李明")

    def test_unknown_latin_and_uyghur_names_are_not_sent(self) -> None:
        latin = deidentify("明天下午 Alice Smith 做笔录", DeidContext())
        self.assertFalse(latin.confident)
        self.assertIn("unknown_name", latin.reasons)
        self.assertNotIn("Alice", latin.text)
        self.assertNotIn("Smith", latin.text)
        uyghur = deidentify("明天下午和阿依古丽做笔录", DeidContext())
        self.assertFalse(uyghur.confident)
        self.assertIn("unknown_name", uyghur.reasons)
        self.assertNotIn("阿依古丽", uyghur.text)

    def test_separated_phone_id_and_chinese_case_numbers_do_not_leak(self) -> None:
        phone = deidentify("电话 1 3 8 0 0 1 3 8 0 0 0", DeidContext())
        self.assertIn("[电话1]", phone.text)
        self.assertNotIn("13800138000", re.sub(r"\D", "", phone.text))
        self.assertNotRegex(phone.text, r"1\s*3\s*8")
        spaced_id = "身份证 " + " ".join("110101199003071234")
        identified = deidentify(spaced_id, DeidContext())
        self.assertIn("[证件号1]", identified.text)
        self.assertNotIn("110101199003071234", re.sub(r"\D", "", identified.text))
        numerals = deidentify("案号二〇二六沪零一零一民初一二三号", DeidContext())
        self.assertIn("[案号1]", numerals.text)
        self.assertNotIn("二〇二六", numerals.text)
        self.assertNotIn("零一零一", numerals.text)
        self.assertNotIn("一二三", numerals.text)
        wide = deidentify("电话１　３　８　０　０　１　３　８　０　０　０", DeidContext())
        self.assertIn("[电话1]", wide.text)
        self.assertNotIn("13800138000", re.sub(r"\D", "", wide.text))
        self.assertNotIn("１", wide.text)
        split_case = deidentify("案号 A B C，明天", DeidContext())
        self.assertIn("[案号1]", split_case.text)
        self.assertNotIn("B", split_case.text)
        self.assertNotIn("C", split_case.text)

    def test_fullwidth_case_numbers_still_mask(self) -> None:
        exact = deidentify(
            "案号２０２６－０８７",
            DeidContext(case_numbers=("2026-087",)),
        )
        self.assertTrue(exact.confident, (exact.reasons, exact.text))
        self.assertEqual(exact.text, "案号[案号1]")
        self.assertNotIn("087", exact.text)
        self.assertNotIn("０８７", exact.text)
        judicial = deidentify("（２０２６）沪０１０１民初１２３４号明天", DeidContext())
        self.assertTrue(judicial.confident, (judicial.reasons, judicial.text))
        self.assertIn("[案号1]", judicial.text)
        self.assertNotIn("1234", judicial.text)
        self.assertNotIn("0101", judicial.text)
        self.assertNotIn("１２３４", judicial.text)

    def test_split_names_and_other_scripts_do_not_leak(self) -> None:
        context = DeidContext(directory_names=("李明",), room_names=("笔录室 1",))
        known = (
            "明天和李/明做笔录",
            "明天和李_/明做笔录",
            "明天和李\u3000明做笔录",
            "明天和李\u200b明做笔录",
        )
        for raw in known:
            with self.subTest(raw=raw):
                result = deidentify(raw, context)
                self.assertTrue(result.confident, (result.reasons, result.text))
                self.assertIn("[同事1]", result.text)
                self.assertNotIn("李", result.text)
                self.assertNotIn("李明", result.text)
                self.assertNotRegex(result.text, r"李\W*明")
                self.assertEqual(result.mapping["[同事1]"], "李明")
        blocked = (
            "明天 L i M i n g 做笔录",
            "明天和Иван做笔录",
            "明天和محمد做笔录",
            "明天和李月做笔录",
            "明天和明月华做笔录",
        )
        for raw in blocked:
            with self.subTest(raw=raw):
                result = deidentify(raw, context)
                self.assertFalse(result.confident, (result.reasons, result.text))
                self.assertIn("unknown_name", result.reasons)
                compact = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", result.text).casefold()
                self.assertNotIn("liming", compact)
                self.assertNotIn("иван", result.text.casefold())
                self.assertNotIn("محمد", result.text)
                self.assertNotIn("李月", result.text)
                self.assertNotIn("明月华", result.text)

    def test_split_keywords_and_spoken_digits_do_not_leak(self) -> None:
        phone = deidentify("联系电 话1/3/8/0/0/1/3/8/0/0/0", DeidContext())
        self.assertNotIn("13800138000", re.sub(r"\D", "", phone.text))
        self.assertNotRegex(phone.text, r"1\s*/\s*3")
        spoken = deidentify("电话幺三八洞洞幺三八洞洞洞", DeidContext())
        self.assertNotIn("幺三八", spoken.text)
        self.assertNotIn("13800138000", _digits_of(spoken.text))
        case = deidentify("案 号 二 〇 二 六 沪 零 一 零 一 民 初 一 二 三 号", DeidContext())
        self.assertNotIn("二〇二六", re.sub(r"\s", "", case.text))
        self.assertNotIn("零一零一", case.text)
        self.assertNotIn("一二三", case.text)
        self.assertNotIn("2026", _digits_of(case.text))

    def test_separator_fuzz_does_not_leave_a_sendable_secret(self) -> None:
        context = DeidContext(
            directory_names=("李明",),
            room_names=("笔录室 1", "笔录室 2"),
            case_numbers=("2026-087",),
        )
        secrets = (
            "李明",
            "李月",
            "阿依古丽",
            "LiMing",
            "Иван",
            "محمد",
            "13800138000",
            "110101199003071234",
            "二〇二六沪零一",
            "幺三八洞洞幺三八洞洞洞",
        )
        separators = (" ", "\u3000", "\u200b", "/", "_", "-", ".", ",", "·", "|")
        keywords = ("", "案号", "案 号", "电话", "联系电 话", "身份证", "编号")
        for secret in secrets:
            for separator in separators:
                for keyword in keywords:
                    sprinkled = separator.join(secret)
                    raw = f"明天{keyword}{sprinkled}做笔录"
                    result = deidentify(raw, context)
                    with self.subTest(raw=raw):
                        self.assertIn("明天", result.text)
                        if result.confident:
                            _assert_no_secret(self, result.text, secret)

    def test_known_single_character_name_is_masked(self) -> None:
        result = deidentify("明天和彤做笔录", DeidContext(directory_names=("彤",)))
        self.assertTrue(result.confident, (result.reasons, result.text))
        self.assertIn("明天", result.text)
        self.assertNotIn("彤", result.text)
        self.assertEqual(result.mapping["[同事1]"], "彤")
        party = deidentify("明天约彤", DeidContext(party_names=("彤",)))
        self.assertNotIn("彤", party.text)
        self.assertEqual(party.mapping["[当事人1]"], "彤")
        inside_word = deidentify("明天做笔录", DeidContext(directory_names=("明",)))
        self.assertIn("明天", inside_word.text)
        self.assertNotIn("[同事", inside_word.text)
        separate = deidentify("明天和明做笔录", DeidContext(directory_names=("明",)))
        self.assertIn("明天", separate.text)
        self.assertNotIn("和明", separate.text)
        self.assertEqual(separate.mapping["[同事1]"], "明")

    def test_compatibility_han_known_name_is_masked(self) -> None:
        compat = "\uf9e1明"
        result = deidentify(f"明天和{compat}做笔录", DeidContext(directory_names=("李明",)))
        self.assertTrue(result.confident, (result.reasons, result.text))
        self.assertIn("明天", result.text)
        self.assertNotIn("\uf9e1", result.text)
        self.assertNotIn("李明", result.text)
        self.assertNotIn("李", result.text)
        self.assertEqual(result.mapping["[同事1]"], "李明")

    def test_duration_and_clock_latin_stay_only_in_context(self) -> None:
        kept = deidentify("明天 90 min，14:00pm，9 am", DeidContext())
        self.assertTrue(kept.confident, (kept.reasons, kept.text))
        self.assertIn("min", kept.text)
        self.assertIn("pm", kept.text)
        self.assertIn("am", kept.text)
        name = deidentify("明天 Min 做笔录", DeidContext())
        self.assertFalse(name.confident)
        self.assertNotIn("min", name.text.casefold())
        wide = deidentify("明天Ａｉ做笔录", DeidContext())
        self.assertFalse(wide.confident)
        self.assertNotIn("Ai", wide.text)
        self.assertNotIn("Ａｉ", wide.text)
        self.assertNotIn("ai", wide.text.casefold())

    def test_ordinary_booking_words_stay_sendable(self) -> None:
        context = DeidContext(room_names=("笔录室 1", "笔录室 2"))
        samples = (
            "帮我约明天下午一小时",
            "明天上午九点三十分，笔录室1，一小时",
            "帮我订明天下午",
            "三十分钟",
            "明天晚上谈话",
            "会见室明天上午一小时",
        )
        for raw in samples:
            with self.subTest(raw=raw):
                result = deidentify(raw, context)
                self.assertTrue(result.confident, (result.reasons, result.text))
                self.assertNotIn("[当事人", result.text)

    def test_non_string_input_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            deidentify(None)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            DeidContext(case_numbers=("ok", 1))  # type: ignore[arg-type]

    def test_gateway_module_does_not_import_network_libraries(self) -> None:
        source = Path(deidentify.__code__.co_filename).read_text(encoding="utf-8")
        tree = ast.parse(source)
        banned = {"urllib", "http", "socket", "requests", "ssl", "ftplib", "smtplib"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [(node.module or "").split(".")[0]]
            else:
                continue
            for module in modules:
                self.assertNotIn(module, banned)

    def test_deidentify_does_not_open_a_socket(self) -> None:
        def fail_closed(*_args, **_kwargs):
            raise AssertionError("脱敏网关不应打开套接字")

        with mock.patch.object(socket, "socket", fail_closed):
            result = deidentify("明天下午找个笔录室，一小时", DeidContext(room_names=("笔录室 1",)))
        self.assertTrue(result.confident)
        self.assertIn("笔录室", result.text)


class DeidRoomWhitelistTests(BackendTestCase):
    def test_whitelist_follows_room_rows_the_caller_reads(self) -> None:
        self.setup_system()
        self.login()
        created = {
            "笔录室 12": 12,
            "310室": 13,
        }
        for name, sort_order in created.items():
            response = self.write(
                "POST",
                "/api/v1/rooms",
                {"name": name, "sortOrder": sort_order},
            )
            self.assertEqual(response.status_code, 201, response.get_json())
        inactive = self.write(
            "POST",
            "/api/v1/rooms",
            {"name": "409室", "sortOrder": 14, "isActive": False},
        )
        self.assertEqual(inactive.status_code, 201, inactive.get_json())

        listed = self.client.get("/api/v1/rooms").get_json()["items"]
        active_names = tuple(item["name"] for item in listed if item["isActive"])
        self.assertIn("笔录室 1", active_names)
        self.assertIn("笔录室 2", active_names)
        self.assertIn("笔录室 12", active_names)
        self.assertIn("310室", active_names)
        self.assertNotIn("409室", active_names)

        context = DeidContext(room_names=active_names)
        kept = (
            "明天上午 90 分钟，2号笔录室",
            "下午请到12号笔录室",
            "明天下午使用310室",
            "10月20日14:00-15:00要笔录室 1",
        )
        for text in kept:
            with self.subTest(text=text):
                result = deidentify(text, context)
                self.assertEqual(result.text, text)
                self.assertTrue(result.confident, result.reasons)
                self.assertEqual(result.mapping, {})

        unknown_room = deidentify("明天下午使用410室", context)
        self.assertNotIn("410", unknown_room.text)
        self.assertIn("[编号1]", unknown_room.text)

        inactive_only = deidentify("明天去409室", context)
        self.assertNotIn("409", inactive_only.text)
        restored = deidentify("明天去409室", DeidContext(room_names=(*active_names, "409室")))
        self.assertEqual(restored.text, "明天去409室")
        self.assertTrue(restored.confident)


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


if __name__ == "__main__":
    unittest.main()

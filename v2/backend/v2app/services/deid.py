"""本机脱敏网关（一句话预约，文本）。

原则：宁可多遮，不可漏遮。模型只需要知道“这里有一个当事人或案号”，
不需要原文。拿不准就返回 ``confident=False``，调用方不得把文本发出本机。

本模块只使用标准库，不联网，也不读取数据库。案号、当事人、同事姓名和
房间名全部由调用方按现行权限放进 :class:`DeidContext`：

- ``case_numbers``：请求者已经可见的案号（本人历史，以及共享日历上的
  active 预约）。不要放入他人已取消记录。
- ``party_names``：只放请求者本人预约上的当事人（``owner_user_id`` 为
  请求者），可以包含已经结束的 active 预约。共享日历上看得到的他人当事人
  不在此列，避免把别人的案子回填到这句话上。
- ``directory_names``：启用账号的显示名，与用户目录同一人群。
- ``room_names``：应保持可读的房间名，通常是 ``rooms`` 表里启用中的
  ``name``。网关只保护调用方交来的这些名字及其写法变体。

第一期没有语音；这里只处理已经写下来的句子。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..common import DOUBLE_CHARACTER_SURNAMES, mask_party_name


CJK = "\u4e00-\u9fa5"
OPEN = r"[(（〔\[【]"
CLOSE = r"[)）〕\]】]"
SENT_P0, SENT_P1 = "\ue000", "\ue001"
SENT_W0, SENT_W1 = "\ue002", "\ue003"
_SENTINEL_PAIR = re.compile(
    f"{re.escape(SENT_P0)}.{re.escape(SENT_P1)}|{re.escape(SENT_W0)}.{re.escape(SENT_W1)}"
)

MAX_SOURCE_CHARS = 500
MAX_MASKED_CHARS = 200
MAX_REPLACEMENTS = 6

# 这些原因表示“不要外发”。ambiguous_person 只记审计，不单独拦截。
BLOCKING_REASONS = frozenset(
    {
        "unknown_name",
        "email_marker",
        "document_keyword",
        "source_too_long",
        "masked_too_long",
        "too_many_replacements",
        "unknown_digit_run",
    }
)

_SINGLE_SURNAME_TEXT = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
    "杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍"
    "虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚"
    "程嵇邢滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓"
    "牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙"
    "叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻"
    "莘党翟谭贡劳逄姬申扶堵冉宰郦雍郤璩桑桂濮牛寿通边扈燕冀郏浦尚农温"
    "别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡"
    "国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾"
    "毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓阳肖闫覃"
)
_SINGLE_SURNAMES = frozenset(char for char in _SINGLE_SURNAME_TEXT if "\u4e00" <= char <= "\u9fff")
_COMPOUND_SURNAMES = tuple(sorted(set(DOUBLE_CHARACTER_SURNAMES), key=len, reverse=True))
_SURNAME_ALT = "|".join(
    re.escape(item)
    for item in sorted(
        (*_COMPOUND_SURNAMES, *_SINGLE_SURNAMES),
        key=len,
        reverse=True,
    )
)
_TITLE_RE = re.compile(rf"(?:{_SURNAME_ALT})(?:某某|女士|先生|老师|同志|某)")
_XIAO_RE = re.compile(rf"(?:小|老)(?:{_SURNAME_ALT})")
_XIAO_LAO_STOP = frozenset(
    {
        "小时",
        "小姐",
        "小伙",
        "小心",
        "小说",
        "小朋友",
        "老师",
        "老板",
        "老实",
        "老人",
        "老年",
        "老公",
        "老婆",
        "老百姓",
        "老鼠",
        "老虎",
        "老家",
    }
)

# 预约句子里的时间、房间和工作词。先把这些吃掉，剩下的短汉字串才当成人名。
_LEXICON_TEXT = """
今天 明天 后天 大后天 昨天 前天 今晚 明早 明晚 当天 当日 次日
上午 下午 中午 晚上 早上 凌晨 傍晚 夜里
星期一 星期二 星期三 星期四 星期五 星期六 星期日 星期天
周一 周二 周三 周四 周五 周六 周日 周天
下周 上周 这周 本周
下周一 下周二 下周三 下周四 下周五 下周六 下周日
下个月 上个月 这个月 下月 上月 明年 去年 今年
一点 两点 三点 四点 五点 六点 七点 八点 九点 十点 十一点 十二点
两点半 三点半 四点半 一点半 点半 半个 一刻 三刻
小时 钟头 分钟 半小时 一小时 两小时 一个小时 两个小时 个小时
笔录 笔录室 询问 询问室 调解 调解室 谈话 谈话笔录 会议室 小会议室
案子 案件 预约 记录 开庭
案号 案件号 案件编号 编号 卷号 文号 文号是 卷宗 身份证 证件 电话
联系电话 当事人电话 当事人 同事
找个 那个 这个 这些 那些 还是 一起 帮忙 再约 再约一次 一次
两个 三个 一个 两个案子 那个案子 个人
结束 开始 使用 联系 改到 下个 要用 前结束
明天下午 明天上午 后天上午 周五上午 周四下午 下周三上午 上午两小时
明天下午两点 这个案子 下周一
"""
_PARTICLE_TEXT = "的了和是在要与及或到去用来请把被从对给就也再不没会能为个向往做约看帮找让很及"
_LEXICON = frozenset(
    word
    for word in _LEXICON_TEXT.split()
    if len(word) >= 2
) | frozenset(
    char for char in _PARTICLE_TEXT if char not in _SINGLE_SURNAMES or char == "和"
)

_DOC_KEYWORDS = ("护照号", "户籍地址", "护照", "户籍", "住址", "住所")
_DOC_DELIMITERS = frozenset("，。,.;；、！？!?\n")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+")
_CJK_RUN_RE = re.compile(rf"[{CJK}]+")
_LONG_DIGIT_RE = re.compile(r"\d{3,}")
_SHORT_DIGIT_RE = re.compile(r"(?<=第)\d{2,}|\d{2,}(?=[号字])")
_ID_RE = r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])|(?<![0-9])\d{15}(?![0-9])"
_MOBILE_RE = r"(?<![0-9])(?:\+?86[-\s]?)?1[3-9]\d[-\s]?\d{4}[-\s]?\d{4}(?![0-9])"
_LANDLINE_RE = r"(?<![0-9])0\d{2,3}[-\s]?\d{7,8}(?![0-9])"


@dataclass(frozen=True)
class DeidContext:
    """调用方已经按权限收好的词表。网关不会再去查库。"""

    case_numbers: tuple[str, ...] = ()
    party_names: tuple[str, ...] = ()
    directory_names: tuple[str, ...] = ()
    room_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_numbers", _as_strings(self.case_numbers))
        object.__setattr__(self, "party_names", _as_strings(self.party_names))
        object.__setattr__(self, "directory_names", _as_strings(self.directory_names))
        object.__setattr__(self, "room_names", _as_strings(self.room_names))


@dataclass
class DeidResult:
    """一次脱敏的结果。``mapping`` 只留在本机，用来把占位符填回原值。"""

    text: str
    mapping: dict[str, str]
    confident: bool
    reasons: list[str]


def deidentify(text: str, context: DeidContext | None = None) -> DeidResult:
    """把一句预约话脱敏。``confident`` 为假时调用方不得外发 ``text``。"""

    if not isinstance(text, str):
        raise TypeError("待脱敏文本必须是字符串")
    if context is None:
        context = DeidContext()
    elif not isinstance(context, DeidContext):
        raise TypeError("脱敏上下文必须是 DeidContext")
    return _Session(context).run(text)


def _as_strings(values: Iterable[str]) -> tuple[str, ...]:
    items: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise TypeError("脱敏上下文只接受字符串")
        items.append(value)
    return tuple(items)


def _halfwidth(text: str) -> str:
    out: list[str] = []
    for char in text:
        code = ord(char)
        if (
            0xFF10 <= code <= 0xFF19
            or 0xFF21 <= code <= 0xFF3A
            or 0xFF41 <= code <= 0xFF5A
            or char in "－／＿"
        ):
            out.append(chr(code - 0xFEE0))
        elif char == "＠":
            out.append("@")
        elif "\ue000" <= char <= "\uf8ff":
            continue
        else:
            out.append(char)
    return "".join(out)


def _identity(value: str) -> str:
    text = _halfwidth(value).replace(" ", "")
    for full, half in (
        ("（", "("),
        ("）", ")"),
        ("〔", "("),
        ("〕", ")"),
        ("【", "("),
        ("】", ")"),
        ("[", "("),
        ("]", ")"),
    ):
        text = text.replace(full, half)
    return text


def _case_variants(value: str) -> set[str]:
    base = _halfwidth(value.strip())
    variants = {base, base.replace(" ", "")}
    swaps = (
        ("（", "("),
        ("）", ")"),
        ("〔", "("),
        ("〕", ")"),
        ("[", "("),
        ("]", ")"),
        ("【", "("),
        ("】", ")"),
    )
    for source in list(variants):
        folded = source
        for full, half in swaps:
            folded = folded.replace(full, half)
        variants.add(folded)
        variants.add(folded.replace("(", "（").replace(")", "）"))
    return {item for item in variants if item}


def _room_variants(name: str) -> set[str]:
    normalized = _halfwidth(name.strip())
    variants = {normalized, normalized.replace(" ", "")}
    match = re.fullmatch(rf"([{CJK}]+)\s*(\d+)", normalized)
    if match:
        prefix, number = match.groups()
        variants |= {
            f"{number}号{prefix}",
            f"{prefix}{number}号",
            f"{number}号{prefix[-1]}",
        }
    return {item for item in variants if len(item) >= 2}


def _unique_stripped(values: tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        if len(cleaned) < 2 or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _usable_mask(masked: str, original: str) -> bool:
    return bool(masked) and masked != original and masked != "访客" and "*" in masked


def _is_personal_name(chunk: str) -> bool:
    if len(chunk) < 2 or chunk in _LEXICON:
        return False
    for compound in _COMPOUND_SURNAMES:
        if chunk.startswith(compound):
            given = len(chunk) - len(compound)
            return given == 0 or given in (1, 2)
    return chunk[0] in _SINGLE_SURNAMES and len(chunk) in (2, 3)


def _lexicon_cover(run: str) -> tuple[list[bool], list[int]]:
    consumed = [False] * len(run)
    cover = [0] * len(run)
    found: list[tuple[int, int]] = []
    for word in _LEXICON:
        start = 0
        while True:
            index = run.find(word, start)
            if index < 0:
                break
            found.append((len(word), index))
            start = index + 1
    found.sort(key=lambda item: (-item[0], item[1]))
    for length, index in found:
        if any(consumed[index : index + length]):
            continue
        for offset in range(length):
            consumed[index + offset] = True
            cover[index + offset] = length
    return consumed, cover


def _name_spans(run: str) -> list[tuple[int, int]]:
    consumed, cover = _lexicon_cover(run)
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(run):
        if consumed[index]:
            index += 1
            continue
        end = index
        while end < len(run) and not consumed[end]:
            end += 1
        start = index
        if (
            start > 0
            and cover[start - 1] == 1
            and run[start - 1] in _SINGLE_SURNAMES
        ):
            start -= 1
        chunk = run[start:end]
        if _is_personal_name(chunk):
            spans.append((start, end))
        elif len(chunk) >= 4:
            cursor = 0
            while cursor < len(chunk):
                matched = 0
                for compound in _COMPOUND_SURNAMES:
                    if chunk.startswith(compound, cursor):
                        room = len(chunk) - (cursor + len(compound))
                        take = len(compound) + min(2, room)
                        if take > len(compound):
                            matched = take
                        break
                if matched:
                    spans.append((start + cursor, start + cursor + matched))
                    cursor += matched
                else:
                    cursor += 1
        index = end
    return spans


class _Session:
    def __init__(self, context: DeidContext) -> None:
        self.context = context
        self.mapping: dict[str, str] = {}
        self.reasons: list[str] = []
        self._reason_set: set[str] = set()
        self._counts: dict[str, int] = {}
        self._seen: dict[tuple[str, str], str] = {}
        self._placeholders: list[str] = []
        self._white: list[str] = []
        self.replacements = 0

    def run(self, raw: str) -> DeidResult:
        if len(raw) > MAX_SOURCE_CHARS:
            self._add_reason("source_too_long")
        text = _halfwidth(raw)
        if "@" in text:
            self._add_reason("email_marker")
            text = _EMAIL_RE.sub(lambda match: self._placeholder("编号", match.group(0)), text)

        text = self._exact(text)
        text = self._party_patterns(text)
        text = self._sub(_ID_RE, "证件号", text)
        text = self._sub(_MOBILE_RE, "电话", text)
        text = self._sub(_LANDLINE_RE, "电话", text)
        text = self._protect_whitelist(text)
        text = self._structural(text)
        text = self._catchall_digits(text)
        text = self._unknown_names(text)
        text = self._document_tails(text)
        text = self._digit_backstop(text)
        text = self._restore(text)

        if len(text) > MAX_MASKED_CHARS:
            self._add_reason("masked_too_long")
        if self.replacements > MAX_REPLACEMENTS:
            self._add_reason("too_many_replacements")
        confident = not any(reason in BLOCKING_REASONS for reason in self.reasons)
        return DeidResult(
            text=text,
            mapping=dict(self.mapping),
            confident=confident,
            reasons=list(self.reasons),
        )

    def _add_reason(self, reason: str) -> None:
        if reason not in self._reason_set:
            self._reason_set.add(reason)
            self.reasons.append(reason)

    def _placeholder(self, label: str, original: str) -> str:
        group = "案号/编号" if label in ("案号", "编号") else label
        key = (group, _identity(original))
        self.replacements += 1
        cached = self._seen.get(key)
        if cached is not None:
            return cached
        self._counts[label] = self._counts.get(label, 0) + 1
        name = f"[{label}{self._counts[label]}]"
        index = len(self._placeholders)
        self._placeholders.append(name)
        self.mapping[name] = original
        token = SENT_P0 + chr(0xE100 + index) + SENT_P1
        self._seen[key] = token
        return token

    def _protect(self, match: re.Match[str]) -> str:
        index = len(self._white)
        self._white.append(match.group(0))
        return SENT_W0 + chr(0xE100 + index) + SENT_W1

    def _sub(self, pattern: str, label: str, text: str, group: int = 0) -> str:
        def replace(match: re.Match[str]) -> str:
            if group == 0:
                return self._placeholder(label, match.group(0))
            whole, part = match.group(0), match.group(group)
            if _SENTINEL_PAIR.search(part):
                return whole
            start = match.start(group) - match.start(0)
            end = match.end(group) - match.start(0)
            return whole[:start] + self._placeholder(label, part) + whole[end:]

        return re.sub(pattern, replace, text)

    def _exact(self, text: str) -> str:
        parties = _unique_stripped(self.context.party_names)
        directory = _unique_stripped(self.context.directory_names)
        party_keys = {_identity(name) for name in parties}
        directory_only: list[str] = []
        for name in directory:
            if _identity(name) in party_keys:
                self._add_reason("ambiguous_person")
            else:
                directory_only.append(name)

        entries: list[tuple[str, str, str]] = []
        for case_number in _unique_stripped(self.context.case_numbers):
            for variant in _case_variants(case_number):
                entries.append((variant, "案号", case_number))
        for name in parties:
            entries.append((name, "当事人", name))
            masked = mask_party_name(name)
            if _usable_mask(masked, name):
                entries.append((masked, "当事人", name))
        for name in directory_only:
            entries.append((name, "同事", name))

        entries.sort(key=lambda item: len(item[0]), reverse=True)
        for variant, label, restore in entries:
            if label == "案号" or not re.search(rf"[{CJK}]", variant):
                pattern = rf"(?<![A-Za-z0-9]){re.escape(variant)}(?![A-Za-z0-9])"
            else:
                pattern = re.escape(variant)
            text = re.sub(
                pattern,
                lambda _match, item_label=label, item_restore=restore: self._placeholder(
                    item_label, item_restore
                ),
                text,
            )
        return text

    def _party_patterns(self, text: str) -> str:
        def title(match: re.Match[str]) -> str:
            return self._placeholder("当事人", match.group(0))

        def xiao(match: re.Match[str]) -> str:
            token = match.group(0)
            if token in _XIAO_LAO_STOP:
                return token
            return self._placeholder("当事人", token)

        text = _TITLE_RE.sub(title, text)
        return _XIAO_RE.sub(xiao, text)

    def _protect_whitelist(self, text: str) -> str:
        variants = {
            variant
            for name in self.context.room_names
            for variant in _room_variants(name)
        }
        for room in sorted(variants, key=len, reverse=True):
            text = re.sub(re.escape(room) + r"(?!\d)", self._protect, text)
        patterns = (
            r"(?:(?:19|20)\d{2}\s*年\s*)?\d{1,2}\s*月\s*\d{1,2}\s*[日号]?",
            r"(?<![0-9A-Za-z\-/])(?:19|20)\d{2}-(?:0?[1-9]|1[0-2])-(?:0?[1-9]|[12]\d|3[01])(?![0-9\-])",
            r"(?<![0-9/])(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])(?![0-9/])",
            rf"(?:(?<=[月到至和或是在])|(?<![{CJK}A-Za-z0-9\-_/]))(?:0?[1-9]|[12]\d|3[01])\s*号(?![{CJK}]*?[0-9])(?=[上下早中晚那当的有开\s,，。]|$)",
            r"(?<![0-9])(?:[01]?\d|2[0-3])\s*[:：]\s*[0-5]\d(?![0-9])",
            r"(?<![0-9])(?:[01]?\d|2[0-3])\s*点\s*(?:半|一刻|三刻|[0-5]?\d\s*分?)?",
            r"(?<![0-9])\d{1,3}(?:\.\d)?\s*个?\s*(?:小时|钟头|分钟|min|h)(?![A-Za-z])",
            r"(?<![0-9])\d{1,2}\s*(?:个人|人|位)",
        )
        for pattern in patterns:
            text = re.sub(pattern, self._protect, text)
        return text

    def _structural(self, text: str) -> str:
        token = (
            rf"(?:{OPEN}\s*(?:19|20)\d{{2}}\s*{CLOSE}|[{CJK}]{{0,6}}?(?:{OPEN}\s*(?:19|20)\d{{2}}\s*{CLOSE}"
            rf"|[A-Za-z0-9][A-Za-z0-9\-_/]*))+(?:\s*号)?"
        )
        text = self._sub(
            rf"(?:案号|案件号|案件编号|编号|卷号|文号|(?i:no\.?|№))\s*(?:是|为|[:：])?\s*({token})",
            "案号",
            text,
            group=1,
        )
        text = self._sub(
            rf"{OPEN}\s*(?:19|20)\d{{2}}\s*{CLOSE}\s*(?:[{CJK}A-Za-z0-9]{{0,15}}?\d+\s*号|[{CJK}A-Za-z0-9]{{0,15}}\d+)",
            "案号",
            text,
        )
        text = self._sub(
            rf"(?:[{CJK}]{{1,6}}|(?:19|20)\d{{2}})字第\s*\d+\s*号|第\s*\d{{1,6}}\s*号",
            "案号",
            text,
        )
        text = self._sub(
            r"(?<![A-Za-z0-9])(?:19|20)\d{2}\s*[-_/—–]\s*[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)*",
            "案号",
            text,
        )
        text = self._sub(
            r"(?<![A-Za-z0-9])(?=[A-Za-z0-9\-_/]*[A-Za-z])(?=[A-Za-z0-9\-_/]*\d)[A-Za-z0-9][A-Za-z0-9\-_/]{2,}",
            "编号",
            text,
        )
        return text

    def _catchall_digits(self, text: str) -> str:
        text = self._sub(r"\d{3,}", "编号", text)
        return self._sub(r"(?<=第)\d{2,}|\d{2,}(?=[号字])", "编号", text)

    def _unknown_names(self, text: str) -> str:
        pieces: list[str] = []
        cursor = 0
        for match in _CJK_RUN_RE.finditer(text):
            pieces.append(text[cursor : match.start()])
            pieces.append(self._mask_names(match.group(0)))
            cursor = match.end()
        pieces.append(text[cursor:])
        return "".join(pieces)

    def _mask_names(self, run: str) -> str:
        spans = _name_spans(run)
        if not spans:
            return run
        pieces: list[str] = []
        cursor = 0
        for start, end in spans:
            pieces.append(run[cursor:start])
            self._add_reason("unknown_name")
            pieces.append(self._placeholder("当事人", run[start:end]))
            cursor = end
        pieces.append(run[cursor:])
        return "".join(pieces)

    def _document_tails(self, text: str) -> str:
        occupied = [False] * len(text)
        spans: list[tuple[int, int]] = []
        for keyword in _DOC_KEYWORDS:
            start = 0
            while True:
                index = text.find(keyword, start)
                if index < 0:
                    break
                if any(occupied[index : index + len(keyword)]):
                    start = index + len(keyword)
                    continue
                for offset in range(len(keyword)):
                    occupied[index + offset] = True
                tail = index + len(keyword)
                end = tail
                while end < len(text) and text[end] not in _DOC_DELIMITERS:
                    end += 1
                if _unreplaced_residue(text[tail:end]):
                    spans.append((tail, end))
                    self._add_reason("document_keyword")
                start = end
        for tail, end in sorted(spans, reverse=True):
            residue = _unreplaced_residue(text[tail:end])
            text = text[:tail] + self._placeholder("证件号", residue) + text[end:]
        return text

    def _digit_backstop(self, text: str) -> str:
        visible = _SENTINEL_PAIR.sub(" ", text)
        if _LONG_DIGIT_RE.search(visible) or _SHORT_DIGIT_RE.search(visible):
            self._add_reason("unknown_digit_run")
            text = self._sub(r"\d{3,}", "编号", text)
            text = self._sub(r"(?<=第)\d{2,}|\d{2,}(?=[号字])", "编号", text)
        if re.search(_ID_RE, visible) or re.search(_MOBILE_RE, visible) or re.search(_LANDLINE_RE, visible):
            self._add_reason("unknown_digit_run")
            text = self._sub(_ID_RE, "证件号", text)
            text = self._sub(_MOBILE_RE, "电话", text)
            text = self._sub(_LANDLINE_RE, "电话", text)
        return text

    def _restore(self, text: str) -> str:
        text = re.sub(
            re.escape(SENT_W0) + r"(.)" + re.escape(SENT_W1),
            lambda match: self._white[ord(match.group(1)) - 0xE100],
            text,
        )
        return re.sub(
            re.escape(SENT_P0) + r"(.)" + re.escape(SENT_P1),
            lambda match: self._placeholders[ord(match.group(1)) - 0xE100],
            text,
        )


def _unreplaced_residue(fragment: str) -> str:
    return _SENTINEL_PAIR.sub("", fragment).strip()

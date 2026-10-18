"""本机脱敏网关（一句话预约，文本）。

原则：外发决定是白名单。预约词、调用方交来的房间名、以及严格识别的日期时间
可以留下；其余字母、汉字和数字默认遮住。拿不准就返回 ``confident=False``，
调用方不得把文本发出本机。宁可多遮，不可漏遮。

威胁模型（后续验收按这条，而不是按故意混淆）：网关防范的是普通工作人员在
正常书写时的意外泄露。要挡住常见姓名、案号、电话、证件号，以及常见输入变体
（空格、全角、零宽字符、常见分隔符、中文和口语数字、NFKC 兼容字符）。
故意混淆是已知残余风险，不作为本阶段的阻断项，包括：

- 非拉丁、非汉字的十进制数字，例如阿拉伯-印度数字、泰文数字；
- 数字与汉字交错，例如「1甲3乙8丙」；
- 混合文字编码。

首要安全边界在架构上：当事人、案号和事项不采用模型输出，模型只解析意图。
脱敏是第二道防线。

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
import unicodedata
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
小时 钟头 分钟 半小时 一小时 两小时 一个小时 两个小时 三个小时 个小时
一个半小时 半个小时 三十分钟 十五分钟 四十五分钟 三十分 十五分 四十五分
笔录 笔录室 询问 询问室 调解 调解室 谈话 谈话笔录 会见 会议室 小会议室
案子 案件 预约 预订 记录 开庭 开会 帮我 帮我约 帮我订 楼上 楼下
案号 案件号 案件编号 编号 卷号 文号 文号是 卷宗 身份证 证件 电话
联系电话 当事人电话 当事人 同事
找个 那个 这个 这些 那些 还是 一起 帮忙 再约 再约一次 一次
两个 三个 一个 两个案子 那个案子 个人
结束 开始 使用 联系 改到 下个 要用 前结束
以前 以后 之前 之后
明天下午 明天上午 后天上午 周五上午 周四下午 下周三上午 上午两小时
这个案子 下周一
"""
_PARTICLE_TEXT = (
    "的了和是在要与及或到去用来请把被从对给就也再不没会能为个向往做约看帮找让很及我订室"
)
_LEXICON = frozenset(word for word in _LEXICON_TEXT.split() if len(word) >= 2) | frozenset(
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
# 外发前按“像数字”处理的字符。分隔符收掉之后，连续出现就要遮。
_DIGIT_CHARS = frozenset(
    "0123456789〇零一二三四五六七八九十百千两幺洞壹贰貳叁參肆伍陆陸柒捌玖拾佰仟"
)
# 「no」只留给案号标记 No.。min / h / am / pm / ai 不在这里全局放行。
_LATIN_ALLOW = frozenset({"no"})
_NAME_MARKS = (
    "当事人",
    "同事",
    "老师",
    "律师",
    "经理",
    "先生",
    "女士",
    "客户",
)
_NAME_CHARS = frozenset("和跟与给找同约")
_KEYWORD_TAILS = (
    ("联系电话", "电话"),
    ("当事人电话", "电话"),
    ("手机", "电话"),
    ("电话", "电话"),
    ("身份证", "证件号"),
    ("证件号", "证件号"),
    ("证件", "证件号"),
    ("案件编号", "案号"),
    ("案件号", "案号"),
    ("案号", "案号"),
    ("编号", "案号"),
    ("卷号", "案号"),
    ("文号", "案号"),
)


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


def _nfkc_keep(char: str) -> bool:
    if char == " " or unicodedata.category(char) == "Zs":
        return True
    if _is_han(char):
        return True
    return char.isascii() and char.isalnum()


def _normalize_text(text: str) -> str:
    """整句做兼容归一，再去掉格式字符和组合记号。

    汉字的兼容形式（例如兼容区里的姓）和变成字母、数字的 NFKC 结果都会采纳，
    这样已知姓名在匹配前已经折成同一字形。中文标点仍留在原文里，避免逗号被
    折成英文逗号。非拉丁十进制数字不在这里折成 ASCII，那是已知残余风险。
    """

    out: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if category == "Cf" or category.startswith("M"):
            continue
        folded = unicodedata.normalize("NFKC", char)
        if folded != char and folded and all(_nfkc_keep(item) for item in folded):
            for item in folded:
                out.append(" " if unicodedata.category(item) == "Zs" else item)
            continue
        if category == "Zs" and char != " ":
            out.append(" ")
        else:
            out.append(char)
    return _halfwidth("".join(out))


def _is_han(char: str) -> bool:
    code = ord(char)
    return 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF or 0xF900 <= code <= 0xFAFF


def _is_basic_latin(char: str) -> bool:
    return char.isascii() and char.isalpha()


def _is_other_letter(char: str) -> bool:
    return (
        unicodedata.category(char).startswith("L")
        and not _is_han(char)
        and not _is_basic_latin(char)
    )


def _is_digit_like(char: str) -> bool:
    return char in _DIGIT_CHARS


def _is_private(char: str) -> bool:
    return "\ue000" <= char <= "\uf8ff"


def _is_gap(char: str) -> bool:
    """字母或数字以外的字符，都可能被拿来拆开姓名、关键词或号码。"""

    if _is_private(char):
        return False
    if _is_han(char) or _is_basic_latin(char) or _is_other_letter(char):
        return False
    if _is_digit_like(char):
        return False
    return True


def _collapse_between(text: str, is_core) -> tuple[str, list[int]]:
    """去掉夹在同类字符之间的任意分隔，并记下每个保留字符的原位置。

    检测在收紧后的文本上进行，替换仍落回原文，所以没被遮住的空格保持原样。
    """

    out: list[str] = []
    origins: list[int] = []
    index = 0
    size = len(text)
    while index < size:
        if is_core(text[index]):
            out.append(text[index])
            origins.append(index)
            index += 1
            continue
        gap_end = index
        while gap_end < size and _is_gap(text[gap_end]):
            gap_end += 1
        between_cores = (
            bool(out)
            and is_core(text[origins[-1]])
            and gap_end > index
            and gap_end < size
            and is_core(text[gap_end])
        )
        if between_cores:
            index = gap_end
            continue
        if gap_end == index:
            out.append(text[index])
            origins.append(index)
            index += 1
            continue
        for cursor in range(index, gap_end):
            out.append(text[cursor])
            origins.append(cursor)
        index = gap_end
    return "".join(out), origins


def _crosses_sentinel(fragment: str) -> bool:
    return SENT_P0 in fragment or SENT_W0 in fragment or bool(_SENTINEL_PAIR.search(fragment))


def _letter_core(char: str) -> bool:
    return _is_han(char) or _is_basic_latin(char) or _is_other_letter(char)


def _han_name_position(text: str, index: int) -> bool:
    """短汉字挨着「和 / 给 / 约 / 老师」这类位置时，按姓名处理。"""

    if index > 0 and text[index - 1] in _NAME_CHARS:
        return True
    if index + 1 < len(text) and text[index + 1] in _NAME_CHARS:
        return True
    before = text[:index]
    after = text[index + 1 :]
    if before.endswith("总"):
        return True
    return any(before.endswith(mark) or after.startswith(mark) for mark in _NAME_MARKS)


def _anchor_label(text: str, origin_start: int) -> str | None:
    window = text[max(0, origin_start - 16) : origin_start]
    collapsed, _origins = _collapse_between(window, _letter_core)
    for keyword, label in _KEYWORD_TAILS:
        if collapsed.endswith(keyword):
            return label
    return None


def _digit_run_present(text: str, minimum: int) -> bool:
    run = 0
    for char in text:
        if _is_digit_like(char):
            run += 1
            if run >= minimum:
                return True
        else:
            run = 0
    return False


def _lexicon_prefix_length(text: str, index: int) -> int:
    best = 0
    for word in _LEXICON:
        if len(word) < 2 or len(word) <= best:
            continue
        if text.startswith(word, index):
            best = len(word)
    return best


def _identity(value: str) -> str:
    text, _origins = _collapse_between(_normalize_text(value), _letter_core)
    text = text.replace(" ", "")
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


def _unique_stripped(values: tuple[str, ...], *, minimum: int = 2) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        if len(cleaned) < minimum or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _lexicon_covers(text: str, index: int, length: int) -> bool:
    """单字已知姓名落在「明天」这类预约词里面时，不要把它抠出来。"""

    for word in _LEXICON:
        if len(word) < 2:
            continue
        start = max(0, index - len(word) + 1)
        for cursor in range(start, index + 1):
            if text.startswith(word, cursor) and cursor + len(word) >= index + length:
                return True
    return False


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


def _lexicon_cover(run: str, *, minimum: int = 1) -> tuple[list[bool], list[int]]:
    consumed = [False] * len(run)
    cover = [0] * len(run)
    found: list[tuple[int, int]] = []
    for word in _LEXICON:
        if len(word) < minimum:
            continue
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
        text = _normalize_text(raw)
        if "@" in text:
            self._add_reason("email_marker")
            text = _EMAIL_RE.sub(lambda match: self._placeholder("编号", match.group(0)), text)

        text = self._exact(text)
        text = self._party_patterns(text)
        text = self._keyword_tails(text)
        text = self._protect_whitelist(text)
        text = self._structural(text)
        text = self._sub(_ID_RE, "证件号", text)
        text = self._sub(_MOBILE_RE, "电话", text)
        text = self._sub(_LANDLINE_RE, "电话", text)
        text = self._catchall_digits(text)
        text = self._mask_digit_like(text)
        text = self._document_tails(text)
        text = self._han_residual(text)
        text = self._mask_script(
            text,
            _is_other_letter,
            allow=frozenset(),
            reason="unknown_name",
            label="当事人",
        )
        text = self._mask_script(
            text,
            _is_basic_latin,
            allow=self._latin_allow(),
            reason="unknown_name",
            label="当事人",
        )
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
        parties = _unique_stripped(self.context.party_names, minimum=1)
        directory = _unique_stripped(self.context.directory_names, minimum=1)
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
            if len(name) < 2:
                continue
            folded = _normalize_text(name)
            entries.append((folded, "当事人", name))
            masked = mask_party_name(name)
            if _usable_mask(masked, name):
                entries.append((masked, "当事人", name))
        for name in directory_only:
            if len(name) < 2:
                continue
            entries.append((_normalize_text(name), "同事", name))

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
        named = [(item, "当事人") for item in parties]
        named.extend((item, "同事") for item in directory_only)
        for name, label in named:
            text = self._mask_collapsed_name(text, name, label, name)
        return text

    def _mask_collapsed_name(self, text: str, name: str, label: str, restore: str) -> str:
        """在收掉分隔符之后的文本里找已知姓名，再把原文那一段换成占位符。

        单字姓名也要遮。它若是「明天」这类预约词的一部分，则留在词里。
        """

        target, _origins = _collapse_between(_normalize_text(name), _letter_core)
        target = "".join(char for char in target if _letter_core(char))
        if not target:
            return text
        search_from = 0
        while True:
            collapsed, origins = _collapse_between(text, _letter_core)
            index = collapsed.find(target, search_from)
            if index < 0:
                return text
            if len(target) == 1 and _lexicon_covers(collapsed, index, 1):
                search_from = index + 1
                continue
            origin_start = origins[index]
            origin_end = origins[index + len(target) - 1] + 1
            if _crosses_sentinel(text[origin_start:origin_end]):
                return text
            replacement = self._placeholder(label, restore)
            updated = text[:origin_start] + replacement + text[origin_end:]
            if updated == text:
                return text
            text = updated
            search_from = 0

    def _keyword_tails(self, text: str) -> str:
        """敏感词按收掉分隔符之后的样子匹配，后面没被词表解释掉的内容整段遮住。

        「案 号」「联系电 话」和「案号 A B C」都不能只遮住其中一个字。
        预约常用词（明天、下午）会截断尾巴。
        """

        collapsed, origins = _collapse_between(text, _letter_core)
        occupied = [False] * len(collapsed)
        found: list[tuple[int, int, str]] = []
        for keyword, label in _KEYWORD_TAILS:
            start = 0
            while True:
                index = collapsed.find(keyword, start)
                if index < 0:
                    break
                end = index + len(keyword)
                if any(occupied[index:end]):
                    start = index + 1
                    continue
                if not all(_letter_core(collapsed[offset]) for offset in range(index, end)):
                    start = index + 1
                    continue
                for offset in range(index, end):
                    occupied[offset] = True
                found.append((index, end, label))
                start = end
        spans: list[tuple[int, int, str]] = []
        for index, end, label in found:
            tail_start = origins[end - 1] + 1
            while tail_start < len(text) and text[tail_start] in " \t":
                tail_start += 1
            if tail_start < len(text) and text[tail_start] in "是为:：":
                tail_start += 1
                while tail_start < len(text) and text[tail_start] in " \t:：":
                    tail_start += 1
            tail_end = tail_start
            while tail_end < len(text):
                if text[tail_end] in _DOC_DELIMITERS or text[tail_end] in (SENT_P0, SENT_W0):
                    break
                if text[tail_end] in "和与及或的":
                    break
                if _lexicon_prefix_length(text, tail_end) >= 2:
                    break
                tail_end += 1
            while tail_end > tail_start and text[tail_end - 1] in " \t":
                tail_end -= 1
            residue = text[tail_start:tail_end]
            if residue and not _crosses_sentinel(residue):
                spans.append((tail_start, tail_end, label))
        for start, end, label in sorted(spans, key=lambda item: item[0], reverse=True):
            text = text[:start] + self._placeholder(label, text[start:end]) + text[end:]
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
        variants = {variant for name in self.context.room_names for variant in _room_variants(name)}
        for room in sorted(variants, key=len, reverse=True):
            text = re.sub(re.escape(room) + r"(?!\d)", self._protect, text)
        patterns = (
            r"(?:(?:19|20)\d{2}\s*年\s*)?\d{1,2}\s*月\s*\d{1,2}\s*[日号]?",
            r"(?<![0-9A-Za-z\-/])(?:19|20)\d{2}-(?:0?[1-9]|1[0-2])-(?:0?[1-9]|[12]\d|3[01])(?![0-9\-])",
            r"(?<![0-9/])(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])(?![0-9/])",
            rf"(?:(?<=[月到至和或是在])|(?<![{CJK}A-Za-z0-9\-_/]))(?:0?[1-9]|[12]\d|3[01])\s*号(?![{CJK}]*?[0-9])(?=[上下早中晚那当的有开\s,，。]|$)",
            r"(?<![0-9A-Za-z])(?:[01]?\d|2[0-3])\s*[:：]\s*[0-5]\d\s*(?:[AaPp][Mm])?(?![A-Za-z0-9])",
            r"(?<![A-Za-z])[AaPp][Mm]\s*(?=(?:[01]?\d|2[0-3])(?:\s*[:：]\s*[0-5]\d)?)",
            r"(?<![A-Za-z0-9])(?:[01]?\d|2[0-3])\s*[AaPp][Mm](?![A-Za-z])",
            r"(?<![0-9])(?:[01]?\d|2[0-3])\s*点\s*(?:半|一刻|三刻|[0-5]?\d\s*分?)?",
            r"(?:十[一二]|[一二三四五六七八九两]|十)\s*点\s*(?:半|一刻|三刻|[零〇一二三四五六七八九十两]{1,3}\s*分)?",
            r"(?:一个半小时|半个小时|[一二两三]\s*个\s*半\s*小时|[一二两三]\s*个?\s*小时|半\s*小时|[零〇一二三四五六七八九十两]{1,3}\s*分钟)",
            r"(?<![0-9])\d{1,3}(?:\.\d)?\s*个?\s*(?:小时|钟头|分钟|[Mm][Ii][Nn]|[Hh])(?![A-Za-z])",
            r"(?<![0-9])\d{1,2}\s*(?:个人|人|位)",
            r"(?<![0-9])\d{1,2}\s*楼",
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

    def _latin_allow(self) -> frozenset[str]:
        # 房间名里的拉丁字母只随整段房间名保护，不把 ai / min 之类单独放行。
        return _LATIN_ALLOW

    def _mask_script(
        self, text: str, is_core, *, allow: frozenset[str], reason: str, label: str
    ) -> str:
        """非白名单的字母串整段遮住。分隔符先收掉，所以 L i M i n g 不会漏。"""

        collapsed, origins = _collapse_between(text, is_core)
        spans: list[tuple[int, int]] = []
        index = 0
        while index < len(collapsed):
            if not is_core(collapsed[index]):
                index += 1
                continue
            end = index
            while end < len(collapsed) and is_core(collapsed[end]):
                end += 1
            token = collapsed[index:end]
            if token.casefold() in allow:
                index = end
                continue
            origin_start = origins[index]
            origin_end = origins[end - 1] + 1
            if not _crosses_sentinel(text[origin_start:origin_end]):
                spans.append((origin_start, origin_end))
            index = end
        return self._cover_spans(text, spans, label=label, reason=reason)

    def _mask_digit_like(self, text: str) -> str:
        """任意分隔符收掉之后，未受保护的数字串都遮住。

        三位及以上一律遮。关键词后面的两位也遮，避免「案号 12」留下可还原的号。
        """

        collapsed, origins = _collapse_between(text, _is_digit_like)
        spans: list[tuple[int, int, str]] = []
        index = 0
        while index < len(collapsed):
            if not _is_digit_like(collapsed[index]):
                index += 1
                continue
            end = index
            while end < len(collapsed) and _is_digit_like(collapsed[end]):
                end += 1
            origin_start = origins[index]
            origin_end = origins[end - 1] + 1
            length = end - index
            label = _anchor_label(text, origin_start)
            if not _crosses_sentinel(text[origin_start:origin_end]) and (
                length >= 3 or (label is not None and length >= 2)
            ):
                spans.append((origin_start, origin_end, label or "编号"))
            index = end
        for origin_start, origin_end, label in sorted(
            spans, key=lambda item: item[0], reverse=True
        ):
            text = (
                text[:origin_start]
                + self._placeholder(label, text[origin_start:origin_end])
                + text[origin_end:]
            )
        return text

    def _han_residual(self, text: str) -> str:
        """预约词表盖不住的汉字，处在姓名位置或本身就是短串时不得外发。

        不再因为里面有「月」这类单字就放行。拿不准的长串同样遮住并拒绝外发。
        """

        collapsed, origins = _collapse_between(text, _is_han)
        spans: list[tuple[int, int]] = []
        for match in _CJK_RUN_RE.finditer(collapsed):
            run = match.group(0)
            base = match.start()
            consumed, _cover = _lexicon_cover(run, minimum=2)
            for offset, char in enumerate(run):
                if not consumed[offset] and char in _LEXICON:
                    consumed[offset] = True
            index = 0
            while index < len(run):
                if consumed[index]:
                    index += 1
                    continue
                end = index
                while end < len(run) and not consumed[end]:
                    end += 1
                chunk = run[index:end]
                chunk_at = index
                index = end
                if chunk in _DOC_KEYWORDS:
                    continue
                name_like = len(chunk) == 1 and _han_name_position(collapsed, base + chunk_at)
                if len(chunk) < 2 and not name_like:
                    continue
                origin_start = origins[base + chunk_at]
                origin_end = origins[base + index - 1] + 1
                if not _crosses_sentinel(text[origin_start:origin_end]):
                    spans.append((origin_start, origin_end))
        return self._cover_spans(text, spans, label="当事人", reason="unknown_name")

    def _cover_spans(
        self,
        text: str,
        spans: list[tuple[int, int]],
        *,
        label: str,
        reason: str,
    ) -> str:
        merged: list[tuple[int, int]] = []
        for start, end in sorted(spans):
            if end <= start:
                continue
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        for start, end in reversed(merged):
            piece = text[start:end]
            if not piece.strip() or _crosses_sentinel(piece):
                continue
            self._add_reason(reason)
            text = text[:start] + self._placeholder(label, piece.strip()) + text[end:]
        return text

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
        collapsed, _origins = _collapse_between(visible, _is_digit_like)
        if _digit_run_present(collapsed, 3):
            self._add_reason("unknown_digit_run")
            text = self._mask_digit_like(text)
        if _LONG_DIGIT_RE.search(visible) or _SHORT_DIGIT_RE.search(visible):
            self._add_reason("unknown_digit_run")
            text = self._sub(r"\d{3,}", "编号", text)
            text = self._sub(r"(?<=第)\d{2,}|\d{2,}(?=[号字])", "编号", text)
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

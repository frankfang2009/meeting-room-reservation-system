"""本机出站编码器（一句话预约）。

外发决定是白名单，不是姓名或案号识别。归一化之后只做正向最长匹配：
词表里的预约词、调用方交来的房间名和事项类别、紧邻时间单位的数字、冒号钟点
和结构化日期可以留下；其余连续片段换成 ``[P1]``、``[P2]``。同一片段在一次
编码里共用一个占位符。

数字预算是同一笔，覆盖每一种会带出数值的词元（ASCII、全角、中文数字），
不论它是钟点、``N点``、``N号``、日期、时长还是数字单位。一条消息最多：

- 1 个日期表达（``YYYY-MM-DD``、``M/D``、``M-D``，或 ``N月`` 再接一个 ``N日``
  / ``N号``）。月 1–12，日 1–31。四位年份只允许出现在完整的 ``YYYY-MM-DD`` 里。
- 2 个时刻（``HH:MM``，或 ``N点``、``N点半``、``N点N分``）。小时 0–23。
  分钟只允许 00、15、30、45。预约格子是 30 分钟，00 和 30 可以直接落槽；
  15 和 45 是用户会写的刻钟，本机看见后必须澄清，不能当成任意两位分钟。
  不采用 5 的倍数：05、10、20 这类值不落在 30 分钟格子上，却能多藏一对数字。
- 1 个时长。分钟数必须是 15 的倍数，并且在 15–180 之间（业务上限 180）。
  ``90分钟``、``120分钟``、``1.5小时`` 可以留下；``200分钟``、``3.5小时`` 不行。
- 1 个 ``N号``，只当作日（1–31）。
- 1 个房间序号，而且必须是本次启用房间已经公开的号，不能是任意数字。
- 上述词元合计最多 16 个数字字符。中文数字按它表示的十进制位数计
  （``五`` 算 1 位，``十五`` 算 2 位），不再是零成本。

``13:38``、``12:34``、``6:7``、``13:81``、``14:00:30`` 不合格。超出个数的
``5点6点7点`` 或 ``一号三号八号`` 只留下额度内的第一段，其余是占位符。

能留下来的信息量约 25 比特：日期 ``log2(12×31)``，两个刻钟
``2×log2(24×4)``，一个 15 分钟对齐的时长 ``log2(12)``。手机号大约 33 比特，
身份证号大约 60 比特，带法院代字的案号更长。这条通道装不下一整串电话、
证件号或案号。见 :func:`numeric_capacity_bits`。

不变式 I1：每个外发词元要么在词表（含本次房间名、事项类别和上述合法数字）里，
要么是占位符，并且没有超出上面的额度。``release_outbound`` 复验单条文本。
``release_outbound_payload`` 复验整包：固定系统提示是常量，每段动态文字
（服务器上下文和用户编码句）都要过闸门。映射只留在本机，审计不记原文。

本模块只使用标准库，不联网，也不读取数据库。案号、当事人、目录姓名、房间名
全部由调用方按现行权限放进 :class:`DeidContext`。事项只接受代码里的类别，
不接受历史里的自由文本。已知姓名和案号按整段精确匹配收成占位符。

残余风险（负责人 2026-10-20 已确认）：完全由词表词拼成的名字（如「周五」，
或「高明天」里的「明天」）会以这些词离开本机。预约词和这种名字无法区分，
``@`` 引用可以避开；单字姓氏不进词表，所以姓本身不会作为单字放行。
阿拉伯-印度数字、泰文数字和故意交错的编码不在本词表的归一化范围内。
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping, Sequence

MAX_SOURCE_CHARS = 500
MAX_NUMERIC_DIGITS = 16
MAX_RELEASED_DIGITS = MAX_NUMERIC_DIGITS
MAX_STRUCTURED_DIGITS = MAX_NUMERIC_DIGITS
MAX_CLOCK_TOKENS = 2
MAX_TIME_EXPRESSIONS = 2
MAX_DATE_TOKENS = 1
MAX_DATE_EXPRESSIONS = 1
MAX_DURATION_TOKENS = 1
MAX_DAY_NUMBERS = 1
MAX_ROOM_ORDINALS = 1
MAX_DURATION_MINUTES = 180
ALLOWED_MINUTES = frozenset({0, 15, 30, 45})
PURPOSE_CATEGORIES = (
    "询问笔录",
    "谈话笔录",
    "调解笔录",
    "会见",
    "谈话",
    "开会",
)

_SURNAME_TEXT = (
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
SURNAMES = frozenset(char for char in _SURNAME_TEXT if "\u4e00" <= char <= "\u9fff")

_DAY_TEXT = """
今天 明天 后天 大后天 昨天 前天 当晚 当天 当日 次日
今早 今晚 明早 明晚 今儿 明儿 后儿
星期一 星期二 星期三 星期四 星期五 星期六 星期日 星期天
周一 周二 周三 周四 周五 周六 周日 周天
礼拜一 礼拜二 礼拜三 礼拜四 礼拜五 礼拜六 礼拜日 礼拜天
下周 上周 这周 本周 下月 上月 这个月 下个月 上个月
下周一 下周二 下周三 下周四 下周五 下周六 下周日
月初 月底 一会儿 半天
"""
_PERIOD_TEXT = "上午 下午 中午 晚上 早上 凌晨 傍晚 夜里"
_CLOCK_TEXT = """
一点 两点 三点 四点 五点 六点 七点 八点 九点 十点 十一点 十二点
一点半 两点半 三点半 四点半 五点半 六点半 七点半 八点半 九点半 十点半 十一点半 十二点半
半小时 半个小时 半个钟头 一个小时 两个小时 三个小时 一个半小时 两个半小时
一小时 两小时 三小时 一钟头 两钟头 一个钟头 两个钟头 个把小时
三十分钟 十五分钟 四十五分钟 三十分 十五分 四十五分 一刻钟 三刻钟
"""
_DURATION_EXTRA = "半个 一刻 三刻"
_VERB_TEXT = """
预约 预订 取消 做个 补个 找个 约个 要个
笔录 询问 谈话 调解 案子 案件 房间 会议室 笔录室
开会 记录 会见 开庭
"""
_PURPOSE_TEXT = "询问笔录 谈话笔录 调解笔录"
_FUNCTION_TEXT = """
那个 这个 这些 那些 还是 一下 帮我 左右 之前 之后 以后 以前 案号
都可以 具体 几点 随便 差不多 大概 尽量 最好 有空 就行 空的
什么 怎么 哪里 一起 然后 如果 或者 麻烦 谢谢 再约 一次
给我 请帮 帮我约 帮我订 使用 开始 结束 改到 要用 个人
"""
_PUNCT_TEXT = "， 。 、 ； ！ ？ , . ! ? …"
_SINGLE_WANTED = (
    "的了是在要与到去用请把被从对给就也再不没会能为个做约看帮找让很吗吧呀呢啊订留改换空"
)

_UNITS = ("点钟", "分钟", "小时", "钟头", "点半", "点", "时", "分", "号", "日", "月")
_TIME_LEXICON = frozenset(
    """
一点 两点 三点 四点 五点 六点 七点 八点 九点 十点 十一点 十二点
一点半 两点半 三点半 四点半 五点半 六点半 七点半 八点半 九点半 十点半
十一点半 十二点半
三十分 十五分 四十五分
""".split()
)
_DURATION_LEXICON = frozenset(
    """
半小时 半个小时 半个钟头 一个小时 两个小时 三个小时 一个半小时 两个半小时
一小时 两小时 三小时 一钟头 两钟头 一个钟头 两个钟头 个把小时
三十分钟 十五分钟 四十五分钟 一刻钟 三刻钟
半个 一刻 三刻
""".split()
)
_CN_DIGIT = "零一二三四五六七八九"
_PLACEHOLDER_RE = re.compile(r"^\[P([1-9]\d{0,2})\]$")
_CLOCK_TOKEN_RE = re.compile(r"^(?:[01]?\d|2[0-3]):(?:00|15|30|45)$")
_DECIMAL_HOUR_RE = re.compile(r"^(\d{1,2}\.\d)(小时|钟头)$")


def _cn_forms() -> list[tuple[str, int]]:
    forms: list[tuple[str, int]] = [("零", 0), ("〇", 0), ("半", 0), ("两", 2)]
    for number in range(0, 61):
        if number < 10:
            forms.append((_CN_DIGIT[number], number))
            continue
        if number == 10:
            forms.append(("十", number))
            continue
        if number < 20:
            forms.append((f"十{_CN_DIGIT[number - 10]}", number))
            continue
        tens, ones = divmod(number, 10)
        base = f"{_CN_DIGIT[tens]}十"
        forms.append((base if ones == 0 else base + _CN_DIGIT[ones], number))
    forms.sort(key=lambda item: len(item[0]), reverse=True)
    return forms


_CN_FORMS = _cn_forms()


def _catalog(*groups: tuple[str, str]) -> dict[str, str]:
    words: dict[str, str] = {}
    for category, blob in groups:
        for word in blob.split():
            if len(word) == 1 and word in SURNAMES:
                continue
            words.setdefault(word, category)
    singles = "".join(char for char in _SINGLE_WANTED if char not in SURNAMES)
    for char in singles:
        words.setdefault(char, "function")
    return words


LEXICON: dict[str, str] = _catalog(
    ("day", _DAY_TEXT),
    ("period", _PERIOD_TEXT),
    ("clock", _CLOCK_TEXT),
    ("duration", _DURATION_EXTRA),
    ("verb", _VERB_TEXT),
    ("purpose", _PURPOSE_TEXT),
    ("function", _FUNCTION_TEXT),
    ("punct", _PUNCT_TEXT),
)
_BY_FIRST: dict[str, tuple[tuple[str, str], ...]] = {}
for _word, _category in LEXICON.items():
    _BY_FIRST.setdefault(_word[0], []).append((_word, _category))
for _key, _items in list(_BY_FIRST.items()):
    _BY_FIRST[_key] = tuple(sorted(_items, key=lambda item: len(item[0]), reverse=True))


@dataclass(frozen=True)
class Mention:
    """用户用 ``@`` / ``#`` 插入的引用。下标是原句偏移，``value`` 是规范值。"""

    start: int
    end: int
    kind: str
    value: str


@dataclass(frozen=True)
class DeidContext:
    """调用方已经按权限收好的词表。编码器不会再去查库。"""

    case_numbers: tuple[str, ...] = ()
    party_names: tuple[str, ...] = ()
    directory_names: tuple[str, ...] = ()
    room_names: tuple[str, ...] = ()
    purpose_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_numbers", _as_strings(self.case_numbers))
        object.__setattr__(self, "party_names", _as_strings(self.party_names))
        object.__setattr__(self, "directory_names", _as_strings(self.directory_names))
        object.__setattr__(self, "room_names", _as_strings(self.room_names))
        object.__setattr__(self, "purpose_names", _as_strings(self.purpose_names))


@dataclass(frozen=True)
class OutboundToken:
    text: str
    category: str
    start: int
    end: int


@dataclass
class DeidResult:
    """一次编码的结果。``mapping`` 只留在本机。"""

    text: str
    mapping: dict[str, str]
    confident: bool
    reasons: list[str]
    tokens: tuple[OutboundToken, ...] = ()
    mention_roles: dict[str, str] | None = None


class OutboundBlocked(Exception):
    """闸门拒绝外发。``outcome`` 是固定短码，不含原文。"""

    outcome = "blocked_invariant"

    def __str__(self) -> str:
        return self.outcome


def deidentify(
    text: str,
    context: DeidContext | None = None,
    *,
    prior: Mapping[str, str] | None = None,
    mentions: Sequence[Mention] | None = None,
) -> DeidResult:
    """把一句预约话编成词表词和占位符。``confident`` 为假时不得外发。"""

    if not isinstance(text, str):
        raise TypeError("待编码文本必须是字符串")
    if context is None:
        context = DeidContext()
    elif not isinstance(context, DeidContext):
        raise TypeError("编码上下文必须是 DeidContext")
    if len(text) > MAX_SOURCE_CHARS:
        return DeidResult(text="", mapping={}, confident=False, reasons=["source_too_long"])
    if not text.strip():
        return DeidResult(text="", mapping={}, confident=False, reasons=["empty"])
    normalized, spans = _normalize(text)
    pieces = _scan(normalized, spans, text, context, mentions or ())
    tokens, mapping, roles = _assign(pieces, prior or {})
    encoded = " ".join(token.text for token in tokens)
    allowed = allowed_surface(context)
    if not tokens or not satisfies_outbound_invariant(encoded, allowed):
        return DeidResult(text="", mapping={}, confident=False, reasons=["invariant"], tokens=())
    return DeidResult(
        text=encoded,
        mapping=mapping,
        confident=True,
        reasons=[],
        tokens=tuple(tokens),
        mention_roles=roles,
    )


def encode_preview(
    text: str,
    context: DeidContext | None = None,
    *,
    mentions: Sequence[Mention] | None = None,
    server_context: str | None = None,
) -> dict[str, object]:
    """给将来的输入框用的同一编码器。不返回映射里的原文。

    ``outbound`` 是会离开本机的全部动态文字：服务器上下文（如果有）和编码句。
    上下文不是合法的服务器字段时，预览不带它。
    """

    result = deidentify(text, context, mentions=mentions)
    local = 0
    shown: list[dict[str, object]] = []
    for token in result.tokens:
        kept = token.category == "placeholder"
        if kept:
            local += 1
        shown.append({"text": token.text, "local": kept})
    dynamic = [result.text]
    if server_context:
        try:
            dynamic.insert(0, validate_server_context(server_context))
        except OutboundBlocked:
            dynamic = [""]
            result = DeidResult(text="", mapping={}, confident=False, reasons=["invariant"])
    outbound = "\n".join(part for part in dynamic if part)
    return {
        "text": result.text,
        "outbound": outbound,
        "localCount": local,
        "tokens": shown,
        "confident": result.confident and bool(result.text or not text.strip()),
    }


def allowed_surface(context: DeidContext | None = None) -> frozenset[str]:
    """闸门认可的表面形式：固定词表、本次房间变体、代码里的事项类别。"""

    words = set(LEXICON)
    if context is None:
        return frozenset(words)
    for name in context.room_names:
        words.update(room_forms(name))
    for name in context.purpose_names:
        if name in PURPOSE_CATEGORIES:
            words.add(name)
    return frozenset(words)


def room_forms(name: str) -> set[str]:
    """启用房间的可读变体。只由配置生成，不进固定词表。"""

    stripped = " ".join(name.split())
    if not stripped:
        return set()
    forms = {stripped, stripped.replace(" ", "")}
    compact = stripped.replace(" ", "")
    matched = re.fullmatch(r"(.+?)(\d+)", compact)
    if matched and matched.group(1):
        forms.add(f"{matched.group(2)}号{matched.group(1)}")
    return {item for item in forms if item}


def satisfies_outbound_invariant(text: str, allowed: Iterable[str]) -> bool:
    """I1：词元在词表、合法数字或占位符里，并且统一数字额度没超。"""

    if not isinstance(text, str) or not text.strip():
        return False
    allowed_set = allowed if isinstance(allowed, (set, frozenset)) else set(allowed)
    tokens = text.split(" ")
    if any(not token for token in tokens):
        return False
    budget = _fresh_budget()
    room_numbers = _room_numbers_from_allowed(allowed_set)
    previous_was_month = False
    for token in tokens:
        if _PLACEHOLDER_RE.fullmatch(token):
            previous_was_month = False
            continue
        slot = _outbound_slot(token, room_numbers)
        if slot is None:
            if token in allowed_set and not _static_digit_run(token):
                previous_was_month = False
                continue
            return False
        kind, digits = slot
        if not _fits(budget, kind, token, digits, previous_was_month=previous_was_month):
            return False
        _charge(budget, kind, token, digits, previous_was_month=previous_was_month)
        previous_was_month = kind == "date-month"
    return True


def is_grammar_token(token: str) -> bool:
    """闸门会放行的数字词：钟点、日期、时长或数字单位。"""

    return _outbound_slot(token, set(range(1, 100))) is not None


def release_outbound(text: str, allowed: Iterable[str]) -> str:
    """单条文本的出站闸门。不满足 I1 就不把文本交还给调用方去发 HTTP。"""

    if not satisfies_outbound_invariant(text, allowed):
        raise OutboundBlocked()
    return text


def release_outbound_payload(
    system: str,
    user: str,
    allowed: Iterable[str],
    *,
    system_prefix: str,
) -> None:
    """整包闸门。系统提示必须是审过的常量，动态上下文和用户句分别复验。"""

    if not isinstance(system, str) or not isinstance(user, str) or not isinstance(system_prefix, str):
        raise OutboundBlocked()
    prefix = system_prefix + "\n"
    if not system.startswith(prefix):
        raise OutboundBlocked()
    validate_server_context(system[len(prefix) :])
    release_outbound(user, allowed)


def validate_server_context(text: str) -> str:
    """服务器上下文只含日期、钟点、启用房间和事项类别。自由文本过不了。"""

    if not isinstance(text, str):
        raise OutboundBlocked()
    matched = _SERVER_CONTEXT_RE.fullmatch(text)
    if matched is None:
        raise OutboundBlocked()
    year, month, day = (int(matched.group(1)), int(matched.group(2)), int(matched.group(3)))
    try:
        found = date(year, month, day)
    except ValueError as error:
        raise OutboundBlocked() from error
    if _WEEKDAY_LABELS[found.weekday()] != matched.group(4):
        raise OutboundBlocked()
    for clock in (matched.group(5), matched.group(6), matched.group(7)):
        if _SERVER_CLOCK_RE.fullmatch(clock) is None:
            raise OutboundBlocked()
    if matched.group(6) >= matched.group(7):
        raise OutboundBlocked()
    rooms = matched.group(8)
    if rooms != "（无）":
        for room in rooms.split("、"):
            if not room or len(room) > 80 or any(char in room for char in "\n\r"):
                raise OutboundBlocked()
    purposes = matched.group(9)
    if purposes != "（无）":
        for purpose in purposes.split("、"):
            if purpose not in PURPOSE_CATEGORIES:
                raise OutboundBlocked()
    return text


def numeric_capacity_bits() -> float:
    """一条消息里用户还能决定的数字信息量，单位是比特。

    日期是 12×31，两个时刻各是 24×4（分钟只取 00/15/30/45），时长是 15 到 180
    的 15 分钟倍数，共 12 档。房间序号只能复述已经公开的房间号，不另计。
    四位年份必须和月日组成真实日期，不是独立的 0000–9999。
    """

    date_space = 12 * 31
    time_space = 24 * len(ALLOWED_MINUTES)
    duration_space = len(range(15, MAX_DURATION_MINUTES + 1, 15))
    return (
        math.log2(date_space)
        + MAX_TIME_EXPRESSIONS * math.log2(time_space)
        + math.log2(duration_space)
    )


def released_number_digits(text: str, allowed: Iterable[str] | None = None) -> str:
    """按词元顺序抽出数字通道里的十进制数字，用来证明电话拼不回去。"""

    if not isinstance(text, str) or not text.strip():
        return ""
    allowed_set = set() if allowed is None else set(allowed)
    room_numbers = _room_numbers_from_allowed(allowed_set)
    parts: list[str] = []
    for token in text.split(" "):
        if not token or _PLACEHOLDER_RE.fullmatch(token):
            continue
        if token in allowed_set and _outbound_slot(token, room_numbers) is None:
            continue
        digits = _digit_string(token)
        if digits:
            parts.append(digits)
    return "".join(parts)


def lexicon_categories() -> Mapping[str, str]:
    return LEXICON


def _as_strings(values: Iterable[str]) -> tuple[str, ...]:
    items: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise TypeError("编码上下文只接受字符串")
        items.append(value)
    return tuple(items)


def _normalize(text: str) -> tuple[str, list[tuple[int, int]]]:
    chars: list[str] = []
    spans: list[tuple[int, int]] = []
    for index, char in enumerate(text):
        if unicodedata.category(char) == "Cf":
            continue
        norm = unicodedata.normalize("NFKC", char)
        if not norm or norm.isspace() or char.isspace():
            chars.append(" ")
            spans.append((index, index + 1))
            continue
        for piece in norm:
            chars.append(piece)
            spans.append((index, index + 1))
    collapsed: list[str] = []
    collapsed_spans: list[tuple[int, int]] = []
    for char, span in zip(chars, spans, strict=True):
        if char == " " and collapsed and collapsed[-1] == " ":
            start, _end = collapsed_spans[-1]
            collapsed_spans[-1] = (start, span[1])
            continue
        collapsed.append(char)
        collapsed_spans.append(span)
    return "".join(collapsed), collapsed_spans


def _scan(
    normalized: str,
    spans: list[tuple[int, int]],
    original: str,
    context: DeidContext,
    mentions: Sequence[Mention],
) -> list[dict[str, object]]:
    rooms = []
    for name in context.room_names:
        for form in room_forms(name):
            rooms.append((form, "room"))
    rooms.sort(key=lambda item: len(item[0]), reverse=True)
    purposes = [(name, "purpose") for name in context.purpose_names if name in PURPOSE_CATEGORIES]
    purposes.sort(key=lambda item: len(item[0]), reverse=True)
    entities = _entities(context)
    mention_list = [
        item for item in mentions if isinstance(item, Mention) and item.start < item.end
    ]
    pieces: list[dict[str, object]] = []
    budget = _fresh_budget()
    room_numbers = _room_numbers_from_forms(rooms)
    index = 0
    limit = len(normalized)
    while index < limit:
        if normalized[index] == " ":
            index += 1
            continue
        mention = _mention_at(spans[index][0], mention_list)
        if mention is not None:
            end = index
            while end < limit and spans[end][0] < mention.end:
                end += 1
            start = _pull_marks(normalized, index)
            pieces.append(
                {
                    "kind": "placeholder",
                    "start": spans[start][0],
                    "end": spans[end - 1][1],
                    "key": f"mention:{mention.kind}:{mention.value}",
                    "value": mention.value.strip(),
                    "role": mention.kind,
                }
            )
            index = end
            continue
        masked = _masked_decimal(normalized, index)
        if masked:
            end = index + masked
            pieces.append(
                {
                    "kind": "gap",
                    "start": spans[index][0],
                    "end": spans[end - 1][1],
                    "key": normalized[index:end],
                    "value": original[spans[index][0] : spans[end - 1][1]],
                }
            )
            index = end
            continue
        match = _longest(
            normalized, index, rooms, purposes, entities, budget, room_numbers
        )
        if match is None:
            pieces.append(
                {
                    "kind": "gap",
                    "start": spans[index][0],
                    "end": spans[index][1],
                    "key": normalized[index],
                    "value": original[spans[index][0] : spans[index][1]],
                }
            )
            index += 1
            continue
        length, _rank, kind, surface, category = match
        start = _pull_marks(normalized, index)
        orig_start = spans[start][0]
        orig_end = spans[index + length - 1][1]
        if kind == "entity":
            pieces.append(
                {
                    "kind": "placeholder",
                    "start": orig_start,
                    "end": orig_end,
                    "key": surface,
                    "value": original[spans[index][0] : orig_end].strip() or surface,
                    "role": category if category in {"party", "case"} else "",
                }
            )
        else:
            _charge(budget, category, surface)
            pieces.append(
                {
                    "kind": "lex",
                    "text": surface.replace(" ", ""),
                    "category": _EMITTED_CATEGORY.get(category, category),
                    "start": orig_start,
                    "end": orig_end,
                }
            )
        index += length
    return _merge_gaps(pieces, original)


def _pull_marks(normalized: str, index: int) -> int:
    start = index
    while start > 0 and normalized[start - 1] in "@#":
        start -= 1
    return start


_EMITTED_CATEGORY = {
    "structured-clock": "clock",
    "structured-date": "day",
    "time": "clock",
    "date-month": "day",
    "date-day": "day",
    "room-ordinal": "room",
}
_ORDINAL_RE = re.compile(r"^(\d{1,2})号(?:房间|会议室)$")
_WEEKDAY_LABELS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_SERVER_CLOCK_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_SERVER_CONTEXT_RE = re.compile(
    r"^今天是(\d{4})-(\d{2})-(\d{2})（(星期[一二三四五六日])），"
    r"当前时间(\d{2}:\d{2})。工作时段(\d{2}:\d{2})–(\d{2}:\d{2})。"
    r"可用房间：(.*)。事项：(.*)。$"
)
_LEXICON_CANON = {
    "一点": "1",
    "两点": "2",
    "三点": "3",
    "四点": "4",
    "五点": "5",
    "六点": "6",
    "七点": "7",
    "八点": "8",
    "九点": "9",
    "十点": "10",
    "十一点": "11",
    "十二点": "12",
    "一点半": "130",
    "两点半": "230",
    "三点半": "330",
    "四点半": "430",
    "五点半": "530",
    "六点半": "630",
    "七点半": "730",
    "八点半": "830",
    "九点半": "930",
    "十点半": "1030",
    "十一点半": "1130",
    "十二点半": "1230",
    "三十分": "30",
    "十五分": "15",
    "四十五分": "45",
    "半小时": "30",
    "半个小时": "30",
    "半个钟头": "30",
    "一个小时": "60",
    "一小时": "60",
    "一钟头": "60",
    "一个钟头": "60",
    "个把小时": "60",
    "一个半小时": "90",
    "三十分钟": "30",
    "十五分钟": "15",
    "四十五分钟": "45",
    "一刻钟": "15",
    "三刻钟": "45",
    "半个": "30",
    "一刻": "15",
    "三刻": "45",
    "两个小时": "120",
    "两小时": "120",
    "两钟头": "120",
    "两个钟头": "120",
    "两个半小时": "150",
    "三个小时": "180",
    "三小时": "180",
}


def _fresh_budget() -> dict[str, int]:
    return {
        "digits": 0,
        "times": 0,
        "dates": 0,
        "durations": 0,
        "day_numbers": 0,
        "ordinals": 0,
        "date_open": 0,
    }


def _lexicon_slot(word: str) -> tuple[str, int] | None:
    canon = _LEXICON_CANON.get(word)
    if canon is None:
        return None
    if word in _DURATION_LEXICON:
        return ("duration", len(canon))
    if word in _TIME_LEXICON:
        return ("time", len(canon))
    return None


def _longest(
    text: str,
    index: int,
    rooms: list[tuple[str, str]],
    purposes: list[tuple[str, str]],
    entities: list[tuple[str, str]],
    budget: dict[str, int],
    room_numbers: set[int],
) -> tuple[int, int, str, str, str] | None:
    candidates: list[tuple[int, int, str, str, str]] = []
    for word, category in _BY_FIRST.get(text[index], ()):
        if not text.startswith(word, index):
            continue
        slot = _lexicon_slot(word)
        if slot is not None and not _fits(budget, slot[0], word, slot[1]):
            continue
        candidates.append((len(word), 1, "lex", word, category))
        break
    for word, category in rooms:
        if text.startswith(word, index):
            candidates.append((len(word), 1, "lex", word, category))
            break
    for word, category in purposes:
        if text.startswith(word, index):
            candidates.append((len(word), 1, "lex", word, category))
            break
    clock = _match_clock_at(text, index)
    if clock is not None and _fits(budget, "time", clock, sum(char.isdigit() for char in clock)):
        candidates.append((len(clock), 0, "lex", clock, "structured-clock"))
    date = _match_date_at(text, index)
    if date is not None and _fits(budget, "date", date, sum(char.isdigit() for char in date)):
        candidates.append((len(date), 0, "lex", date, "structured-date"))
    numbered = _clock_at(text, index)
    if numbered is not None:
        surface, category, digits = numbered
        if _fits(budget, category, surface, digits):
            candidates.append((len(surface), 0, "lex", surface, category))
    ordinal = _ordinal_at(text, index, room_numbers)
    if ordinal is not None and _fits(
        budget, "room-ordinal", ordinal, sum(char.isdigit() for char in ordinal)
    ):
        candidates.append((len(ordinal), 0, "lex", ordinal, "room-ordinal"))
    for surface, kind in entities:
        if surface and text.startswith(surface, index):
            candidates.append((len(surface), 2, "entity", surface, kind))
            break
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))


def _fits(
    budget: dict[str, int],
    category: str,
    surface: str,
    digits: int | None = None,
    *,
    previous_was_month: bool | None = None,
) -> bool:
    kind, cost = _slot_of(category, surface, digits)
    if budget["digits"] + cost > MAX_NUMERIC_DIGITS:
        return False
    open_month = budget["date_open"] == 1 if previous_was_month is None else previous_was_month
    if kind == "time":
        return budget["times"] + 1 <= MAX_TIME_EXPRESSIONS
    if kind == "date":
        return budget["dates"] + 1 <= MAX_DATE_EXPRESSIONS
    if kind == "date-month":
        return budget["dates"] + 1 <= MAX_DATE_EXPRESSIONS
    if kind == "date-day":
        if surface.endswith("号") and budget["day_numbers"] + 1 > MAX_DAY_NUMBERS:
            return False
        if open_month:
            return True
        return budget["dates"] + 1 <= MAX_DATE_EXPRESSIONS
    if kind == "duration":
        return budget["durations"] + 1 <= MAX_DURATION_TOKENS
    if kind == "room-ordinal":
        return budget["ordinals"] + 1 <= MAX_ROOM_ORDINALS
    return True


def _charge(
    budget: dict[str, int],
    category: str,
    surface: str,
    digits: int | None = None,
    *,
    previous_was_month: bool | None = None,
) -> None:
    kind, cost = _slot_of(category, surface, digits)
    open_month = budget["date_open"] == 1 if previous_was_month is None else previous_was_month
    if kind == "time":
        budget["times"] += 1
    elif kind in {"date", "date-month"}:
        budget["dates"] += 1
    elif kind == "date-day":
        if not open_month:
            budget["dates"] += 1
        if surface.endswith("号"):
            budget["day_numbers"] += 1
    elif kind == "duration":
        budget["durations"] += 1
    elif kind == "room-ordinal":
        budget["ordinals"] += 1
    if kind in {"time", "date", "date-month", "date-day", "duration", "room-ordinal"}:
        budget["digits"] += cost
    budget["date_open"] = 1 if kind == "date-month" else 0


def _slot_of(category: str, surface: str, digits: int | None) -> tuple[str, int]:
    lex = _lexicon_slot(surface)
    if lex is not None:
        return lex
    kind = {
        "structured-clock": "time",
        "structured-date": "date",
    }.get(category, category)
    if digits is None:
        digits = sum(char.isdigit() for char in surface)
        if digits == 0:
            parsed = _classify_number_token(surface)
            if parsed is not None:
                return parsed
    return kind, digits


def _clock_at(text: str, index: int) -> tuple[str, str, int] | None:
    decimal = _decimal_at(text, index)
    if decimal is not None:
        return decimal, "duration", sum(char.isdigit() for char in decimal)
    span = _digit_span(text, index)
    if span is not None:
        number = int(text[index:span])
        classified = _classify_unit(text, span, number, text[index:span])
        if classified is not None:
            return classified
    for head, value in _CN_FORMS:
        if not text.startswith(head, index):
            continue
        classified = _classify_unit(text, index + len(head), value, head)
        if classified is not None:
            return classified
    return None


def _classify_unit(text: str, unit_index: int, number: int, head: str) -> tuple[str, str, int] | None:
    """从数字或中文数字后面的单位判断这是时刻、日期还是时长。"""

    if text.startswith("点半", unit_index) and _hour_ok(number, head):
        surface = head + "点半"
        return surface, "time", _value_width(number) + 2
    unit = _unit_at(text, unit_index)
    if unit is None:
        return None
    surface = head + unit
    if unit in {"点", "时", "点钟"} and _hour_ok(number, head):
        return surface, "time", _value_width(number)
    if unit == "分" and number in ALLOWED_MINUTES and _plain_width(number, head, limit=2):
        return surface, "time", _value_width(number)
    if unit == "分钟" and _duration_value_ok(number, head):
        return surface, "duration", _value_width(number)
    if unit in {"小时", "钟头"} and _plain_width(number, head, limit=2):
        minutes = number * 60
        if minutes in _duration_values():
            return surface, "duration", _value_width(minutes)
    if unit == "月" and 1 <= number <= 12 and _plain_width(number, head, limit=2):
        return surface, "date-month", _value_width(number)
    if unit in {"日", "号"} and 1 <= number <= 31 and _plain_width(number, head, limit=2):
        return surface, "date-day", _value_width(number)
    return None


def _hour_ok(number: int, head: str) -> bool:
    return 0 <= number <= 23 and _plain_width(number, head, limit=2)


def _plain_width(number: int, head: str, *, limit: int) -> bool:
    if any(char.isdigit() for char in head):
        return head.isdigit() and len(head) == len(str(number)) and len(head) <= limit
    return True


def _value_width(number: int) -> int:
    return len(str(number))


def _duration_values() -> set[int]:
    return set(range(15, MAX_DURATION_MINUTES + 1, 15))


def _duration_value_ok(number: int, head: str) -> bool:
    if number not in _duration_values():
        return False
    if any(char.isdigit() for char in head):
        return head.isdigit() and len(head) == len(str(number)) and len(head) <= 3
    return True


def _ordinal_at(text: str, index: int, room_numbers: set[int]) -> str | None:
    if not room_numbers or (index > 0 and text[index - 1].isdigit()):
        return None
    end = index
    while end < len(text) and text[end].isdigit():
        end += 1
    width = end - index
    if width < 1 or width > 2:
        return None
    number = int(text[index:end])
    if number not in room_numbers or len(str(number)) != width:
        return None
    for suffix in ("号房间", "号会议室"):
        if text.startswith(suffix, end):
            return text[index : end + len(suffix)]
    return None


def _room_numbers_from_forms(rooms: list[tuple[str, str]]) -> set[int]:
    return _room_numbers_from_allowed(word for word, _category in rooms)


def _room_numbers_from_allowed(allowed: Iterable[str]) -> set[int]:
    numbers: set[int] = set()
    for token in allowed:
        compact = token.replace(" ", "")
        matched = re.fullmatch(r"(.+?)(\d+)", compact)
        if matched is None:
            matched = re.fullmatch(r"(\d+)号.+", compact)
            if matched is None:
                continue
            numbers.add(int(matched.group(1)))
            continue
        numbers.add(int(matched.group(2)))
    return numbers


def _outbound_slot(token: str, room_numbers: set[int]) -> tuple[str, int] | None:
    lex = _lexicon_slot(token)
    if lex is not None:
        return lex
    if _clock_token(token):
        return ("time", sum(char.isdigit() for char in token))
    if _date_token(token):
        return ("date", sum(char.isdigit() for char in token))
    ordinal = _ORDINAL_RE.fullmatch(token)
    if ordinal is not None and int(ordinal.group(1)) in room_numbers:
        return ("room-ordinal", sum(char.isdigit() for char in token))
    return _classify_number_token(token)


def _classify_number_token(token: str) -> tuple[str, int] | None:
    if _clock_token(token) or _date_token(token):
        return None
    decimal = _decimal_minutes(token)
    if decimal is not None:
        return ("duration", sum(char.isdigit() for char in token))
    for head, value in _CN_FORMS:
        if token.startswith(head) and len(token) > len(head):
            classified = _classify_unit(token, len(head), value, head)
            if classified is not None and classified[0] == token:
                return classified[1], classified[2]
    if token[:1].isdigit():
        end = 0
        while end < len(token) and token[end].isdigit():
            end += 1
        if end == 0:
            return None
        classified = _classify_unit(token, end, int(token[:end]), token[:end])
        if classified is not None and classified[0] == token:
            return classified[1], classified[2]
    return None


def _digit_string(token: str) -> str:
    canon = _LEXICON_CANON.get(token)
    if canon is not None:
        return canon
    digits = "".join(char for char in token if char.isdigit())
    if digits:
        return digits
    parsed = _classify_number_token(token)
    if parsed is None:
        return ""
    for head, value in _CN_FORMS:
        if token.startswith(head):
            return str(value) if not token.endswith("点半") else f"{value}30"
    return ""


def _masked_decimal(text: str, index: int) -> int | None:
    """非法小数时长整段遮盖，避免 ``3.5小时`` 漏出后缀 ``5小时``。"""

    if index > 0 and text[index - 1].isdigit():
        return None
    if _decimal_at(text, index) is not None:
        return None
    end = index
    while end < len(text) and text[end].isdigit():
        end += 1
    if end == index or end >= len(text) or text[end] != ".":
        return None
    frac = end + 1
    if frac >= len(text) or not text[frac].isdigit():
        return None
    while frac < len(text) and text[frac].isdigit():
        frac += 1
    unit = _unit_at(text, frac)
    if unit:
        frac += len(unit)
    return frac - index


def _decimal_at(text: str, index: int) -> str | None:
    if index > 0 and text[index - 1].isdigit():
        return None
    end = index
    while end < len(text) and text[end].isdigit():
        end += 1
    if end == index or end - index > 2 or end >= len(text) or text[end] != ".":
        return None
    frac = end + 1
    if frac >= len(text) or not text[frac].isdigit():
        return None
    if frac + 1 < len(text) and text[frac + 1].isdigit():
        return None
    unit = _unit_at(text, frac + 1)
    if unit not in {"小时", "钟头"}:
        return None
    surface = text[index : frac + 1 + len(unit)]
    if _decimal_minutes(surface) is None:
        return None
    return surface


def _digit_span(text: str, index: int) -> int | None:
    """从 index 起的整段数字。前面已经是数字时返回 None，避免放行长串的后缀。"""

    if index > 0 and text[index - 1].isdigit():
        return None
    end = index
    while end < len(text) and text[end].isdigit():
        end += 1
    if end == index:
        return None
    return end


def _match_clock_at(text: str, index: int) -> str | None:
    """``HH:MM``。分钟必须两位。前后不能再接冒号，也不能接在更长的数字上。"""

    if index > 0 and (text[index - 1].isdigit() or text[index - 1] == ":"):
        return None
    end = index
    while end < len(text) and text[end].isdigit():
        end += 1
    hour_len = end - index
    if hour_len < 1 or hour_len > 2 or end >= len(text) or text[end] != ":":
        return None
    minute = end + 1
    minute_end = minute
    while minute_end < len(text) and text[minute_end].isdigit():
        minute_end += 1
    if minute_end - minute != 2 or (minute_end < len(text) and text[minute_end] == ":"):
        return None
    hour = int(text[index:end])
    minute_value = int(text[minute:minute_end])
    if hour > 23 or minute_value not in ALLOWED_MINUTES:
        return None
    return text[index:minute_end]


def _match_date_at(text: str, index: int) -> str | None:
    """完整 ISO 日期，或单独的 ``M/D``、``M-D``。更长的分隔串整段不放行。"""

    if index > 0 and (text[index - 1].isdigit() or text[index - 1] in "-/"):
        return None
    iso = _iso_date_at(text, index)
    if iso is not None:
        return iso
    for separator in "-/":
        short = _short_date_at(text, index, separator)
        if short is not None:
            return short
    return None


def _iso_date_at(text: str, index: int) -> str | None:
    end = index
    while end < len(text) and text[end].isdigit():
        end += 1
    if end - index != 4 or end >= len(text) or text[end] != "-":
        return None
    month = _read_part(text, end + 1, "-")
    if month is None:
        return None
    value, month_end = month
    day = _read_part(text, month_end + 1, None)
    if day is None or not (1 <= value <= 12 and 1 <= day[0] <= 31):
        return None
    return text[index : day[1]]


def _short_date_at(text: str, index: int, separator: str) -> str | None:
    month = _read_part(text, index, separator)
    if month is None:
        return None
    value, month_end = month
    day = _read_part(text, month_end + 1, None)
    if day is None or not (1 <= value <= 12 and 1 <= day[0] <= 31):
        return None
    return text[index : day[1]]


def _read_part(text: str, index: int, separator: str | None) -> tuple[int, int] | None:
    if index >= len(text) or not text[index].isdigit():
        return None
    if index + 1 < len(text) and text[index + 1].isdigit():
        after = index + 2
        if after < len(text) and text[after].isdigit():
            two_ok = False
        elif separator is None:
            two_ok = after == len(text) or text[after] not in "-/"
        else:
            two_ok = after < len(text) and text[after] == separator
        if two_ok:
            return int(text[index:after]), after
    after = index + 1
    if separator is None:
        if after < len(text) and (text[after].isdigit() or text[after] in "-/"):
            return None
        return int(text[index]), after
    if after < len(text) and text[after] == separator:
        return int(text[index]), after
    return None


def _unit_at(text: str, index: int) -> str | None:
    for unit in _UNITS:
        if text.startswith(unit, index):
            return unit
    return None


def _entities(context: DeidContext) -> list[tuple[str, str]]:
    labeled = (
        *((item, "party") for item in context.party_names),
        *((item, "case") for item in context.case_numbers),
        *((item, "directory") for item in context.directory_names),
    )
    found: list[tuple[str, str]] = []
    for value, kind in labeled:
        normalized, _spans = _normalize(value)
        surface = normalized.strip()
        if surface:
            found.append((surface, kind))
    found.sort(key=lambda item: len(item[0]), reverse=True)
    return found


def _mention_at(orig_index: int, mentions: Sequence[Mention]) -> Mention | None:
    for mention in mentions:
        if mention.kind not in {"party", "case"}:
            continue
        if mention.start <= orig_index < mention.end:
            return mention
    return None


def _merge_gaps(pieces: list[dict[str, object]], original: str) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for piece in pieces:
        if piece["kind"] == "gap" and merged and merged[-1]["kind"] == "gap":
            merged[-1]["end"] = piece["end"]
            continue
        if piece["kind"] == "gap" and str(piece.get("value") or "") in "@#":
            if merged and merged[-1]["kind"] == "placeholder":
                continue
            continue
        merged.append(dict(piece))
    for piece in merged:
        if piece["kind"] == "gap":
            piece["kind"] = "placeholder"
            piece["value"] = original[int(piece["start"]) : int(piece["end"])].strip()
            piece["key"] = str(piece["value"])
            piece["role"] = ""
    return [
        piece for piece in merged if not (piece["kind"] == "placeholder" and not piece.get("value"))
    ]


def _assign(
    pieces: list[dict[str, object]],
    prior: Mapping[str, str],
) -> tuple[list[OutboundToken], dict[str, str], dict[str, str]]:
    mapping = {key: value for key, value in prior.items() if _PLACEHOLDER_RE.fullmatch(key)}
    inverse = {value: key for key, value in mapping.items()}
    next_id = 1 + max((int(_PLACEHOLDER_RE.fullmatch(key).group(1)) for key in mapping), default=0)
    tokens: list[OutboundToken] = []
    roles: dict[str, str] = {}
    for piece in pieces:
        if piece["kind"] == "lex":
            tokens.append(
                OutboundToken(
                    text=str(piece["text"]),
                    category=str(piece["category"]),
                    start=int(piece["start"]),
                    end=int(piece["end"]),
                )
            )
            continue
        value = str(piece.get("value") or "")
        key = inverse.get(value)
        if key is None:
            key = f"[P{next_id}]"
            next_id += 1
            inverse[value] = key
            mapping[key] = value
        role = str(piece.get("role") or "")
        if role:
            roles[key] = role
        tokens.append(
            OutboundToken(
                text=key, category="placeholder", start=int(piece["start"]), end=int(piece["end"])
            )
        )
    return tokens, mapping, roles


def _static_digit_run(token: str) -> bool:
    if token in LEXICON:
        return bool(re.search(r"\d{3,}", token))
    return False


def _clock_token(token: str) -> bool:
    return _CLOCK_TOKEN_RE.fullmatch(token) is not None


def _date_token(token: str) -> bool:
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", token)
    if iso:
        return 1 <= int(iso.group(2)) <= 12 and 1 <= int(iso.group(3)) <= 31
    for separator in "-/":
        if token.count(separator) != 1:
            continue
        month, day = token.split(separator)
        if re.fullmatch(r"\d{1,2}", month) and re.fullmatch(r"\d{1,2}", day):
            return 1 <= int(month) <= 12 and 1 <= int(day) <= 31
    return False


def _decimal_minutes(token: str) -> int | None:
    matched = _DECIMAL_HOUR_RE.fullmatch(token)
    if matched is None:
        return None
    minutes = float(matched.group(1)) * 60
    if minutes <= 0 or minutes > MAX_DURATION_MINUTES:
        return None
    rounded = round(minutes)
    if abs(minutes - rounded) > 1e-6 or rounded not in _duration_values():
        return None
    return int(rounded)


def _number_value(token: str) -> int | None:
    """数字单位词的值。超过 60 只接受不超过 180 分钟的时长。"""

    decimal = _decimal_minutes(token)
    if decimal is not None:
        return decimal
    head = token
    unit = ""
    for candidate in _UNITS:
        if head.endswith(candidate) and len(head) > len(candidate):
            head = head[: -len(candidate)]
            unit = candidate
            break
    if not unit:
        return None
    if head.isdigit():
        value = int(head)
        if unit == "分钟" and 0 <= value <= MAX_DURATION_MINUTES and len(head) <= 3:
            if len(head) >= 3 and value < 100:
                return None
            return value
        if 0 <= value <= 60 and len(head) <= 2:
            return value
        return None
    for surface, value in _CN_FORMS:
        if head == surface:
            return value
    return None

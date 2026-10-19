"""本机出站编码器（一句话预约）。

外发决定是白名单，不是姓名或案号识别。归一化之后只做正向最长匹配：
词表里的预约词、调用方交来的房间名和事项名、以及紧邻时间单位的 1–2 位
数字可以留下；其余连续片段换成 ``[P1]``、``[P2]``。同一片段在一次编码里
共用一个占位符。

不变式 I1：每个外发词元要么在词表（含本次房间名、事项名和合法数字单位）里，
要么是占位符。``release_outbound`` 是发 HTTP 之前要再验一次的闸门。映射只
留在本机，审计不记原文。

本模块只使用标准库，不联网，也不读取数据库。案号、当事人、目录姓名、房间名
和事项名全部由调用方按现行权限放进 :class:`DeidContext`。已知姓名和案号按
整段精确匹配收成占位符，供 ``@`` / ``#`` 引用回填；它们不是词表词。

残余风险：完全由词表词拼成的名字（如「周五」）会以词表词离开本机。单字姓氏
不进词表，所以姓氏本身不会作为单字放行。阿拉伯-印度数字、泰文数字和故意
交错的编码不在本词表的归一化范围内。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

MAX_SOURCE_CHARS = 500
MAX_RELEASED_DIGITS = 8

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
半小时 半个小时 半个钟头 一个小时 两个小时 三个小时 一个半小时
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
什么 怎么 哪里 一起 然后 如果 或者 麻烦 谢谢 再约 一次 两个 三个 一个
给我 请帮 帮我约 帮我订 使用 开始 结束 改到 要用 个人
"""
_PUNCT_TEXT = "， 。 、 ； ！ ？ , . ! ? …"
_SINGLE_WANTED = (
    "的了是在要与到去用请把被从对给就也再不没会能为个做约看帮找让很吗吧呀呢啊订留改换空"
)

_UNITS = ("点钟", "分钟", "小时", "钟头", "点半", "点", "时", "分", "号", "日", "月", "天", "周")
_CN_DIGIT = "零一二三四五六七八九"
_PLACEHOLDER_RE = re.compile(r"^\[P([1-9]\d{0,2})\]$")
_NUM_TOKEN_RE = re.compile(
    r"^(?:60|[1-5][0-9]|[0-9]|半|两|零|〇|"
    r"[一二三四五六七八九]?十[一二三四五六七八九]?|[一二三四五六七八九])"
    r"(?:点钟|分钟|小时|钟头|点半|点|时|分|号|日|月|天|周)$"
)


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
) -> dict[str, object]:
    """给将来的输入框用的同一编码器。不返回映射里的原文。"""

    result = deidentify(text, context, mentions=mentions)
    local = 0
    shown: list[dict[str, object]] = []
    for token in result.tokens:
        kept = token.category == "placeholder"
        if kept:
            local += 1
        shown.append({"text": token.text, "local": kept})
    return {
        "text": result.text,
        "localCount": local,
        "tokens": shown,
        "confident": result.confident,
    }


def allowed_surface(context: DeidContext | None = None) -> frozenset[str]:
    """闸门认可的表面形式：固定词表、本次房间变体、本次事项名。"""

    words = set(LEXICON)
    if context is None:
        return frozenset(words)
    for name in context.room_names:
        words.update(room_forms(name))
    for name in context.purpose_names:
        if name and not (len(name) == 1 and name in SURNAMES):
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
    """I1：每个词元在词表、合法数字单位或占位符里，且放行数字不超过 8 位。"""

    if not isinstance(text, str) or not text.strip():
        return False
    allowed_set = allowed if isinstance(allowed, (set, frozenset)) else set(allowed)
    tokens = text.split(" ")
    if any(not token for token in tokens):
        return False
    digits = 0
    for token in tokens:
        if _PLACEHOLDER_RE.fullmatch(token):
            continue
        if token in allowed_set:
            if _static_digit_run(token):
                return False
            # 房间名里的数字是管理员配置的公开名称，不计入用户句子的 8 位上限。
            continue
        if _NUM_TOKEN_RE.fullmatch(token) and _number_value(token) is not None:
            digits += sum(char.isdigit() for char in token)
            continue
        return False
    return digits <= MAX_RELEASED_DIGITS


def release_outbound(text: str, allowed: Iterable[str]) -> str:
    """唯一出站闸门。不满足 I1 就不把文本交还给调用方去发 HTTP。"""

    if not satisfies_outbound_invariant(text, allowed):
        raise OutboundBlocked()
    return text


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
    purposes = [(name, "purpose") for name in context.purpose_names if name]
    purposes.sort(key=lambda item: len(item[0]), reverse=True)
    entities = _entities(context)
    mention_list = [
        item for item in mentions if isinstance(item, Mention) and item.start < item.end
    ]
    pieces: list[dict[str, object]] = []
    digits_used = 0
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
        match = _longest(normalized, index, rooms, purposes, entities, digits_used)
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
            if category == "clock":
                digits_used += sum(char.isdigit() for char in surface)
            pieces.append(
                {
                    "kind": "lex",
                    "text": surface.replace(" ", ""),
                    "category": category,
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


def _longest(
    text: str,
    index: int,
    rooms: list[tuple[str, str]],
    purposes: list[tuple[str, str]],
    entities: list[tuple[str, str]],
    digits_used: int,
) -> tuple[int, int, str, str, str] | None:
    candidates: list[tuple[int, int, str, str, str]] = []
    for word, category in _BY_FIRST.get(text[index], ()):
        if text.startswith(word, index):
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
    clock = _clock_at(text, index, digits_used)
    if clock is not None:
        surface, _value = clock
        candidates.append((len(surface), 0, "lex", surface, "clock"))
    for surface, kind in entities:
        if surface and text.startswith(surface, index):
            candidates.append((len(surface), 2, "entity", surface, kind))
            break
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))


def _clock_at(text: str, index: int, digits_used: int) -> tuple[str, int] | None:
    arabic_end = _arabic_end(text, index)
    if arabic_end is not None:
        number = int(text[index:arabic_end])
        unit = _unit_at(text, arabic_end)
        if unit is not None:
            added = arabic_end - index
            if digits_used + added <= MAX_RELEASED_DIGITS:
                return text[index : arabic_end + len(unit)], number
    for surface, value in _CN_FORMS:
        if not text.startswith(surface, index):
            continue
        unit = _unit_at(text, index + len(surface))
        if unit is None:
            continue
        if digits_used > MAX_RELEASED_DIGITS:
            return None
        return text[index : index + len(surface) + len(unit)], value
    return None


def _arabic_end(text: str, index: int) -> int | None:
    """1–2 位、且不接在别的数字后面、值在 0–60 的数字。3 位及以上整段不放行。"""

    if index > 0 and text[index - 1].isdigit():
        return None
    end = index
    while end < len(text) and text[end].isdigit():
        end += 1
    length = end - index
    if length < 1 or length > 2:
        return None
    number = int(text[index:end])
    if number > 60:
        return None
    return end


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


def _number_value(token: str) -> int | None:
    matched = _NUM_TOKEN_RE.fullmatch(token)
    if matched is None:
        return None
    head = token
    for unit in _UNITS:
        if head.endswith(unit):
            head = head[: -len(unit)]
            break
    if head.isdigit():
        value = int(head)
        return value if 0 <= value <= 60 else None
    for surface, value in _CN_FORMS:
        if head == surface:
            return value
    return None

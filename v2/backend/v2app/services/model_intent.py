"""把编码后的预约句交给模型，并只接受引用和证据。

词表词自己带候选值。本机只核对字段值是否落在被引用的候选里，不从原句另推时间。
没有字面后检查。模型不输出当事人、案号或自由文本。日期加减和业务硬约束在编排层
完成。功能关闭时不编码、不调用、不审计。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .audit import write_security_audit
from .deid import (
    PURPOSE_CATEGORIES,
    DeidContext,
    Mention,
    OutboundBlocked,
    OutboundToken,
    allowed_surface,
    deidentify,
    release_outbound_payload,
)
from .model_provider import (
    ChatMessage,
    ModelCallError,
    ModelProvider,
    guarded_complete,
    open_configured_provider,
)


AUDIT_ACTION = "model.request"
AUDIT_TARGET_TYPE = "conversation"
FALLBACK_UNAVAILABLE = "这次没能准备好预约，请到日历里手动填写。"
FALLBACK_UNCERTAIN = "这句话没法安全发出去，请到日历里手动填写。"

_AUDIT_OUTCOMES = frozenset(
    {
        "ok",
        "timeout",
        "http_error",
        "invalid_json",
        "blocked_invariant",
        "skipped_uncertain",
        "skipped_unconfigured",
    }
)
_CALLED_OUTCOMES = frozenset({"timeout", "http_error", "invalid_json"})
_INTENTS = frozenset({"book", "reschedule", "cancel", "query", "other"})
_PERIODS = frozenset({"morning", "noon", "afternoon", "evening", "any"})
_ROLES = frozenset({"被询问人", "当事人", "案号", "其他"})
_MISSING = frozenset({"day", "start", "duration", "room", "party", "case", "purpose"})
_INTENT_KEYS = frozenset(
    {
        "intent",
        "day",
        "start",
        "duration",
        "room",
        "purpose",
        "refs",
        "evidence",
        "unknown",
        "missing",
        "need_clarification",
    }
)
_EVIDENCE_FIELDS = frozenset({"day", "start", "duration", "room", "purpose"})
_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_CLOCK_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
_PLACEHOLDER_RE = re.compile(r"^\[P([1-9]\d{0,2})\]$")
_EVIDENCE_CATEGORY = {
    "day": frozenset({"day"}),
    "start": frozenset({"period", "clock"}),
    "duration": frozenset({"duration", "clock"}),
    "room": frozenset({"room"}),
    "purpose": frozenset({"purpose", "verb"}),
}
_SYSTEM_RULES = (
    "你是预约语句解析器。只输出一个 JSON 对象，不要输出解释。\n"
    "用户消息里的每个词要么是预约词，要么是占位符 [P1]、[P2]。"
    "你不能发明人名、案号、电话或任何占位符以外的新词。\n"
    "字段只允许：intent、day、start、duration、room、purpose、refs、evidence、"
    "unknown、missing、need_clarification。\n"
    "intent 是 book、reschedule、cancel、query、other 之一。\n"
    'day 是 {"offset":整数}、{"week_offset":整数,"weekday":1到7}、'
    '{"month":1到12,"day":1到31} 或 null。不要输出公历日期。\n'
    "start.period 是 morning、noon、afternoon、evening、any 之一；"
    "start.time 是 HH:MM 或 null。时段只约束开始时间。\n"
    "用户消息里的 HH:MM（分钟只取 00、15、30、45）、YYYY-MM-DD、M/D、M-D 是钟点或日期词；"
    "15 到 180 且按 15 分钟对齐的 N分钟 和 N.N小时是时长词。day 引用日期词，"
    "start.time 引用钟点词，duration.minutes 引用时长词。"
    "事项只能是上下文列出的类别。\n"
    'duration 是 {"minutes":整数或null}。room 和 purpose 只能是上下文列出的值或 null。\n'
    'refs 是 [{"ph":"P1","role":"被询问人|当事人|案号|其他"}]，ph 必须是本句出现过的占位符。\n'
    "evidence 把 day、start、duration、room、purpose 映射到用户消息词元下标，"
    "而且这些词元的类别要和字段一致。字段值必须就是这些词的候选，不能改成别的钟点、"
    "日期或时长。看不懂的词放进 unknown。\n"
    "missing 只能包含 day、start、duration、room、party、case、purpose。"
)


class _SchemaError(Exception):
    pass


@dataclass(frozen=True)
class ModelSettings:
    enabled: bool = False
    provider_name: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    model: str = ""
    timeout_seconds: int = 10


@dataclass(frozen=True)
class IntentDate:
    kind: str
    relative_days: int | None = None
    week_offset: int | None = None
    weekday: int | None = None
    month: int | None = None
    day: int | None = None


@dataclass(frozen=True)
class RefBinding:
    ph: str
    role: str


@dataclass(frozen=True)
class BookingIntent:
    intent: str
    date: IntentDate
    period: str
    time_start: str | None
    duration_minutes: int | None
    room_name: str | None
    purpose: str | None
    refs: tuple[RefBinding, ...]
    evidence: dict[str, tuple[int, ...]]
    unknown: tuple[str, ...]
    need_clarification: bool
    missing: tuple[str, ...]
    unbound: bool = False


@dataclass
class InterpretResult:
    kind: str
    text: str
    intent: BookingIntent | None
    outcome: str | None
    sent_text: str | None
    mapping: dict[str, str] = field(default_factory=dict)
    mention_roles: dict[str, str] = field(default_factory=dict)
    tokens: tuple[OutboundToken, ...] = ()


def interpret_booking_sentence(
    text: str,
    *,
    context: DeidContext | None = None,
    settings: ModelSettings | None = None,
    provider: ModelProvider | None = None,
    now: datetime,
    work_start: str,
    work_end: str,
    room_names: Sequence[str] = (),
    purpose_names: Sequence[str] = (),
    record_audit: Callable[[Mapping[str, object]], None] | None = None,
    data_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
    mentions: Sequence[Mention] | None = None,
    prior: Mapping[str, str] | None = None,
) -> InterpretResult:
    """解析一句预约话。关闭时直接返回，不编码也不调用模型。"""

    if not isinstance(text, str):
        raise TypeError("待解析文本必须是字符串")
    if not isinstance(now, datetime):
        raise TypeError("服务器时间必须是 datetime")
    active_settings = settings if settings is not None else ModelSettings()
    if not isinstance(active_settings, ModelSettings):
        raise TypeError("模型配置必须是 ModelSettings")
    if active_settings.enabled is not True:
        return InterpretResult(kind="disabled", text="", intent=None, outcome=None, sent_text=None)

    active_context = context if context is not None else DeidContext()
    encoded = deidentify(text, active_context, prior=prior, mentions=mentions)
    counts = _placeholder_counts(encoded.mapping)
    if not encoded.confident:
        outcome = "blocked_invariant" if "invariant" in encoded.reasons else "skipped_uncertain"
        _audit(
            record_audit,
            provider_name=active_settings.provider_name,
            model=active_settings.model,
            sent_text=None,
            placeholder_counts=counts,
            latency_ms=0,
            outcome=outcome,
        )
        return InterpretResult(
            kind="fallback",
            text=FALLBACK_UNCERTAIN,
            intent=None,
            outcome=outcome,
            sent_text=None,
            mapping=encoded.mapping,
            tokens=encoded.tokens,
        )

    rooms = clean_room_names(room_names)
    purposes = outbound_purpose_names(purpose_names)
    context_text = render_server_context(
        now=now,
        work_start=work_start,
        work_end=work_end,
        room_names=rooms,
        purpose_names=purposes,
    )
    if context_text is None or not _timeout_ready(active_settings):
        _audit(
            record_audit,
            provider_name=active_settings.provider_name,
            model=active_settings.model,
            sent_text=None,
            placeholder_counts=counts,
            latency_ms=0,
            outcome="skipped_unconfigured",
        )
        return InterpretResult(
            kind="fallback",
            text=FALLBACK_UNAVAILABLE,
            intent=None,
            outcome="skipped_unconfigured",
            sent_text=None,
        )

    active = provider
    if active is None:
        active = open_configured_provider(
            enabled=True,
            provider_name=active_settings.provider_name,
            base_url=active_settings.base_url,
            model=active_settings.model,
            timeout_seconds=active_settings.timeout_seconds,
            data_dir=data_dir,
            environ=environ,
        )
    if active is None or not _provider_ready(active):
        _audit(
            record_audit,
            provider_name=active_settings.provider_name,
            model=active_settings.model,
            sent_text=None,
            placeholder_counts=counts,
            latency_ms=0,
            outcome="skipped_unconfigured",
        )
        return InterpretResult(
            kind="fallback",
            text=FALLBACK_UNAVAILABLE,
            intent=None,
            outcome="skipped_unconfigured",
            sent_text=None,
        )

    allowed = allowed_surface(active_context)
    try:
        messages = assemble_outbound_messages(
            masked_text=encoded.text,
            context_text=context_text,
            allowed=allowed,
        )
    except OutboundBlocked:
        _audit(
            record_audit,
            provider_name=active_settings.provider_name,
            model=active_settings.model,
            sent_text=None,
            placeholder_counts=counts,
            latency_ms=0,
            outcome="blocked_invariant",
        )
        return InterpretResult(
            kind="fallback",
            text=FALLBACK_UNCERTAIN,
            intent=None,
            outcome="blocked_invariant",
            sent_text=None,
            mapping=encoded.mapping,
            mention_roles=dict(encoded.mention_roles or {}),
            tokens=encoded.tokens,
        )
    started = time.perf_counter()
    try:
        content = guarded_complete(
            active,
            messages,
            encoded_text=encoded.text,
            allowed=allowed,
            system_prefix=_SYSTEM_RULES,
        )
    except OutboundBlocked:
        _audit(
            record_audit,
            provider_name=_label(getattr(active, "name", "")),
            model=_label(getattr(active, "model", "")),
            sent_text=None,
            placeholder_counts=counts,
            latency_ms=_elapsed_ms(started),
            outcome="blocked_invariant",
        )
        return InterpretResult(
            kind="fallback",
            text=FALLBACK_UNCERTAIN,
            intent=None,
            outcome="blocked_invariant",
            sent_text=None,
            mapping=encoded.mapping,
            mention_roles=dict(encoded.mention_roles or {}),
            tokens=encoded.tokens,
        )
    except ModelCallError as error:
        outcome = error.outcome if error.outcome in _CALLED_OUTCOMES else "http_error"
        _audit(
            record_audit,
            provider_name=_label(getattr(active, "name", "")),
            model=_label(getattr(active, "model", "")),
            sent_text=encoded.text,
            placeholder_counts=counts,
            latency_ms=_elapsed_ms(started),
            outcome=outcome,
        )
        return InterpretResult(
            kind="fallback",
            text=FALLBACK_UNAVAILABLE,
            intent=None,
            outcome=outcome,
            sent_text=encoded.text,
            mapping=encoded.mapping,
            mention_roles=dict(encoded.mention_roles or {}),
            tokens=encoded.tokens,
        )

    latency_ms = _elapsed_ms(started)
    provider_name = _label(getattr(active, "name", ""))
    model_name = _label(getattr(active, "model", ""))
    outcome = "ok"
    intent: BookingIntent | None
    if not isinstance(content, str):
        intent = None
        outcome = "invalid_json"
    else:
        try:
            intent = parse_booking_intent(
                content,
                room_names=rooms,
                purpose_names=purposes,
                tokens=encoded.tokens,
            )
        except _SchemaError:
            intent = None
            outcome = "invalid_json"
    _audit(
        record_audit,
        provider_name=provider_name,
        model=model_name,
        sent_text=encoded.text,
        placeholder_counts=counts,
        latency_ms=latency_ms,
        outcome=outcome,
    )
    if outcome != "ok" or intent is None:
        return InterpretResult(
            kind="fallback",
            text=FALLBACK_UNAVAILABLE,
            intent=None,
            outcome=outcome,
            sent_text=encoded.text,
            mapping=encoded.mapping,
            mention_roles=dict(encoded.mention_roles or {}),
            tokens=encoded.tokens,
        )
    return InterpretResult(
        kind="intent",
        text="",
        intent=intent,
        outcome="ok",
        sent_text=encoded.text,
        mapping=encoded.mapping,
        mention_roles=dict(encoded.mention_roles or {}),
        tokens=encoded.tokens,
    )


def assemble_outbound_messages(
    *,
    masked_text: str,
    context_text: str,
    allowed,
) -> tuple[ChatMessage, ChatMessage]:
    """唯一组包函数。固定规则是常量；上下文和用户句都要过整包闸门。"""

    system = f"{_SYSTEM_RULES}\n{context_text}"
    release_outbound_payload(system, masked_text, allowed, system_prefix=_SYSTEM_RULES)
    return (
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=masked_text),
    )


def build_messages(*, masked_text: str, context_text: str) -> tuple[ChatMessage, ChatMessage]:
    """系统消息是固定规则加服务器上下文；用户消息只有编码句。"""

    return assemble_outbound_messages(
        masked_text=masked_text,
        context_text=context_text,
        allowed=allowed_surface(None),
    )


def render_server_context(
    *,
    now: datetime,
    work_start: str,
    work_end: str,
    room_names: Sequence[str],
    purpose_names: Sequence[str] = (),
) -> str | None:
    """工作时间只在本机校验配置，不写进提示词。"""

    if not isinstance(work_start, str) or not isinstance(work_end, str):
        return None
    if not _CLOCK_RE.fullmatch(work_start) or not _CLOCK_RE.fullmatch(work_end):
        return None
    if work_start >= work_end:
        return None
    rooms = clean_room_names(room_names)
    purposes = outbound_purpose_names(purpose_names)
    room_line = "、".join(rooms) if rooms else "（无）"
    purpose_line = "、".join(purposes) if purposes else "（无）"
    weekday = _WEEKDAYS[now.weekday()]
    return (
        f"今天是{now:%Y-%m-%d}（{weekday}），当前时间{now:%H:%M}。"
        f"可用房间：{room_line}。事项：{purpose_line}。"
    )


def outbound_purpose_names(names: Sequence[str]) -> tuple[str, ...]:
    """事项只留下代码里的类别。历史自由文本不进入出站上下文。"""

    return tuple(name for name in clean_room_names(names) if name in PURPOSE_CATEGORIES)


def clean_room_names(names: Sequence[str]) -> tuple[str, ...]:
    if isinstance(names, str):
        return ()
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        if not isinstance(name, str):
            continue
        cleaned = " ".join(name.split())
        if not cleaned or cleaned in seen or len(cleaned) > 80:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return tuple(result)


def parse_booking_intent(
    content: str,
    *,
    room_names: Sequence[str],
    purpose_names: Sequence[str] = (),
    tokens: Sequence[OutboundToken] = (),
) -> BookingIntent:
    try:
        return _parse_booking_intent(
            content,
            room_names=room_names,
            purpose_names=purpose_names,
            tokens=tokens,
        )
    except (TypeError, ValueError):
        raise _SchemaError() from None


def model_request_audit(db, *, actor_user_id: str | None = None):
    """把一次模型调用写进现有 ``security_audit_log``。"""

    def record(details: Mapping[str, object]) -> None:
        write_security_audit(
            db,
            action=AUDIT_ACTION,
            target_type=AUDIT_TARGET_TYPE,
            actor_user_id=actor_user_id,
            details={
                key: details[key]
                for key in (
                    "provider",
                    "model",
                    "sentText",
                    "placeholderCounts",
                    "latencyMs",
                    "outcome",
                )
                if key in details
            },
        )

    return record


def _parse_booking_intent(
    content: str,
    *,
    room_names: Sequence[str],
    purpose_names: Sequence[str],
    tokens: Sequence[OutboundToken],
) -> BookingIntent:
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        raise _SchemaError() from None
    if not isinstance(value, dict) or set(value) != _INTENT_KEYS:
        raise _SchemaError()
    intent = value["intent"]
    if not isinstance(intent, str) or intent not in _INTENTS:
        raise _SchemaError()
    start = value["start"]
    if not isinstance(start, dict) or set(start) != {"period", "time"}:
        raise _SchemaError()
    period = start["period"]
    if not isinstance(period, str) or period not in _PERIODS:
        raise _SchemaError()
    clock = _clock(start["time"])
    duration = value["duration"]
    if not isinstance(duration, dict) or set(duration) != {"minutes"}:
        raise _SchemaError()
    minutes = _duration(duration["minutes"])
    allowed_rooms = set(clean_room_names(room_names))
    room_name = _optional_text(value["room"], limit=80)
    if room_name is not None and room_name not in allowed_rooms:
        room_name = None
    allowed_purposes = set(clean_room_names(purpose_names))
    purpose = _optional_text(value["purpose"], limit=80)
    if purpose is not None and purpose not in allowed_purposes:
        purpose = None
    if type(value["need_clarification"]) is not bool:
        raise _SchemaError()
    present = {token.text for token in tokens if token.category == "placeholder"}
    refs = _refs(value["refs"], present)
    evidence = _evidence(value["evidence"], tokens)
    _require_evidence(
        evidence,
        day_set=value["day"] is not None,
        start_set=period != "any" or clock is not None,
        duration_set=minutes is not None,
        room_set=room_name is not None,
        purpose_set=purpose is not None,
    )
    unknown = _unknown(value["unknown"], tokens)
    parsed_date = _date(value["day"])
    unbound = not _values_bind(
        tokens,
        evidence,
        date=parsed_date,
        period=period,
        clock=clock,
        minutes=minutes,
    )
    return BookingIntent(
        intent=intent,
        date=parsed_date,
        period=period,
        time_start=clock,
        duration_minutes=minutes,
        room_name=room_name,
        purpose=purpose,
        refs=refs,
        evidence=evidence,
        unknown=unknown,
        need_clarification=value["need_clarification"] or bool(unknown) or unbound,
        missing=_missing(value["missing"]),
        unbound=unbound,
    )


def _timeout_ready(settings: ModelSettings) -> bool:
    return type(settings.timeout_seconds) is int and 3 <= settings.timeout_seconds <= 30


def _provider_ready(provider: ModelProvider) -> bool:
    name = getattr(provider, "name", "")
    model = getattr(provider, "model", "")
    if not isinstance(name, str) or not _MODEL_NAME_RE.fullmatch(name):
        return False
    if not isinstance(model, str) or not _MODEL_NAME_RE.fullmatch(model):
        return False
    return True


def _evidence(value: object, tokens: Sequence[OutboundToken]) -> dict[str, tuple[int, ...]]:
    if not isinstance(value, dict) or not set(value) <= _EVIDENCE_FIELDS:
        raise _SchemaError()
    parsed: dict[str, tuple[int, ...]] = {}
    for field_name, raw in value.items():
        if not isinstance(raw, list) or any(type(item) is not int for item in raw):
            raise _SchemaError()
        indexes = tuple(raw)
        allowed = _EVIDENCE_CATEGORY[field_name]
        for index in indexes:
            if index < 0 or index >= len(tokens) or tokens[index].category not in allowed:
                raise _SchemaError()
        parsed[field_name] = indexes
    return parsed


def _require_evidence(
    evidence: Mapping[str, tuple[int, ...]],
    *,
    day_set: bool,
    start_set: bool,
    duration_set: bool,
    room_set: bool,
    purpose_set: bool,
) -> None:
    required = {
        "day": day_set,
        "start": start_set,
        "duration": duration_set,
        "room": room_set,
        "purpose": purpose_set,
    }
    for name, needed in required.items():
        if needed and not evidence.get(name):
            raise _SchemaError()


_DAY_VALUE_KEYS = frozenset({"offset", "weekday", "week_offset", "month", "day"})
_START_VALUE_KEYS = frozenset({"time", "minute", "period"})
_DURATION_VALUE_KEYS = frozenset({"minutes"})


def _values_bind(
    tokens: Sequence[OutboundToken],
    evidence: Mapping[str, tuple[int, ...]],
    *,
    date: IntentDate,
    period: str,
    clock: str | None,
    minutes: int | None,
) -> bool:
    """带候选值的词元必须被对应字段引用，字段里的每个成分也必须有引用。"""

    cited = {name: set(indexes) for name, indexes in evidence.items()}
    grouped_by_field: dict[str, list[dict[str, frozenset[str]]]] = {
        "day": [],
        "start": [],
        "duration": [],
    }
    for index, token in enumerate(tokens):
        grouped = _grouped_values(token)
        if not grouped:
            continue
        field_name = _field_for_value_keys(set(grouped))
        if field_name is None or index not in cited.get(field_name, set()):
            return False
        grouped_by_field[field_name].append(grouped)
    return (
        _day_values_bind(date, grouped_by_field["day"])
        and _start_values_bind(period, clock, grouped_by_field["start"])
        and _duration_values_bind(minutes, grouped_by_field["duration"])
    )


def _grouped_values(token: OutboundToken) -> dict[str, frozenset[str]]:
    grouped: dict[str, set[str]] = {}
    for item in token.values:
        if ":" not in item:
            continue
        key, raw = item.split(":", 1)
        if key and raw:
            grouped.setdefault(key, set()).add(raw)
    return {key: frozenset(values) for key, values in grouped.items()}


def _field_for_value_keys(keys: set[str]) -> str | None:
    if keys & _DURATION_VALUE_KEYS:
        return "duration"
    if keys & _START_VALUE_KEYS:
        return "start"
    if keys & _DAY_VALUE_KEYS:
        return "day"
    return None


def _day_values_bind(value: IntentDate, groups: Sequence[Mapping[str, frozenset[str]]]) -> bool:
    components = _day_components(value)
    covered: set[str] = set()
    for grouped in groups:
        for key, options in grouped.items():
            if key not in _DAY_VALUE_KEYS or components.get(key) not in options:
                return False
            covered.add(key)
    return covered == set(components)


def _day_components(value: IntentDate) -> dict[str, str]:
    if value.kind == "relative" and value.relative_days is not None:
        return {"offset": str(value.relative_days)}
    if value.kind == "week":
        parts: dict[str, str] = {}
        if value.week_offset is not None:
            parts["week_offset"] = str(value.week_offset)
        if value.weekday is not None:
            parts["weekday"] = str(value.weekday)
        return parts
    if value.kind == "calendar":
        parts = {}
        if value.month is not None:
            parts["month"] = str(value.month)
        if value.day is not None:
            parts["day"] = str(value.day)
        return parts
    return {}


def _start_values_bind(
    period: str,
    clock: str | None,
    groups: Sequence[Mapping[str, frozenset[str]]],
) -> bool:
    minute_groups = [grouped["minute"] for grouped in groups if "minute" in grouped]
    time_groups = [grouped["time"] for grouped in groups if "time" in grouped]
    period_groups = [grouped["period"] for grouped in groups if "period" in grouped]
    if period == "any":
        if period_groups:
            return False
    elif not period_groups or any(period not in options for options in period_groups):
        return False
    if clock is None:
        return not time_groups and not minute_groups
    hour, minute = clock.split(":")
    waived = bool(minute_groups)
    if any(minute not in options for options in minute_groups):
        return False
    if not time_groups:
        return False
    for options in time_groups:
        if not _clock_in_options(hour, minute, options, minute_waived=waived):
            return False
    return True


def _clock_in_options(
    hour: str, minute: str, options: frozenset[str], *, minute_waived: bool
) -> bool:
    clock = f"{hour}:{minute}"
    if clock in options:
        return True
    if not minute_waived:
        return False
    return any(candidate == f"{hour}:00" for candidate in options)


def _duration_values_bind(
    minutes: int | None, groups: Sequence[Mapping[str, frozenset[str]]]
) -> bool:
    if minutes is None:
        return not groups
    if not groups:
        return False
    expected = str(minutes)
    return all(expected in grouped.get("minutes", frozenset()) for grouped in groups)


def _refs(value: object, present: set[str]) -> tuple[RefBinding, ...]:
    if not isinstance(value, list) or len(value) > 8:
        raise _SchemaError()
    found: list[RefBinding] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"ph", "role"}:
            raise _SchemaError()
        ph = item["ph"]
        role = item["role"]
        if not isinstance(ph, str) or not isinstance(role, str) or role not in _ROLES:
            raise _SchemaError()
        token = ph if ph.startswith("[") else f"[{ph}]"
        if not _PLACEHOLDER_RE.fullmatch(token):
            raise _SchemaError()
        if token not in present:
            continue
        found.append(RefBinding(ph=token, role=role))
    return tuple(found)


def _unknown(value: object, tokens: Sequence[OutboundToken]) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 8:
        raise _SchemaError()
    found: list[str] = []
    placeholders = {token.text for token in tokens if token.category == "placeholder"}
    for item in value:
        if type(item) is int:
            if item < 0 or item >= len(tokens):
                raise _SchemaError()
            found.append(str(item))
            continue
        if isinstance(item, str):
            token = item if item.startswith("[") else f"[{item}]"
            if token in placeholders:
                found.append(token)
                continue
        raise _SchemaError()
    return tuple(found)


def _date(value: object) -> IntentDate:
    if value is None:
        return IntentDate(kind="missing")
    if not isinstance(value, dict):
        raise _SchemaError()
    if set(value) == {"offset"}:
        return IntentDate(
            kind="relative", relative_days=_as_int(value["offset"], minimum=-366, maximum=366)
        )
    if set(value) == {"week_offset", "weekday"}:
        return IntentDate(
            kind="week",
            week_offset=_as_int(value["week_offset"], minimum=-4, maximum=12),
            weekday=_as_int(value["weekday"], minimum=1, maximum=7),
        )
    if set(value) == {"month", "day"}:
        return IntentDate(
            kind="calendar",
            month=_as_int(value["month"], minimum=1, maximum=12),
            day=_as_int(value["day"], minimum=1, maximum=31),
        )
    raise _SchemaError()


def _missing(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or item not in _MISSING for item in value
    ):
        raise _SchemaError()
    return tuple(dict.fromkeys(value))


def _duration(value: object) -> int | None:
    if value is None:
        return None
    return _as_int(value, minimum=0, maximum=24 * 60)


def _clock(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _CLOCK_RE.fullmatch(value):
        raise _SchemaError()
    return value


def _optional_text(value: object, *, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _SchemaError()
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > limit:
        raise _SchemaError()
    return cleaned


def _as_int(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or isinstance(value, bool) or not minimum <= value <= maximum:
        raise _SchemaError()
    return value


def _placeholder_counts(mapping: Mapping[str, str]) -> dict[str, int]:
    count = sum(1 for key in mapping if _PLACEHOLDER_RE.fullmatch(key))
    return {"P": count} if count else {}


def _label(value: object) -> str:
    if not isinstance(value, str) or not _MODEL_NAME_RE.fullmatch(value):
        return ""
    return value


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _audit(
    record: Callable[[Mapping[str, object]], None] | None,
    *,
    provider_name: str,
    model: str,
    sent_text: str | None,
    placeholder_counts: Mapping[str, int],
    latency_ms: int,
    outcome: str,
) -> None:
    if record is None or outcome not in _AUDIT_OUTCOMES:
        return
    record(
        {
            "provider": _label(provider_name),
            "model": _label(model),
            "sentText": sent_text,
            "placeholderCounts": dict(placeholder_counts),
            "latencyMs": latency_ms,
            "outcome": outcome,
        }
    )

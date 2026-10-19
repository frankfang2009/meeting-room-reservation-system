"""把编码后的预约句交给模型，并只接受引用和证据。

没有本地时间规则，也没有字面后检查。模型不输出当事人、案号或自由文本。
日期加减和业务硬约束在编排层完成。功能关闭时不编码、不调用、不审计。
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
    DeidContext,
    Mention,
    OutboundBlocked,
    OutboundToken,
    allowed_surface,
    deidentify,
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
    'duration 是 {"minutes":整数或null}。room 和 purpose 只能是上下文列出的值或 null。\n'
    'refs 是 [{"ph":"P1","role":"被询问人|当事人|案号|其他"}]，ph 必须是本句出现过的占位符。\n'
    "evidence 把 day、start、duration、room、purpose 映射到用户消息词元下标，"
    "而且这些词元的类别要和字段一致。看不懂的词放进 unknown。\n"
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
    encoded = deidentify(text, active_context, mentions=mentions)
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
    purposes = clean_room_names(purpose_names)
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

    messages = build_messages(masked_text=encoded.text, context_text=context_text)
    allowed = allowed_surface(active_context)
    started = time.perf_counter()
    try:
        content = guarded_complete(
            active,
            messages,
            encoded_text=encoded.text,
            allowed=allowed,
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


def build_messages(*, masked_text: str, context_text: str) -> tuple[ChatMessage, ChatMessage]:
    """系统消息是固定规则加服务器上下文；用户消息只有编码句。"""

    return (
        ChatMessage(role="system", content=f"{_SYSTEM_RULES}\n{context_text}"),
        ChatMessage(role="user", content=masked_text),
    )


def render_server_context(
    *,
    now: datetime,
    work_start: str,
    work_end: str,
    room_names: Sequence[str],
    purpose_names: Sequence[str] = (),
) -> str | None:
    if not isinstance(work_start, str) or not isinstance(work_end, str):
        return None
    if not _CLOCK_RE.fullmatch(work_start) or not _CLOCK_RE.fullmatch(work_end):
        return None
    if work_start >= work_end:
        return None
    rooms = clean_room_names(room_names)
    purposes = clean_room_names(purpose_names)
    room_line = "、".join(rooms) if rooms else "（无）"
    purpose_line = "、".join(purposes) if purposes else "（无）"
    weekday = _WEEKDAYS[now.weekday()]
    return (
        f"今天是{now:%Y-%m-%d}（{weekday}），当前时间{now:%H:%M}。"
        f"工作时段{work_start}–{work_end}。可用房间：{room_line}。事项：{purpose_line}。"
    )


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
    return BookingIntent(
        intent=intent,
        date=_date(value["day"]),
        period=period,
        time_start=clock,
        duration_minutes=minutes,
        room_name=room_name,
        purpose=purpose,
        refs=refs,
        evidence=evidence,
        unknown=unknown,
        need_clarification=value["need_clarification"] or bool(unknown),
        missing=_missing(value["missing"]),
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

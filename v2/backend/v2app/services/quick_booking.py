"""把模型给出的引用编排成可确认的草稿。

每句话都走模型。本模块只做日期加减、工作时间与时长硬约束、本人历史补全
和占位符回填。时段词只约束开始时间：上午 08:30–12:00，中午 11:30–13:30，
下午 12:00–17:30，晚上 18:00–23:00。开始必须落在窗口和工作时间的交集里，
结束可以越过时段窗口，不能越过工作时间。交不上时改到最近的合法开始。
当事人和案号只来自本句占位符或本人历史。事项不采用模型自由文本。
功能默认关闭。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import current_app

from ..common import canonical_json, clean_text, local_now, minutes_to_time, new_id, time_to_minutes
from ..db import get_db, transaction
from ..errors import ApiError
from ..security import locked_actor
from .deid import DeidContext, Mention
from .model_intent import (
    FALLBACK_UNAVAILABLE,
    BookingIntent,
    ModelSettings,
    interpret_booking_sentence,
    model_request_audit,
)
from .model_provider import read_model_api_key
from .reservations import _create_in_transaction, _row_for_id, serialize_reservation


TOKEN_TTL = timedelta(minutes=10)
HORIZON_DAYS = 60
_DURATION_CHOICES = (60, 120, "window")
_GUIDE_TEXT = "改期、取消和查询请到「我的预约」里处理。"
_COUNT_WORDS = {2: "两", 3: "三"}
_BUILTIN_PURPOSES = ("询问笔录", "谈话笔录", "调解笔录")
# 开始时间窗口（含两端）。结束时间可以晚于窗口，不能晚于工作时间。
_PERIOD_BOUNDS = {
    "morning": (8 * 60 + 30, 12 * 60),
    "noon": (11 * 60 + 30, 13 * 60 + 30),
    "afternoon": (12 * 60, 17 * 60 + 30),
    "evening": (18 * 60, 23 * 60),
}
_PERIOD_LABELS = {
    "morning": "上午",
    "noon": "中午",
    "afternoon": "下午",
    "evening": "晚上",
}


def conversation_available(db: sqlite3.Connection) -> bool:
    """功能已开启，且测试替身或已配置的模型密钥可用。"""

    row = db.execute("SELECT enabled, model FROM conversation_settings WHERE id = 1").fetchone()
    if row is None or int(row["enabled"]) != 1:
        return False
    if current_app.config.get("QUICK_BOOKING_PROVIDER") is not None:
        return True
    model = row["model"]
    if not isinstance(model, str) or not model.strip():
        return False
    data_dir = current_app.config.get("DATA_DIR")
    key = read_model_api_key(data_dir=Path(data_dir) if data_dir else None)
    return key is not None


def deid_context_for_user(db: sqlite3.Connection, user_id: str) -> DeidContext:
    """脱敏词表：本人案号和当事人、启用用户的显示名、启用笔录室。

    不读取其他人的预约。显示名只用于把同事姓名打成占位符，不进入提示词。
    """

    parties = db.execute(
        """
        SELECT DISTINCT party_name FROM reservations
        WHERE owner_user_id = ? AND status = 'active' AND party_name != ''
        ORDER BY party_name
        """,
        (user_id,),
    ).fetchall()
    cases = db.execute(
        """
        SELECT DISTINCT case_number FROM reservations
        WHERE owner_user_id = ? AND status = 'active' AND case_number != ''
        ORDER BY case_number
        """,
        (user_id,),
    ).fetchall()
    directory = db.execute(
        """
        SELECT display_name FROM users
        WHERE is_active = 1 AND display_name != ''
        ORDER BY display_name
        """
    ).fetchall()
    rooms = db.execute(
        """
        SELECT name FROM rooms
        WHERE is_active = 1 AND name != ''
        ORDER BY sort_order, name
        """
    ).fetchall()
    own_purposes = db.execute(
        """
        SELECT DISTINCT purpose FROM reservations
        WHERE owner_user_id = ? AND status = 'active' AND purpose != ''
        ORDER BY purpose
        """,
        (user_id,),
    ).fetchall()
    purposes: list[str] = []
    for name in (*_BUILTIN_PURPOSES, *(row["purpose"] for row in own_purposes)):
        if name not in purposes:
            purposes.append(name)
    return DeidContext(
        party_names=tuple(row["party_name"] for row in parties),
        case_numbers=tuple(row["case_number"] for row in cases),
        directory_names=tuple(row["display_name"] for row in directory),
        room_names=tuple(row["name"] for row in rooms),
        purpose_names=tuple(purposes),
    )


def send_message(
    conversation_id: str,
    text: object,
    mentions: object = None,
) -> dict[str, Any]:
    """解析一句预约话。``conversation_id`` 为 ``new`` 时新建会话。"""

    db = get_db()
    settings = _require_feature(db)
    sentence = _sentence(text)
    parsed_mentions = _parse_mentions(mentions)
    actor = locked_actor(db)
    existing = None
    if conversation_id != "new":
        existing = _own_conversation(db, conversation_id, actor["id"])
    context = deid_context_for_user(db, actor["id"])
    result = interpret_booking_sentence(
        sentence,
        context=context,
        settings=_model_settings(settings),
        provider=current_app.config.get("QUICK_BOOKING_PROVIDER"),
        now=local_now(),
        work_start=_work_clock(db, "work_start"),
        work_end=_work_clock(db, "work_end"),
        room_names=context.room_names,
        purpose_names=context.purpose_names,
        record_audit=model_request_audit(db, actor_user_id=actor["id"]),
        data_dir=_data_dir(),
        mentions=parsed_mentions,
    )
    if result.kind == "intent" and result.intent is not None and result.intent.intent == "book":
        planned = _plan(
            db,
            actor["id"],
            result.intent,
            result.mapping,
            result.mention_roles,
        )
    elif result.kind == "intent" and result.intent is not None:
        planned = _guide()
    else:
        planned = _fallback(result.text or FALLBACK_UNAVAILABLE)
    with transaction(db):
        actor = locked_actor(db)
        _require_feature(db)
        if existing is None:
            conversation_id = new_id()
            db.execute(
                """
                INSERT INTO conversations (id, owner_user_id, title, draft_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    actor["id"],
                    _title(planned["title"]),
                    canonical_json(planned["draft"]),
                ),
            )
        else:
            conversation_id = existing["id"]
            _revoke_unused_tokens(db, conversation_id, actor["id"])
            db.execute(
                """
                UPDATE conversations
                SET title = ?, draft_json = ?, status = 'open',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id = ? AND owner_user_id = ?
                """,
                (
                    _title(planned["title"]),
                    canonical_json(planned["draft"]),
                    conversation_id,
                    actor["id"],
                ),
            )
        prepared = _issue_token(db, actor, conversation_id, planned)
    return _envelope(conversation_id, planned, prepared)


def prepare_booking(body: dict[str, Any]) -> dict[str, Any]:
    """按用户点选的房间、时长、案号或事项重算草稿，不调用模型。"""

    db = get_db()
    _require_feature(db)
    if not isinstance(body, dict):
        raise ApiError(400, "JSON_REQUIRED", "请求 JSON 必须是对象")
    unknown = set(body) - {"draftId", "roomId", "duration", "caseNumber", "purpose", "partyName"}
    if unknown or "draftId" not in body:
        raise ApiError(422, "VALIDATION_ERROR", "请检查输入内容", fields={"draftId": "请提供草稿"})
    draft_id = body["draftId"]
    if not isinstance(draft_id, str) or not draft_id:
        raise ApiError(422, "VALIDATION_ERROR", "请检查输入内容", fields={"draftId": "请提供草稿"})
    actor = locked_actor(db)
    row = _own_conversation(db, draft_id, actor["id"])
    state = _load_draft(row["draft_json"])
    _apply_prepare(state, body, db, actor["id"])
    planned = _finalize(db, actor["id"], state)
    with transaction(db):
        actor = locked_actor(db)
        _require_feature(db)
        _revoke_unused_tokens(db, row["id"], actor["id"])
        db.execute(
            """
            UPDATE conversations
            SET title = ?, draft_json = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ? AND owner_user_id = ?
            """,
            (
                _title(planned["title"]),
                canonical_json(planned["draft"]),
                row["id"],
                actor["id"],
            ),
        )
        prepared = _issue_token(db, actor, row["id"], planned)
    return _envelope(row["id"], planned, prepared)


def commit_booking(body: dict[str, Any]) -> dict[str, Any]:
    """在同一个事务里复核凭证、权限和冲突，再走现有创建预约路径。"""

    db = get_db()
    _require_feature(db)
    if not isinstance(body, dict) or not isinstance(body.get("token"), str) or not body["token"]:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={"token": "请提供预约凭证"},
        )
    token = body["token"]
    digest = _token_hash(token)
    with transaction(db):
        actor = locked_actor(db)
        _require_feature(db)
        row = db.execute(
            "SELECT * FROM prepared_bookings WHERE token_hash = ?",
            (digest,),
        ).fetchone()
        if row is None or row["owner_user_id"] != actor["id"]:
            raise ApiError(404, "NOT_FOUND", "预约草稿不存在")
        if int(row["session_version"]) != int(actor["session_version"]):
            raise ApiError(401, "SESSION_EXPIRED", "账号状态已变化，请重新登录")
        if row["used_at"] is not None:
            raise ApiError(409, "PREPARED_USED", "这份预约草稿已经确认过")
        expires_at = _parse_expiry(row["expires_at"])
        if local_now() >= expires_at:
            raise ApiError(410, "PREPARED_EXPIRED", "这份预约草稿已过期")
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise ApiError(410, "PREPARED_EXPIRED", "这份预约草稿已过期")
        reservation_id = _create_in_transaction(db, actor, payload)
        db.execute(
            """
            UPDATE prepared_bookings
            SET used_at = ?
            WHERE id = ? AND used_at IS NULL
            """,
            (local_now().isoformat(), row["id"]),
        )
        db.execute(
            """
            UPDATE conversations
            SET status = 'booked', reservation_id = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ? AND owner_user_id = ?
            """,
            (reservation_id, row["conversation_id"], actor["id"]),
        )
        conversation_id = row["conversation_id"]
    saved = _row_for_id(db, reservation_id)
    body_out = serialize_reservation(saved, actor)
    body_out["conversationId"] = conversation_id
    return body_out


def _require_feature(db: sqlite3.Connection) -> sqlite3.Row:
    row = db.execute("SELECT * FROM conversation_settings WHERE id = 1").fetchone()
    if row is None or int(row["enabled"]) != 1:
        raise ApiError(403, "FEATURE_DISABLED", "对话预约未开启")
    return row


def _model_settings(row: sqlite3.Row) -> ModelSettings:
    return ModelSettings(
        enabled=True,
        provider_name=str(row["provider"] or "deepseek"),
        base_url=str(row["base_url"] or "https://api.deepseek.com"),
        model=str(row["model"] or ""),
        timeout_seconds=int(row["timeout_seconds"]),
    )


def _sentence(text: object) -> str:
    if not isinstance(text, str):
        raise ApiError(422, "VALIDATION_ERROR", "请检查输入内容", fields={"text": "请输入一句话"})
    sentence = text.strip()
    if not sentence:
        raise ApiError(422, "VALIDATION_ERROR", "请检查输入内容", fields={"text": "请输入一句话"})
    if len(sentence) > 200:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={"text": "一句话不能超过 200 个字符"},
        )
    return sentence


def _own_conversation(db: sqlite3.Connection, conversation_id: str, user_id: str) -> sqlite3.Row:
    row = db.execute(
        """
        SELECT * FROM conversations
        WHERE id = ? AND owner_user_id = ?
        """,
        (conversation_id, user_id),
    ).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "预约草稿不存在")
    return row


def _parse_mentions(value: object) -> tuple[Mention, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ApiError(
            422, "VALIDATION_ERROR", "请检查输入内容", fields={"mentions": "引用格式不正确"}
        )
    found: list[Mention] = []
    for item in value:
        if not isinstance(item, dict):
            raise ApiError(
                422, "VALIDATION_ERROR", "请检查输入内容", fields={"mentions": "引用格式不正确"}
            )
        kind = item.get("kind")
        raw_value = item.get("value")
        if kind not in {"party", "case"} or not isinstance(raw_value, str) or not raw_value.strip():
            raise ApiError(
                422, "VALIDATION_ERROR", "请检查输入内容", fields={"mentions": "引用格式不正确"}
            )
        try:
            start = int(item["start"])
            end = int(item["end"])
        except (KeyError, TypeError, ValueError) as error:
            raise ApiError(
                422, "VALIDATION_ERROR", "请检查输入内容", fields={"mentions": "引用格式不正确"}
            ) from error
        found.append(Mention(start=start, end=end, kind=kind, value=raw_value.strip()))
    return tuple(found)


def _plan(
    db: sqlite3.Connection,
    user_id: str,
    intent: BookingIntent,
    mapping: dict[str, str],
    mention_roles: dict[str, str] | None = None,
) -> dict[str, Any]:
    today = local_now().date()
    target = _resolve_date(intent, today)
    state = _blank_state()
    state["timePart"] = intent.period or "any"
    state["timeStart"] = intent.time_start
    state["periodLabel"] = _PERIOD_LABELS.get(intent.period or "", "")
    state["startExplicit"] = bool(intent.time_start)
    state["mustClarify"] = bool(intent.need_clarification)
    if intent.unknown:
        state["unknownClarify"] = True
    if intent.duration_minutes is None and (
        intent.need_clarification or "duration" in intent.missing
    ):
        state["ambiguousDuration"] = True
        state["duration"] = None
    elif intent.duration_minutes is None:
        state["duration"] = None
        state["durationExplicit"] = False
    elif _duration_allowed(intent.duration_minutes):
        state["duration"] = intent.duration_minutes
        state["durationExplicit"] = True
    else:
        state["duration"] = _nearest_duration(intent.duration_minutes)
        state["durationExplicit"] = False
        state["snapped"] = True
    if target is None or target < today or (target - today).days > HORIZON_DAYS:
        state["date"] = None
        return _finalize(db, user_id, state)
    state["date"] = target.isoformat()
    room_name = intent.room_name
    if room_name:
        room = db.execute(
            "SELECT id FROM rooms WHERE name = ? AND is_active = 1",
            (room_name,),
        ).fetchone()
        if room is not None:
            state["roomId"] = room["id"]
            state["roomExplicit"] = True
    parties, cases = _bindings(intent, mapping, mention_roles or {})
    if len(parties) > 1 or len(cases) > 1:
        state["identityClarify"] = True
        state["identityOptions"] = parties or cases
    else:
        state["partyName"] = parties[0] if parties else ""
        state["caseNumber"] = cases[0] if cases else ""
    sentence_purpose = intent.purpose or ""
    _refresh_identity(
        db,
        user_id,
        state,
        keep_case=bool(state.get("caseNumber")),
        keep_purpose=bool(sentence_purpose),
    )
    if sentence_purpose:
        state["purpose"] = sentence_purpose
        state["purposeFromHistory"] = False
    if state.get("duration") is None and not state.get("ambiguousDuration"):
        state["duration"] = _median_duration(db, user_id, state.get("purpose") or "")
        state["durationExplicit"] = False
    return _finalize(db, user_id, state)


def _bindings(
    intent: BookingIntent,
    mapping: dict[str, str],
    mention_roles: dict[str, str],
) -> tuple[list[str], list[str]]:
    parties: list[str] = []
    cases: list[str] = []
    for ref in intent.refs:
        forced = mention_roles.get(ref.ph)
        role = ref.role
        if forced == "party":
            role = "当事人"
        elif forced == "case":
            role = "案号"
        if role == "其他":
            continue
        value = _restore(ref.ph, mapping)
        if not value:
            continue
        if role in {"当事人", "被询问人"}:
            if value not in parties:
                parties.append(value)
        elif role == "案号" and value not in cases:
            cases.append(value)
    return parties, cases


def _nearest_duration(minutes: int) -> int:
    snapped = int(round(minutes / 30.0) * 30)
    return min(180, max(30, snapped))


def _median_duration(db: sqlite3.Connection, user_id: str, purpose: str) -> int:
    if not purpose:
        return 60
    rows = db.execute(
        """
        SELECT start_time, end_time FROM reservations
        WHERE owner_user_id = ? AND status = 'active' AND purpose = ?
        """,
        (user_id, purpose),
    ).fetchall()
    values: list[int] = []
    for row in rows:
        span = time_to_minutes(row["end_time"], field="end") - time_to_minutes(
            row["start_time"], field="start"
        )
        if _duration_allowed(span):
            values.append(span)
    if not values:
        return 60
    values.sort()
    return values[len(values) // 2]


def _apply_prepare(
    state: dict[str, Any],
    body: dict[str, Any],
    db: sqlite3.Connection,
    user_id: str,
) -> None:
    keep_case = "caseNumber" not in body and bool(state.get("caseNumber"))
    keep_purpose = "purpose" in body
    if "partyName" in body:
        state["partyName"] = _optional_text(body["partyName"], "partyName", "当事人姓名")
        if "caseNumber" not in body:
            state["caseNumber"] = ""
            keep_case = False
    if "caseNumber" in body:
        state["caseNumber"] = _optional_text(body["caseNumber"], "caseNumber", "案号")
        state["caseFromHistory"] = False
        keep_case = True
    if "purpose" in body:
        state["purpose"] = _optional_text(body["purpose"], "purpose", "事项")
        state["purposeFromHistory"] = False
    if "partyName" in body or "caseNumber" in body:
        _refresh_identity(
            db,
            user_id,
            state,
            keep_case=keep_case,
            keep_purpose=keep_purpose,
        )
    if "roomId" in body:
        room_id = body["roomId"]
        if not isinstance(room_id, str) or not room_id:
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "请检查输入内容",
                fields={"roomId": "请选择笔录室"},
            )
        state["roomId"] = room_id
        state["roomExplicit"] = True
    if "duration" in body:
        duration = body["duration"]
        if duration == "window":
            state["durationMode"] = "window"
            state["ambiguousDuration"] = False
            state["durationExplicit"] = False
        elif type(duration) is int and 30 <= duration <= 180 and duration % 30 == 0:
            state["duration"] = duration
            state["durationMode"] = "fixed"
            state["ambiguousDuration"] = False
            state["durationExplicit"] = True
        else:
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                "请检查输入内容",
                fields={"duration": "预约时长必须是 30–180 分钟，且按 30 分钟递增"},
            )


def _refresh_identity(
    db: sqlite3.Connection,
    user_id: str,
    state: dict[str, Any],
    *,
    keep_case: bool,
    keep_purpose: bool,
) -> None:
    """事项只从本人历史带出。找不到时留空，提交会被现有校验拦住。"""

    prefs = _preferences(db, user_id)
    party = state.get("partyName") or ""
    case_number = state.get("caseNumber") or ""
    rows = _own_rows(db, user_id, party=party or None, case_number=case_number or None)
    if not keep_case and party:
        rows = _own_rows(db, user_id, party=party)
        distinct: list[sqlite3.Row] = []
        seen: set[str] = set()
        for row in rows:
            if row["case_number"] in seen:
                continue
            seen.add(row["case_number"])
            distinct.append(row)
        state["caseOptions"] = [
            {
                "caseNumber": row["case_number"],
                "purpose": row["purpose"],
                "date": row["booking_date"],
            }
            for row in distinct[:8]
        ]
        if len(distinct) == 1:
            _apply_history_row(state, distinct[0], keep_purpose=keep_purpose)
        elif len(distinct) > 1:
            state["caseNumber"] = ""
            state["caseFromHistory"] = False
            if not keep_purpose:
                state["purpose"] = ""
                state["purposeFromHistory"] = False
            state["tagSlot"] = _fallback_tag(prefs)
        else:
            state["caseNumber"] = ""
            state["caseFromHistory"] = False
            if not keep_purpose:
                state["purpose"] = ""
                state["purposeFromHistory"] = False
            state["tagSlot"] = _fallback_tag(prefs)
        return
    if case_number or party:
        matched = rows[0] if rows else None
        if matched is not None:
            _apply_history_row(state, matched, keep_purpose=keep_purpose)
            if keep_case:
                state["caseNumber"] = case_number or matched["case_number"]
            return
    if not keep_purpose:
        state["purpose"] = ""
        state["purposeFromHistory"] = False
    state["caseFromHistory"] = False
    state["historyDate"] = None
    state["tagSlot"] = _fallback_tag(prefs)
    state["caseOptions"] = []


def _apply_history_row(state: dict[str, Any], row: sqlite3.Row, *, keep_purpose: bool) -> None:
    state["caseNumber"] = row["case_number"]
    state["caseFromHistory"] = True
    state["historyDate"] = row["booking_date"]
    state["tagSlot"] = int(row["tag_slot"])
    state["caseOptions"] = []
    if not keep_purpose:
        state["purpose"] = row["purpose"]
        state["purposeFromHistory"] = True


def _finalize(db: sqlite3.Connection, user_id: str, state: dict[str, Any]) -> dict[str, Any]:
    if state.get("unknownClarify"):
        return _clarify(state, "这句话里有没看懂的部分。", options=[])
    if state.get("identityClarify"):
        return _clarify(state, "这句话里有不止一个当事人或案号，要哪一个？", options=[])
    if not state.get("date"):
        return _clarify(state, "这个日期不在可预约范围内。", options=[])
    settings = _system_settings(db)
    _constrain_start(state, settings)
    rooms = _active_rooms(db)
    windows = _free_windows(db, state, rooms, settings)
    if not windows:
        if _period_too_short(state, settings):
            return _clarify(state, "这个时段在工作时间里不够放下这段时长。", options=[])
        return _clarify(state, "这个时段没有空着的笔录室。", options=[])
    chosen = _choose_room(db, user_id, state, windows)
    if state.get("ambiguousDuration"):
        options = _room_options(windows if chosen is None else [chosen])
        return _clarify(
            state,
            _room_question(state, len(options) or len(windows)),
            options=options[:3],
            duration=True,
            default_duration=int(_preferences(db, user_id)["default_duration"]),
        )
    if chosen is None:
        options = _room_options(windows)
        return _clarify(state, _room_question(state, len(options)), options=options[:3])
    if len(state.get("caseOptions") or []) > 1 and not state.get("caseNumber"):
        return _clarify(
            state,
            "这个当事人名下有多条案号，要带哪一条？",
            options=[
                {
                    "type": "case",
                    "caseNumber": item["caseNumber"],
                    "date": item["date"],
                    "purpose": item["purpose"],
                }
                for item in state["caseOptions"]
            ],
        )
    start = _start_for(state, chosen, settings)
    if start is None:
        return _clarify(state, "这个时段没有足够的连续空档。", options=_room_options(windows)[:3])
    state["roomId"] = chosen["roomId"]
    state["start"] = minutes_to_time(start)
    if state.get("durationMode") == "window":
        state["duration"] = chosen["maxDuration"]
        state["durationExplicit"] = False
    if not _card_is_legal(state, settings):
        return _clarify(
            state, "这个时段放不进工作时间。", options=_nearest_options(db, state, settings)
        )
    if state.get("mustClarify"):
        return _clarify(
            state,
            "这句话还需要确认。",
            options=_room_options([chosen]),
        )
    return _prepared_plan(state, chosen["roomName"])


def _choose_room(
    db: sqlite3.Connection,
    user_id: str,
    state: dict[str, Any],
    windows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    by_id = {item["roomId"]: item for item in windows}
    explicit = state.get("roomId")
    if state.get("roomExplicit") and explicit:
        return by_id.get(explicit)
    if not windows:
        return None
    recent = _recent_room_id(db, user_id, state)
    if recent and recent in by_id:
        return by_id[recent]
    prefs = _preferences(db, user_id)
    default_room = prefs["default_room_id"]
    if default_room and default_room in by_id:
        return by_id[default_room]
    counts = _most_used(db, user_id)
    ranked = sorted(
        ((counts.get(item["roomId"], 0), item["roomId"]) for item in windows),
        key=lambda item: (-item[0], item[1]),
    )
    best_count = ranked[0][0]
    leaders = [item for item in ranked if item[0] == best_count and item[0] > 0]
    if len(leaders) == 1:
        return by_id[leaders[0][1]]
    if len(windows) == 1:
        return windows[0]
    return None


def _free_windows(
    db: sqlite3.Connection,
    state: dict[str, Any],
    rooms: list[sqlite3.Row],
    settings: sqlite3.Row,
) -> list[dict[str, Any]]:
    step = int(settings["slot_minutes"])
    work_start = time_to_minutes(settings["work_start"], field="workStart")
    work_end = time_to_minutes(settings["work_end"], field="workEnd")
    window_start, window_end = _time_window(state, work_start, work_end, step)
    target = date.fromisoformat(state["date"])
    if target == local_now().date():
        window_start = max(window_start, _strictly_after_now(work_start, step))
    needed = (
        step
        if state.get("ambiguousDuration") or state.get("durationMode") == "window"
        else int(state.get("duration") or step)
    )
    result = []
    for room in rooms:
        occupied = {
            row["slot_start"]
            for row in db.execute(
                """
                SELECT s.slot_start
                FROM reservation_slots s
                JOIN reservations r ON r.id = s.reservation_id
                WHERE s.room_id = ? AND s.booking_date = ? AND r.status = 'active'
                """,
                (room["id"], state["date"]),
            )
        }
        spans = _free_spans(window_start, window_end, step, occupied)
        usable = [span for span in spans if span[1] - span[0] >= needed]
        if not usable:
            continue
        span = usable[0]
        length = span[1] - span[0]
        length -= length % step
        length = min(length, int(settings["max_duration_minutes"]))
        if length < step:
            continue
        result.append(
            {
                "roomId": room["id"],
                "roomName": room["name"],
                "sortOrder": room["sort_order"],
                "windowStart": minutes_to_time(span[0]),
                "windowEnd": minutes_to_time(span[1]),
                "maxDuration": length,
                "spanStart": span[0],
                "spanEnd": span[1],
            }
        )
    result.sort(key=lambda item: (item["sortOrder"], item["roomName"]))
    return result


def _free_spans(
    window_start: int,
    window_end: int,
    step: int,
    occupied: set[str],
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = window_start
    span_start = None
    while cursor + step <= window_end:
        if minutes_to_time(cursor) in occupied:
            if span_start is not None:
                spans.append((span_start, cursor))
                span_start = None
        elif span_start is None:
            span_start = cursor
        cursor += step
    if span_start is not None:
        spans.append((span_start, cursor))
    return spans


def _start_for(state: dict[str, Any], room: dict[str, Any], settings: sqlite3.Row) -> int | None:
    step = int(settings["slot_minutes"])
    if state.get("durationMode") == "window":
        return int(room["spanStart"])
    duration = int(state.get("duration") or 0)
    if duration < step or duration > int(settings["max_duration_minutes"]):
        return None
    span_end = int(room["spanEnd"])
    work_start = time_to_minutes(settings["work_start"], field="workStart")
    if state.get("timeStart"):
        start = time_to_minutes(state["timeStart"], field="start")
        if (start - work_start) % step != 0:
            return None
        if not (int(room["spanStart"]) <= start and start + duration <= span_end):
            return None
        high = state.get("startHigh")
        if isinstance(high, int) and start > high:
            return None
        return start
    if int(room["spanStart"]) + duration <= span_end:
        return int(room["spanStart"])
    return None


def _constrain_start(state: dict[str, Any], settings: sqlite3.Row) -> None:
    """把开始时间收进时段窗口和工作时间。交不上时改到最近的合法开始。"""

    step = int(settings["slot_minutes"])
    work_start = time_to_minutes(settings["work_start"], field="workStart")
    work_end = time_to_minutes(settings["work_end"], field="workEnd")
    part = state.get("timePart") or "any"
    bounds = _PERIOD_BOUNDS.get(part)
    low, high = bounds if bounds is not None else (work_start, work_end)
    duration = state.get("duration")
    if not isinstance(duration, int) or duration < step:
        duration = step
    legal_low = max(low, work_start)
    legal_high = min(high, work_end)
    if state.get("date") == local_now().date().isoformat():
        legal_low = max(legal_low, _strictly_after_now(work_start, step))
    latest_fit = work_end - duration
    if legal_low > legal_high or legal_low > latest_fit:
        if low >= work_end:
            snapped = _align_down(work_end - duration, work_start, step)
        else:
            snapped = (
                legal_low if legal_low <= latest_fit else _align_down(latest_fit, work_start, step)
            )
        state["timeStart"] = minutes_to_time(snapped)
        state["startExplicit"] = False
        state["snapped"] = True
        state["periodMissed"] = True
        state["startLow"] = snapped
        state["startHigh"] = snapped
        return
    legal_high = min(legal_high, latest_fit)
    clock = state.get("timeStart")
    if clock:
        start = time_to_minutes(clock, field="start")
        if start < legal_low or start > legal_high or (start - work_start) % step != 0:
            snapped = min(max(start, legal_low), legal_high)
            snapped = _align_down(snapped, work_start, step)
            if snapped < legal_low:
                snapped = legal_low
            state["timeStart"] = minutes_to_time(snapped)
            state["startExplicit"] = False
            state["snapped"] = True
        state["startLow"] = legal_low
        state["startHigh"] = legal_high
        return
    state["timeStart"] = minutes_to_time(legal_low)
    state["startExplicit"] = False
    state["startLow"] = legal_low
    state["startHigh"] = legal_high


def _align_down(value: int, origin: int, step: int) -> int:
    if value <= origin:
        return origin
    return origin + ((value - origin) // step) * step


def _card_is_legal(state: dict[str, Any], settings: sqlite3.Row) -> bool:
    duration = state.get("duration")
    start = state.get("start")
    if (
        not isinstance(duration, int)
        or not isinstance(start, str)
        or not _duration_allowed(duration)
    ):
        return False
    work_start = time_to_minutes(settings["work_start"], field="workStart")
    work_end = time_to_minutes(settings["work_end"], field="workEnd")
    start_minutes = time_to_minutes(start, field="start")
    return work_start <= start_minutes and start_minutes + duration <= work_end


def _outside_clarify(
    db: sqlite3.Connection,
    state: dict[str, Any],
    settings: sqlite3.Row,
    label: str,
) -> dict[str, Any]:
    hours = f"{settings['work_start']}–{settings['work_end']}"
    if label:
        text = f"{label}不在工作时间（{hours}）内。"
    else:
        text = f"这个时段不在工作时间（{hours}）内。"
    return _clarify(state, text, options=_nearest_options(db, state, settings))


def _nearest_options(
    db: sqlite3.Connection,
    state: dict[str, Any],
    settings: sqlite3.Row,
) -> list[dict[str, Any]]:
    step = int(settings["slot_minutes"])
    work_start = time_to_minutes(settings["work_start"], field="workStart")
    work_end = time_to_minutes(settings["work_end"], field="workEnd")
    duration = state.get("duration")
    maximum = int(settings["max_duration_minutes"])
    if not isinstance(duration, int) or duration < step or duration > maximum:
        duration = 60 if step <= 60 <= maximum else step
    duration -= duration % step
    if duration < step or work_end - work_start < duration:
        return []
    part = state.get("timePart") or ""
    bounds = _PERIOD_BOUNDS.get(part)
    clock = state.get("timeStart")
    if part == "exact" and clock:
        after = time_to_minutes(clock, field="start") >= work_end
    elif bounds is not None and bounds[0] >= work_end:
        after = True
    elif bounds is not None and bounds[1] <= work_start:
        after = False
    else:
        after = False
    start = work_end - duration if after else work_start
    if start < work_start or (start - work_start) % step != 0:
        return []
    options = []
    for room in _active_rooms(db):
        options.append(
            {
                "type": "room",
                "roomId": room["id"],
                "roomName": room["name"],
                "date": state.get("date"),
                "windowStart": minutes_to_time(start),
                "windowEnd": minutes_to_time(start + duration),
                "maxDuration": duration,
            }
        )
        if len(options) == 3:
            break
    return options


def _period_too_short(state: dict[str, Any], settings: sqlite3.Row) -> bool:
    work_start = time_to_minutes(settings["work_start"], field="workStart")
    work_end = time_to_minutes(settings["work_end"], field="workEnd")
    start = state.get("startLow")
    if not isinstance(start, int):
        start = work_start
    duration = state.get("duration")
    return isinstance(duration, int) and duration > work_end - max(start, work_start)


def _time_window(
    state: dict[str, Any], work_start: int, work_end: int, step: int
) -> tuple[int, int]:
    low = state.get("startLow")
    begin = low if isinstance(low, int) else work_start
    begin = max(begin, work_start)
    if begin >= work_end:
        return begin, begin
    return begin, work_end


def _ceil_minutes(value: int, origin: int, step: int) -> int:
    if value <= origin:
        return origin
    delta = value - origin
    if delta % step == 0:
        return value
    return origin + ((delta + step - 1) // step) * step


def _strictly_after_now(origin: int, step: int) -> int:
    now = local_now()
    current = now.hour * 60 + now.minute
    if current < origin:
        return origin
    delta = current - origin
    return origin + (delta // step + 1) * step


def _room_options(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "room",
            "roomId": item["roomId"],
            "roomName": item["roomName"],
            "date": None,
            "windowStart": item["windowStart"],
            "windowEnd": item["windowEnd"],
            "maxDuration": item["maxDuration"],
        }
        for item in windows
    ]


def _clarify(
    state: dict[str, Any],
    text: str,
    *,
    options: list[dict[str, Any]],
    duration: bool = False,
    default_duration: int = 60,
) -> dict[str, Any]:
    if state.get("date"):
        for option in options:
            if option.get("date") is None and option.get("type") == "room":
                option["date"] = state["date"]
    reply: dict[str, Any] = {"kind": "clarify", "text": text, "options": options}
    if duration:
        default = default_duration if default_duration in (60, 120) else 60
        reply["duration"] = {"choices": list(_DURATION_CHOICES), "default": default}
    return {"kind": "clarify", "title": _clarify_title(state), "draft": state, "reply": reply}


def _prepared_plan(state: dict[str, Any], room_name: str) -> dict[str, Any]:
    target = date.fromisoformat(state["date"])
    today = local_now().date()
    relative = _relative_day(target, today)
    start = state["start"]
    duration = int(state["duration"])
    end = minutes_to_time(time_to_minutes(start, field="start") + duration)
    inferred = []
    if not state.get("startExplicit"):
        inferred.append("start")
    if not state.get("roomExplicit"):
        inferred.append("roomId")
    if not state.get("durationExplicit"):
        inferred.append("duration")
    if state.get("caseFromHistory"):
        inferred.append("caseNumber")
    if state.get("purposeFromHistory"):
        inferred.append("purpose")
    missing = []
    if not state.get("partyName"):
        missing.append("party")
    if not state.get("caseNumber"):
        missing.append("case")
    if not state.get("purpose"):
        missing.append("purpose")
    booking = {
        "date": state["date"],
        "start": start,
        "duration": duration,
        "end": end,
        "roomId": state["roomId"],
        "roomName": room_name,
        "partyName": state.get("partyName") or "",
        "caseNumber": state.get("caseNumber") or "",
        "purpose": state.get("purpose") or "",
        "notes": state.get("notes") or "",
        "tagId": f"tag-{int(state.get('tagSlot') or 1)}",
    }
    when = f"{relative}{_time_label(state)}"
    pieces = [f"帮你找到了：{room_name} {when}空着"]
    history = state.get("historyDate")
    if state.get("caseFromHistory") and history:
        history_day = date.fromisoformat(history)
        pieces.append(f"案号从你 {history_day.month} 月 {history_day.day} 日的预约里带出来了")
    if "purpose" in missing:
        pieces.append("事项还需要补一下")
    if state.get("periodMissed") and state.get("periodLabel"):
        pieces.append(f"{state['periodLabel']}不在工作时间内，改到了最近的可预约开始时间")
    return {
        "kind": "prepared",
        "title": f"{room_name} · {target.month}月{target.day}日 {start}",
        "draft": state,
        "reply": {
            "kind": "prepared",
            "text": "，".join(pieces) + "。",
            "prepared": {
                "booking": booking,
                "inferred": inferred,
                "missing": missing,
                "relativeDay": relative,
            },
        },
    }


def _duration_allowed(minutes: int) -> bool:
    return 30 <= minutes <= 180 and minutes % 30 == 0


def _revoke_unused_tokens(
    db: sqlite3.Connection,
    conversation_id: str,
    owner_id: str,
) -> None:
    """草稿被替换后，旧的未使用凭证不能再提交。"""

    db.execute(
        """
        UPDATE prepared_bookings
        SET used_at = ?
        WHERE conversation_id = ? AND owner_user_id = ? AND used_at IS NULL
        """,
        (local_now().isoformat(), conversation_id, owner_id),
    )


def _issue_token(
    db: sqlite3.Connection,
    actor: dict[str, Any],
    conversation_id: str,
    planned: dict[str, Any],
) -> dict[str, Any] | None:
    reply = planned["reply"]
    if reply["kind"] != "prepared":
        return None
    prepared = reply["prepared"]
    booking = prepared["booking"]
    payload = {
        "date": booking["date"],
        "roomId": booking["roomId"],
        "start": booking["start"],
        "duration": booking["duration"],
        "partyName": booking["partyName"],
        "caseNumber": booking["caseNumber"],
        "purpose": booking["purpose"],
        "notes": booking["notes"],
        "tagId": booking["tagId"],
    }
    db.execute(
        """
        UPDATE prepared_bookings
        SET used_at = ?
        WHERE conversation_id = ? AND owner_user_id = ? AND used_at IS NULL
        """,
        (local_now().isoformat(), conversation_id, actor["id"]),
    )
    token = "qb_" + secrets.token_urlsafe(32)
    expires = local_now() + TOKEN_TTL
    db.execute(
        """
        INSERT INTO prepared_bookings (
            id, token_hash, owner_user_id, session_version, conversation_id,
            payload_json, inferred_json, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(),
            _token_hash(token),
            actor["id"],
            int(actor["session_version"]),
            conversation_id,
            canonical_json(payload),
            canonical_json(prepared["inferred"]),
            expires.isoformat(),
        ),
    )
    return {"token": token, "expiresAt": expires.isoformat()}


def _envelope(
    conversation_id: str,
    planned: dict[str, Any],
    prepared: dict[str, Any] | None,
) -> dict[str, Any]:
    reply = dict(planned["reply"])
    if reply["kind"] == "prepared" and prepared is not None:
        card = dict(reply["prepared"])
        card["token"] = prepared["token"]
        card["expiresAt"] = prepared["expiresAt"]
        reply["prepared"] = card
    status = "open"
    return {
        "conversation": {
            "id": conversation_id,
            "title": _title(planned["title"]),
            "status": status,
        },
        "reply": reply,
    }


def _guide() -> dict[str, Any]:
    return {
        "kind": "clarify",
        "title": "我的预约",
        "draft": _blank_state(),
        "reply": {"kind": "clarify", "text": _GUIDE_TEXT, "options": []},
    }


def _fallback(text: str) -> dict[str, Any]:
    return {
        "kind": "fallback",
        "title": "手动预约",
        "draft": _blank_state(),
        "reply": {"kind": "fallback", "text": text, "formPrefill": {}},
    }


def _blank_state() -> dict[str, Any]:
    return {
        "date": None,
        "timePart": "any",
        "timeStart": None,
        "timeEnd": None,
        "duration": None,
        "durationMode": "fixed",
        "ambiguousDuration": False,
        "durationExplicit": False,
        "roomId": None,
        "roomExplicit": False,
        "startExplicit": False,
        "endExplicit": False,
        "mustClarify": False,
        "unknownClarify": False,
        "identityClarify": False,
        "snapped": False,
        "periodMissed": False,
        "startLow": None,
        "startHigh": None,
        "partyName": "",
        "caseNumber": "",
        "purpose": "",
        "notes": "",
        "tagSlot": 1,
        "caseOptions": [],
        "caseFromHistory": False,
        "purposeFromHistory": False,
        "historyDate": None,
        "periodLabel": None,
    }


def _load_draft(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ApiError(409, "PREPARED_EXPIRED", "这份预约草稿已过期") from error
    if not isinstance(value, dict):
        raise ApiError(409, "PREPARED_EXPIRED", "这份预约草稿已过期")
    state = _blank_state()
    state.update({key: value[key] for key in state if key in value})
    return state


def _resolve_date(intent: BookingIntent, today: date) -> date | None:
    choice = intent.date
    if choice.kind == "relative" and choice.relative_days is not None:
        return today + timedelta(days=choice.relative_days)
    if choice.kind == "week" and choice.week_offset is not None and choice.weekday is not None:
        monday = today - timedelta(days=today.weekday())
        return monday + timedelta(days=choice.week_offset * 7 + choice.weekday - 1)
    if choice.kind == "calendar" and choice.month is not None and choice.day is not None:
        try:
            return date(today.year, choice.month, choice.day)
        except ValueError:
            return None
    return None


def _restore(ref: str | None, mapping: dict[str, str]) -> str | None:
    if ref is None:
        return None
    value = mapping.get(ref)
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 120:
        return None
    return cleaned


def _own_rows(
    db: sqlite3.Connection,
    user_id: str,
    *,
    party: str | None = None,
    case_number: str | None = None,
) -> list[sqlite3.Row]:
    clauses = ["owner_user_id = ?", "status = 'active'"]
    parameters: list[Any] = [user_id]
    if party is not None:
        clauses.append("party_name = ?")
        parameters.append(party)
    if case_number is not None:
        clauses.append("case_number = ?")
        parameters.append(case_number)
    return db.execute(
        f"""
        SELECT party_name, case_number, purpose, tag_slot, booking_date, room_id
        FROM reservations
        WHERE {" AND ".join(clauses)}
        ORDER BY booking_date DESC, start_time DESC, created_at DESC
        """,
        parameters,
    ).fetchall()


def _recent_room_id(db: sqlite3.Connection, user_id: str, state: dict[str, Any]) -> str | None:
    case_number = state.get("caseNumber") or None
    party = state.get("partyName") or None
    if not case_number and not party:
        return None
    rows = _own_rows(db, user_id, party=party, case_number=case_number if case_number else None)
    if case_number and not rows and party:
        rows = _own_rows(db, user_id, party=party)
    if not rows:
        return None
    room_id = rows[0]["room_id"]
    return room_id if isinstance(room_id, str) and room_id else None


def _latest_own(db: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    rows = _own_rows(db, user_id)
    return rows[0] if rows else None


def _most_used(db: sqlite3.Connection, user_id: str) -> dict[str, int]:
    since = (local_now().date() - timedelta(days=90)).isoformat()
    rows = db.execute(
        """
        SELECT room_id, COUNT(*) AS uses
        FROM reservations
        WHERE owner_user_id = ? AND status = 'active' AND booking_date >= ?
        GROUP BY room_id
        """,
        (user_id, since),
    ).fetchall()
    return {row["room_id"]: int(row["uses"]) for row in rows}


def _preferences(db: sqlite3.Connection, user_id: str) -> sqlite3.Row:
    row = db.execute(
        """
        SELECT default_duration, default_room_id, default_tag_slot
        FROM user_preferences WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
    if row is None:
        raise ApiError(503, "DATABASE_UNAVAILABLE", "数据库暂时不可用，请联系管理员")
    return row


def _fallback_tag(prefs: sqlite3.Row) -> int:
    slot = prefs["default_tag_slot"]
    if slot in (1, 2, 3, 4):
        return int(slot)
    return 1


def _active_rooms(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        """
        SELECT id, name, sort_order FROM rooms
        WHERE is_active = 1
        ORDER BY sort_order, name
        """
    ).fetchall()


def _system_settings(db: sqlite3.Connection) -> sqlite3.Row:
    row = db.execute("SELECT * FROM system_settings WHERE id = 1").fetchone()
    if row is None:
        raise ApiError(503, "DATABASE_UNAVAILABLE", "数据库暂时不可用，请联系管理员")
    return row


def _work_clock(db: sqlite3.Connection, column: str) -> str:
    if column not in {"work_start", "work_end"}:
        raise RuntimeError("未知的工作时间字段")
    return str(_system_settings(db)[column])


def _room_question(state: dict[str, Any], count: int) -> str:
    when = ""
    if state.get("date"):
        when = _relative_day(date.fromisoformat(state["date"]), local_now().date())
    when += _time_label(state)
    if count <= 1:
        if state.get("ambiguousDuration"):
            return f"{when}的时长要选一下。"
        return f"{when}要哪间？"
    amount = _COUNT_WORDS.get(count, str(count))
    return f"{when}有{amount}间空着，要哪间？"


def _clarify_title(state: dict[str, Any]) -> str:
    if not state.get("date"):
        return "选择日期"
    relative = _relative_day(date.fromisoformat(state["date"]), local_now().date())
    return f"{relative}{_time_label(state)}".strip() or "选择笔录室"


def _relative_day(target: date, today: date) -> str:
    delta = (target - today).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    if delta == 2:
        return "后天"
    return f"{target.month}月{target.day}日"


def _time_label(state: dict[str, Any]) -> str:
    part = state.get("timePart")
    if part == "exact" and state.get("timeStart"):
        return str(state["timeStart"])
    if state.get("periodLabel"):
        return str(state["periodLabel"])
    return _PERIOD_LABELS.get(part, "")


def _optional_text(value: object, field: str, label: str) -> str:
    if not isinstance(value, str):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "请检查输入内容",
            fields={field: f"请输入{label}"},
        )
    return clean_text(value, field=field, label=label, maximum=120, required=False)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_expiry(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ApiError(410, "PREPARED_EXPIRED", "这份预约草稿已过期") from error
    if parsed.tzinfo is None:
        raise ApiError(410, "PREPARED_EXPIRED", "这份预约草稿已过期")
    return parsed


def _title(text: str) -> str:
    compact = " ".join(text.split())
    return compact[:40] or "预约"


def _data_dir() -> Path | None:
    value = current_app.config.get("DATA_DIR")
    if not value:
        return None
    return Path(value)

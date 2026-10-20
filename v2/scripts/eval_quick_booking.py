#!/usr/bin/env python3
"""用真实模型供应商评测冻结语料。不进入 CI。

没有 ``MRV2_MODEL_API_KEY`` 时直接退出。有密钥时每句跑 3 次，取多数结果，
并分开打印整卡一致率和一次就能出卡片的比例。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

CORPUS = BACKEND / "tests" / "fixtures" / "booking-acceptance.json"


def main() -> int:
    key = os.environ.get("MRV2_MODEL_API_KEY", "").strip()
    if not key:
        print("no MRV2_MODEL_API_KEY; real-provider eval skipped")
        return 0
    from v2app import create_app
    from v2app.services.model_provider import DeepSeekProvider

    cases = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        app = create_app(
            {
                "TESTING": True,
                "DATA_DIR": str(data),
                "DATABASE": str(data / "eval.db"),
                "SECRET_KEY": "eval-secret",
                "INSTALL_ID": "eval-install",
            }
        )
        provider = DeepSeekProvider(
            api_key=key,
            base_url=os.environ.get("MRV2_MODEL_BASE_URL", "https://api.deepseek.com"),
            model=os.environ.get("MRV2_MODEL_NAME", "deepseek-chat"),
            timeout_seconds=20,
        )
        app.config["QUICK_BOOKING_PROVIDER"] = provider
        client = app.test_client()
        _bootstrap(client)
        matched = 0
        one_shot_hits = 0
        prepared_cases = 0
        for case in cases:
            votes = [_ask(client, case["text"]) for _ in range(3)]
            reply = _majority(votes)
            expect = case["expect"]
            ok = _matches(reply, expect)
            if expect["kind"] == "prepared":
                prepared_cases += 1
                if ok:
                    one_shot_hits += 1
            if ok:
                matched += 1
        ratio = matched / len(cases)
        card_ratio = one_shot_hits / prepared_cases if prepared_cases else 0
        print(f"Q={matched}/{len(cases)} ({ratio:.1%}) real-provider majority of 3")
        print(f"one-shot cards={one_shot_hits}/{prepared_cases} ({card_ratio:.1%}) benign prepared")
    return 0


def _write(client, method: str, path: str, payload: dict):
    token = client.get("/api/v1/session").get_json()["csrfToken"]
    return client.open(
        path,
        method=method,
        json=payload,
        headers={"Host": "localhost:8080", "X-CSRF-Token": token},
    )


def _bootstrap(client) -> None:
    setup = _write(
        client,
        "POST",
        "/api/v1/setup/complete",
        {
            "admin": {
                "username": "admin",
                "password": "admin-pass-123",
                "name": "评测管理员",
                "department": "评测",
            },
            "rooms": [{"name": "笔录室 1"}, {"name": "笔录室 2"}],
            "workStart": "08:30",
            "workEnd": "17:30",
        },
    )
    if setup.status_code != 201:
        raise SystemExit(f"setup failed: {setup.status_code} {setup.get_json()}")
    _write(client, "POST", "/api/v1/session", {"username": "admin", "password": "admin-pass-123"})
    enabled = _write(
        client,
        "PUT",
        "/api/v1/admin/conversation",
        {"enabled": True, "externalNoticeAccepted": True},
    )
    if enabled.status_code != 200:
        raise SystemExit(f"enable failed: {enabled.status_code} {enabled.get_json()}")


def _ask(client, text: str) -> dict:
    response = _write(client, "POST", "/api/v1/conversations/new/messages", {"text": text})
    body = response.get_json(silent=True) or {}
    return body.get("reply") or {"kind": "fallback"}


def _majority(votes: list[dict]) -> dict:
    signature = Counter(_signature(vote) for vote in votes)
    chosen, _count = signature.most_common(1)[0]
    for vote in votes:
        if _signature(vote) == chosen:
            return vote
    return votes[0]


def _signature(reply: dict) -> str:
    booking = (reply.get("prepared") or {}).get("booking") or {}
    return json.dumps(
        {
            "kind": reply.get("kind"),
            "date": booking.get("date"),
            "start": booking.get("start"),
            "end": booking.get("end"),
            "duration": booking.get("duration"),
            "roomName": booking.get("roomName"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _matches(reply: dict, expect: dict) -> bool:
    if reply.get("kind") != expect.get("kind"):
        return False
    if expect.get("kind") != "prepared":
        if expect.get("date"):
            options = reply.get("options") or []
            return bool(options) and options[0].get("date") == expect["date"]
        return True
    booking = (reply.get("prepared") or {}).get("booking") or {}
    return (
        booking.get("date") == expect.get("date")
        and booking.get("start") == expect.get("start")
        and booking.get("end") == expect.get("end")
        and booking.get("duration") == expect.get("duration")
        and booking.get("roomName") == expect.get("room")
    )


if __name__ == "__main__":
    raise SystemExit(main())

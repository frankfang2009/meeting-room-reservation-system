from __future__ import annotations

from typing import Any, Optional

from ..common import canonical_json, new_id


AUTH_FAILURE_AUDIT_MAX_ROWS = 20_000


def write_security_audit(
    db,
    *,
    action: str,
    target_type: str,
    actor_user_id: Optional[str] = None,
    target_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    db.execute(
        """
        INSERT INTO security_audit_log
            (id, actor_user_id, action, target_type, target_id, details_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(), actor_user_id, action, target_type, target_id,
            canonical_json(details or {}),
        ),
    )


def write_bounded_auth_failure(
    db,
    *,
    ip_fingerprint: str,
    username_fingerprint: str,
    reason: str,
) -> None:
    """Aggregate hostile login traffic without allowing SQLite growth attacks."""

    row = db.execute(
        """
        SELECT id, details_json FROM security_audit_log
        WHERE action = 'auth.login_failed' AND target_id = ?
          AND occurred_at >= strftime('%Y-%m-%dT%H:%M:%fZ','now','-1 hour')
        ORDER BY occurred_at DESC LIMIT 1
        """,
        (ip_fingerprint,),
    ).fetchone()
    if row is not None:
        import json

        try:
            details = json.loads(row["details_json"])
        except (TypeError, ValueError):
            details = {}
        details.update(
            {
                "count": int(details.get("count", 0)) + 1,
                "lastReason": reason,
                "lastUsernameFingerprint": username_fingerprint,
                "ipFingerprint": ip_fingerprint,
                "result": "failed",
            }
        )
        db.execute(
            """
            UPDATE security_audit_log
            SET details_json = ?,
                occurred_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ?
            """,
            (canonical_json(details), row["id"]),
        )
        return
    count = db.execute(
        "SELECT COUNT(*) FROM security_audit_log WHERE action = 'auth.login_failed'"
    ).fetchone()[0]
    target_id = ip_fingerprint
    overflow = None
    if count >= AUTH_FAILURE_AUDIT_MAX_ROWS:
        target_id = "bounded-overflow"
        overflow = db.execute(
            """
            SELECT id, details_json FROM security_audit_log
            WHERE action = 'auth.login_failed' AND target_id = 'bounded-overflow'
            LIMIT 1
            """
        ).fetchone()
        if overflow is None:
            overflow = db.execute(
                """
                SELECT id, details_json FROM security_audit_log
                WHERE action = 'auth.login_failed'
                ORDER BY occurred_at, id LIMIT 1
                """
            ).fetchone()
            if overflow is not None:
                db.execute(
                    "UPDATE security_audit_log SET target_id = 'bounded-overflow' WHERE id = ?",
                    (overflow["id"],),
                )
    if overflow is None:
        overflow = db.execute(
        """
        SELECT id, details_json FROM security_audit_log
        WHERE action = 'auth.login_failed' AND target_id = ?
        LIMIT 1
        """,
        (target_id,),
        ).fetchone()
    if overflow is not None:
        import json

        try:
            details = json.loads(overflow["details_json"])
        except (TypeError, ValueError):
            details = {}
        details["count"] = int(details.get("count", 0)) + 1
        details["lastReason"] = reason
        details["lastUsernameFingerprint"] = username_fingerprint
        details["ipFingerprint"] = ip_fingerprint
        details["result"] = "failed"
        db.execute(
            """
            UPDATE security_audit_log
            SET details_json = ?,
                occurred_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ?
            """,
            (canonical_json(details), overflow["id"]),
        )
        return
    write_security_audit(
        db,
        action="auth.login_failed",
        target_type="session",
        target_id=target_id,
        details={
            "count": 1,
            "lastReason": reason,
            "lastUsernameFingerprint": username_fingerprint,
            "ipFingerprint": ip_fingerprint,
            "result": "failed",
        },
    )

from __future__ import annotations

from typing import Any, Optional

from ..common import canonical_json, new_id


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

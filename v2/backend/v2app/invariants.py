from __future__ import annotations

import sqlite3


class ApplicationInvariantError(RuntimeError):
    """The SQLite file is readable but violates required V2 business structure."""


def _scalar(db: sqlite3.Connection, query: str) -> int:
    row = db.execute(query).fetchone()
    return int(row[0]) if row is not None else 0


def validate_application_invariants(
    db: sqlite3.Connection,
    *,
    setup_complete: bool,
) -> None:
    settings = [int(row[0]) for row in db.execute("SELECT id FROM system_settings")]
    if settings != [1]:
        raise ApplicationInvariantError("系统设置记录缺失或重复")

    global_slots = [
        int(row[0]) for row in db.execute("SELECT slot FROM global_tags ORDER BY slot")
    ]
    if global_slots != [1, 2]:
        raise ApplicationInvariantError("单位标签 1、2 不完整")

    user_count = _scalar(db, "SELECT COUNT(*) FROM users")
    preference_count = _scalar(db, "SELECT COUNT(*) FROM user_preferences")
    missing_preferences = _scalar(
        db,
        """
        SELECT COUNT(*)
        FROM users
        LEFT JOIN user_preferences ON user_preferences.user_id = users.id
        WHERE user_preferences.user_id IS NULL
        """,
    )
    if preference_count != user_count or missing_preferences:
        raise ApplicationInvariantError("用户与个人偏好记录不是一一对应")

    room_count = _scalar(db, "SELECT COUNT(*) FROM rooms")
    if setup_complete and (user_count < 1 or room_count < 1):
        raise ApplicationInvariantError("已完成设置的数据库缺少用户或笔录室")
    if not setup_complete and (user_count or preference_count or room_count):
        raise ApplicationInvariantError("未完成设置的数据库含有业务主体")

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from flask import Flask, current_app, g

from .invariants import ApplicationInvariantError, validate_application_invariants


PRODUCT_GENERATION = 2
SCHEMA_VERSION = 4
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2, 3, SCHEMA_VERSION})
V1_FINGERPRINT = {"users", "rooms", "reservations", "reservation_slots"}
DEFAULT_REMINDER_TEMPLATE = (
    "【笔录提醒】{当事人姓名}您好，您预约的笔录时间为{日期} {开始时间}，"
    "地点：{笔录室}，请提前到达。如有变动我们会再联系您。"
)

EXPECTED_TABLES = {
    "app_meta",
    "system_settings",
    "users",
    "user_preferences",
    "global_tags",
    "rooms",
    "reservations",
    "reservation_slots",
    "reservation_events",
    "api_tokens",
    "security_audit_log",
}

# 回执表按 schema 版本命名：v1/v2 为预约+版本维度的 reminder_receipts，
# v3 起为事件维度的 notice_receipts（迁移时重建并转换已确认的变更回执）。
RECEIPTS_TABLE_BY_VERSION = {
    1: "reminder_receipts",
    2: "reminder_receipts",
    3: "notice_receipts",
    4: "notice_receipts",
}

# v4 新增交接请求表；v3 及更早的库没有它，版本门只对 v4 校验存在性。
HANDOVER_TABLE_MIN_VERSION = 4

NOTICE_RECEIPTS_TABLE_DDL = """
    CREATE TABLE notice_receipts (
        event_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        acknowledged_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        PRIMARY KEY (event_id, user_id),
        FOREIGN KEY (event_id) REFERENCES reservation_events(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
"""

NOTICE_RECEIPTS_INDEX_DDL = (
    "CREATE INDEX idx_notice_receipts_user ON notice_receipts(user_id)"
)

HANDOVER_REQUESTS_TABLE_DDL = """
    CREATE TABLE handover_requests (
        id TEXT PRIMARY KEY,
        reservation_id TEXT NOT NULL,
        from_user_id TEXT NOT NULL,
        to_user_id TEXT NOT NULL,
        expected_revision INTEGER NOT NULL CHECK (expected_revision >= 1),
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'accepted', 'declined', 'withdrawn', 'expired')),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        decided_at TEXT,
        FOREIGN KEY (reservation_id) REFERENCES reservations(id) ON DELETE CASCADE,
        FOREIGN KEY (from_user_id) REFERENCES users(id),
        FOREIGN KEY (to_user_id) REFERENCES users(id)
    )
"""

HANDOVER_REQUESTS_INDEX_DDL = (
    "CREATE UNIQUE INDEX idx_handover_pending_per_reservation "
    "ON handover_requests(reservation_id) WHERE status = 'pending'"
)

HANDOVER_REQUESTS_INBOX_INDEX_DDL = (
    "CREATE INDEX idx_handover_requests_to_user "
    "ON handover_requests(to_user_id, status)"
)

# v4 迁移用：与原 reservation_events 同构，event_type 枚举扩展 'handover'。
RESERVATION_EVENTS_V4_DDL = """
    CREATE TABLE reservation_events_new (
        id TEXT PRIMARY KEY,
        reservation_id TEXT NOT NULL,
        actor_user_id TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK (event_type IN ('created', 'updated', 'cancelled', 'handover')),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        before_json TEXT,
        after_json TEXT,
        occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        FOREIGN KEY (reservation_id) REFERENCES reservations(id),
        FOREIGN KEY (actor_user_id) REFERENCES users(id)
    )
"""

SCHEMA_V2_COLUMNS = (
    (
        "rooms",
        "show_on_display",
        "INTEGER NOT NULL DEFAULT 1 CHECK (show_on_display IN (0, 1))",
    ),
    (
        "user_preferences",
        "default_tag_slot",
        "INTEGER CHECK (default_tag_slot BETWEEN 1 AND 4)",
    ),
    (
        "user_preferences",
        "reminder_lead_minutes",
        "INTEGER NOT NULL DEFAULT 30 CHECK (reminder_lead_minutes IN (15, 30, 60))",
    ),
    (
        "user_preferences",
        "reminder_template",
        f"TEXT NOT NULL DEFAULT '{DEFAULT_REMINDER_TEMPLATE}'",
    ),
)

SCHEMA_V3_COLUMNS = (
    (
        "user_preferences",
        "reminder_sound",
        "INTEGER NOT NULL DEFAULT 1 CHECK (reminder_sound IN (0, 1))",
    ),
)


class DatabaseGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatabaseStartupState:
    ready: bool
    setup_complete: bool
    initialized: bool = False
    code: str = "READY"
    message: str = "数据库已就绪"


def _recovery(code: str, message: str) -> DatabaseStartupState:
    return DatabaseStartupState(
        ready=False,
        setup_complete=False,
        code=code,
        message=message,
    )


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE app_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE system_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        work_start TEXT NOT NULL,
        work_end TEXT NOT NULL,
        slot_minutes INTEGER NOT NULL CHECK (slot_minutes = 30),
        max_duration_minutes INTEGER NOT NULL CHECK (max_duration_minutes = 180)
    )
    """,
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL COLLATE NOCASE UNIQUE,
        password_hash TEXT NOT NULL,
        display_name TEXT NOT NULL,
        department TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL CHECK (role IN ('admin', 'employee')),
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
        session_version INTEGER NOT NULL DEFAULT 1 CHECK (session_version >= 1),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )
    """,
    """
    CREATE TABLE rooms (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
        show_on_display INTEGER NOT NULL DEFAULT 1 CHECK (show_on_display IN (0, 1)),
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )
    """,
    f"""
    CREATE TABLE user_preferences (
        user_id TEXT PRIMARY KEY,
        default_duration INTEGER NOT NULL DEFAULT 60
            CHECK (default_duration BETWEEN 30 AND 180 AND default_duration % 30 = 0),
        default_room_id TEXT,
        default_tag_slot INTEGER CHECK (default_tag_slot BETWEEN 1 AND 4),
        booking_change_notifications INTEGER NOT NULL DEFAULT 1
            CHECK (booking_change_notifications IN (0, 1)),
        booking_reminder INTEGER NOT NULL DEFAULT 1
            CHECK (booking_reminder IN (0, 1)),
        reminder_sound INTEGER NOT NULL DEFAULT 1
            CHECK (reminder_sound IN (0, 1)),
        reminder_lead_minutes INTEGER NOT NULL DEFAULT 30
            CHECK (reminder_lead_minutes IN (15, 30, 60)),
        reminder_template TEXT NOT NULL DEFAULT '{DEFAULT_REMINDER_TEMPLATE}',
        personal_tag_3_label TEXT NOT NULL DEFAULT '标签 3',
        personal_tag_4_label TEXT NOT NULL DEFAULT '标签 4',
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (default_room_id) REFERENCES rooms(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE global_tags (
        slot INTEGER PRIMARY KEY CHECK (slot IN (1, 2)),
        label TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )
    """,
    """
    CREATE TABLE reservations (
        id TEXT PRIMARY KEY,
        room_id TEXT,
        room_name_snapshot TEXT NOT NULL,
        booking_date TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        owner_user_id TEXT NOT NULL,
        owner_name_snapshot TEXT NOT NULL,
        party_name TEXT NOT NULL,
        case_number TEXT NOT NULL,
        purpose TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        tag_slot INTEGER NOT NULL CHECK (tag_slot BETWEEN 1 AND 4),
        tag_label_snapshot TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
        revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        cancelled_at TEXT,
        cancelled_by_user_id TEXT,
        FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE SET NULL,
        FOREIGN KEY (owner_user_id) REFERENCES users(id),
        FOREIGN KEY (cancelled_by_user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE reservation_slots (
        reservation_id TEXT NOT NULL,
        room_id TEXT NOT NULL,
        booking_date TEXT NOT NULL,
        slot_start TEXT NOT NULL,
        PRIMARY KEY (room_id, booking_date, slot_start),
        UNIQUE (reservation_id, slot_start),
        FOREIGN KEY (reservation_id) REFERENCES reservations(id) ON DELETE CASCADE,
        FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE reservation_events (
        id TEXT PRIMARY KEY,
        reservation_id TEXT NOT NULL,
        actor_user_id TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK (event_type IN ('created', 'updated', 'cancelled', 'handover')),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        before_json TEXT,
        after_json TEXT,
        occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        FOREIGN KEY (reservation_id) REFERENCES reservations(id),
        FOREIGN KEY (actor_user_id) REFERENCES users(id)
    )
    """,
    NOTICE_RECEIPTS_TABLE_DDL,
    HANDOVER_REQUESTS_TABLE_DDL,
    """
    CREATE TABLE api_tokens (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        token_prefix TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        scopes TEXT NOT NULL,
        created_by_user_id TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        expires_at TEXT,
        revoked_at TEXT,
        last_used_at TEXT,
        FOREIGN KEY (created_by_user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE security_audit_log (
        id TEXT PRIMARY KEY,
        actor_user_id TEXT,
        action TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT,
        details_json TEXT NOT NULL DEFAULT '{}',
        occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        FOREIGN KEY (actor_user_id) REFERENCES users(id)
    )
    """,
    "CREATE INDEX idx_reservations_date_room ON reservations(booking_date, room_id, start_time)",
    "CREATE INDEX idx_reservations_owner_date ON reservations(owner_user_id, booking_date, start_time)",
    "CREATE INDEX idx_events_reservation ON reservation_events(reservation_id, occurred_at)",
    NOTICE_RECEIPTS_INDEX_DDL,
    HANDOVER_REQUESTS_INDEX_DDL,
    HANDOVER_REQUESTS_INBOX_INDEX_DDL,
)


def _connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
    else:
        db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 10000")
    if not readonly:
        db.execute("PRAGMA synchronous = FULL")
    return db


def _table_names(db: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _meta_value(db: sqlite3.Connection, key: str) -> Optional[str]:
    row = db.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row is not None else None


def _column_names(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f'PRAGMA table_info("{table}")')}


def _missing_v2_columns(db: sqlite3.Connection) -> list[str]:
    columns_by_table: dict[str, set[str]] = {}
    missing = []
    for table, column, _declaration in SCHEMA_V2_COLUMNS:
        columns = columns_by_table.setdefault(table, _column_names(db, table))
        if column not in columns:
            missing.append(f"{table}.{column}")
    return missing


def _missing_v3_columns(db: sqlite3.Connection) -> list[str]:
    columns_by_table: dict[str, set[str]] = {}
    missing = []
    for table, column, _declaration in SCHEMA_V3_COLUMNS:
        columns = columns_by_table.setdefault(table, _column_names(db, table))
        if column not in columns:
            missing.append(f"{table}.{column}")
    return missing


def _receipts_table_for(version: str) -> Optional[str]:
    if version.isdigit() and int(version) in RECEIPTS_TABLE_BY_VERSION:
        return RECEIPTS_TABLE_BY_VERSION[int(version)]
    return None


def _missing_v4_requirements(db: sqlite3.Connection, tables: set[str]) -> list[str]:
    """v4 结构要求：交接表存在且事件表枚举已扩展。"""

    missing: list[str] = []
    if "handover_requests" not in tables:
        missing.append("handover_requests")
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'reservation_events'"
    ).fetchone()
    if row is not None and "'handover'" not in str(row[0]):
        missing.append("reservation_events.event_type 枚举未扩展")
    return missing


def classify_existing_database(path: Path) -> str:
    try:
        db = _connect(path, readonly=True)
    except sqlite3.Error as error:
        raise DatabaseGenerationError("数据库文件不是可识别的 SQLite 数据库") from error
    try:
        tables = _table_names(db)
        if not tables:
            return "empty"
        generation = None
        if "app_meta" in tables:
            try:
                generation = _meta_value(db, "product_generation")
            except sqlite3.Error as error:
                raise DatabaseGenerationError("数据库代际元数据无法读取") from error
        if generation != str(PRODUCT_GENERATION):
            if V1_FINGERPRINT.issubset(tables):
                raise DatabaseGenerationError(
                    "检测到 V1 数据库；V2 是全新安装，禁止读取或迁移 V1 数据"
                )
            raise DatabaseGenerationError("数据库缺少有效的 V2 产品代际标识")
        version = _meta_value(db, "schema_version")
        if version not in {str(item) for item in SUPPORTED_SCHEMA_VERSIONS}:
            raise DatabaseGenerationError(
                f"数据库结构版本不受支持：可迁移 1、2、3 或当前 {SCHEMA_VERSION}，"
                f"实际 {version or '缺失'}"
            )
        setup_value = _meta_value(db, "setup_complete")
        if setup_value not in ("0", "1"):
            raise DatabaseGenerationError("数据库 setup_complete 元数据无效")
        for key in ("data_sequence", "backup_sequence"):
            sequence_value = _meta_value(db, key)
            if (
                sequence_value is None
                or not sequence_value.isdigit()
                or int(sequence_value) < 0
            ):
                raise DatabaseGenerationError(f"数据库 {key} 元数据无效")
        missing = EXPECTED_TABLES - tables
        if missing:
            raise DatabaseGenerationError(
                "V2 数据库结构不完整：" + ", ".join(sorted(missing))
            )
        receipts_table = _receipts_table_for(version)
        if receipts_table is None or receipts_table not in tables:
            raise DatabaseGenerationError(
                "V2 数据库结构不完整：" + (receipts_table or "提醒回执表")
            )
        if version == str(SCHEMA_VERSION):
            missing_columns = (
                _missing_v2_columns(db)
                + _missing_v3_columns(db)
                + _missing_v4_requirements(db, tables)
            )
            if missing_columns:
                raise DatabaseGenerationError(
                    "V2 数据库当前结构缺少列或表："
                    + ", ".join(missing_columns)
                )
        return f"v{version}"
    finally:
        db.close()


def _add_column_if_missing(
    db: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    if column in _column_names(db, table):
        return
    db.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {declaration}')


def migrate_schema_v1_to_v2(path: Path) -> bool:
    """Atomically and idempotently upgrade a generation-2 schema-v1 database.

    本函数只负责 v1→v2（补列并把版本如实写为 2）；推进到更高版本由
    prepare_database 串联的后续迁移负责。
    """

    db = _connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            if _meta_value(db, "product_generation") != str(PRODUCT_GENERATION):
                raise DatabaseGenerationError("迁移目标不是 V2 产品代际")
            version = _meta_value(db, "schema_version")
            if version == "2" or version == str(SCHEMA_VERSION):
                missing = _missing_v2_columns(db)
                if missing:
                    raise DatabaseGenerationError(
                        "已标记为 schema v2 的数据库缺少列："
                        + ", ".join(missing)
                    )
                db.execute("COMMIT")
                return False
            if version != "1":
                raise DatabaseGenerationError(
                    f"不支持从 schema {version or '缺失'} 迁移到 2"
                )
            missing_tables = EXPECTED_TABLES - _table_names(db)
            if missing_tables:
                raise DatabaseGenerationError(
                    "待迁移的 V2 数据库结构不完整："
                    + ", ".join(sorted(missing_tables))
                )
            for table, column, declaration in SCHEMA_V2_COLUMNS:
                _add_column_if_missing(db, table, column, declaration)
            updated = db.execute(
                "UPDATE app_meta SET value = '2' "
                "WHERE key = 'schema_version' AND value = '1'",
            )
            if updated.rowcount != 1:
                raise DatabaseGenerationError("数据库 schema_version 迁移写入失败")
            missing = _missing_v2_columns(db)
            if missing:
                raise DatabaseGenerationError(
                    "数据库迁移后仍缺少列：" + ", ".join(missing)
                )
            db.execute("COMMIT")
            return True
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
    finally:
        db.close()


def migrate_schema_v2_to_v3(path: Path) -> bool:
    """Atomically and idempotently upgrade a generation-2 schema-v2 database to v3.

    v3 把提醒回执从预约+版本维度重建为事件维度的 notice_receipts：
    已确认的变更回执按 (reservation_id, revision) 关联转换为事件回执；
    未确认回执与临近提醒回执直接丢弃——新模型中临近提醒不再需要确认，
    未确认的变更通知会在新模型下重新出现，由用户按事件确认。
    同时补充 user_preferences.reminder_sound 列。
    """

    db = _connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            if _meta_value(db, "product_generation") != str(PRODUCT_GENERATION):
                raise DatabaseGenerationError("迁移目标不是 V2 产品代际")
            version = _meta_value(db, "schema_version")
            tables = _table_names(db)
            if version == "3" or version == str(SCHEMA_VERSION):
                missing = _missing_v2_columns(db) + _missing_v3_columns(db)
                if missing:
                    raise DatabaseGenerationError(
                        "已标记为 schema v3 的数据库缺少列：" + ", ".join(missing)
                    )
                if "notice_receipts" not in tables:
                    raise DatabaseGenerationError(
                        "已标记为 schema v3 的数据库缺少 notice_receipts 表"
                    )
                db.execute("COMMIT")
                return False
            if version != "2":
                raise DatabaseGenerationError(
                    f"不支持从 schema {version or '缺失'} 迁移到 3"
                )
            missing_tables = EXPECTED_TABLES - tables
            if missing_tables:
                raise DatabaseGenerationError(
                    "待迁移的 V2 数据库结构不完整："
                    + ", ".join(sorted(missing_tables))
                )
            if "reminder_receipts" not in tables:
                raise DatabaseGenerationError(
                    "待迁移的 V2 数据库缺少 reminder_receipts 表"
                )
            for table, column, declaration in SCHEMA_V3_COLUMNS:
                _add_column_if_missing(db, table, column, declaration)
            db.execute(NOTICE_RECEIPTS_TABLE_DDL)
            db.execute(
                """
                INSERT INTO notice_receipts (event_id, user_id, acknowledged_at)
                SELECT e.id, rr.user_id, rr.acknowledged_at
                FROM reminder_receipts rr
                JOIN reservation_events e
                  ON e.reservation_id = rr.reservation_id
                 AND e.revision = rr.reservation_revision
                WHERE rr.kind = 'change'
                  AND rr.acknowledged_at IS NOT NULL
                  AND e.event_type IN ('updated', 'cancelled')
                  AND e.actor_user_id != rr.user_id
                """
            )
            db.execute("DROP TABLE reminder_receipts")
            db.execute(NOTICE_RECEIPTS_INDEX_DDL)
            updated = db.execute(
                """
                UPDATE app_meta SET value = '3'
                WHERE key = 'schema_version' AND value = '2'
                """,
            )
            if updated.rowcount != 1:
                raise DatabaseGenerationError("数据库 schema_version 迁移写入失败")
            missing = _missing_v2_columns(db) + _missing_v3_columns(db)
            if missing:
                raise DatabaseGenerationError(
                    "数据库迁移后仍缺少列：" + ", ".join(missing)
                )
            if "notice_receipts" not in _table_names(db):
                raise DatabaseGenerationError("数据库迁移后仍缺少 notice_receipts 表")
            db.execute("COMMIT")
            return True
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
    finally:
        db.close()


def migrate_schema_v3_to_v4(path: Path) -> bool:
    """Atomically and idempotently upgrade a generation-2 schema-v3 database to v4.

    v4 新增工作交接：handover_requests 表（partial unique 保证同一预约同时
    只有一个待处理请求），并把 reservation_events 的 event_type 枚举扩展
    'handover'。SQLite 不能修改 CHECK 约束，事件表按"建新表-搬数据-删旧表-
    改名-重建索引"整体重建，全部行原样保留，任一步失败整体回滚。
    """

    db = _connect(path)
    # 重建 reservation_events 时，DROP TABLE 会触发 notice_receipts 的
    # ON DELETE CASCADE。该专用迁移连接在事务前临时关闭外键动作，并在
    # 提交前显式检查完整性，以保留引用同一事件 id 的既有回执。
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            if _meta_value(db, "product_generation") != str(PRODUCT_GENERATION):
                raise DatabaseGenerationError("迁移目标不是 V2 产品代际")
            version = _meta_value(db, "schema_version")
            tables = _table_names(db)
            if version == str(SCHEMA_VERSION):
                missing = (
                    _missing_v2_columns(db)
                    + _missing_v3_columns(db)
                    + _missing_v4_requirements(db, tables)
                )
                if missing:
                    raise DatabaseGenerationError(
                        "已标记为 schema v4 的数据库缺少结构：" + ", ".join(missing)
                    )
                db.execute("COMMIT")
                return False
            if version != "3":
                raise DatabaseGenerationError(
                    f"不支持从 schema {version or '缺失'} 迁移到 {SCHEMA_VERSION}"
                )
            missing_tables = EXPECTED_TABLES - tables
            if missing_tables:
                raise DatabaseGenerationError(
                    "待迁移的 V2 数据库结构不完整："
                    + ", ".join(sorted(missing_tables))
                )
            if "notice_receipts" not in tables:
                raise DatabaseGenerationError(
                    "待迁移的 schema v3 数据库缺少 notice_receipts 表"
                )
            if "handover_requests" in tables:
                raise DatabaseGenerationError(
                    "待迁移的 schema v3 数据库已包含 handover_requests 表"
                )
            db.execute(RESERVATION_EVENTS_V4_DDL)
            db.execute(
                """
                INSERT INTO reservation_events_new (
                    id, reservation_id, actor_user_id, event_type, revision,
                    before_json, after_json, occurred_at
                )
                SELECT id, reservation_id, actor_user_id, event_type, revision,
                       before_json, after_json, occurred_at
                FROM reservation_events
                """
            )
            db.execute("DROP TABLE reservation_events")
            db.execute(
                "ALTER TABLE reservation_events_new RENAME TO reservation_events"
            )
            db.execute(
                "CREATE INDEX idx_events_reservation "
                "ON reservation_events(reservation_id, occurred_at)"
            )
            db.execute(HANDOVER_REQUESTS_TABLE_DDL)
            db.execute(HANDOVER_REQUESTS_INDEX_DDL)
            db.execute(HANDOVER_REQUESTS_INBOX_INDEX_DDL)
            updated = db.execute(
                """
                UPDATE app_meta SET value = '4'
                WHERE key = 'schema_version' AND value = '3'
                """,
            )
            if updated.rowcount != 1:
                raise DatabaseGenerationError("数据库 schema_version 迁移写入失败")
            missing = (
                _missing_v2_columns(db)
                + _missing_v3_columns(db)
                + _missing_v4_requirements(db, _table_names(db))
            )
            if missing:
                raise DatabaseGenerationError(
                    "数据库迁移后仍缺少结构：" + ", ".join(missing)
                )
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise DatabaseGenerationError("数据库迁移后存在外键引用错误")
            db.execute("COMMIT")
            return True
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
    finally:
        try:
            db.execute("PRAGMA foreign_keys = ON")
        finally:
            db.close()


def _initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = _connect(path)
    try:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("BEGIN IMMEDIATE")
        try:
            if _table_names(db):
                raise DatabaseGenerationError("初始化时数据库已包含未知结构")
            for statement in SCHEMA_STATEMENTS:
                db.execute(statement)
            db.executemany(
                "INSERT INTO app_meta (key, value) VALUES (?, ?)",
                (
                    ("product_generation", str(PRODUCT_GENERATION)),
                    ("schema_version", str(SCHEMA_VERSION)),
                    ("setup_complete", "0"),
                    ("data_sequence", "0"),
                    ("backup_sequence", "0"),
                ),
            )
            db.execute(
                """
                INSERT INTO system_settings
                    (id, work_start, work_end, slot_minutes, max_duration_minutes)
                VALUES (1, '08:30', '17:30', 30, 180)
                """
            )
            db.executemany(
                "INSERT INTO global_tags (slot, label) VALUES (?, ?)",
                ((1, "标签 1"), (2, "标签 2")),
            )
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
    finally:
        db.close()


def database_health(db: sqlite3.Connection) -> dict[str, Any]:
    quick_rows = [str(row[0]) for row in db.execute("PRAGMA quick_check").fetchall()]
    foreign_key_rows = db.execute("PRAGMA foreign_key_check").fetchall()
    return {
        "quickCheck": quick_rows,
        "quickCheckOk": quick_rows == ["ok"],
        "foreignKeyErrors": len(foreign_key_rows),
        "foreignKeysOk": not foreign_key_rows,
    }


def prepare_database(
    path: Path,
    *,
    mirror_setup_complete: Optional[bool] = None,
) -> DatabaseStartupState:
    if path.exists() and path.is_dir():
        return _recovery("DATABASE_PATH_INVALID", "数据库路径无效")
    missing_or_empty = not path.exists()
    if path.exists():
        try:
            missing_or_empty = path.stat().st_size == 0
        except OSError:
            return _recovery("DATABASE_UNAVAILABLE", "数据库无法读取")
    if missing_or_empty:
        if mirror_setup_complete is True:
            return _recovery(
                "DATABASE_MISSING_AFTER_SETUP",
                "已完成设置的安装缺少数据库，已进入恢复模式",
            )
        try:
            _initialize_database(path)
        except (OSError, sqlite3.Error, DatabaseGenerationError):
            return _recovery("DATABASE_INITIALIZATION_FAILED", "数据库初始化失败")
        return DatabaseStartupState(
            ready=True,
            setup_complete=False,
            initialized=True,
        )

    try:
        classification = classify_existing_database(path)
    except DatabaseGenerationError:
        return _recovery(
            "DATABASE_GENERATION_INVALID",
            "数据库不属于可识别的 V2 代际，已进入恢复模式",
        )
    except (OSError, sqlite3.Error):
        return _recovery("DATABASE_UNAVAILABLE", "数据库无法读取，已进入恢复模式")

    if classification in ("v1", "v2", "v3"):
        try:
            if classification == "v1":
                migrate_schema_v1_to_v2(path)
            if classification in ("v1", "v2"):
                migrate_schema_v2_to_v3(path)
            migrate_schema_v3_to_v4(path)
            if classify_existing_database(path) != f"v{SCHEMA_VERSION}":
                raise DatabaseGenerationError("数据库迁移后版本复检失败")
        except Exception:
            return _recovery(
                "DATABASE_MIGRATION_FAILED",
                "数据库结构升级失败，已回滚并进入恢复模式",
            )

    try:
        db = _connect(path, readonly=True)
        try:
            health = database_health(db)
            setup_complete = _meta_value(db, "setup_complete") == "1"
            pre_setup_has_state = bool(
                not setup_complete
                and any(
                    db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                    for table in (
                        "users",
                        "rooms",
                        "user_preferences",
                        "reservations",
                        "reservation_slots",
                        "reservation_events",
                        "api_tokens",
                    )
                )
            )
            invariant_error = None
            try:
                validate_application_invariants(
                    db,
                    setup_complete=setup_complete,
                )
            except ApplicationInvariantError as error:
                invariant_error = str(error)
        finally:
            db.close()
    except (OSError, sqlite3.Error):
        return _recovery("DATABASE_UNAVAILABLE", "数据库无法读取，已进入恢复模式")
    if not health["quickCheckOk"]:
        return _recovery(
            "DATABASE_INTEGRITY_FAILED",
            "数据库完整性检查失败，已进入恢复模式",
        )
    if not health["foreignKeysOk"]:
        return _recovery(
            "DATABASE_FOREIGN_KEY_FAILED",
            "数据库关联完整性检查失败，已进入恢复模式",
        )
    if invariant_error:
        return _recovery(
            "DATABASE_APPLICATION_INVARIANT_FAILED",
            "数据库业务完整性检查失败，已进入恢复模式",
        )
    if mirror_setup_complete is True and not setup_complete:
        return _recovery(
            "SETUP_STATE_CONFLICT",
            "安装状态与数据库矛盾，已进入恢复模式",
        )
    if pre_setup_has_state:
        return _recovery(
            "SETUP_STATE_INVALID",
            "未完成设置的数据库含有业务数据，已进入恢复模式",
        )
    return DatabaseStartupState(ready=True, setup_complete=setup_complete)


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = _connect(Path(current_app.config["DATABASE"]))
    return g.db


def close_db(_error: Optional[BaseException] = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def transaction(
    db: Optional[sqlite3.Connection] = None,
    *,
    track_change: bool = True,
) -> Iterator[sqlite3.Connection]:
    connection = db or get_db()
    if connection.in_transaction:
        raise RuntimeError("不允许嵌套数据库写事务")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        if track_change:
            connection.execute(
                """
                INSERT INTO app_meta (key, value) VALUES ('data_sequence', '1')
                ON CONFLICT(key) DO UPDATE
                SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)
                """
            )
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def is_setup_complete(db: Optional[sqlite3.Connection] = None) -> bool:
    connection = db or get_db()
    return _meta_value(connection, "setup_complete") == "1"


def database_setup_complete(path: Path) -> bool:
    db = _connect(path, readonly=True)
    try:
        return _meta_value(db, "setup_complete") == "1"
    finally:
        db.close()


def register_db(app: Flask) -> None:
    app.teardown_appcontext(close_db)

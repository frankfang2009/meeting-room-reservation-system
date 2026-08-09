from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from flask import Flask, current_app, g


PRODUCT_GENERATION = 2
SCHEMA_VERSION = 1
V1_FINGERPRINT = {"users", "rooms", "reservations", "reservation_slots"}

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
    "reminder_receipts",
    "api_tokens",
    "security_audit_log",
}


class DatabaseGenerationError(RuntimeError):
    pass


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
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )
    """,
    """
    CREATE TABLE user_preferences (
        user_id TEXT PRIMARY KEY,
        default_duration INTEGER NOT NULL DEFAULT 60
            CHECK (default_duration BETWEEN 30 AND 180 AND default_duration % 30 = 0),
        default_room_id TEXT,
        booking_change_notifications INTEGER NOT NULL DEFAULT 1
            CHECK (booking_change_notifications IN (0, 1)),
        booking_reminder INTEGER NOT NULL DEFAULT 1
            CHECK (booking_reminder IN (0, 1)),
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
        purpose TEXT NOT NULL DEFAULT '工伤笔录',
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
        event_type TEXT NOT NULL CHECK (event_type IN ('created', 'updated', 'cancelled')),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        before_json TEXT,
        after_json TEXT,
        occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        FOREIGN KEY (reservation_id) REFERENCES reservations(id),
        FOREIGN KEY (actor_user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE reminder_receipts (
        reservation_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        reservation_revision INTEGER NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('change', 'upcoming')),
        delivered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        acknowledged_at TEXT,
        PRIMARY KEY (reservation_id, user_id, reservation_revision, kind),
        FOREIGN KEY (reservation_id) REFERENCES reservations(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
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
        if version != str(SCHEMA_VERSION):
            raise DatabaseGenerationError(
                f"数据库结构版本不受支持：需要 {SCHEMA_VERSION}，实际 {version or '缺失'}"
            )
        missing = EXPECTED_TABLES - tables
        if missing:
            raise DatabaseGenerationError(
                "V2 数据库结构不完整：" + ", ".join(sorted(missing))
            )
        return "v2"
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


def prepare_database(path: Path) -> None:
    if path.exists() and path.is_dir():
        raise DatabaseGenerationError("数据库路径指向文件夹")
    state = classify_existing_database(path) if path.exists() else "empty"
    if state == "empty":
        _initialize_database(path)


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = _connect(Path(current_app.config["DATABASE"]))
    return g.db


def close_db(_error: Optional[BaseException] = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def transaction(db: Optional[sqlite3.Connection] = None) -> Iterator[sqlite3.Connection]:
    connection = db or get_db()
    if connection.in_transaction:
        raise RuntimeError("不允许嵌套数据库写事务")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
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

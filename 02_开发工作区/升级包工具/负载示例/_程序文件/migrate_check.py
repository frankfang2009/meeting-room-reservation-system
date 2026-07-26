from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence


PROJECT_DIR = Path(__file__).resolve().parent
LOG_DIR = PROJECT_DIR / "logs"
UPGRADE_LOG_ENV = "MEETING_ROOM_UPGRADE_LOG"


def _configure_console_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _open_existing_database(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise RuntimeError(f"数据库文件不存在：{path}")
    uri = path.resolve().as_uri() + "?mode=rw"
    db = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
    db.execute("PRAGMA busy_timeout = 10000")
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _read_schema_version(db: sqlite3.Connection) -> int:
    try:
        row = db.execute(
            "SELECT value FROM app_meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.Error as error:
        raise RuntimeError("数据库缺少结构版本信息") from error
    if row is None:
        raise RuntimeError("数据库缺少结构版本信息")
    try:
        version = int(row[0])
    except (TypeError, ValueError) as error:
        raise RuntimeError("数据库结构版本无效") from error
    if version < 1 or str(version) != str(row[0]).strip():
        raise RuntimeError("数据库结构版本无效")
    return version


def _check_database(path: Path, expected_schema_version: Optional[int] = None) -> None:
    db = _open_existing_database(path)
    try:
        checkpoint = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is not None and checkpoint[0] != 0:
            raise RuntimeError("数据库 WAL 文件仍在使用，无法完成检查点")

        integrity_rows = db.execute("PRAGMA integrity_check").fetchall()
        if integrity_rows != [("ok",)]:
            details = "; ".join(str(row[0]) for row in integrity_rows)
            raise RuntimeError(f"数据库完整性检查失败：{details}")

        foreign_key_errors = db.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError(
                f"数据库存在 {len(foreign_key_errors)} 条外键错误"
            )

        if expected_schema_version is not None:
            actual_schema_version = _read_schema_version(db)
            if actual_schema_version != expected_schema_version:
                raise RuntimeError(
                    "数据库结构版本不匹配："
                    f"应为 {expected_schema_version}，实际为 {actual_schema_version}"
                )
    finally:
        db.close()


def _run_migration() -> tuple[Path, int]:
    os.chdir(PROJECT_DIR)
    from app import SCHEMA_VERSION, app, init_db

    with app.app_context():
        database = Path(app.config["DATABASE"])
        if not database.is_file():
            raise RuntimeError(
                "数据库文件不存在，疑似数据丢失；升级已停止。"
                "请不要继续操作，并联系维护人员"
            )
        init_db()
    _check_database(database, expected_schema_version=SCHEMA_VERSION)
    return database, SCHEMA_VERSION


def _write_failure_log(message: str) -> None:
    configured = os.environ.get(UPGRADE_LOG_ENV)
    if configured:
        target = Path(configured)
    else:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        target = LOG_DIR / f"upgrade_migrate_{datetime.now():%Y%m%d_%H%M%S_%f}.log"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} 数据库检查失败：{message}\n")
    except OSError:
        pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="会议室预约系统数据库升级检查")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--precheck", metavar="数据库路径", type=Path)
    modes.add_argument("--migrate", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_console_streams()
    arguments = _parser().parse_args(argv)
    try:
        if arguments.precheck is not None:
            _check_database(arguments.precheck)
            print(f"数据库预检通过：{arguments.precheck}")
        else:
            database, schema_version = _run_migration()
            print(
                f"数据库迁移与检查完成：{database}（结构版本 {schema_version}）"
            )
        return 0
    except Exception as error:
        message = str(error) or error.__class__.__name__
        _write_failure_log(message)
        print(f"数据库检查失败：{message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from v2app.backup import (
    create_backup,
    maintenance_lock,
    reserve_backup_sequence,
    scheduled_backup_due,
)
from v2app.common import canonical_json, new_id
from v2app.db import prepare_database
from v2app.runtime.identity import load_existing_install_id
from v2app.runtime.install_state import load_install_json


APP_DIR = Path(__file__).resolve().parent
PROGRAM_DIR = APP_DIR.parent


@dataclass(frozen=True)
class _CliPaths:
    program_dir: Path
    data_dir: Path
    backup_dir: Path


def _production_paths() -> _CliPaths:
    """Derive every mutable production path from this installed entrypoint."""

    program_dir = APP_DIR.parent
    return _CliPaths(
        program_dir=program_dir,
        data_dir=program_dir / "data",
        backup_dir=program_dir / "backups",
    )


def _logger(program_dir: Path) -> logging.Logger:
    path = program_dir / "logs" / "backup.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("meeting_room_v2.backup_cli")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(
            path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def _canonical_uuid4(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError) as error:
        raise RuntimeError("expected install_id 无效") from error
    if str(parsed) != value or parsed.version != 4 or parsed.variant != uuid.RFC_4122:
        raise RuntimeError("expected install_id 无效")
    return value


def _write_status(data_dir: Path, *, status: str, detail: str, sequence=None) -> None:
    target = data_dir / "backup-status.json"
    temporary = target.with_suffix(".json.part")
    value = {
        "schema": 1,
        "status": status,
        "detail": detail,
        "sequence": sequence,
        "updatedAtUtc": dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def _audit(db: sqlite3.Connection, action: str, details: dict) -> None:
    db.execute(
        """
        INSERT INTO security_audit_log
            (id, actor_user_id, action, target_type, target_id, details_json)
        VALUES (?, NULL, ?, 'system', NULL, ?)
        """,
        (new_id(), action, canonical_json(details)),
    )


def _reserve_sequence(
    db: sqlite3.Connection,
    mode: str,
    *,
    backup_dir: Path,
    install_id: str,
) -> tuple[int, int]:
    db.execute("BEGIN IMMEDIATE")
    try:
        sequence, data_sequence = reserve_backup_sequence(
            db,
            backup_dir,
            install_id=install_id,
        )
        _audit(
            db,
            "backup.requested",
            {"mode": mode, "sequence": sequence, "result": "requested"},
        )
        db.execute("COMMIT")
        return sequence, data_sequence
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


def _record_result(
    db: sqlite3.Connection, *, action: str, mode: str, sequence: int, result: str
) -> None:
    db.execute("BEGIN IMMEDIATE")
    try:
        _audit(
            db,
            action,
            {"mode": mode, "sequence": sequence, "result": result},
        )
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


def _run_backup(args: argparse.Namespace, paths: _CliPaths) -> int:
    program_dir = paths.program_dir
    data_dir = paths.data_dir
    backup_dir = paths.backup_dir
    logger = _logger(program_dir)
    operation = "catch-up" if args.catch_up else "scheduled" if args.scheduled else "manual"
    db = None
    sequence = None
    try:
        if (args.scheduled or args.catch_up) and args.expected_install_id is None:
            raise RuntimeError("计划备份必须绑定 expected install_id")
        metadata = load_install_json(data_dir / "install.json")
        if metadata is None:
            raise RuntimeError("缺少 install.json")
        install_id = load_existing_install_id(data_dir / "install_id")
        if metadata["install_id"] != install_id:
            raise RuntimeError("安装身份文件不一致")
        if args.expected_install_id is not None:
            expected = _canonical_uuid4(args.expected_install_id)
            if expected != install_id:
                raise RuntimeError("expected install_id 与当前安装不一致")
        database = data_dir / "reservation.db"
        if not database.is_file() or database.stat().st_size == 0:
            raise RuntimeError("备份源数据库缺失或为空")
        startup = prepare_database(
            database, mirror_setup_complete=metadata["setup_complete"]
        )
        if not startup.ready:
            raise RuntimeError(f"数据库未就绪：{startup.code}")
        if not startup.setup_complete:
            logger.info("backup skipped because setup is incomplete mode=%s", operation)
            _write_status(data_dir, status="skipped", detail="setup_incomplete")
            return 0
        with maintenance_lock(
            data_dir / "maintenance.lock",
            operation="backup",
            install_id=install_id,
        ):
            if (args.scheduled or args.catch_up) and not scheduled_backup_due(
                backup_dir,
                install_id=install_id,
                catch_up=args.catch_up,
            ):
                logger.info("backup idempotent no-op mode=%s", operation)
                _write_status(data_dir, status="current", detail="idempotent_noop")
                return 0
            db = sqlite3.connect(database, timeout=10, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            sequence, data_sequence = _reserve_sequence(
                db,
                operation,
                backup_dir=backup_dir,
                install_id=install_id,
            )
            target, sidecar = create_backup(
                database,
                backup_dir,
                install_id=install_id,
                sequence=sequence,
                source_data_sequence=data_sequence,
            )
        _record_result(
            db,
            action="backup.succeeded",
            mode=operation,
            sequence=sequence,
            result="succeeded",
        )
        logger.info("backup succeeded sequence=%s file=%s", sequence, target.name)
        _write_status(
            data_dir,
            status="succeeded",
            detail=sidecar["createdAtUtc"],
            sequence=sequence,
        )
        print(f"备份完成：{target}")
        return 0
    except Exception as error:
        logger.exception("backup failed mode=%s sequence=%s", operation, sequence)
        if db is not None and sequence is not None:
            try:
                _record_result(
                    db,
                    action="backup.failed",
                    mode=operation,
                    sequence=sequence,
                    result="failed",
                )
            except Exception:
                logger.exception("failed to write backup failure audit")
        try:
            _write_status(data_dir, status="failed", detail=type(error).__name__, sequence=sequence)
        except Exception:
            logger.exception("failed to write backup status")
        print(
            "备份失败：没有生成可用备份。请在系统状态中重试；"
            "仍失败请提交 _程序文件\\logs\\backup.log。",
            file=sys.stderr,
        )
        return 1
    finally:
        if db is not None:
            db.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="backup.py")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--scheduled", action="store_true")
    mode.add_argument("--catch-up", action="store_true")
    parser.add_argument("--expected-install-id")
    args = parser.parse_args(argv)
    return _run_backup(args, _production_paths())


if __name__ == "__main__":
    raise SystemExit(main())

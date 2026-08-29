from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import logging
import os
import secrets
import shutil
import sqlite3
import stat
import sys
import uuid
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

from service import _pid_exists
from v2app.backup import (
    load_backup_sidecar,
    maintenance_lock,
    sha256_file,
    sidecar_path,
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
    path = program_dir / "logs" / "restore.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("meeting_room_v2.restore_cli")
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


def _assert_plain_protected_backup(path: Path, backup_dir: Path) -> Path:
    if not path.is_absolute():
        raise RuntimeError("恢复备份必须使用绝对路径")
    try:
        resolved = path.resolve(strict=True)
        protected = backup_dir.resolve(strict=True)
        info = resolved.lstat()
    except OSError as error:
        raise RuntimeError("恢复备份不存在或无法读取") from error
    if resolved.parent != protected:
        raise RuntimeError("只允许从本安装受保护的 backups 目录恢复")
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise RuntimeError("恢复备份必须是普通文件")
    return resolved


def _assert_plain_sidecar(path: Path, backup_dir: Path) -> Path:
    try:
        protected = backup_dir.resolve(strict=True)
        info = path.lstat()
    except OSError as error:
        raise RuntimeError("恢复备份 sidecar 不存在或无法读取") from error
    if path.parent.resolve(strict=True) != protected:
        raise RuntimeError("恢复备份 sidecar 不在受保护目录")
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise RuntimeError("恢复备份 sidecar 必须是普通文件")
    return path


def _assert_service_stopped(data_dir: Path, install_id: str) -> None:
    pid_path = data_dir / "service.pid"
    if not pid_path.exists():
        return
    try:
        value = json.loads(pid_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise RuntimeError("service.pid 无法验证，拒绝恢复") from error
    if not isinstance(value, dict) or value.get("installId") != install_id:
        raise RuntimeError("service.pid 安装身份不匹配，拒绝恢复")
    pid = value.get("pid")
    if type(pid) is not int or pid <= 0:
        raise RuntimeError("service.pid 进程号无效，拒绝恢复")
    if _pid_exists(pid):
        raise RuntimeError("服务仍在运行，请先安全停止服务")
    raise RuntimeError("service.pid 过期且状态不明，请先执行 service.py --stop")


def _copy_fsync(source: Path, target: Path) -> None:
    with source.open("rb") as input_handle, target.open("xb") as output_handle:
        shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
        output_handle.flush()
        os.fsync(output_handle.fileno())


def _pre_restore_snapshot(
    database: Path, backup_dir: Path, install_id: str
) -> tuple[Path, dict[str, Any]]:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = backup_dir / f"pre-restore-{stamp}-{uuid.uuid4().hex[:8]}"
    snapshot.mkdir(parents=False, exist_ok=False)
    files = []
    sources = (
        (
            database,
            Path(str(database) + "-wal"),
            Path(str(database) + "-shm"),
        )
        if database.is_file()
        else ()
    )
    for source in sources:
        if not source.exists():
            continue
        target = snapshot / source.name
        _copy_fsync(source, target)
        files.append(
            {"name": target.name, "bytes": target.stat().st_size, "sha256": sha256_file(target)}
        )
    had_database = any(item["name"] == database.name for item in files)
    manifest = {
        "schema": 1,
        "kind": "meeting-room-v2-pre-restore-snapshot",
        "installId": install_id,
        "createdAtUtc": dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "files": files,
        "hadDatabase": had_database,
    }
    manifest_path = snapshot / "manifest.json"
    with manifest_path.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return snapshot, manifest


def _session_version_high_water(database: Path) -> Optional[int]:
    if not database.is_file():
        return 0
    db = sqlite3.connect(database, timeout=10)
    try:
        row = db.execute(
            "SELECT COALESCE(MAX(session_version), 0) FROM users"
        ).fetchone()
        return int(row[0])
    except sqlite3.DatabaseError as error:
        error_code = getattr(error, "sqlite_errorcode", None)
        if not isinstance(error_code, int) or error_code & 0xFF not in {
            sqlite3.SQLITE_CORRUPT,
            sqlite3.SQLITE_NOTADB,
        }:
            raise
        return None
    finally:
        db.close()


def _record_restore_audit(
    database: Path,
    backup_name: str,
    sequence: int,
    *,
    pre_restore_session_version_high_water: Optional[int],
) -> None:
    db = sqlite3.connect(database, timeout=10, isolation_level=None)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        restored_high_water = int(
            db.execute(
                "SELECT COALESCE(MAX(session_version), 0) FROM users"
            ).fetchone()[0]
        )
        if pre_restore_session_version_high_water is None:
            invalidation_floor = (1 << 61) | secrets.randbits(61)
        else:
            invalidation_floor = pre_restore_session_version_high_water
        next_session_version = max(restored_high_water, invalidation_floor) + 1
        db.execute(
            """
            UPDATE users
            SET session_version = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            """,
            (next_session_version,),
        )
        db.execute(
            """
            INSERT INTO security_audit_log
                (id, actor_user_id, action, target_type, target_id, details_json)
            VALUES (?, NULL, 'restore.succeeded', 'system', ?, ?)
            """,
            (
                new_id(),
                backup_name,
                canonical_json({"sequence": sequence, "result": "succeeded"}),
            ),
        )
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def _verify_rollback(database: Path, manifest: dict[str, Any]) -> None:
    expected = {item["name"]: item for item in manifest["files"]}
    for target in (
        database,
        Path(str(database) + "-wal"),
        Path(str(database) + "-shm"),
    ):
        item = expected.get(target.name)
        if item is None:
            if target.exists():
                raise RuntimeError("恢复失败后的回滚残留了额外数据库文件")
            continue
        if (
            not target.is_file()
            or target.stat().st_size != item["bytes"]
            or sha256_file(target) != item["sha256"]
        ):
            raise RuntimeError("恢复失败后的数据库回滚复检失败")


def restore_backup(
    *,
    database: Path,
    backup: Path,
    backup_dir: Path,
    data_dir: Path,
    install_id: str,
) -> dict[str, Any]:
    backup = _assert_plain_protected_backup(backup, backup_dir)
    sidecar = _assert_plain_sidecar(sidecar_path(backup), backup_dir)
    value = load_backup_sidecar(
        sidecar, expected_install_id=install_id, verify_hash=True
    )
    _assert_service_stopped(data_dir, install_id)
    pre_restore_session_version_high_water = _session_version_high_water(database)
    snapshot, _manifest = _pre_restore_snapshot(database, backup_dir, install_id)
    temporary = data_dir / f".{database.name}.restore.{uuid.uuid4().hex}.part"
    old_wal = Path(str(database) + "-wal")
    old_shm = Path(str(database) + "-shm")
    mutation_started = False
    try:
        _copy_fsync(backup, temporary)
        mutation_started = True
        for side_file in (old_wal, old_shm):
            with contextlib.suppress(FileNotFoundError):
                side_file.unlink()
        os.replace(temporary, database)
        state = prepare_database(database, mirror_setup_complete=True)
        if not state.ready or not state.setup_complete:
            raise RuntimeError("恢复后数据库复检失败")
        _record_restore_audit(
            database,
            backup.name,
            value["sequence"],
            pre_restore_session_version_high_water=(
                pre_restore_session_version_high_water
            ),
        )
        state = prepare_database(database, mirror_setup_complete=True)
        if not state.ready:
            raise RuntimeError("恢复审计写入后复检失败")
        return {
            "restored": True,
            "backupFile": backup.name,
            "sequence": value["sequence"],
            "preRestoreSnapshot": snapshot.name,
        }
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        if not mutation_started:
            raise
        snapshot_database = snapshot / database.name
        if snapshot_database.exists():
            rollback = data_dir / f".{database.name}.rollback.{uuid.uuid4().hex}.part"
            _copy_fsync(snapshot_database, rollback)
            os.replace(rollback, database)
            for suffix in ("-wal", "-shm"):
                snapshot_file = snapshot / (database.name + suffix)
                target = Path(str(database) + suffix)
                with contextlib.suppress(FileNotFoundError):
                    target.unlink()
                if snapshot_file.exists():
                    _copy_fsync(snapshot_file, target)
        else:
            for target in (database, old_wal, old_shm):
                with contextlib.suppress(FileNotFoundError):
                    target.unlink()
        _verify_rollback(database, _manifest)
        raise


def _run_restore(args: argparse.Namespace, paths: _CliPaths) -> int:
    program_dir = paths.program_dir
    data_dir = paths.data_dir
    backup_dir = paths.backup_dir
    logger = _logger(program_dir)
    try:
        expected = _canonical_uuid4(args.expected_install_id)
        metadata = load_install_json(data_dir / "install.json")
        if metadata is None:
            raise RuntimeError("缺少 install.json")
        install_id = load_existing_install_id(data_dir / "install_id")
        if expected != install_id or metadata["install_id"] != install_id:
            raise RuntimeError("恢复身份与当前安装不一致")
        with maintenance_lock(
            data_dir / "maintenance.lock",
            operation="restore",
            install_id=install_id,
        ):
            result = restore_backup(
                database=data_dir / "reservation.db",
                backup=Path(args.backup),
                backup_dir=backup_dir,
                data_dir=data_dir,
                install_id=install_id,
            )
        logger.info(
            "restore succeeded backup=%s sequence=%s snapshot=%s",
            result["backupFile"],
            result["sequence"],
            result["preRestoreSnapshot"],
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception:
        logger.exception("restore failed")
        print(
            "恢复失败：系统不会把当前数据库当作已恢复。请保留现场，"
            "不要重复覆盖，并提交 _程序文件\\logs\\restore.log。",
            file=sys.stderr,
        )
        return 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="restore.py")
    parser.add_argument("--backup", required=True)
    parser.add_argument("--expected-install-id", required=True)
    args = parser.parse_args(argv)
    return _run_restore(args, _production_paths())


if __name__ == "__main__":
    raise SystemExit(main())

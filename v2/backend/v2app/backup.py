from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional


def create_backup(
    source: Path,
    backup_dir: Path,
    *,
    keep_count: int = 30,
    now: Optional[datetime] = None,
) -> Path:
    if not source.is_file():
        raise RuntimeError("数据库文件不存在")
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now or datetime.now()
    target = backup_dir / f"reservation_v2_{timestamp:%Y-%m-%d_%H%M%S_%f}.db"
    temporary = target.with_suffix(".db.part")
    source_db = sqlite3.connect(source, timeout=10)
    target_db = sqlite3.connect(temporary)
    try:
        source_db.backup(target_db)
        target_db.commit()
        if target_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("备份完整性检查失败")
        if target_db.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("备份外键检查失败")
        journal = target_db.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if str(journal).lower() != "delete":
            raise RuntimeError("无法把备份转换成单文件格式")
        target_db.close()
        source_db.close()
        temporary.replace(target)
    except Exception:
        try:
            target_db.close()
        except Exception:
            pass
        try:
            source_db.close()
        except Exception:
            pass
        for path in (temporary, Path(str(temporary) + "-wal"), Path(str(temporary) + "-shm")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise

    backups = sorted(backup_dir.glob("reservation_v2_*.db"), reverse=True)
    for old in backups[max(1, keep_count):]:
        try:
            old.unlink()
        except OSError:
            continue
    return target


def latest_backup(backup_dir: Path) -> Optional[Path]:
    backups = sorted(backup_dir.glob("reservation_v2_*.db"), reverse=True)
    return backups[0] if backups else None

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
SOURCE = PROJECT_DIR / "data" / "reservation.db"
BACKUP_DIR = PROJECT_DIR / "backups"
KEEP_COUNT = 30


def main(source: Path = SOURCE, backup_dir: Path = BACKUP_DIR) -> int:
    if not source.exists():
        print("还没有系统数据，请先启动一次会议室预约系统。")
        return 1

    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"reservation_{datetime.now():%Y-%m-%d_%H%M%S_%f}.db"
    temporary = target.with_suffix(".db.part")
    source_db = None
    target_db = None
    try:
        source_db = sqlite3.connect(source, timeout=10)
        target_db = sqlite3.connect(temporary)
        source_db.backup(target_db)
        target_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        journal_mode = target_db.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if journal_mode.lower() != "delete":
            raise RuntimeError("无法把备份转换成单文件格式")
        result = target_db.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"备份完整性检查失败：{result}")
        target_db.close()
        target_db = None
        source_db.close()
        source_db = None
        temporary.replace(target)
    except Exception as error:
        print(f"备份失败：{error}")
        return 1
    finally:
        if target_db is not None:
            target_db.close()
        if source_db is not None:
            source_db.close()
        for leftover in (
            temporary,
            Path(f"{temporary}-wal"),
            Path(f"{temporary}-shm"),
            Path(f"{target}-wal"),
            Path(f"{target}-shm"),
        ):
            try:
                leftover.unlink()
            except FileNotFoundError:
                pass

    backups = sorted(backup_dir.glob("reservation_*.db"), reverse=True)
    for backup in backups[:KEEP_COUNT]:
        backup_db = None
        try:
            backup_db = sqlite3.connect(backup)
            backup_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            backup_db.execute("PRAGMA journal_mode=DELETE")
            if backup_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                continue
        except sqlite3.Error:
            continue
        finally:
            if backup_db is not None:
                backup_db.close()
        for suffix in ("-wal", "-shm"):
            try:
                Path(f"{backup}{suffix}").unlink()
            except FileNotFoundError:
                pass

    for old_backup in backups[KEEP_COUNT:]:
        old_backup.unlink()
        for suffix in ("-wal", "-shm"):
            try:
                Path(f"{old_backup}{suffix}").unlink()
            except FileNotFoundError:
                pass

    print(f"备份完成：{target.name}")
    print(f"保存位置：{backup_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

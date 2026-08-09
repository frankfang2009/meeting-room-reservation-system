from __future__ import annotations

import os
from pathlib import Path

from v2app.backup import create_backup


def main() -> int:
    backend_dir = Path(__file__).resolve().parent
    data_dir = Path(os.environ.get("MEETING_ROOM_V2_DATA_DIR", backend_dir / "data"))
    try:
        target = create_backup(
            data_dir / "reservation.db",
            data_dir.parent / "backups",
        )
    except Exception as error:
        print(f"备份失败：{error}")
        return 1
    print(f"备份完成：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

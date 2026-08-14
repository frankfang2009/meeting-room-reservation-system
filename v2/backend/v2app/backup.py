from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from .invariants import ApplicationInvariantError, validate_application_invariants


BACKUP_KIND = "meeting-room-v2-backup"
BACKUP_SIDECAR_SCHEMA = 1
BACKUP_PREFIX = "reservation-v2-backup-"
BACKUP_KEEP_COUNT = 30
_SQLITE_COMPANION_SUFFIXES = ("-wal", "-shm", "-journal")
_LOCK_OPERATIONS = {"backup", "restore"}
_LOCK_IDENTITY_METHODS = {
    "darwin-procpath",
    "linux-proc",
    "posix-conservative",
    "windows-native",
}


def _utc_now(now: Optional[dt.datetime] = None) -> dt.datetime:
    value = now or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError("备份时钟必须带时区")
    return value.astimezone(dt.timezone.utc)


def _utc_text(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_utc(value: Any) -> dt.datetime:
    if not isinstance(value, str) or "T" not in value:
        raise RuntimeError("备份时间无效")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as error:
        raise RuntimeError("备份时间无效") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError("备份时间缺少时区")
    return parsed.astimezone(dt.timezone.utc)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sqlite_companion_paths(path: Path) -> tuple[Path, ...]:
    return tuple(Path(str(path) + suffix) for suffix in _SQLITE_COMPANION_SUFFIXES)


def _remove_sqlite_companions(path: Path) -> None:
    for companion in _sqlite_companion_paths(path):
        try:
            companion.unlink()
        except FileNotFoundError:
            continue


def _remove_orphaned_temporary_companions(database_path: Path) -> None:
    pattern = f".{database_path.name}.*.part-*"
    for companion in database_path.parent.glob(pattern):
        with contextlib.suppress(OSError):
            companion.unlink()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.part"
    )
    encoded = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _identity_digest(method: str, value: str) -> dict[str, str]:
    return {
        "method": method,
        "fingerprint": hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest(),
    }


def _windows_process_identity(pid: int) -> Optional[dict[str, str]]:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error in {87, 1168}:
            return None
        raise RuntimeError("无法核验维护锁进程状态")
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise RuntimeError("无法核验维护锁进程状态")
        if exit_code.value != still_active:
            return None
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise RuntimeError("无法核验维护锁进程身份")
        size = wintypes.DWORD(32768)
        image = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, image, ctypes.byref(size)
        ):
            raise RuntimeError("无法核验维护锁进程身份")
        created = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return _identity_digest(
            "windows-native", f"{created}:{image.value.casefold()}"
        )
    finally:
        kernel32.CloseHandle(handle)


def _darwin_process_identity(pid: int) -> Optional[dict[str, str]]:
    import ctypes

    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pidpath = libproc.proc_pidpath
        proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        proc_pidpath.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(4096)
        length = proc_pidpath(pid, buffer, len(buffer))
    except (AttributeError, OSError) as error:
        raise RuntimeError("无法核验维护锁进程身份") from error
    if length <= 0:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, OverflowError):
            return None
        except PermissionError as error:
            raise RuntimeError("无法核验维护锁进程身份") from error
        raise RuntimeError("无法核验维护锁进程身份")
    try:
        executable = buffer.value.decode("utf-8")
    except UnicodeError as error:
        raise RuntimeError("无法核验维护锁进程身份") from error
    # proc_pidpath has no creation time.  A reused PID pointing to a different
    # executable is reclaimed; the same executable is conservatively treated
    # as still active rather than risking deletion of a live lock.
    return _identity_digest("darwin-procpath", executable)


def _process_identity(pid: int) -> Optional[dict[str, str]]:
    """Return a PID-reuse-resistant identity, None only for a dead PID."""

    if os.name == "nt":
        return _windows_process_identity(pid)
    if sys.platform == "darwin":
        return _darwin_process_identity(pid)
    if sys.platform.startswith("linux"):
        proc = Path("/proc") / str(pid)
        try:
            raw_stat = (proc / "stat").read_text(encoding="ascii")
            closing = raw_stat.rfind(")")
            fields = raw_stat[closing + 2 :].split()
            if closing < 1 or len(fields) <= 19:
                raise RuntimeError("维护锁进程身份格式无效")
            start_ticks = fields[19]
            executable = os.readlink(proc / "exe")
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
                encoding="ascii"
            ).strip()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as error:
            raise RuntimeError("无法核验维护锁进程身份") from error
        return _identity_digest(
            "linux-proc", f"{boot_id}:{start_ticks}:{executable}"
        )

    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OverflowError):
        return None
    except PermissionError as error:
        raise RuntimeError("无法核验维护锁进程身份") from error
    # On an unknown POSIX platform we cannot safely read a creation time.
    # Retaining a lock across same-PID reuse is a safe false positive.
    return _identity_digest("posix-conservative", str(pid))


def _validate_install_id(value: Any) -> str:
    if not isinstance(value, str):
        raise RuntimeError("维护锁安装身份无效")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise RuntimeError("维护锁安装身份无效") from error
    if parsed.version != 4 or str(parsed) != value:
        raise RuntimeError("维护锁安装身份无效")
    return value


def _validate_process_identity(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"method", "fingerprint"}:
        raise RuntimeError("维护锁进程身份无效")
    method = value.get("method")
    fingerprint = value.get("fingerprint")
    if method not in _LOCK_IDENTITY_METHODS or not isinstance(
        fingerprint, str
    ) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise RuntimeError("维护锁进程身份无效")
    return {"method": method, "fingerprint": fingerprint}


def _read_maintenance_record(path: Path) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except (FileNotFoundError, OSError) as error:
        raise RuntimeError("维护锁状态不可识别，拒绝自动清理") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 8192:
            raise RuntimeError("维护锁状态不可识别，拒绝自动清理")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            value = json.load(handle)
    except (OSError, UnicodeError, ValueError) as error:
        raise RuntimeError("维护锁状态不可识别，拒绝自动清理") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    required = {
        "schema",
        "pid",
        "operation",
        "installId",
        "token",
        "createdAtUtc",
        "processIdentity",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeError("维护锁状态不可识别，拒绝自动清理")
    if value["schema"] != 1 or type(value["pid"]) is not int or not (
        1 <= value["pid"] <= 0xFFFFFFFF
    ):
        raise RuntimeError("维护锁状态不可识别，拒绝自动清理")
    if value["operation"] not in _LOCK_OPERATIONS:
        raise RuntimeError("维护锁状态不可识别，拒绝自动清理")
    _validate_install_id(value["installId"])
    if not isinstance(value["token"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", value["token"]
    ):
        raise RuntimeError("维护锁状态不可识别，拒绝自动清理")
    _parse_utc(value["createdAtUtc"])
    value["processIdentity"] = _validate_process_identity(value["processIdentity"])
    return value


@contextmanager
def _maintenance_guard(path: Path, *, blocking: bool) -> Iterator[None]:
    guard_path = path.with_name(path.name + ".guard")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(guard_path, flags, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError("维护锁守卫文件无效")
        if info.st_size < 1:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            msvcrt.locking(descriptor, mode, 1)
        else:
            import fcntl

            mode = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            fcntl.flock(descriptor, mode)
    except (OSError, RuntimeError) as error:
        with contextlib.suppress(UnboundLocalError, OSError):
            os.close(descriptor)
        raise RuntimeError("系统正在核验其他备份或恢复任务") from error
    try:
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@contextmanager
def maintenance_lock(
    path: Path, *, operation: str, install_id: str
) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if operation not in _LOCK_OPERATIONS:
        raise RuntimeError("维护锁操作类型无效")
    install_id = _validate_install_id(install_id)
    token = secrets.token_hex(32)
    record = {
        "schema": 1,
        "pid": os.getpid(),
        "operation": operation,
        "installId": install_id,
        "token": token,
        "createdAtUtc": _utc_text(_utc_now()),
        "processIdentity": _process_identity(os.getpid()),
    }
    _validate_process_identity(record["processIdentity"])
    with _maintenance_guard(path, blocking=False):
        if path.exists():
            existing = _read_maintenance_record(path)
            if existing["installId"] != install_id:
                raise RuntimeError("维护锁安装身份不一致，拒绝自动清理")
            current_identity = _process_identity(existing["pid"])
            if current_identity == existing["processIdentity"]:
                raise RuntimeError("系统正在执行其他备份或恢复任务")
            # None means the original process is gone.  A different verified
            # identity means the PID was reused.  Both are safe to reclaim.
            path.unlink()
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    try:
        yield
    finally:
        try:
            with _maintenance_guard(path, blocking=True):
                current = _read_maintenance_record(path)
                if (
                    current["installId"] == install_id
                    and current["token"] == token
                    and current["processIdentity"] == record["processIdentity"]
                ):
                    path.unlink()
        except RuntimeError:
            # A corrupted or replaced lock is never deleted based on guesses.
            pass


def _database_metadata(db: sqlite3.Connection) -> dict[str, Any]:
    try:
        meta = dict(db.execute("SELECT key, value FROM app_meta").fetchall())
    except sqlite3.Error as error:
        raise RuntimeError("备份源不是可识别的 V2 数据库") from error
    if meta.get("product_generation") != "2" or meta.get("schema_version") != "1":
        raise RuntimeError("备份源数据库代际或结构版本无效")
    if meta.get("setup_complete") not in ("0", "1"):
        raise RuntimeError("备份源 setup_complete 无效")
    try:
        raw_data_sequence = meta["data_sequence"]
        if not raw_data_sequence.isdigit():
            raise ValueError("not a non-negative integer")
        data_sequence = int(raw_data_sequence)
    except (KeyError, ValueError) as error:
        raise RuntimeError("备份源数据序列无效") from error
    return {
        "productGeneration": 2,
        "databaseSchemaVersion": 1,
        "setupComplete": meta["setup_complete"] == "1",
        "sourceDataSequence": data_sequence,
    }


def _verify_sqlite(path: Path) -> dict[str, Any]:
    try:
        db = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=10,
        )
        db.row_factory = sqlite3.Row
        try:
            integrity = [
                row[0] for row in db.execute("PRAGMA integrity_check").fetchall()
            ]
            if integrity != ["ok"]:
                raise RuntimeError("备份完整性检查失败")
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("备份外键检查失败")
            metadata = _database_metadata(db)
            try:
                validate_application_invariants(
                    db,
                    setup_complete=metadata["setupComplete"],
                )
            except ApplicationInvariantError as error:
                raise RuntimeError("备份业务完整性检查失败") from error
            return metadata
        finally:
            db.close()
    except sqlite3.Error as error:
        raise RuntimeError("备份数据库无法读取") from error


def sidecar_path(database_path: Path) -> Path:
    return database_path.with_suffix(".json")


def load_backup_sidecar(
    path: Path,
    *,
    expected_install_id: Optional[str] = None,
    verify_hash: bool = False,
) -> dict[str, Any]:
    try:
        if path.stat().st_size > 64 * 1024:
            raise RuntimeError("备份 sidecar 体积异常")
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError("备份 sidecar 缺失") from error
    except (OSError, UnicodeError, ValueError) as error:
        raise RuntimeError("备份 sidecar 无法读取") from error
    required = {
        "schema",
        "kind",
        "installId",
        "productGeneration",
        "databaseSchemaVersion",
        "setupComplete",
        "databaseSha256",
        "databaseBytes",
        "sequence",
        "sourceDataSequence",
        "createdAtUtc",
        "fileName",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeError("备份 sidecar 字段无效")
    if (
        value["schema"] != BACKUP_SIDECAR_SCHEMA
        or value["kind"] != BACKUP_KIND
        or value["productGeneration"] != 2
        or value["databaseSchemaVersion"] != 1
        or value["setupComplete"] is not True
    ):
        raise RuntimeError("备份 sidecar 不属于已设置的 V2")
    if expected_install_id is not None and value["installId"] != expected_install_id:
        raise RuntimeError("备份 install_id 与当前安装不一致")
    if not isinstance(value["installId"], str) or not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        value["installId"],
    ):
        raise RuntimeError("备份 install_id 无效")
    if type(value["sequence"]) is not int or value["sequence"] < 1:
        raise RuntimeError("备份序列无效")
    if type(value["sourceDataSequence"]) is not int or value["sourceDataSequence"] < 0:
        raise RuntimeError("备份数据序列无效")
    if type(value["databaseBytes"]) is not int or value["databaseBytes"] < 1:
        raise RuntimeError("备份大小无效")
    if not isinstance(value["databaseSha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", value["databaseSha256"]
    ):
        raise RuntimeError("备份 SHA-256 无效")
    _parse_utc(value["createdAtUtc"])
    database_path = path.with_name(value["fileName"])
    expected_name = f"{BACKUP_PREFIX}{value['sequence']:08d}.db"
    if value["fileName"] != expected_name or database_path.parent != path.parent:
        raise RuntimeError("备份文件名与序列不一致")
    if verify_hash:
        if not database_path.is_file() or database_path.stat().st_size != value["databaseBytes"]:
            raise RuntimeError("备份文件缺失或大小不一致")
        if sha256_file(database_path) != value["databaseSha256"]:
            raise RuntimeError("备份 SHA-256 校验失败")
        metadata = _verify_sqlite(database_path)
        if (
            metadata["productGeneration"] != value["productGeneration"]
            or metadata["databaseSchemaVersion"] != value["databaseSchemaVersion"]
            or metadata["setupComplete"] != value["setupComplete"]
            or metadata["sourceDataSequence"] != value["sourceDataSequence"]
        ):
            raise RuntimeError("备份 sidecar 与数据库内部元数据不一致")
    return value


def backup_records(
    backup_dir: Path,
    *,
    expected_install_id: Optional[str] = None,
    verify_hash: bool = False,
) -> list[tuple[Path, dict[str, Any]]]:
    records = []
    for sidecar in backup_dir.glob(f"{BACKUP_PREFIX}*.json"):
        try:
            value = load_backup_sidecar(
                sidecar,
                expected_install_id=expected_install_id,
                verify_hash=verify_hash,
            )
        except RuntimeError:
            continue
        records.append((sidecar.with_name(value["fileName"]), value))
    records.sort(key=lambda item: item[1]["sequence"], reverse=True)
    return records


def latest_backup_record(
    backup_dir: Path,
    *,
    expected_install_id: Optional[str] = None,
    verify_hash: bool = False,
) -> Optional[tuple[Path, dict[str, Any]]]:
    records = backup_records(
        backup_dir,
        expected_install_id=expected_install_id,
    )
    if not verify_hash:
        return records[0] if records else None
    for database, value in records:
        try:
            verified = load_backup_sidecar(
                sidecar_path(database),
                expected_install_id=expected_install_id,
                verify_hash=True,
            )
        except RuntimeError:
            continue
        return database, verified
    return None


def latest_backup(backup_dir: Path) -> Optional[Path]:
    record = latest_backup_record(backup_dir, verify_hash=True)
    return record[0] if record else None


def create_backup(
    source: Path,
    backup_dir: Path,
    *,
    install_id: str,
    sequence: int,
    source_data_sequence: Optional[int] = None,
    keep_count: int = BACKUP_KEEP_COUNT,
    now: Optional[dt.datetime] = None,
) -> tuple[Path, dict[str, Any]]:
    if type(sequence) is not int or sequence < 1:
        raise RuntimeError("备份序列必须是正整数")
    if type(keep_count) is not int or not 1 <= keep_count <= 1000:
        raise RuntimeError("备份保留数量无效")
    if not source.is_file():
        raise RuntimeError("数据库文件不存在")
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{BACKUP_PREFIX}{sequence:08d}.db"
    sidecar = sidecar_path(target)
    if target.exists() or sidecar.exists():
        raise RuntimeError("备份序列目标已存在，拒绝覆盖")
    temporary = backup_dir / f".{target.name}.{secrets.token_hex(6)}.part"
    source_db = None
    target_db = None
    try:
        source_db = sqlite3.connect(source, timeout=10)
        source_db.row_factory = sqlite3.Row
        source_metadata = _database_metadata(source_db)
        if not source_metadata["setupComplete"]:
            raise RuntimeError("首次设置未完成，跳过备份")
        if (
            source_data_sequence is not None
            and source_metadata["sourceDataSequence"] != source_data_sequence
        ):
            raise RuntimeError("备份数据序列在启动前已变化")
        target_db = sqlite3.connect(temporary)
        source_db.backup(target_db)
        target_db.commit()
        target_db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        journal_mode = target_db.execute("PRAGMA journal_mode=DELETE").fetchone()
        if not journal_mode or str(journal_mode[0]).lower() != "delete":
            raise RuntimeError("备份数据库无法退出 WAL 模式")
        target_db.commit()
        target_db.close()
        target_db = None
        source_db.close()
        source_db = None
        _remove_sqlite_companions(temporary)
        metadata = _verify_sqlite(temporary)
        _remove_sqlite_companions(temporary)
        if source_data_sequence is None:
            source_data_sequence = metadata["sourceDataSequence"]
        elif metadata["sourceDataSequence"] != source_data_sequence:
            raise RuntimeError("备份数据序列与请求不一致")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        created_at = _utc_now(now)
        value = {
            "schema": BACKUP_SIDECAR_SCHEMA,
            "kind": BACKUP_KIND,
            "installId": install_id,
            "productGeneration": 2,
            "databaseSchemaVersion": 1,
            "setupComplete": True,
            "databaseSha256": sha256_file(target),
            "databaseBytes": target.stat().st_size,
            "sequence": sequence,
            "sourceDataSequence": source_data_sequence,
            "createdAtUtc": _utc_text(created_at),
            "fileName": target.name,
        }
        _atomic_json(sidecar, value)
        load_backup_sidecar(
            sidecar, expected_install_id=install_id, verify_hash=True
        )
        _remove_sqlite_companions(target)
    except Exception:
        if target_db is not None:
            with contextlib.suppress(Exception):
                target_db.close()
        if source_db is not None:
            with contextlib.suppress(Exception):
                source_db.close()
        for path in (temporary, *_sqlite_companion_paths(temporary), target, *_sqlite_companion_paths(target), sidecar):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
        raise

    records = backup_records(
        backup_dir,
        expected_install_id=install_id,
        verify_hash=True,
    )
    for database, _value in records:
        with contextlib.suppress(OSError):
            _remove_sqlite_companions(database)
        _remove_orphaned_temporary_companions(database)
    for old_database, old_value in records[keep_count:]:
        old_sidecar = sidecar_path(old_database)
        # Delete the data first; a crash leaves an invalid sidecar that is
        # ignored and never mistaken for a restorable backup.
        try:
            old_database.unlink()
        except OSError:
            # Keep the sidecar if the database could not be removed; this
            # avoids turning a still-valid backup into an unexplained orphan.
            continue
        with contextlib.suppress(OSError):
            old_sidecar.unlink()
        with contextlib.suppress(OSError):
            _remove_sqlite_companions(old_database)
        _remove_orphaned_temporary_companions(old_database)
    return target, value


def reserve_backup_sequence(
    db: sqlite3.Connection,
    backup_dir: Path,
    *,
    install_id: str,
) -> tuple[int, int]:
    """Reserve a sequence that stays monotonic even after restoring an older DB."""

    if not db.in_transaction:
        raise RuntimeError("备份序列必须在写事务中预留")
    row = db.execute(
        "SELECT value FROM app_meta WHERE key = 'backup_sequence'"
    ).fetchone()
    try:
        database_floor = int(row[0]) if row is not None else 0
    except (TypeError, ValueError) as error:
        raise RuntimeError("数据库备份序列无效") from error
    latest = latest_backup_record(backup_dir, expected_install_id=install_id)
    sidecar_floor = latest[1]["sequence"] if latest else 0
    sequence = max(database_floor, sidecar_floor) + 1
    db.execute(
        """
        INSERT INTO app_meta (key, value) VALUES ('backup_sequence', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(sequence),),
    )
    data_row = db.execute(
        "SELECT value FROM app_meta WHERE key = 'data_sequence'"
    ).fetchone()
    try:
        data_sequence = int(data_row[0]) if data_row is not None else 0
    except (TypeError, ValueError) as error:
        raise RuntimeError("数据库数据序列无效") from error
    if data_sequence < 0:
        raise RuntimeError("数据库数据序列无效")
    return sequence, data_sequence


def backup_is_caught_up(
    backup_dir: Path,
    *,
    install_id: str,
    current_data_sequence: int,
) -> bool:
    latest = latest_backup_record(
        backup_dir,
        expected_install_id=install_id,
        verify_hash=True,
    )
    return bool(
        latest
        and latest[1]["sourceDataSequence"] == current_data_sequence
    )


def scheduled_backup_due(
    backup_dir: Path,
    *,
    install_id: str,
    now: Optional[dt.datetime] = None,
    catch_up: bool,
) -> bool:
    current = now or dt.datetime.now().astimezone()
    if current.tzinfo is None or current.utcoffset() is None:
        raise RuntimeError("调度时钟必须带时区")
    latest = latest_backup_record(
        backup_dir,
        expected_install_id=install_id,
        verify_hash=True,
    )
    created_local = None
    if latest:
        created = _parse_utc(latest[1]["createdAtUtc"])
        created_local = created.astimezone(current.tzinfo)
    if not catch_up:
        # The scheduled task means "once per local calendar day", not "once
        # every exact 24 hours".  This avoids skipping today's 02:00 run when
        # yesterday's run completed a few seconds late.
        return created_local is None or created_local.date() < current.date()
    if latest and current.astimezone(dt.timezone.utc) - created < dt.timedelta(
        hours=24
    ):
        return False
    boundary = current.replace(hour=2, minute=0, second=0, microsecond=0)
    if current < boundary:
        boundary -= dt.timedelta(days=1)
    if latest:
        if created_local >= boundary:
            return False
    return True

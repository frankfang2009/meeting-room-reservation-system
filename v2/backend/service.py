from __future__ import annotations

import datetime as dt
import errno
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional, Union

from server import run_server_once


PRODUCT_GENERATION = 2
SERVICE_PORT = 8080
MAX_CONTROL_BYTES = 16 * 1024
APP_DIR = Path(__file__).resolve().parent
SERVICE_DIR = APP_DIR
PROGRAM_DIR = APP_DIR.parent
DATA_DIR = PROGRAM_DIR / "data"
INSTALL_INFO_PATH = DATA_DIR / "install.json"
INSTALL_ID_PATH = DATA_DIR / "install_id"
PID_PATH = DATA_DIR / "service.pid"
LOG_DIR = PROGRAM_DIR / "logs"
LOG_PATH = LOG_DIR / "service.log"

INSTALL_FIELDS = {
    "schema",
    "product_generation",
    "install_id",
    "installed_version",
    "installed_at_utc",
    "port",
    "setup_bind",
    "lan_bind",
    "setup_complete",
}
PID_FIELDS = {
    "schema",
    "pid",
    "token",
    "executable",
    "servicePath",
    "installId",
    "port",
    "startedAtUtc",
}


class ServiceAlreadyRunning(RuntimeError):
    pass


def service_failure_message(error: BaseException) -> str:
    logs_hint = (
        "_程序文件\\logs"
        if os.name == "nt"
        else "应用文件夹内的 logs（logs 目录）"
    )
    if isinstance(error, OSError) and (
        error.errno == errno.EADDRINUSE
        or getattr(error, "winerror", None) == 10048
        or "address already in use" in str(error).casefold()
    ):
        return (
            "V2 服务无法启动：8080 端口已被其他程序占用。"
            "请关闭占用程序后重新运行“① 启动系统”；"
            f"仍失败请提交 {logs_hint}。"
        )
    return (
        "V2 服务启动或操作未完成。请重新运行“① 启动系统”；"
        f"仍失败请提交 {logs_hint}，日志中已记录详细原因。"
    )


def configure_logging(path: Path = LOG_PATH) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = str(path.resolve())
    root = logging.getLogger()
    for handler in root.handlers:
        if (
            getattr(handler, "_meeting_room_v2_service", False)
            and getattr(handler, "baseFilename", None) == resolved
        ):
            return logging.getLogger("meeting_room_v2.service")
    handler = RotatingFileHandler(
        path,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler._meeting_room_v2_service = True  # type: ignore[attr-defined]
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    return logging.getLogger("meeting_room_v2.service")


def _canonical_uuid4(value: Any) -> str:
    if not isinstance(value, str):
        raise RuntimeError("install_id 类型无效")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise RuntimeError("install_id 格式无效") from error
    if (
        str(parsed) != value
        or parsed.version != 4
        or parsed.variant != uuid.RFC_4122
    ):
        raise RuntimeError("install_id 必须是标准 UUIDv4")
    return value


def _read_limited(path: Path, *, label: str, maximum: int = MAX_CONTROL_BYTES) -> bytes:
    try:
        if path.stat().st_size > maximum:
            raise RuntimeError(f"{label}体积异常")
        return path.read_bytes()
    except FileNotFoundError as error:
        raise RuntimeError(f"缺少{label}") from error
    except OSError as error:
        raise RuntimeError(f"无法读取{label}") from error


def load_install_identity() -> dict[str, Any]:
    raw = _read_limited(INSTALL_INFO_PATH, label="install.json")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise RuntimeError("install.json 无法解析") from error
    if not isinstance(value, dict) or set(value) != INSTALL_FIELDS:
        raise RuntimeError("install.json 字段不符合 V2 约定")
    if value.get("schema") != 1 or value.get("product_generation") != 2:
        raise RuntimeError("安装身份不属于 V2")
    install_id = _canonical_uuid4(value.get("install_id"))
    if value.get("port") != SERVICE_PORT:
        raise RuntimeError("安装端口不是 8080")
    if value.get("setup_bind") != "127.0.0.1" or value.get("lan_bind") != "0.0.0.0":
        raise RuntimeError("安装绑定模式无效")
    if not isinstance(value.get("setup_complete"), bool):
        raise RuntimeError("setup_complete 类型无效")
    if not isinstance(value.get("installed_version"), str) or not value["installed_version"]:
        raise RuntimeError("安装版本无效")
    if not isinstance(value.get("installed_at_utc"), str) or not value["installed_at_utc"]:
        raise RuntimeError("安装时间无效")
    try:
        file_install_id = _read_limited(
            INSTALL_ID_PATH, label="install_id", maximum=128
        ).decode("ascii").strip()
    except UnicodeError as error:
        raise RuntimeError("install_id 文件编码无效") from error
    if file_install_id != install_id:
        raise RuntimeError("install.json 与 install_id 文件不一致")
    return value


EDITION_MARKER_PATH = APP_DIR / "EDITION"
MACOS_EDITION_VALUE = "macos-selfhost"


def _edition_is_macos() -> bool:
    if os.name == "nt":
        return False
    try:
        return (
            EDITION_MARKER_PATH.read_text(encoding="utf-8").strip()
            == MACOS_EDITION_VALUE
        )
    except OSError:
        return False


def ensure_macos_install_identity() -> None:
    """macOS 自托管版首次启动生成安装身份；其余环境不做任何事。

    写入顺序是 install_id 文件在前、install.json 在后，保证并发的应用
    启动要么看到完整身份，要么像开发态一样看不到 install.json。
    """

    if not _edition_is_macos():
        return
    if INSTALL_INFO_PATH.exists() and INSTALL_ID_PATH.exists():
        return
    if INSTALL_INFO_PATH.exists() or INSTALL_ID_PATH.exists():
        raise RuntimeError("macOS 安装身份文件不完整，请恢复完整应用文件夹后重试")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    install_id = str(uuid.uuid4())
    now_utc = (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    descriptor = os.open(
        INSTALL_ID_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write((install_id + "\n").encode("ascii"))
        handle.flush()
        os.fsync(handle.fileno())
    from v2app import PRODUCT_VERSION

    record = {
        "schema": 1,
        "product_generation": PRODUCT_GENERATION,
        "install_id": install_id,
        "installed_version": PRODUCT_VERSION.removeprefix("V"),
        "installed_at_utc": now_utc,
        "port": SERVICE_PORT,
        "setup_bind": "127.0.0.1",
        "lan_bind": "0.0.0.0",
        "setup_complete": False,
    }
    encoded = (
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor = os.open(
        INSTALL_INFO_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _normalized_path(value: Union[str, Path]) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(value)))


def _allowed_service_executables() -> set[str]:
    runtime = PROGRAM_DIR / "runtime"
    if runtime.is_dir():
        if os.name == "nt":
            return {
                _normalized_path(runtime / "python.exe"),
                _normalized_path(runtime / "pythonw.exe"),
            }
        # macOS 自托管便携包：runtime/bin 下的解释器（物化副本，无符号链接）。
        return {
            _normalized_path(candidate)
            for candidate in (
                runtime / "bin" / "python3.13",
                runtime / "bin" / "python3",
                runtime / "bin" / "python",
            )
        }
    # Development-only fallback. Production packages always contain runtime.
    return {_normalized_path(Path(sys.executable).resolve())}


def _assert_current_executable_allowed() -> str:
    executable = _normalized_path(Path(sys.executable).resolve())
    if executable not in _allowed_service_executables():
        raise RuntimeError("服务必须由本安装的 runtime 启动")
    return executable


def _windows_pid_exists(pid: int) -> bool:
    # On Windows os.kill(pid, 0) is not a POSIX-style existence probe. CPython
    # delegates non-console signals to TerminateProcess, so it must never be
    # used here. Query-only process access cannot terminate the target.
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    still_active = 259
    error_invalid_parameter = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, wintypes.LPDWORD]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(
        process_query_limited_information | synchronize, False, pid
    )
    if not handle:
        # Access denied means a process exists but the caller cannot inspect it;
        # only INVALID_PARAMETER is treated as a confirmed missing PID.
        return ctypes.get_last_error() != error_invalid_parameter
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_exists(pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError as error:
        if error.errno == errno.ESRCH:
            return False
        return True
    return True


def _validate_pid_record(
    value: Any, *, identity: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != PID_FIELDS:
        raise RuntimeError("service.pid 字段无效")
    if value.get("schema") != 1:
        raise RuntimeError("service.pid 版本无效")
    if not isinstance(value.get("pid"), int) or isinstance(value.get("pid"), bool):
        raise RuntimeError("service.pid 进程号无效")
    if value["pid"] <= 0:
        raise RuntimeError("service.pid 进程号无效")
    if not isinstance(value.get("token"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", value["token"]
    ):
        raise RuntimeError("service.pid 随机身份无效")
    if value.get("installId") != identity["install_id"]:
        raise RuntimeError("service.pid 安装身份不匹配")
    if value.get("port") != SERVICE_PORT:
        raise RuntimeError("service.pid 端口无效")
    if _normalized_path(str(value.get("servicePath") or "")) != _normalized_path(
        SERVICE_DIR / "service.py"
    ):
        raise RuntimeError("service.pid 入口路径不匹配")
    if _normalized_path(str(value.get("executable") or "")) not in _allowed_service_executables():
        raise RuntimeError("service.pid runtime 路径不匹配")
    if not isinstance(value.get("startedAtUtc"), str) or not value["startedAtUtc"]:
        raise RuntimeError("service.pid 启动时间无效")
    return value


def _read_pid_record(identity: dict[str, Any]) -> dict[str, Any]:
    raw = _read_limited(PID_PATH, label="service.pid")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise RuntimeError("service.pid 无法解析") from error
    return _validate_pid_record(value, identity=identity)


def _write_pid_exclusive(value: dict[str, Any]) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    descriptor = os.open(PID_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _remove_pid_if_token(token: str) -> bool:
    try:
        raw = PID_PATH.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, ValueError):
        return False
    if not isinstance(value, dict) or value.get("token") != token:
        return False
    try:
        # Re-read immediately before unlinking so a replacement owner is never
        # removed by an exiting or stale process.
        if PID_PATH.read_bytes() != raw:
            return False
        PID_PATH.unlink()
        return True
    except (FileNotFoundError, OSError):
        return False


def _health_payload(identity: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(
            f"http://127.0.0.1:{SERVICE_PORT}/healthz", timeout=timeout
        ) as response:
            raw = response.read(MAX_CONTROL_BYTES + 1)
    except (OSError, urllib.error.URLError) as error:
        raise RuntimeError("回环健康检查未通过") from error
    if len(raw) > MAX_CONTROL_BYTES:
        raise RuntimeError("健康响应体积异常")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise RuntimeError("健康响应无法解析") from error
    if not isinstance(payload, dict):
        raise RuntimeError("健康响应格式无效")
    setup_complete = payload.get("setup_complete")
    expected = {
        "ok": True,
        "product_generation": PRODUCT_GENERATION,
        "install_id": identity["install_id"],
        "port": SERVICE_PORT,
    }
    if any(payload.get(key) != expected_value for key, expected_value in expected.items()):
        raise RuntimeError("回环健康身份不匹配")
    if not isinstance(setup_complete, bool):
        raise RuntimeError("健康响应 setup_complete 无效")
    expected_bind = "lan" if setup_complete else "loopback"
    if payload.get("bind_mode") != expected_bind:
        raise RuntimeError("健康响应绑定模式不一致")
    return payload


def check_service(identity: dict[str, Any], *, timeout: float = 3.0) -> None:
    _health_payload(identity, timeout=timeout)


def _claim_pid(identity: dict[str, Any]) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": 1,
        "pid": os.getpid(),
        "token": secrets.token_hex(32),
        "executable": _assert_current_executable_allowed(),
        "servicePath": _normalized_path(SERVICE_DIR / "service.py"),
        "installId": identity["install_id"],
        "port": SERVICE_PORT,
        "startedAtUtc": dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }
    for _attempt in range(2):
        try:
            _write_pid_exclusive(record)
            return record
        except FileExistsError:
            existing = _read_pid_record(identity)
            if _pid_exists(existing["pid"]):
                try:
                    check_service(identity, timeout=1.0)
                except RuntimeError as error:
                    raise RuntimeError(
                        "service.pid 指向活跃进程，但服务身份无法确认"
                    ) from error
                raise ServiceAlreadyRunning("服务已在运行")
            if not _remove_pid_if_token(existing["token"]):
                raise RuntimeError("无法安全清理过期 service.pid")
    raise RuntimeError("无法取得服务进程所有权")


def stop_service(identity: dict[str, Any], *, timeout: float = 15.0) -> None:
    logger = logging.getLogger("meeting_room_v2.service")
    try:
        record = _read_pid_record(identity)
    except RuntimeError:
        if not PID_PATH.exists():
            return
        raise
    if not _pid_exists(record["pid"]):
        if not _remove_pid_if_token(record["token"]):
            raise RuntimeError("无法安全清理已停止服务的 PID 文件")
        return

    logger.info("authenticated stop requested pid=%s", record["pid"])

    payload = {
        key: record[key]
        for key in ("pid", "executable", "servicePath", "installId")
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{SERVICE_PORT}/_service/stop",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Meeting-Room-Service-Token": record["token"],
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=3.0) as response:
            raw = response.read(MAX_CONTROL_BYTES + 1)
    except (OSError, urllib.error.URLError) as error:
        raise RuntimeError("服务拒绝或无法确认安全停止指令") from error
    if len(raw) > MAX_CONTROL_BYTES:
        raise RuntimeError("停止响应体积异常")
    try:
        response_payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise RuntimeError("停止响应无法解析") from error
    if response_payload != {"stopping": True, "pid": record["pid"]}:
        raise RuntimeError("停止响应身份不一致")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not PID_PATH.exists() and not _pid_exists(record["pid"]):
            logger.info("service stop confirmed pid=%s", record["pid"])
            return
        time.sleep(0.1)
    raise RuntimeError("服务未在限定时间内停止")


def _open_browser_once() -> None:
    time.sleep(0.75)
    try:
        webbrowser.open(f"http://127.0.0.1:{SERVICE_PORT}/")
    except Exception:
        # Browser opening is optional and must never take down the service.
        return


def _launch_backup_catch_up(identity: dict[str, Any]) -> None:
    """Start the idempotent catch-up worker without blocking the listener."""

    logger = logging.getLogger("meeting_room_v2.service")
    command = [
        sys.executable,
        str(APP_DIR / "backup.py"),
        "--catch-up",
        "--expected-install-id",
        identity["install_id"],
    ]
    options: dict[str, Any] = {
        "cwd": str(APP_DIR),
        # 补跑 worker 无控制台，真实 Windows 默认本地代码页会让中文报告
        # 在成功路径上失败；强制 UTF-8 与备份入口的流重配置互为兜底。
        "env": {**os.environ, "PYTHONUTF8": "1"},
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(command, **options)
        logger.info("backup catch-up worker launched")
    except Exception:
        # Backup failures must never take the listener down. The service log
        # records launch failures; a launched worker has its own backup.log.
        logger.exception("failed to launch backup catch-up worker")


def _launch_update_check_worker() -> None:
    """macOS 版的受控版本检查：单次、限频、失败只记日志。"""

    logger = logging.getLogger("meeting_room_v2.service")
    try:
        from v2app import PRODUCT_VERSION
        from v2app.services import update_check

        update_check.maybe_periodic_check(
            data_dir=DATA_DIR,
            current_version=PRODUCT_VERSION,
        )
    except Exception:
        logger.exception("update check worker failed")


def run_service(identity: dict[str, Any]) -> None:
    logger = logging.getLogger("meeting_room_v2.service")
    record = _claim_pid(identity)
    logger.info("service process starting pid=%s port=%s", record["pid"], SERVICE_PORT)
    stop_event = threading.Event()
    if os.environ.get("MEETING_ROOM_OPEN_BROWSER") == "1":
        threading.Thread(target=_open_browser_once, daemon=True).start()
    _launch_backup_catch_up(identity)
    if _edition_is_macos():
        threading.Thread(target=_launch_update_check_worker, daemon=True).start()
    config = {
        "DATA_DIR": str(DATA_DIR),
        "SERVICE_PORT": SERVICE_PORT,
        "STATIC_DIR": str(APP_DIR / "static"),
        "SERVICE_CONTROL": record,
        "SERVICE_STOP_EVENT": stop_event,
        "UPDATE_CHECK_ENABLED": _edition_is_macos(),
    }
    try:
        while run_server_once(
            SERVICE_PORT,
            app_config=config,
            service_stop_event=stop_event,
        ):
            # The first worker intentionally skips an incomplete setup. Once
            # setup commits and the listener is recreated in LAN mode, launch
            # catch-up again so the first durable backup is not delayed a day.
            _launch_backup_catch_up(identity)
    finally:
        _remove_pid_if_token(record["token"])
        logger.info("service process stopped pid=%s", record["pid"])


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments not in ([], ["--check"], ["--stop"]):
        print("Usage: service.py [--check|--stop]", file=sys.stderr)
        return 2
    try:
        logger = configure_logging()
    except Exception as error:
        print(f"V2 日志初始化失败：{error}", file=sys.stderr)
        return 1
    action = "start" if not arguments else arguments[0].lstrip("-")
    logger.info("service command requested action=%s", action)
    try:
        if os.name != "nt":
            ensure_macos_install_identity()
        identity = load_install_identity()
        if arguments == ["--check"]:
            check_service(identity)
            print("V2 服务健康且安装身份一致")
            return 0
        if arguments == ["--stop"]:
            stop_service(identity)
            print("V2 服务已停止")
            return 0
        run_service(identity)
        return 0
    except ServiceAlreadyRunning:
        logger.info("service already running")
        print("V2 服务已在运行")
        return 0
    except KeyboardInterrupt:
        logger.info("service interrupted from console")
        return 0
    except Exception as error:
        logger.exception("service command failed action=%s", action)
        print(service_failure_message(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

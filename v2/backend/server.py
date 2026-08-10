from __future__ import annotations

import os
import logging
import sys
import threading
import time
from typing import Any, Optional

from waitress import create_server

from v2app import create_app
from v2app.db import is_setup_complete


def determine_bind_host(app) -> str:
    if not app.config.get("SYSTEM_READY", False):
        return "127.0.0.1"
    return "0.0.0.0" if app.config.get("DATABASE_SETUP_COMPLETE") else "127.0.0.1"


def _port() -> int:
    value = os.environ.get("MEETING_ROOM_V2_PORT", "8080")
    try:
        port = int(value)
    except ValueError as error:
        raise RuntimeError("MEETING_ROOM_V2_PORT 必须是整数") from error
    if not 1 <= port <= 65535:
        raise RuntimeError("端口必须在 1–65535 之间")
    return port


def run_server_once(
    port: int,
    *,
    app_config: Optional[dict[str, Any]] = None,
    service_stop_event: Optional[threading.Event] = None,
) -> bool:
    setup_completed = threading.Event()
    config = dict(app_config or {})
    config["SETUP_COMPLETED_EVENT"] = setup_completed
    if service_stop_event is not None:
        config["SERVICE_STOP_EVENT"] = service_stop_event
    app = create_app(config)
    host = determine_bind_host(app)
    app.config["ACTIVE_BIND_MODE"] = "lan" if host == "0.0.0.0" else "loopback"
    logger = logging.getLogger("meeting_room_v2.service")
    logger.info("listener starting host=%s port=%s", host, port)
    server = create_server(app, host=host, port=port, threads=8)
    close_requested = threading.Event()
    listener_closed = threading.Event()
    close_lock = threading.Lock()
    server_stopped = threading.Event()

    def close_listener_in_loop(reason: str) -> None:
        with close_lock:
            if listener_closed.is_set():
                return
            listener_closed.set()
        logger.info("listener close requested reason=%s", reason)
        # This callback runs in Waitress's asyncore thread. Mark existing
        # keep-alive channels to close only after their buffered response is
        # flushed, then remove the accepting socket and wakeup trigger from
        # the same thread that owns select(). Closing them from the monitor
        # thread makes select() observe a stale fd and raise EBADF.
        for channel in tuple(getattr(server, "active_channels", {}).values()):
            channel.close_when_flushed = True
        server.close()

    def request_listener_close(reason: str) -> None:
        with close_lock:
            if close_requested.is_set():
                return
            close_requested.set()
        trigger = getattr(getattr(server, "trigger", None), "pull_trigger", None)
        if callable(trigger):
            trigger(lambda: close_listener_in_loop(reason))
        else:
            # Test doubles and non-Waitress adapters do not expose a trigger.
            close_listener_in_loop(reason)

    def close_after_runtime_signal() -> None:
        while not server_stopped.is_set():
            if service_stop_event is not None and service_stop_event.wait(0.1):
                # Let the authenticated stop response flush before closing.
                logger.info("listener received authenticated stop signal")
                time.sleep(0.25)
                request_listener_close("service-stop")
                return
            if setup_completed.wait(0.1):
                # Leave enough time for Waitress to flush the setup response
                # before recreating the listener on 0.0.0.0.
                logger.info("setup committed; listener restarting in LAN mode")
                time.sleep(0.5)
                request_listener_close("setup-complete")
                return

    should_monitor = host == "127.0.0.1" or service_stop_event is not None
    monitor: Optional[threading.Thread] = None
    if should_monitor:
        monitor = threading.Thread(target=close_after_runtime_signal, daemon=True)
        monitor.start()
    if host == "127.0.0.1":
        print(f"V2 首次设置服务已启动：http://127.0.0.1:{port}")
    else:
        print(f"V2 局域网服务已启动：端口 {port}")
    try:
        server.run()
    finally:
        server_stopped.set()
        if not listener_closed.is_set():
            # run() has returned, so no select() call can race this fallback.
            close_listener_in_loop("server-run-returned")
        dispatcher = getattr(server, "task_dispatcher", None)
        if dispatcher is not None:
            dispatcher.shutdown()
        if monitor is not None and monitor.is_alive():
            monitor.join(timeout=1.0)
        logger.info("listener closed host=%s port=%s", host, port)
    return setup_completed.is_set() and not (
        service_stop_event is not None and service_stop_event.is_set()
    )


def main() -> int:
    try:
        port = _port()
        while run_server_once(port):
            # Setup was committed. Recreate the app/server so the bind decision
            # is based on durable database state, never on an in-memory flag.
            continue
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(f"V2 启动失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

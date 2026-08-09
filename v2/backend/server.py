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
    with app.app_context():
        return "0.0.0.0" if is_setup_complete() else "127.0.0.1"


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
    logger = logging.getLogger("meeting_room_v2.service")
    logger.info("listener starting host=%s port=%s", host, port)
    server = create_server(app, host=host, port=port, threads=8)

    def close_after_runtime_signal() -> None:
        while True:
            if service_stop_event is not None and service_stop_event.wait(0.1):
                # Let the authenticated stop response flush before closing.
                logger.info("listener received authenticated stop signal")
                time.sleep(0.25)
                server.close()
                return
            if setup_completed.wait(0.1):
                # Leave enough time for Waitress to flush the setup response
                # before recreating the listener on 0.0.0.0.
                logger.info("setup committed; listener restarting in LAN mode")
                time.sleep(0.5)
                server.close()
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
        server.close()
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

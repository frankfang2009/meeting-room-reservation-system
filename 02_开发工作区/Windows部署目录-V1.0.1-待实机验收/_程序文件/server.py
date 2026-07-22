from __future__ import annotations

import logging
import ipaddress
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
LOG_DIR = PROJECT_DIR / "logs"


def _configure_output() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_DIR / "server.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)


def _local_ip() -> str:
    def usable(value: str) -> bool:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        return bool(
            address.version == 4
            and address.is_private
            and not address.is_loopback
            and not address.is_link_local
            and address not in ipaddress.ip_network("198.18.0.0/15")
        )

    route_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        route_socket.connect(("10.255.255.255", 1))
        route_ip = route_socket.getsockname()[0]
        if usable(route_ip):
            return route_ip
    except OSError:
        pass
    finally:
        route_socket.close()

    candidates: set[str] = set()
    commands = []
    if sys.platform == "win32":
        commands.append(["ipconfig"])
    elif sys.platform == "darwin":
        commands.append(["ifconfig"])
    else:
        commands.append(["hostname", "-I"])

    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=3,
            )
            output = result.stdout.decode("utf-8", errors="ignore")
            if sys.platform == "darwin":
                values = re.findall(r"\binet\s+((?:\d{1,3}\.){3}\d{1,3})", output)
            elif sys.platform == "win32":
                values = []
                for line in output.splitlines():
                    if "IPv4" in line:
                        values.extend(
                            re.findall(r"(?:\d{1,3}\.){3}\d{1,3}", line)
                        )
            else:
                values = re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", output)

            for value in values:
                if usable(value):
                    candidates.add(value)
        except (OSError, subprocess.SubprocessError):
            pass

    if candidates:
        def rank(value: str) -> tuple[int, int]:
            if value.startswith("192.168."):
                group = 0
            elif value.startswith("10."):
                group = 1
            else:
                group = 2
            return (group, int(ipaddress.ip_address(value)))

        return sorted(candidates, key=rank)[0]

    try:
        hostname_ip = socket.gethostbyname(socket.gethostname())
        return hostname_ip if usable(hostname_ip) else "本机IP"
    except OSError:
        return "本机IP"


def _app_is_running(port: int) -> bool:
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/login",
            headers={"User-Agent": "MeetingRoomLauncher/1.0"},
        )
        with urllib.request.urlopen(request, timeout=1.5) as response:
            return response.headers.get("X-Meeting-Room-System") == "1"
    except (OSError, urllib.error.URLError):
        return False


def _open_browser(url: str, delay: float = 1.2) -> None:
    if os.environ.get("MEETING_ROOM_OPEN_BROWSER") != "1":
        return
    if delay <= 0:
        webbrowser.open(url)
        return
    timer = threading.Timer(delay, lambda: webbrowser.open(url))
    timer.daemon = True
    timer.start()


def main() -> int:
    _configure_output()
    _configure_logging()
    port_text = os.environ.get("MEETING_ROOM_PORT", "8080")
    try:
        port = int(port_text)
    except ValueError:
        port = 8080

    if sys.argv[1:] == ["--check"]:
        return 0 if _app_is_running(port) else 1

    local_url = f"http://127.0.0.1:{port}"
    if _app_is_running(port):
        print()
        print("会议室预约系统本来就在运行，正在为你打开浏览器。")
        _open_browser(local_url, delay=0)
        return 10

    try:
        from waitress import create_server
        from app import app, init_db

        server = create_server(app, host="0.0.0.0", port=port, threads=8)
    except Exception as error:
        logging.exception("服务绑定失败")
        print(f"启动失败：端口 {port} 可能已被其他程序占用。")
        print("如果另一个会议室预约系统正在运行，请直接打开浏览器。")
        print(f"技术信息：{error}")
        return 1

    try:
        with app.app_context():
            init_db()
    except Exception as error:
        server.close()
        logging.exception("系统数据初始化失败")
        print("启动失败：系统无法创建或读取数据文件。")
        print("请把整个文件夹放到本地固定盘，并确认文件夹可以写入。")
        print(f"技术信息：{error}")
        return 1

    lan_url = f"http://{_local_ip()}:{port}"
    logging.info("会议室预约系统启动，本机地址=%s，局域网地址=%s", local_url, lan_url)
    print()
    print("=" * 58)
    print("  会议室预约系统已经启动")
    print()
    print(f"  本机打开：{local_url}")
    print(f"  同事访问：{lan_url}")
    print()
    credential_file = Path(app.config["INITIAL_CREDENTIAL_FILE"])
    if credential_file.exists():
        print("  首次登录用户名：admin")
        print("  首次登录密码：请打开 data 文件夹里的“首次登录账号密码.txt”")
        print("  登录后，请在用户管理中立即修改管理员密码。")
    else:
        print("  管理员密码已经设置，可直接使用现有账号登录。")
    print("  关闭这个窗口即可停止系统。")
    print("=" * 58)
    print()
    _open_browser(local_url)

    try:
        server.run()
    except KeyboardInterrupt:
        print("系统已停止。")
    except Exception:
        logging.exception("服务运行失败")
        print("系统运行时发生错误，请把 logs 文件夹交给维护人员。")
        return 1
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

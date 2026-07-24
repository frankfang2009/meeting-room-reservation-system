from __future__ import annotations

import json
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
import uuid
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parent
LOG_DIR = PROJECT_DIR / "logs"
INSTALL_ID_FILENAME = "install_id"
HEALTH_RESPONSE_MAX_BYTES = 8 * 1024
PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
VIRTUAL_ADAPTER_KEYWORDS = (
    "vpn",
    "virtual",
    "vmware",
    "virtualbox",
    "vbox",
    "hyper-v",
    "vethernet",
    "wsl",
    "docker",
    "tailscale",
    "zerotier",
    "wireguard",
    "wintun",
    "openvpn",
    "fortinet",
    "anyconnect",
    "globalprotect",
    "nordlynx",
    "hamachi",
    "pulse secure",
    "protonvpn",
    "clash",
    "surge",
    "ppp",
    "ras async",
    "tap",
    "tun",
    "loopback",
    "bluetooth",
    "utun",
    "bridge",
    "awdl",
    "llw",
    "vmenet",
    "vmnet",
    "本地连接*",
    "未知适配器",
    "虚拟",
    "隧道",
    "unknown adapter",
)


def _canonical_uuid4(value: object) -> Optional[str]:
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError):
        return None
    canonical = str(parsed)
    if (
        value != canonical
        or parsed.version != 4
        or parsed.variant != uuid.RFC_4122
    ):
        return None
    return canonical


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


def _usable_private_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        address.version == 4
        and any(address in network for network in PRIVATE_NETWORKS)
        and not address.is_loopback
        and not address.is_link_local
    )


def _adapter_is_virtual(name: str) -> bool:
    lowered = name.lower()
    return any(keyword in lowered for keyword in VIRTUAL_ADAPTER_KEYWORDS)


def _extract_ip_candidates(
    platform: str,
    output: str,
) -> list[tuple[str, int]]:
    candidates: list[tuple[str, int]] = []
    if platform == "win32":
        adapter_name = ""
        adapter_virtual = False
        for raw_line in output.splitlines():
            stripped = raw_line.strip()
            if (
                stripped.endswith(":")
                and raw_line
                and not raw_line[0].isspace()
            ):
                adapter_name = stripped[:-1]
                adapter_virtual = _adapter_is_virtual(adapter_name)
                continue
            if _adapter_is_virtual(stripped):
                adapter_virtual = True
            if "IPv4" not in stripped:
                continue
            if adapter_virtual:
                continue
            for value in re.findall(r"(?:\d{1,3}\.){3}\d{1,3}", stripped):
                if _usable_private_ip(value):
                    candidates.append((value, 2))
    elif platform == "darwin":
        adapter_name = ""
        for raw_line in output.splitlines():
            heading = re.match(r"^([A-Za-z0-9_.:-]+):\s+flags=", raw_line)
            if heading:
                adapter_name = heading.group(1)
                continue
            match = re.search(
                r"\binet\s+((?:\d{1,3}\.){3}\d{1,3})",
                raw_line,
            )
            if not match or _adapter_is_virtual(adapter_name):
                continue
            value = match.group(1)
            if _usable_private_ip(value):
                confidence = 2 if re.fullmatch(r"en\d+", adapter_name) else 1
                candidates.append((value, confidence))
    else:
        for value in re.findall(
            r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])",
            output,
        ):
            if _usable_private_ip(value):
                candidates.append((value, 1))
    return candidates


def _select_local_ip(
    route_ip: str,
    candidates: list[tuple[str, int]],
    hostname_ip: str,
) -> str:
    by_address: dict[str, int] = {}
    for value, confidence in candidates:
        by_address[value] = max(confidence, by_address.get(value, 0))

    if not by_address and _usable_private_ip(hostname_ip):
        by_address[hostname_ip] = 1
    if not by_address:
        logging.warning("未从物理网络适配器获得可靠的局域网地址")
        return "本机IP"

    if route_ip in by_address:
        return route_ip

    best_confidence = max(by_address.values())
    finalists = [
        value for value in by_address if by_address[value] == best_confidence
    ]
    if len(finalists) == 1:
        return finalists[0]
    logging.warning(
        "发现多个同等可信的局域网地址 %s，不自动选择",
        "、".join(sorted(finalists)),
    )
    return "本机IP"


def _local_ip() -> str:
    route_ip = ""
    route_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        route_socket.connect(("10.255.255.255", 1))
        route_ip = route_socket.getsockname()[0]
    except OSError:
        pass
    finally:
        route_socket.close()

    command: list[str]
    if sys.platform == "win32":
        command = ["ipconfig"]
    elif sys.platform == "darwin":
        command = ["ifconfig"]
    else:
        command = ["hostname", "-I"]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=3,
        )
        output = result.stdout.decode("utf-8", errors="ignore")
    except (OSError, subprocess.SubprocessError):
        output = ""

    try:
        hostname_ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        hostname_ip = ""
    return _select_local_ip(
        route_ip,
        _extract_ip_candidates(sys.platform, output),
        hostname_ip,
    )


def _read_local_install_id() -> tuple[Optional[str], str]:
    data_dir = Path(
        os.environ.get("MEETING_ROOM_DATA_DIR", PROJECT_DIR / "data")
    )
    path = data_dir / INSTALL_ID_FILENAME
    try:
        if path.stat().st_size > 128:
            return None, "invalid"
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "invalid"
    canonical = _canonical_uuid4(value)
    if canonical is None:
        return None, "invalid"
    return canonical, "ok"


def _port_accepts_connection(port: int) -> bool:
    try:
        connection = socket.create_connection(("127.0.0.1", port), timeout=0.4)
    except OSError:
        return False
    connection.close()
    return True


def _probe_app(port: int) -> dict[str, Optional[str]]:
    local_install_id, local_identity_state = _read_local_install_id()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/healthz",
            headers={"User-Agent": "MeetingRoomLauncher/1.0"},
        )
        with urllib.request.urlopen(request, timeout=1.5) as response:
            system_header = response.headers.get("X-Meeting-Room-System")
            body = response.read(HEALTH_RESPONSE_MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        error_headers = getattr(error, "headers", None) or {}
        kind = (
            "meeting-room-unverified"
            if error_headers.get("X-Meeting-Room-System") == "1"
            else "occupied"
        )
        return {
            "kind": kind,
            "local_identity_state": local_identity_state,
            "local_install_id": local_install_id,
            "remote_install_id": None,
            "remote_mode": None,
        }
    except (OSError, urllib.error.URLError):
        kind = "occupied" if _port_accepts_connection(port) else "none"
        return {
            "kind": kind,
            "local_identity_state": local_identity_state,
            "local_install_id": local_install_id,
            "remote_install_id": None,
            "remote_mode": None,
        }

    if system_header != "1":
        kind = "occupied"
        payload = None
    elif len(body) > HEALTH_RESPONSE_MAX_BYTES:
        kind = "meeting-room-unverified"
        payload = None
    else:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            payload = None
        kind = "meeting-room-unverified"

    remote_install_id = None
    remote_mode = None
    if isinstance(payload, dict) and payload.get("ok") is True:
        remote_value = payload.get("install_id")
        remote_mode_value = payload.get("mode")
        remote_canonical = _canonical_uuid4(remote_value)
        if (
            remote_canonical is not None
            and remote_mode_value in ("normal", "upgrade-check")
        ):
            remote_install_id = remote_canonical
            remote_mode = remote_mode_value
            if remote_mode != "normal":
                kind = "upgrade-check"
            elif local_identity_state != "ok":
                kind = "local-identity-problem"
            elif remote_install_id != local_install_id:
                kind = "other-installation"
            else:
                kind = "same-installation"

    return {
        "kind": kind,
        "local_identity_state": local_identity_state,
        "local_install_id": local_install_id,
        "remote_install_id": remote_install_id,
        "remote_mode": remote_mode,
    }


def _app_is_running(port: int) -> bool:
    return _probe_app(port)["kind"] == "same-installation"


def _print_port_conflict(port: int, probe: dict[str, Optional[str]]) -> None:
    kind = probe["kind"]
    print()
    if kind == "other-installation":
        print(f"启动已停止：端口 {port} 正由另一套会议室预约系统使用。")
        print("为了避免打开错误的预约数据，本次不会连接或打开它。")
        print("请先关闭另一套系统，或联系维护人员检查安装位置。")
    elif kind == "local-identity-problem":
        print("启动已停止：本地安装标识缺失或损坏，无法确认正在运行的系统。")
        print("为了避免打开错误的预约数据，本次不会连接或打开它。")
        print("请联系维护人员检查 data 文件夹里的 install_id。")
    elif kind == "upgrade-check":
        print(f"启动已停止：端口 {port} 正在进行升级检查。")
        print("请等待升级窗口完成后，再启动会议室预约系统。")
    elif kind == "meeting-room-unverified":
        print(f"启动已停止：端口 {port} 上有一套无法核实身份的会议室预约系统。")
        print("为了避免打开错误的预约数据，本次不会连接或打开它。")
    else:
        print(f"启动已停止：端口 {port} 已被其他程序占用。")
        print("请关闭占用该端口的程序，或联系维护人员处理。")


def _open_browser(url: str, delay: float = 1.2) -> None:
    if os.environ.get("MEETING_ROOM_OPEN_BROWSER") != "1":
        return
    if delay <= 0:
        webbrowser.open(url)
        return
    timer = threading.Timer(delay, lambda: webbrowser.open(url))
    timer.daemon = True
    timer.start()


def _bind_host() -> str:
    return (
        "127.0.0.1"
        if os.environ.get("MEETING_ROOM_UPGRADE_CHECK") == "1"
        else "0.0.0.0"
    )


def _print_pending_network_change() -> None:
    try:
        from app import app, pending_network_change

        pending = pending_network_change(
            Path(app.config["NETWORK_STATE_FILE"])
        )
    except Exception:
        logging.exception("读取待确认的局域网地址提醒失败")
        return
    if not pending:
        return
    print()
    if pending["kind"] == "changed":
        print("注意：同事访问网址已经变化，请把新网址发给同事。")
        print(f"原网址：{pending['old_url']}")
        print(f"新网址：{pending['new_url']}")
    else:
        print("注意：地址记录需要重新核对。")
        print("系统无法可靠确认原网址，请把当前网址重新发给同事。")
        print(f"当前网址：{pending['new_url']}")
    print("预约数据没有变化；管理员登录后也会持续看到这条提醒。")


def main() -> int:
    _configure_output()
    port_text = os.environ.get("MEETING_ROOM_PORT", "8080")
    try:
        port = int(port_text)
    except ValueError:
        port = 8080

    if sys.argv[1:] == ["--check"]:
        return 0 if _app_is_running(port) else 1

    _configure_logging()
    local_url = f"http://127.0.0.1:{port}"
    upgrade_check = os.environ.get("MEETING_ROOM_UPGRADE_CHECK") == "1"
    probe = _probe_app(port)
    if probe["kind"] == "same-installation" and not upgrade_check:
        print()
        print("会议室预约系统本来就在运行，正在为你打开浏览器。")
        _print_pending_network_change()
        _open_browser(local_url, delay=0)
        return 10
    if probe["kind"] != "none":
        if probe["kind"] == "same-installation" and upgrade_check:
            print()
            print("升级检查无法开始：会议室预约系统仍在正常运行。")
            print("请先停止现有服务，再重新运行升级。")
        else:
            _print_port_conflict(port, probe)
        return 1
    if probe["local_identity_state"] == "invalid":
        print()
        print("启动已停止：data 文件夹里的 install_id 已损坏。")
        print("为了避免识别到错误的预约数据，系统不会自动更换安装标识。")
        print("请联系维护人员检查并恢复该文件。")
        return 1

    bind_host = _bind_host()
    try:
        from waitress import create_server
        from app import app, init_db, observe_network_url

        server = create_server(app, host=bind_host, port=port, threads=8)
    except Exception as error:
        logging.exception("服务绑定失败")
        retry_probe = _probe_app(port)
        if retry_probe["kind"] == "same-installation" and not upgrade_check:
            print()
            print("会议室预约系统已经由另一次启动成功，正在为你打开浏览器。")
            _print_pending_network_change()
            _open_browser(local_url, delay=0)
            return 10
        if retry_probe["kind"] == "same-installation":
            print()
            print("升级检查无法开始：会议室预约系统仍在正常运行。")
            print("请先停止现有服务，再重新运行升级。")
        elif retry_probe["kind"] != "none":
            _print_port_conflict(port, retry_probe)
        else:
            print(f"启动失败：无法使用端口 {port}。")
            print("请联系维护人员检查端口权限或系统配置。")
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

    if upgrade_check:
        logging.info(
            "会议室预约系统升级检查模式启动，本机地址=%s，安装标识=%s",
            local_url,
            app.config["INSTALL_ID"],
        )
        try:
            server.run()
        except KeyboardInterrupt:
            pass
        except Exception:
            logging.exception("升级检查服务运行失败")
            return 1
        finally:
            server.close()
        return 0

    local_ip = _local_ip()
    current_lan_url = (
        f"http://{local_ip}:{port}"
        if _usable_private_ip(local_ip)
        else None
    )
    app.config["CURRENT_LAN_URL"] = current_lan_url
    lan_url = current_lan_url or "本机IP"
    network_result = observe_network_url(
        Path(app.config["NETWORK_STATE_FILE"]),
        lan_url,
        log_dir=LOG_DIR,
    )
    warning_fallback = (
        dict(network_result["pending"])
        if network_result["pending"] and not network_result["updated"]
        else None
    )
    app.config["NETWORK_WARNING_FALLBACK"] = warning_fallback
    app.config["NETWORK_WARNING_PERSIST_FAILED"] = bool(warning_fallback)
    logging.info(
        "会议室预约系统启动，本机地址=%s，局域网地址=%s，安装标识=%s",
        local_url,
        lan_url,
        app.config["INSTALL_ID"],
    )
    print()
    print("=" * 58)
    print("  会议室预约系统已经启动")
    print()
    print(f"  本机打开：{local_url}")
    if lan_url == "本机IP":
        print("  同事访问：暂时无法可靠识别，请检查网络后重启系统")
    else:
        print(f"  同事访问：{lan_url}")
    print()
    pending = network_result["pending"]
    if pending:
        if pending["kind"] == "changed":
            print("  注意：同事访问网址已经变化，请把新网址发给同事。")
            print(f"  原网址：{pending['old_url']}")
            print(f"  新网址：{pending['new_url']}")
        else:
            print("  注意：地址记录需要重新核对。")
            print("  系统无法可靠确认原网址，请把当前网址重新发给同事。")
            print(f"  当前网址：{pending['new_url']}")
        if network_result["updated"]:
            print("  预约数据没有变化；管理员登录后也会持续看到提醒。")
        else:
            print("  预约数据没有变化，但本次未能保存网页提醒。")
            print("  请立即把新网址通知同事，并联系维护人员检查 data 文件夹。")
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

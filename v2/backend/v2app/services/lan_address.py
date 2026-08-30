"""局域网服务地址的探测与受控缓存。

探测逻辑原本只在 service 启动器进程启动时计算一次并冻结进 Flask config；
开机自启动等场景下网络尚未就绪时会把 `None` 永久冻结，系统状态页因此长期
隐藏局域网地址。现在 API 改读带 TTL 的 `current_lan_address`：探测失败时
短间隔重试，探测成功后低频复探，既能从启动竞态中自愈，也能在 DHCP 换址
后更新展示。探测是纯本地查询（主机名解析与不发包的 UDP connect），只在
缓存过期时的请求线程内单飞执行。
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from typing import Any, Optional


MISSING_RETRY_SECONDS = 15.0
RESOLVED_REFRESH_SECONDS = 600.0

_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)

_CACHE: dict[str, Any] = {"port": None, "url": None, "checked_at": None}
_CACHE_LOCK = threading.Lock()


def _private_lan_url(candidates: set[str], port: int) -> Optional[str]:
    accepted = []
    for value in candidates:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version != 4 or not any(
            address in network for network in _PRIVATE_NETWORKS
        ):
            continue
        if (
            address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
        ):
            continue
        accepted.append(address)
    if not accepted:
        return None
    selected = min(accepted, key=int)
    return f"http://{selected}:{port}"


def discover_lan_address(port: int = 8080) -> Optional[str]:
    candidates: set[str] = set()
    for host in {socket.gethostname(), socket.getfqdn()}:
        try:
            for item in socket.getaddrinfo(host, None, socket.AF_INET):
                candidates.add(item[4][0])
        except OSError:
            continue
    # UDP connect determines the selected local interface without transmitting
    # application data. Multiple RFC1918 destinations cover disconnected LANs
    # whose hostname is not registered in local DNS.
    for target in ("10.0.0.1", "172.16.0.1", "192.168.0.1"):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((target, 9))
            candidates.add(probe.getsockname()[0])
        except OSError:
            pass
        finally:
            probe.close()
    return _private_lan_url(candidates, port)


def current_lan_address(port: int = 8080) -> Optional[str]:
    now = time.monotonic()
    with _CACHE_LOCK:
        if _CACHE["port"] != port:
            _CACHE.update({"port": port, "url": None, "checked_at": None})
        max_age = (
            RESOLVED_REFRESH_SECONDS if _CACHE["url"] else MISSING_RETRY_SECONDS
        )
        checked_at = _CACHE["checked_at"]
        if checked_at is not None and now - checked_at < max_age:
            return _CACHE["url"]
        # 探测在锁内单飞：主机名解析最坏情形可阻塞数秒，不能让并发请求
        # 同时触发；调用方均为 30 秒轮询的管理端点，等待可接受。
        url = discover_lan_address(port)
        _CACHE["url"] = url
        _CACHE["checked_at"] = time.monotonic()
        return url


def reset_lan_address_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.update({"port": None, "url": None, "checked_at": None})

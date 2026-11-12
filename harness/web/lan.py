"""局域网测试入口：本机管理与受限共享分别监听，共用同一运行实例。"""
from __future__ import annotations

import asyncio
import ipaddress
import threading
from urllib.parse import urlsplit

from ..i18n import tr
from .shared_access import private_http_host


def validate_lan_origin(origin):
    """只绑定明确的私有 IPv4，不使用全接口监听或公网域名。"""
    try:
        if not isinstance(origin, str):
            raise ValueError
        parts = urlsplit(origin)
        address = ipaddress.ip_address(parts.hostname or '')
        valid = (parts.scheme == 'http' and parts.geturl() == origin and not parts.path
                 and not parts.query and not parts.fragment and not parts.username and not parts.password
                 and isinstance(address, ipaddress.IPv4Address) and private_http_host(str(address)))
        port = parts.port if parts.port is not None else 80
        valid = valid and port > 0
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(tr('web.lan.invalid_origin'))
    return str(address), port


def wait_for_shutdown():
    """由主线程等待终止信号，不占用模型事件循环。"""
    threading.Event().wait()


def serve_lan(harness, *, origin, host='127.0.0.1', port=8765, dist=None):
    """CLI 同时启动本机完整入口及局域网共享入口，退出时收回两个监听和运行资源。"""
    from .server import serve, stop
    lan_host, lan_port = validate_lan_origin(origin)
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == 'localhost'
    if not loopback:
        raise ValueError(tr('web.lan.loopback_required'))
    local = serve(harness, host=host, port=port, background=True, dist=dist)
    shared = None
    try:
        shared = serve(harness, host=lan_host, port=lan_port, background=True, dist=dist,
            shared_authenticator=harness.session_credentials.authenticate, shared_browser_origin=origin)
        harness.shared_origin = origin
        print(tr('web.lan.local', url=f'http://{host}:{local.server_address[1]}'), flush=True)
        print(tr('web.lan.shared', url=origin + '/shared'), flush=True)
        wait_for_shutdown()
    except KeyboardInterrupt:
        pass
    finally:
        harness.shared_origin = None
        if shared is not None:
            stop(shared)
        try:
            asyncio.run_coroutine_threadsafe(harness.close(), local._harness_loop).result(15)
        finally:
            stop(local)

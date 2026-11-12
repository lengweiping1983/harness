"""IPv4 优先的 HTTPS 连接helper。

为什么需要它：本机 ``getaddrinfo(host, 443)`` 会把 AAAA（IPv6）记录排在最前，
而很多宽带/企业网络的 IPv6 出口是"通但极慢"。表现是首字节延迟几十秒，
随后 ``WinError 10054`` / ``WinError 10060``，而且完全看不出是网络选路问题。
实测同一主机：IPv4 只需 0.4s，IPv6 要 40s+ 或直接超时。

本模块只影响传输层选路，不改变任何 HTTP 语义；纯 IPv6 环境会自动回落默认逻辑。
"""

from __future__ import annotations

import http.client
from functools import lru_cache
import socket
import ssl
import urllib.parse
import urllib.request
from typing import Any
from .request_control import current_request, create_socket


class _ControlledConnection:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._request_control = current_request()
        if self._request_control is not None:
            self._request_control.track_connection(self)
            self._create_connection = lambda address, timeout, source: create_socket(
                address, timeout, source, self._request_control)


class _HTTPConnection(_ControlledConnection, http.client.HTTPConnection):
    pass


class _HTTPSConnection(_ControlledConnection, http.client.HTTPSConnection):
    def connect(self):
        if self._request_control is None:
            return super().connect()
        http.client.HTTPConnection.connect(self)
        self.sock = self._request_control.wrap_socket(self._context, self.sock,
            server_hostname=self._tunnel_host or self.host)
        try:
            self.sock.do_handshake()
            self._request_control.check()
        except BaseException:
            self.close()
            raise


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_HTTPConnection, req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_HTTPSConnection, req, context=self._context)


@lru_cache(maxsize=8)
def _controlled_opener(proxy_settings):
    # 处理器没有单个请求的控制状态，连接构造器读取线程局部归属。
    # 避免每次 HTTP 调用都重新加载 TLS 根证书。
    return urllib.request.build_opener(urllib.request.ProxyHandler(dict(proxy_settings)),
                                       _HTTPHandler(), _HTTPSHandler())


def controlled_urlopen(request, *, timeout):
    """保留 urllib 的代理和重定向处理，同时管理连接及响应头等待。"""
    if current_request() is None:
        return urllib.request.urlopen(request, timeout=timeout)
    proxy_settings = tuple(sorted(urllib.request.getproxies().items()))
    return _controlled_opener(proxy_settings).open(request, timeout=timeout)


class IPv4PreferredHTTPSConnection(_HTTPSConnection):
    """强制 IPv4 优先的 HTTPS 连接。

    ``http.client.HTTPSConnection.connect()`` 默认交给
    ``socket.create_connection()``，而后者按 ``getaddrinfo()`` 的顺序取第一个
    地址 —— 在双栈机器上常常是 AAAA 记录。若本机 IPv6 出口不通或极慢，
    请求会表现为首字节延迟几十秒、随后连接重置，而且看不出是选路问题。

    这里只把地址族偏好从 AF_UNSPEC 改成 AF_INET，不改变 TLS、Host 头或任何
    HTTP 语义；没有 IPv4 地址时回落到系统默认（纯 IPv6 环境仍可用）。
    """

    def connect(self) -> None:  # pragma: no cover - 需要真实网络
        """建立 TCP + TLS 连接，地址族固定为 IPv4（不可用时回落默认）。"""
        sock = self._new_ipv4_socket()
        if not sock:
            # 主机没有 A 记录（例如纯 IPv6 环境）时交回标准库逻辑。
            super().connect()
            return
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
            sock = self.sock
        self.sock = self._wrap_ipv4_socket(sock)

    def _new_ipv4_socket(self) -> socket.socket | None:
        """创建一个已连上主机的 AF_INET 套接字；没有 IPv4 时返回 ``None``。"""
        host, port = self.host, self.port
        last_error: OSError | None = None
        control = self._request_control
        if control is not None:
            control.check()
        try:
            infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        except socket.gaierror:
            return None
        if control is not None:
            control.check()
        for family, socktype, proto, _canonname, sockaddr in infos:
            sock = socket.socket(family, socktype, proto)
            try:
                if control is not None:
                    control.track_socket(sock)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(self.timeout)
                sock.connect(sockaddr)
                if control is not None:
                    control.check()
                return sock
            except OSError as exc:
                last_error = exc
                sock.close()
        if last_error is not None:
            # 有 A 记录但全部连不上：应如实报错，而不是静默降级。
            raise last_error
        return None

    def _wrap_ipv4_socket(self, sock: socket.socket) -> Any:
        """在已连好的 IPv4 套接字上完成 TLS 握手（沿用标准库的超时与上下文）。"""
        server_hostname = self._tunnel_host or self.host
        context = self._context or ssl.create_default_context()
        if self._request_control is None:
            return context.wrap_socket(sock, server_hostname=server_hostname)
        wrapped = self._request_control.wrap_socket(context, sock, server_hostname=server_hostname)
        try:
            wrapped.do_handshake()
            self._request_control.check()
            return wrapped
        except BaseException:
            wrapped.close()
            raise


class ResponseProxy:
    """包装 ``HTTPResponse``，让 ``close()`` 同时释放底层连接。

    ``http.client`` 里的 ``HTTPResponse.close()`` 只关闭响应体，底层 ``HTTPSConnection``
    与其 SSL 套掤字仍被引用，长期运行会留下 ``ResourceWarning: unclosed
    ssl.SSLSocket``。这里把两者绑在一起，调用方只需要 :meth:`close`。
    """

    __slots__ = ("_response", "_conn", "_closed")

    def __init__(self, response: Any, conn: Any) -> None:
        self._response = response
        self._conn = conn
        self._closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        return self._response.read(*args, **kwargs)

    def close(self) -> None:
        """关闭响应体并释放连接（幂等）。"""
        if self._closed:
            return
        self._closed = True
        try:
            self._response.close()
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass


def ipv4_preferred_get(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    method: str = "GET",
    data: bytes | None = None,
    url_validator=None,
) -> Any:
    """使用标准重定向语义及 IPv4 优先 TLS，每次跳转先复核地址。"""
    class PreferredHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(IPv4PreferredHTTPSConnection, req, context=self._context)

    class CheckedRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if url_validator is not None:
                url_validator(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    return urllib.request.build_opener(_HTTPHandler(), PreferredHTTPSHandler(), CheckedRedirect()).open(
        request, timeout=timeout)


def tcp_reachable(host: str, port: int = 443, timeout: float = 2.0) -> bool:
    """快速探测 TCP 可达性（不做 TLS）。

    用于搜索引擎这类“可能整体崩塌”的外部依赖：先用 1~2 秒探一下，
    不可达就立刻跳过，避免每次都把 20s 耗在已知死了的主机上。
    """
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    for _family, socktype, proto, _canon, sockaddr in infos:
        sock = socket.socket(socket.AF_INET, socktype, proto)
        try:
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            return True
        except OSError:
            continue
        finally:
            sock.close()
    return False


def supports_ipv4(host: str, port: int = 443) -> bool:
    """主机是否有可用的 IPv4 地址（用于诊断与测试）。"""
    try:
        socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    return True

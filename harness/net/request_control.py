"""在 HTTP 响应头到达前管理单请求归属，不修改全局套接字行为。"""
from __future__ import annotations

from harness.i18n import tr

from contextlib import contextmanager
import socket
import threading

_CURRENT = threading.local()


def current_request():
    return getattr(_CURRENT, 'request', None)


def abort_socket(sock) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        # Windows 在关闭后可能仍保留阻塞读取；应关闭实际描述符，
        # 即使 BufferedReader 还持有另一个引用。
        descriptor = sock.detach()
        if descriptor >= 0:
            socket.close(descriptor)
    except OSError:
        pass


class RequestControl:
    def __init__(self, cancelled: threading.Event):
        self.cancelled = cancelled
        self.lock = threading.Lock()
        self.sockets = set()
        self.connections = set()

    def check(self) -> None:
        if self.cancelled.is_set():
            raise InterruptedError(tr('runtime.validation.7f19f9047fb7'))

    def track_socket(self, sock) -> None:
        with self.lock:
            if not self.cancelled.is_set():
                self.sockets.add(sock)
                return
        abort_socket(sock)
        self.check()

    def track_connection(self, conn) -> None:
        with self.lock:
            self.check()
            self.connections.add(conn)

    def wrap_socket(self, context, sock, *, server_hostname):
        # SSL 会从原始套接字接管描述符；转移归属期间，
        # 取消操作不能关闭或复用该描述符。
        with self.lock:
            self.check()
            wrapped = context.wrap_socket(sock, server_hostname=server_hostname,
                                          do_handshake_on_connect=False)
            self.sockets.add(wrapped)
            return wrapped

    @contextmanager
    def scope(self):
        previous = current_request()
        _CURRENT.request = self
        try:
            self.check()
            yield
        finally:
            _CURRENT.request = previous

    def close(self) -> None:
        self.cancelled.set()
        with self.lock:
            sockets, connections = self.sockets, self.connections
            self.sockets, self.connections = set(), set()
        for sock in sockets:
            abort_socket(sock)
        for conn in connections:
            try:
                conn.close()
            except OSError:
                pass


def create_socket(address, timeout, source_address, control: RequestControl):
    control.check()
    infos = socket.getaddrinfo(address[0], address[1], 0, socket.SOCK_STREAM)
    control.check()  # 延迟到达的 DNS 结果不能启动已取消的请求。
    error = OSError(tr('network.addresses.empty'))
    for family, socktype, proto, _, sockaddr in infos:
        control.check()
        sock = socket.socket(family, socktype, proto)
        try:
            control.track_socket(sock)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            control.check()
            return sock
        except OSError as exc:
            error = exc
            sock.close()
    raise error

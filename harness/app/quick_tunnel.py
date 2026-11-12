"""临时域名申请转交所属系统代理；不代理隧道数据或管理用户账号。"""
from __future__ import annotations

from contextvars import copy_context
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import secrets
import threading
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .mcp_errors import McpServiceError
from ..i18n import tr

PROVISIONING_URL = 'https://api.trycloudflare.com/tunnel'
MAX_RESPONSE = 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    """申请只发给官方接口；重定向视为原始 HTTP 失败，不重放 POST。"""

    def redirect_request(self, request, file, code, message, headers, newurl):
        return None


class QuickTunnelService:
    """只接收本次 cloudflared 的一次申请，凭据不写磁盘或日志。"""

    def __init__(self, proxy):
        self._stop = threading.Event()
        self._used = False
        self._thread = None
        self._path = '/' + secrets.token_urlsafe(32)
        owner = self
        opener = build_opener(ProxyHandler({'https': proxy}), _NoRedirect())

        class Handler(BaseHTTPRequestHandler):
            """只在回环地址和不可猜测路径上接收空正文申请。"""

            def setup(self):
                """本机半条请求也有读取上限，不能永久占据所属线程。"""
                super().setup()
                self.connection.settimeout(3)

            def log_message(self, format, *args):
                """不记录临时域名、一次性路径或隧道凭据。"""
                return

            def do_POST(self):
                """官方申请结果原样返回；失败和未知结果均不重试。"""
                lengths = self.headers.get_all('Content-Length', [])
                if (self.path != owner._path + '/tunnel' or self.headers.get('Origin') is not None
                        or self.headers.get('Transfer-Encoding') is not None or lengths != ['0']):
                    self.send_error(400)
                    return
                if owner._used or owner._stop.is_set():
                    self.send_error(409)
                    return
                owner._used = True
                request = Request(PROVISIONING_URL, data=b'', method='POST',
                                  headers={'Content-Type': 'application/json', 'User-Agent': self.headers.get('User-Agent', 'Harness-MCP')})
                status = 502
                body = b''
                try:
                    try:
                        response = opener.open(request, timeout=10)
                    except HTTPError as exc:
                        response = exc
                    with response:
                        chunks, size = [], 0
                        deadline = time.monotonic() + 20
                        while True:
                            chunk = response.read1(min(65536, MAX_RESPONSE + 1 - size))
                            size += len(chunk)
                            if size > MAX_RESPONSE or time.monotonic() > deadline:
                                raise OSError('provisioning response limit')
                            if not chunk:
                                break
                            chunks.append(chunk)
                        status, body = response.status, b''.join(chunks)
                except OSError as exc:
                    detail = str(exc).replace(proxy, '[proxy]')
                    body = json.dumps({'errors': [{'code': 0, 'message': tr('ui.mcpService.error.provisioningTransport', detail=detail)}]}, ensure_ascii=False).encode('utf-8')
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    # 申请可能已在上游受理；下游断开不构成重发许可。
                    return

        self._server = HTTPServer(('127.0.0.1', 0), Handler)
        self._server.timeout = .2
        self._server.socket.settimeout(.5)
        self.url = f'http://127.0.0.1:{self._server.server_port}' + self._path

    def start(self):
        """线程继承本次语言；启动失败关闭尚未使用的监听句柄。"""
        def serve():
            """停止不撤销已发出的申请，等待所属请求完成后关闭监听。"""
            try:
                while not self._stop.is_set():
                    self._server.handle_request()
            finally:
                self._server.server_close()

        self._thread = threading.Thread(target=copy_context().run, args=(serve,), daemon=True, name='harness-quick-service')
        try:
            self._thread.start()
        except BaseException:
            self._thread = None
            self._server.server_close()
            raise
        return self.url

    def stop(self):
        """未完成申请保留线程所有权；后续显式停止继续等待原收尾。"""
        self._stop.set()
        if self._thread is None:
            self._server.server_close()
            return
        self._thread.join(timeout=3)
        if self._thread.is_alive():
            raise McpServiceError('ui.mcpService.error.cleanupPending')

"""Web 服务端：HTTP 路由桥接 + WebSocket 会话驱动。

这一层只做三件事，把业务逻辑全部交给上层模块：

1. **HTTP** —— 把 :mod:`~harness.web.router` 解析出来的处理器结果写回 socket，
   并统一处理 gzip / ETag / 错误映射；
2. **WebSocket** —— 完成握手、读帧、驱动一次回合、把事件推给所有连接；
3. **生命周期** —— 启动/停止事件循环、监听 socket 与全部长连接。

实时通道的约束：

* **回合串行化** —— 同一会话的并发输入排队执行（持久运行队列），
  避免两个回合交叉写事件导致界面错乱；
* **多端同步** —— 回合事件经 :class:`~harness.web.hub.ConnectionHub` 广播给
  同一会话的其它标签页，慢客户端走独立队列，不会拖慢驱动回合；
* **断线补发** —— 事件带 ``seq``，重连时按 ``since`` 补齐；
* **死连接保护** —— 发送超时即断开，避免一个卡死的 socket 让回合永久挂起。
"""

from __future__ import annotations

from harness.i18n import tr

from pathlib import Path

import asyncio
import base64
import binascii
import concurrent.futures
import contextlib
import json
import logging
import os
import socket
import threading
import time
import uuid
import urllib.parse
from collections import deque
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .query import event_cursor
from ..core.errors import HarnessError, PersistenceError, SessionNotFoundError
from ..core.cleanup import raise_cleanup_errors
from ..core.jsonutil import to_jsonable
from ..interaction.questions import QuestionError
from .attachments import MAX_BATCH_BYTES
from .context import WebContext
from .errors import ApiError, BadRequest, NotFound, PayloadTooLarge
from .hub import ConnectionHub
from .error_messages import public_error_message
from .responses import bytes_response, error_response, json_response, text_response
from .router import Request, Response, normalize_segments
from .static_assets import StaticAssets
from .wsproto import (
    CLOSE_GOING_AWAY,
    CLOSE_NORMAL,
    CLOSE_PROTOCOL_ERROR,
    Frame,
    FrameDecoder,
    OP_CLOSE,
    OP_PING,
    OP_PONG,
    OP_TEXT,
    ProtocolError,
    accept_key,
    decode_close_payload,
    encode_close,
    encode_frame,
)

logger = logging.getLogger(__name__)

#: 单次发送的上限（秒）。超过视为死连接并断开，防止回合被永久挂住。
SEND_TIMEOUT = 20.0

#: 读缓冲大小。
READ_CHUNK = 65536

# 一次套接字写入可包含多个完整 WebSocket 帧；大事件仍各自成帧，
# 累积的小帧总量不超出此预算。
SEND_BATCH_BYTES = 65536

#: 单条客户端消息上限（4 MiB），与帧解码器一致。
MAX_CLIENT_MESSAGE = 4 * 1024 * 1024


# ---------------------------------------------------------------------------
# WebSocket 连接
# ---------------------------------------------------------------------------


#: 普通请求限制为 4 MiB，避免所有接口都承担附件级内存开销。
MAX_REQUEST_BODY_BYTES = 4 * 1024 * 1024

#: 附件保留完整批次容量，另留 1 MiB 给 JSON 结构和文件名等元数据。
MAX_ATTACHMENT_REQUEST_BODY_BYTES = 4 * ((MAX_BATCH_BYTES + 2) // 3) + 1024 * 1024


class WebSocketConnection:
    """服务端侧的一条 WebSocket 连接。

    收发使用事件循环的原生套接字 I/O，不占用模型和工具共享的线程池。
    帧编码与增量解码保持独立，发送锁避免控制帧和数据帧交错。
    """

    def __init__(
        self,
        sock: socket.socket,
        *,
        send_timeout: float = SEND_TIMEOUT,
        max_message_bytes: int = MAX_CLIENT_MESSAGE,
    ) -> None:
        self.sock = sock
        self.sock.setblocking(False)
        self.lock = asyncio.Lock()
        self.closed = False
        self._send_timeout = send_timeout
        self._decoder = FrameDecoder(max_message_bytes=max_message_bytes)
        self.snapshot_view = "full"
        self.access_check = None
        self.payload_projection = None
        self._pending_frames: deque[Frame] = deque()
        self._socket_tasks: set[asyncio.Task[Any]] = set()

    # -- 发送 ------------------------------------------------------------
    async def send_json(self, payload: Any) -> None:
        """发送一条 JSON 文本消息。"""
        if self.payload_projection is not None:
            payload = self.payload_projection(payload)
            if payload is None:
                return
        await self.send_text(json.dumps(to_jsonable(payload), ensure_ascii=False))

    async def send_text(self, text: str) -> None:
        await self._send_frame(OP_TEXT, text.encode("utf-8"))

    async def send_json_batch(self, payloads: list[Any]) -> None:
        """通过有界套接字写入，将排队事件发送为独立帧。"""
        batch = bytearray()
        for payload in payloads:
            if self.payload_projection is not None:
                payload = self.payload_projection(payload)
                if payload is None:
                    continue
            if self.closed:
                return
            frame = encode_frame(OP_TEXT, json.dumps(to_jsonable(payload), ensure_ascii=False).encode('utf-8'))
            if batch and len(batch) + len(frame) > SEND_BATCH_BYTES:
                await self._send_encoded(bytes(batch))
                batch.clear()
            if len(frame) >= SEND_BATCH_BYTES:
                await self._send_encoded(frame)
            else:
                batch.extend(frame)
        if batch:
            await self._send_encoded(bytes(batch))

    async def _send_frame(self, opcode: int, data: bytes) -> None:
        await self._send_encoded(encode_frame(opcode, data))

    async def _send_encoded(self, frame: bytes) -> None:
        """写出完整帧，慢客户端的等待不占用线程池。"""
        loop = asyncio.get_running_loop()
        try:
            async with self.lock:
                if self.closed:
                    return
                if self.access_check is not None and not self.access_check():
                    await self._abort_io()
                    return
                async with asyncio.timeout(self._send_timeout):
                    await self._socket_io(loop.sock_sendall(self.sock, frame))
        except asyncio.CancelledError:
            # 被取消的发送可能已交付部分帧，此套接字不能再安全发送
            # 任何后续帧，包括关闭帧。
            requested = asyncio.current_task().cancelling()
            await self._abort_io()
            if requested:
                raise
        except (OSError, TimeoutError, RuntimeError) as exc:
            logger.debug(tr('log.a5ed924111e4'), exc)
            await self._abort_io()

    async def _socket_io(self, operation: Any) -> Any:
        task = asyncio.create_task(operation)
        self._socket_tasks.add(task)
        try:
            return await task
        finally:
            self._socket_tasks.discard(task)

    def _abort(self) -> None:
        self.closed = True
        # Windows 上仅关闭套接字不一定能结束待处理的重叠 WSASend，
        # 还需取消其原生 I/O 句柄。
        for task in self._socket_tasks:
            task.cancel()
        # 唤醒并发读取或写入；HTTP 处理器负责描述符释放，
        # 循环退出后由其关闭套接字和文件包装器。
        with contextlib.suppress(OSError):
            self.sock.shutdown(socket.SHUT_RDWR)

    async def _abort_io(self) -> None:
        self._abort()
        cleanup = asyncio.gather(*tuple(self._socket_tasks), return_exceptions=True)
        cancelled_again = False
        while True:
            try:
                await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError:
                cancelled_again = True
        if cancelled_again:
            raise asyncio.CancelledError

    async def send_pong(self, payload: bytes) -> None:
        """回一条 pong，应答客户端 keepalive ping。

        没有它，浏览器在 60 秒无 pong 时会判定连接死掉并主动断开，
        而模型首个 token 经常就要几十秒。
        """
        await self._send_frame(OP_PONG, payload)

    async def send_close(self, code: int = CLOSE_NORMAL, reason: str = "") -> None:
        try:
            # 时限涵盖获取锁，已经受阻的数据发送
            # 不能让关闭操作等待更长的生成发送时限。
            async with asyncio.timeout(2.0):
                async with self.lock:
                    if not self.closed:
                        await self._socket_io(asyncio.get_running_loop().sock_sendall(self.sock, encode_close(code, reason)))
        except (OSError, TimeoutError, RuntimeError):
            logger.debug(tr('log.408b8220875b'), exc_info=True)
        finally:
            await self._abort_io()

    async def close(self) -> None:
        """发送关闭帧并中断收发；套接字描述符由 HTTP 调用方释放。"""
        if self.closed:
            return
        await self.send_close(CLOSE_NORMAL)

    # -- 读取 -----------------------------------------------------------
    async def read_message(self) -> Frame | None:
        """异步读取直到拿到一条完整消息；连接关闭返回 ``None``。

        解码器是连接级的，多余的帧会留在缓冲里，下次调用继续取，
        因此分片消息不会因为两次 ``recv`` 被拆开而损坏。
        """
        loop = asyncio.get_running_loop()
        while not self.closed:
            if self._pending_frames:
                return self._pending_frames.popleft()
            try:
                chunk = await self._socket_io(loop.sock_recv(self.sock, READ_CHUNK))
            except asyncio.CancelledError:
                requested = asyncio.current_task().cancelling()
                await self._abort_io()
                if requested:
                    raise
                return None
            except (OSError, RuntimeError):
                await self._abort_io()
                return None
            if not chunk:
                await self._abort_io()
                return None
            # 协议错误时暂时保持连接，让调用方发送协议关闭帧，
            # 随后 send_close() 关闭套接字。
            self._pending_frames.extend(self._decoder.feed(chunk))
            if self._pending_frames:
                return self._pending_frames.popleft()
            # 缓冲里没有完整帧（分片未收齐），继续 recv。
        return None


# ---------------------------------------------------------------------------
# HTTP 处理器
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    """HTTP 请求处理器。

    真正的业务在 :mod:`harness.web.api` 里，这里只做协议适配与错误翻译。
    ``harness`` / ``loop`` / ``ws_clients`` 由 :func:`serve` 注入。
    """

    protocol_version = "HTTP/1.1"
    harness: Any = None
    loop: Any = None
    ws_clients: set = None  # type: ignore[assignment]
    _web_ctx: WebContext | None = None

    # -- 上下文 ----------------------------------------------------------
    @property
    def ctx(self) -> WebContext:
        """惰性构建运行时上下文（测试直接 new Handler 时也能工作）。"""
        context = self.__class__.__dict__.get("_web_ctx") or getattr(self, "_web_ctx", None)
        if context is None:
            context = WebContext(
                harness=self.harness,
                loop=self.loop,
                hub=ConnectionHub(),
                assets=StaticAssets.locate(),
            )
            self._web_ctx = context
        return context

    @property
    def router(self):
        return self.ctx.router()

    # -- 日志 ------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("HTTP %s - %s", self.address_string(), fmt % args)

    def handle(self) -> None:
        """浏览器切换预览时可能中止持久连接。"""
        try:
            super().handle()
        except ConnectionError:
            self.close_connection = True
            logger.debug(tr('log.93388f113e16'))

    # -- 响应写入 --------------------------------------------------------
    def _send(self, response: Response) -> None:
        """把 :class:`~harness.web.router.Response` 写回 socket。"""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        self.send_response(response.status)
        for name, value in response.headers.items():
            # 传输层统一计算实际长度，避免文件/静态资源响应重复发送该头。
            if name.lower() != "content-length":
                self.send_header(name, value)
        if self.close_connection and not any(name.lower() == 'connection' for name in response.headers):
            self.send_header('Connection', 'close')
        self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        if response.status not in (204, 304) and self.command != "HEAD":
            self.wfile.write(response.body)

    def _accept_encoding(self) -> str:
        getter = getattr(self.headers, "get", None)
        return (getter("Accept-Encoding", "") if getter else "") or ""

    def _if_none_match(self) -> str:
        getter = getattr(self.headers, "get", None)
        return (getter("If-None-Match", "") if getter else "") or ""

    def _error(self, exc: Exception) -> None:
        """把异常翻译成 HTTP 响应。

        ``HarnessError`` 按已知领域类型使用 400、404 或 503，结构为 ``{"error", "code", "details"}``；
        ``ApiError`` 用自己的状态码；其余一律 500 且不泄露栈信息。
        """
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            self.close_connection = True
            logger.debug(tr('log.a17ee6553499'))
            return
        if isinstance(exc, ApiError):
            payload = exc.to_dict()
            status = exc.status
        elif isinstance(exc, HarnessError):
            payload = {
                "error": exc.message,
                "code": exc.code,
                "details": to_jsonable(exc.details) or None,
            }
            status = 503 if isinstance(exc, PersistenceError) else 404 if isinstance(exc, SessionNotFoundError) else 400
        else:
            logger.exception(tr('log.95d5dca987bd'))
            payload = {"error": tr('python.server.internal_error'), "code": "internal/error"}
            status = 500
        if payload.get("code") == "request/origin":
            self.close_connection = True
        response = error_response(
            payload["error"], status=status, code=payload.get("code", "error"),
            details=payload.get("details"), accept_encoding=self._accept_encoding(),
        )
        if payload.get('code') == 'websocket/version':
            response.with_header('Sec-WebSocket-Version', '13')
        if status == 405:
            allowed = (payload.get('details') or {}).get('allow', [])
            if allowed:
                response.with_header('Allow', ', '.join(allowed))
        if status == 401 and self.ctx.shared_access is not None:
            response.with_header('WWW-Authenticate', 'Bearer realm="harness-session"')
        self._send(self._apply_cors(response))

    # -- 请求构造 --------------------------------------------------------
    def _build_request(self) -> Request:
        parsed = urllib.parse.urlparse(self.path)
        supplied = self.headers.get("X-Request-ID", "")
        self._request_id = supplied if supplied and len(supplied) <= 128 and all(c.isascii() and (c.isalnum() or c in "-_ .") for c in supplied) else uuid.uuid4().hex
        if "X-Request-ID" in self.headers:
            self.headers.replace_header("X-Request-ID", self._request_id)
        else:
            self.headers["X-Request-ID"] = self._request_id
        raw_length = self.headers.get("Content-Length", "0")
        if not raw_length.isascii() or not raw_length.isdecimal() or len(self.headers.get_all('Content-Length', [])) > 1 or len(self.headers.get_all('Transfer-Encoding', [])) > 0:
            self.close_connection = True
            raise BadRequest(tr('python.audit.invalid_length'), code='request/invalid-length')
        limit = (
            MAX_ATTACHMENT_REQUEST_BODY_BYTES
            if self.command == "POST" and normalize_segments(parsed.path) == ("api", "v1", "attachments")
            else MAX_REQUEST_BODY_BYTES
        )
        normalized_length = raw_length.lstrip('0') or '0'
        if len(normalized_length) > len(str(limit)):
            self.close_connection = True
            raise PayloadTooLarge(tr('python.server.8a9282e1fcd7'), code='request/body-too-large',
                                  details={'maxBytes': limit})
        length = int(normalized_length)
        if length > limit:
            # 在读取前拒绝，避免为超限请求分配请求体缓冲区。
            self.close_connection = True
            raise PayloadTooLarge(
                tr('python.server.8a9282e1fcd7'),
                code="request/body-too-large",
                details={"bytes": length, "maxBytes": limit},
            )
        if getattr(self, '_expect_continue', False):
            # 长度准入成功后才允许上传；超限请求直接返回最终拒绝，不读取正文。
            self.send_response_only(100)
            self.end_headers()
        prior_timeout = self.connection.gettimeout()
        try:
            deadline = time.monotonic() + 30.0
            body = bytearray()
            while len(body) < length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                self.connection.settimeout(remaining)
                chunk = self.rfile.read1(min(65536, length - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
        except TimeoutError as exc:
            self.close_connection = True
            raise ApiError(tr('python.audit.body_timeout'), status=408, code='request/body-timeout') from exc
        finally:
            self.connection.settimeout(prior_timeout)
        if len(body) != length:
            self.close_connection = True
            raise BadRequest(tr('python.audit.body_incomplete'), code='request/body-incomplete')
        return Request(
            method=self.command,
            path=parsed.path,
            # 保留显式空值交由业务校验，不能把空会话解释成全局查询。
            query=urllib.parse.parse_qs(parsed.query, keep_blank_values=True),
            headers=self.headers,
            body=bytes(body),
            client_ip=self.client_address[0],
        )

    def _origin_allowed(self) -> bool:
        origin_values = self.headers.get_all("Origin", [])
        if len(origin_values) > 1:
            return False
        shared = self.ctx.shared_access
        if shared is not None and shared.browser_origin is not None:
            return shared.origin_allowed(self.headers)
        host_values = self.headers.get_all("Host", [])
        if len(host_values) > 1:
            return False
        host = host_values[0] if host_values else ""
        try:
            hostname = urllib.parse.urlparse("http://" + host).hostname if host else None
        except ValueError:
            return False
        bound = getattr(getattr(self, "server", None), "server_address", (None,))[0]
        if hostname and hostname not in {"localhost", "127.0.0.1", "::1", bound}:
            return False
        sites = {site.strip().lower() for value in self.headers.get_all('Sec-Fetch-Site', [])
                 for site in value.split(',')}
        if sites & {'cross-site', 'same-site'}:
            return False
        origin = origin_values[0] if origin_values else ""
        if not origin:
            # 无浏览器来源的本机客户端继续可用；浏览器跨站请求不能借省略来源放行。
            return True
        try:
            parsed = urllib.parse.urlparse(origin)
            has_origin_suffix = "?" in origin or "#" in origin
        except ValueError:
            return False
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or has_origin_suffix
            or parsed.username is not None
            or parsed.password is not None
        ):
            return False
        return parsed.netloc == host

    def _apply_cors(self, response: Response) -> Response:
        response.headers.setdefault("X-Request-ID", getattr(self, "_request_id", None) or uuid.uuid4().hex)
        vary = response.headers.get("Vary", "")
        if 'origin' not in {part.strip().lower() for part in vary.split(',')}:
            response.headers["Vary"] = f"{vary}, Origin" if vary else "Origin"
        origin = self.headers.get("Origin", "")
        if origin and self._origin_allowed():
            response.headers.setdefault("Access-Control-Allow-Origin", origin)
            response.headers.setdefault("Access-Control-Allow-Headers", "Content-Type,Idempotency-Key,X-Request-ID")
            response.headers.setdefault("Access-Control-Allow-Methods", "GET,HEAD,POST,PATCH,DELETE,OPTIONS")
        return response

    # -- HTTP 动词 -------------------------------------------------------
    def handle_one_request(self) -> None:
        """每个 HTTP 请求单独绑定语言，错误响应也使用同一上下文。"""
        from ..core.language import current_language
        # 长连接会阻塞等待下一次请求，语言必须在请求解析完成后再读取。
        token = current_language.set(current_language.get())
        try:
            super().handle_one_request()
        finally:
            current_language.reset(token)

    def do_GET(self) -> None:  # noqa: N802
        request = None
        try:
            request = self._build_request()
            if request.path == "/" or not (request.path == "/api" or request.path.startswith("/api/")):
                response = self._serve_frontend(request)
            else:
                response = self._dispatch(request)
            self._send(self._apply_cors(response))
        except Exception as exc:
            self._error(exc)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        try:
            request = self._build_request()
            self._send(self._apply_cors(self._dispatch(request)))
        except Exception as exc:
            self._error(exc)

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            request = self._build_request()
            self._send(self._apply_cors(self._dispatch(request)))
        except Exception as exc:
            self._error(exc)

    def do_PATCH(self) -> None:  # noqa: N802
        try:
            request = self._build_request()
            self._send(self._apply_cors(self._dispatch(request)))
        except Exception as exc:
            self._error(exc)

    def do_OPTIONS(self) -> None:  # noqa: N802
        try:
            if not self._origin_allowed():
                raise BadRequest(tr('python.server.7ff293468b64'), code="request/origin", status=403)
            self._send(self._apply_cors(Response(status=204)))
        except Exception as exc:
            self._error(exc)

    def send_error(self, code, message=None, explain=None) -> None:
        """未实现的 API 方法也经统一路由、鉴权和 JSON 错误出口处理。"""
        if code == 501 and (self.path.split('?', 1)[0] == '/api' or self.path.startswith('/api/')):
            self.do_POST()
            return
        super().send_error(code, message, explain)

    # -- 分发 ------------------------------------------------------------
    def _dispatch(self, request: Request) -> Response:
        if not self._origin_allowed():
            raise BadRequest(tr('python.server.7ff293468b64'), code="request/origin", status=403)
        if request.method == 'HEAD':
            request = replace(request, method='GET')
        handler, params = self.router.resolve(request.method, request.path)
        if self.ctx.shared_access is not None and self.ctx.shared_access.browser_endpoint(request):
            return handler(replace(request, params=params), **params)
        if self.ctx.shared_access is not None:
            async def authorize():
                return self.ctx.shared_access.authorize_http(request, params)
            authorized_identity, visible = self.ctx.call(authorize())
            request = replace(request, subject=authorized_identity.subject, visible_session_ids=visible)
        # Request 是不可变数据类，路径参数只能通过 replace 注入。
        request = replace(request, params=params)
        result = handler(request, **params)
        if self.ctx.shared_access is not None:
            current_identity, current_visible = self.ctx.call(authorize())
            if current_identity != authorized_identity:
                raise ApiError('access/unauthenticated', code='access/unauthenticated', status=401)
            if current_visible != visible:
                raise ApiError('access/changed', code='access/changed', status=403)
        if isinstance(result, Response):
            return result
        if isinstance(result, tuple):
            payload, status = result
            return json_response(payload, status=status)
        return json_response(result)

    def _serve_frontend(self, request: Request) -> Response:
        """服务前端资源；缺少构建时显示构建说明。"""
        assets = self.ctx.assets
        if assets is None:
            # 首次启动时尚未构建，也能在构建发布后恢复完整界面；
            # 不把一次启动期间缺失永久缓存。
            assets = StaticAssets.locate(self.ctx.dist_root)
            if assets is not None:
                self.ctx.assets = assets
        if assets is not None:
            served = assets.serve(
                request.path,
                if_none_match=self._if_none_match(),
                accept_encoding=self._accept_encoding(),
                spa_fallback=True,
            )
            if served is not None:
                return served
        if request.path == "/":
            from .build_info import render_build_info
            from ..core.language import current_language

            return text_response(
                render_build_info(current_language.get()),
                content_type="text/html; charset=utf-8",
                headers={"Cache-Control": "no-cache"},
                accept_encoding=self._accept_encoding(),
            )
        return error_response(tr('python.server.2fa6436dac83', p0=f'{request.path}'), status=404, code="static/not-found")

    # ------------------------------------------------------------------
    # WebSocket 通信
    # ------------------------------------------------------------------
    def parse_request(self) -> bool:
        """先让基类解析请求行，再判断 WebSocket 升级。

        不能在 ``handle_one_request`` 里直接读 ``self.path``：基类会先调用
        ``parse_request()`` 解析请求行、再分发 ``do_GET``，在那之前
        ``self.path`` 还没赋值，直接访问会抛 ``AttributeError``，
        连带让 socketserver 的线程整个崩掉。
        """
        self._expect_continue = False
        if not super().parse_request():
            return False
        from ..core.language import current_language, resolve_language
        preference = self.harness.config.language
        actual = preference if preference in ('zh-CN', 'en') else resolve_language(preference).effective_language
        current_language.set(actual)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith('/ws/'):
            error = (NotFound(tr('python.router.f94ec39b7259', p0=parsed.path[:200]), code='route/not-found')
                     if self._origin_allowed() else BadRequest(tr('python.server.7ff293468b64'), code='request/origin', status=403))
            self._error(error)
            self.close_connection = True
            return False
        if parsed.path == "/ws":
            if not self._origin_allowed():
                self._error(BadRequest(tr('python.server.7ff293468b64'), code='request/origin', status=403))
                self.close_connection = True
                return False
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            for parameter in ('session', 'view', 'since'):
                if len(query.get(parameter, [''])) != 1:
                    self._error(BadRequest(tr('web.query_invalid'), code='request/invalid-query', details={'parameter': parameter}))
                    self.close_connection = True
                    return False
            try:
                self._validate_ws_handshake()
            except BadRequest as exc:
                self._error(exc)
                self.close_connection = True
                return False
            if self.ctx.shared_access is not None:
                try:
                    async def authorize():
                        identity = self.ctx.shared_access.identity(self.headers)
                        self.ctx.shared_access.require_read((query.get('session') or [''])[0], identity)
                        return identity
                    self._shared_identity = self.ctx.call(authorize())
                except ApiError as exc:
                    self._error(exc)
                    self.close_connection = True
                    return False
            session_id = (query.get("session") or [""])[0]
            since = (query.get("since") or ["0"])[0]
            try:
                self.do_GET_ws(session_id, since, snapshot_view=(query.get("view") or ["full"])[0])
            except ApiError as exc:
                self._error(exc)
            except (ConnectionError, OSError):
                logger.debug(tr('log.c00b9ac2ce42'), exc_info=True)
            self.close_connection = True
            return False
        return True

    def handle_expect_100(self) -> bool:
        """将继续上传的临时回执延后到请求头长度校验之后。"""
        self._expect_continue = True
        return True

    def _validate_ws_handshake(self) -> None:
        """升级和权限读取前校验HTTP/1握手的必需字段。"""
        def reject(field: str) -> None:
            raise BadRequest(tr('web.websocket.invalid_handshake', field=field),
                code='websocket/version' if field == 'Sec-WebSocket-Version' else 'websocket/handshake',
                status=426 if field == 'Sec-WebSocket-Version' else 400)

        if self.command != 'GET':
            reject('method')
        if tuple(int(part) for part in self.request_version[5:].split('.')) < (1, 1):
            reject('version')
        for field in ('Host', 'Sec-WebSocket-Key', 'Sec-WebSocket-Version'):
            values = self.headers.get_all(field, [])
            if len(values) != 1 or not values[0].strip():
                reject(field)
        for field, token in (('Upgrade', 'websocket'), ('Connection', 'upgrade')):
            tokens = {part.strip().lower() for value in self.headers.get_all(field, []) for part in value.split(',')}
            if token not in tokens:
                reject(field)
        if self.headers['Sec-WebSocket-Version'].strip() != '13':
            reject('Sec-WebSocket-Version')
        try:
            key = base64.b64decode(self.headers['Sec-WebSocket-Key'].strip(), validate=True)
        except (ValueError, binascii.Error):
            reject('Sec-WebSocket-Key')
        else:
            if len(key) != 16:
                reject('Sec-WebSocket-Key')

    def do_GET_ws(self, session_id: str, since: str = "0", *, snapshot_view: str = "full") -> None:
        """把连接升级为 WebSocket，并驱动会话的消息循环。"""
        cursor = event_cursor(since)
        key = self.headers.get("Sec-WebSocket-Key", "").strip()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept_key(key))
        self.end_headers()

        sock = self.connection
        conn = WebSocketConnection(sock)
        conn.snapshot_view = snapshot_view
        if self.ctx.shared_access is not None:
            def authorized():
                try:
                    current = self.ctx.shared_access.identity(self.headers)
                    if current != self._shared_identity:
                        return False
                    self.ctx.shared_access.require_read(session_id, current)
                    return True
                except ApiError:
                    return False
            conn.access_check = authorized
            from .shared_projection import frame
            conn.payload_projection = frame
        self.ws_clients.add(conn)
        hub = self.ctx.hub

        async def run() -> None:
            unsubscribe_access = None
            try:
                last_seq = hub.last_seq(session_id)
                from .views import session_detail
                snapshot = session_detail(self.harness, session_id, view=conn.snapshot_view)
                hub.subscribe(session_id, conn)
                await conn.send_json(
                    {
                        "type": "ready",
                        "session_id": session_id,
                        "last_seq": last_seq,
                        "replay": hub.replay(session_id, cursor),
                        "pending": len(self.harness.runtime.queue(session_id)),
                        **{key: value for key, value in self.harness.runtime.queue_control(session_id).items()
                           if key in {'paused', 'pause_reason', 'updated_at', 'waiting_reason'}},
                        **hub.recovery(session_id, cursor),
                        "snapshot": snapshot,
                        "draft": hub.draft(session_id),
                        "member_previews": hub.member_previews(session_id),
                        "generation": hub.generation(session_id),
                        "running": session_id in self.harness.runtime.active,
                    }
                )
                if conn.access_check is not None:
                    def changed(event):
                        if event.type == 'note' and event.data.get('kind') in {'collaboration/access', 'collaboration/credential'} and not conn.access_check():
                            conn._abort()
                    unsubscribe_access = self.harness.get_session(session_id).subscribe(changed)
                    if not conn.access_check():
                        conn._abort()
                try:
                    while not conn.closed:
                        try:
                            frame = await conn.read_message()
                        except ProtocolError as exc:
                            await conn.send_close(exc.code, str(exc))
                            return
                        if frame is None:
                            return
                        if frame.opcode == OP_PING:
                            await conn.send_pong(frame.payload)
                            continue
                        if frame.opcode == OP_PONG:
                            continue
                        if frame.opcode == OP_CLOSE:
                            code, reason = _safe_close(frame.payload)
                            await conn.send_close(code or CLOSE_NORMAL, reason)
                            return
                        if frame.opcode != OP_TEXT:
                            continue
                        try:
                            payload = json.loads(frame.payload.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            await conn.send_json({"type": "error", "message": tr('python.server.b7f79d0c78ac')})
                            continue
                        if isinstance(payload, dict):
                            await self._handle_ws_payload(conn, session_id, payload)
                except asyncio.CancelledError:
                    logger.debug(tr('log.0c45c515d847'))
                    raise
                except Exception:
                    logger.exception(tr('log.145fa9f1114a'))
                    with contextlib.suppress(Exception):
                        await conn.send_json({
                            "type": "error", "code": "internal/error",
                            "message": tr('python.server.internal_error'),
                        })
            finally:
                if unsubscribe_access is not None:
                    unsubscribe_access()
                hub.unsubscribe(session_id, conn)
                self.ws_clients.discard(conn)
                await conn.close()

        future = asyncio.run_coroutine_threadsafe(run(), self.loop)
        try:
            future.result()
        except concurrent.futures.CancelledError:
            # stop() 取消了仍在跑的对话循环，属于正常关闭路径。
            logger.debug(tr('log.53cbf102b445'))
        finally:
            self.ws_clients.discard(conn)
            # 订阅仅由所属循环的run.finally撤销，HTTP线程不再次操作异步队列。
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

    async def _handle_ws_payload(self, conn: Any, session_id: str, payload: dict) -> None:
        """长连接上的每次新操作采用当前设置，避免连接建立时冻结界面错误语言。"""
        from ..core.language import current_language, resolve_language
        token = current_language.set(resolve_language(self.harness.config.language).effective_language)
        try:
            await self._handle_ws_payload_in_language(conn, session_id, payload)
        finally:
            current_language.reset(token)

    async def _handle_ws_payload_in_language(
        self, conn: Any, session_id: str, payload: dict
    ) -> None:
        """处理一条 WebSocket 应用层消息。"""
        if self.ctx.shared_access is not None:
            if conn.access_check is None or not conn.access_check():
                await conn._abort_io()
                return
            if payload.get('action') not in {'ping', 'resume'}:
                await conn.send_json({'type': 'error', 'code': 'access/read-only', 'message': public_error_message('access/read-only', 'access/read-only')})
                return
        def failure_payload(exc: HarnessError) -> dict:
            """本地化已知机器码文案，保留结构化诊断和请求回执。"""
            result = exc.to_dict()
            result['message'] = public_error_message(exc.message, exc.code)
            return result

        action = payload.get("action")
        if action == "send":
            text = payload.get("text", "")
            refs = payload.get("attachments")
            request_id = payload.get("request_id")
            try:
                if not isinstance(text, str):
                    raise HarnessError(tr('web.input.invalid_text'), code='run/invalid')
                text = text.strip()
                # 纯附件消息保留空正文，不把非文本转换成用户输入。
                if not text and (refs is None or refs == []):
                    return
                if request_id is not None and (not isinstance(request_id, str) or not request_id or len(request_id) > 128):
                    raise HarnessError(tr('python.server.ddb6bc234a7f'), code="run/invalid")
                preview, prompt_input = self._prepare_turn_input(session_id, text, refs)
                record = self.harness.runtime.submit(session_id, preview, key=request_id, request_id=request_id, prompt_input=prompt_input, recipients=payload.get("recipients"), recipient_team=payload.get("recipient_team"), selection_revision=payload.get("selection_revision"), recipient_identities=payload.get("recipient_identities"))
                if request_id:
                    await conn.send_json({"type": "submission/accepted", "request_id": request_id, "run_id": record.id})
                event = {"type": "queue_state", **self.harness.runtime.queue_control(session_id)}
                self.ctx.hub.publish(session_id, self.ctx.hub.stamp(session_id, event))
            except HarnessError as exc:
                await conn.send_json({"type": "error", **failure_payload(exc), "action": "send", "submitted": False, "request_id": request_id})
            return
        elif action == "answer":
            # 应答失败（缺 id / 未知 / 过期）只能回告给当前连接，
            # 不能抛出去把 WebSocket 循环打断，否则前端只看到一次静默断连。
            try:
                await self._answer_question(conn, payload)
            except QuestionError as exc:
                await conn.send_json({
                    "type": "error", "code": "question/invalid-answer",
                    "message": public_error_message(str(exc), "question/invalid-answer"),
                })
        elif action == "cancel":
            try:
                cancelled = await self.harness.cancel(session_id)
            except HarnessError as exc:
                await conn.send_json({"type": "error", **failure_payload(exc)})
            else:
                if not cancelled:
                    await conn.send_json({"type": "done"})
        elif action == "steer":
            try:
                text = payload.get("text", "")
                if not isinstance(text, str):
                    raise HarnessError(tr('web.input.invalid_text'), code='run/invalid')
                text, supplied = self._prepare_turn_input(session_id, text, payload.get("attachments"))
                result = await self.harness.steer(session_id, text, expected_turn=payload.get('expected_turn'),
                    request_id=payload.get('request_id'), attachments=(supplied or {}).get("attachment_refs", []),
                    original_text=(supplied or {}).get("text"))
                await conn.send_json({"type": "steer/accepted", **result})
            except (HarnessError, ValueError) as exc:
                code = exc.code if isinstance(exc, HarnessError) else "run/invalid"
                message = public_error_message(exc.message, code) if isinstance(exc, HarnessError) else tr('web.input.invalid_text')
                await conn.send_json({"type": "error", "message": message, "code": code, "action": "steer", "submitted": False, "request_id": payload.get("request_id")})
        elif action == "clear_queue":
            for row in self.harness.runtime.queue(session_id):
                self.harness.runtime.remove(row["id"])
            event = {"type": "queue_state", **self.harness.runtime.queue_control(session_id)}
            self.ctx.hub.publish(session_id, self.ctx.hub.stamp(session_id, event))
        elif action == "plan_mode":
            # 回合中途切模式是合法的：会排队到下一个 step 边界结算。
            try:
                active = payload.get("active")
                if not isinstance(active, bool):
                    raise HarnessError(tr('python.interaction.1f2b7a2fd995'), code='plan-mode/bad-active')
                state = await self.harness.set_plan_mode(session_id, active)
                await conn.send_json({"type": "plan_mode", **state})
            except HarnessError as exc:
                await conn.send_json({"type": "error", **failure_payload(exc)})
        elif action == "resume":
            try:
                since = event_cursor(payload.get("since", 0))
            except BadRequest as exc:
                await conn.send_json({'type': 'error', 'message': exc.message, 'code': exc.code, 'details': exc.details, 'action': 'resume'})
                return
            recovery = self.ctx.hub.recovery(session_id, since)
            if recovery["reset_required"] or payload.get("force_snapshot") is True:
                from .views import session_detail
                await conn.send_json({"type": "ready", **recovery, "snapshot": session_detail(self.harness, session_id, view=getattr(conn, "snapshot_view", "full")), "draft": self.ctx.hub.draft(session_id), "member_previews": self.ctx.hub.member_previews(session_id), "generation": self.ctx.hub.generation(session_id), **self.harness.runtime.queue_control(session_id)})
                return
            for event in self.ctx.hub.replay(session_id, since):
                await conn.send_json(event)
            await conn.send_json(
                {
                    "type": "resumed",
                    **self.harness.runtime.queue_control(session_id),
                    "last_seq": self.ctx.hub.last_seq(session_id),
                }
            )
        elif action == "ping":
            await conn.send_json({"type": "pong", "t": payload.get("t")})

    def _prepare_turn_input(self, session_id: str, text: str, refs: "list | None") -> tuple[str, dict | None]:
        """入队时物化文件，实际启动时才决定附件说明语言。"""
        if refs is None or refs == []:
            return text, None
        from .api.attachments import _store
        from .materialize import resolve_refs
        from .prompt import compose_prompt

        session = self.harness.get_session(session_id)
        cwd = session.header.cwd
        root = Path(cwd) if cwd else self.ctx.workspace_root()
        ctx = resolve_refs(_store(self.ctx), refs, workspace_root=root)
        from ..core.language import current_language, resolve_language
        token = current_language.set(resolve_language(self.harness.config.language).effective_language)
        try:
            from dataclasses import asdict
            structured = {"text": text, "parts": [asdict(part) for part in ctx.parts],
                          "attachment_refs": [item.to_json() for item in ctx.attachments]}
            return compose_prompt(text, ctx.parts), structured
        finally:
            current_language.reset(token)

    async def _answer_question(
        self,
        conn: Any | None,
        payload: dict,
    ) -> dict[str, Any]:
        """把一次用户应答喂给挂起的 ``ask_user_question`` 调用。

        WebSocket 与 HTTP 使用同一问题状态和通知入口。
        """
        question_id = payload.get("question_id")
        if not isinstance(question_id, str) or not question_id.strip():
            raise QuestionError(tr('python.server.e25920e0b3d5'))
        question_id = question_id.strip()
        try:
            result = await self.harness.questions.answer_and_notify(
                question_id, payload.get("answers")
            )
        except QuestionError:
            logger.debug(tr('log.7e62d4a7aa30'), question_id, exc_info=True)
            raise
        if conn is not None and not getattr(conn, "closed", False):
            await conn.send_json({"type": "question_answered", **result})
        return result

# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _safe_close(payload: bytes) -> tuple[int | None, str]:
    try:
        return decode_close_payload(payload)
    except ProtocolError:
        return CLOSE_PROTOCOL_ERROR, tr('python.server.945a83f8cc32')


def _drain_loop(loop: "asyncio.AbstractEventLoop") -> None:
    """收干净一个已停转的事件循环：取消挂起任务再关闭。

    只 ``loop.stop()`` 而不 ``close()``，会在进程退出时报
    ``ResourceWarning: unclosed event loop``。
    """
    errors: list[BaseException] = []
    try:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            if not task.cancelling():
                task.cancel()
        if pending:
            results = loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            errors.extend(result for result in results if isinstance(result, BaseException)
                          and not isinstance(result, asyncio.CancelledError))
    except BaseException as exc:
        errors.append(exc)
    try:
        loop.run_until_complete(loop.shutdown_asyncgens())
    except BaseException as exc:
        errors.append(exc)
    try:
        loop.close()
    except BaseException as exc:
        errors.append(exc)
    raise_cleanup_errors(errors)


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


def serve(
    harness: Any,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    background: bool = False,
    dist: str | os.PathLike[str] | None = None,
    shared_authenticator: Any = None,
    shared_browser_origin: str | None = None,
) -> Any:
    """启动 Web 服务。

    :param background: ``True`` 时在后台线程启动并返回服务器对象；
                       ``False`` 时阻塞运行（Ctrl-C 停止）。
    :param dist: 前端构建目录；``None`` 时自动探测。
    :param shared_authenticator: 显式共享受限入口的宿主凭据验证器；默认不启用。
    :param shared_browser_origin: 显式启用原生凭据 Cookie 交换的精确来源。
    """
    if shared_authenticator is not None and not callable(shared_authenticator):
        raise TypeError(tr('web.shared.authenticator_required'))
    shared = None
    if shared_browser_origin is not None and shared_authenticator is None:
        raise ValueError('access/browser-authenticator-required')
    if shared_authenticator is not None:
        from .shared_access import SharedAccess
        shared = SharedAccess(harness.session_access, shared_authenticator,
                              credentials=harness.session_credentials, browser_origin=shared_browser_origin)
    existing = getattr(getattr(harness, "runtime", None), "loop", None)
    owns_loop = existing is None or existing.is_closed() or not existing.is_running()
    thread = None
    if owns_loop:
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True, name="harness-loop")
    else:
        loop = existing
        if not background:
            raise RuntimeError(tr('python.server.6aeea5e7c6b2'))

    context = None
    server = None
    try:
        if owns_loop:
            thread.start()
        assets = StaticAssets.locate(dist)
        context = WebContext(
            harness=harness,
            loop=loop,
            hub=ConnectionHub(),
            assets=assets,
            dist_root=assets.root if assets else Path(dist).expanduser().resolve() if dist is not None else None,
        )
        context.shared_access = shared
        context.start_event_bridge()
        from .api import build_router

        handler = type(
            "BoundHandler",
            (Handler,),
            {
                "harness": harness,
                "loop": loop,
                "ws_clients": set(),
                "_web_ctx": context,
                "_router": build_router(context),
            },
        )
        server = ThreadingHTTPServer((host, port), handler)
        server.daemon_threads = True
        # 记住事件循环与上下文，stop() 需要它们收尾。
        server._harness_owns_loop = owns_loop
        server._harness_loop = loop
        server._harness_loop_thread = thread
        server._harness_ctx = context
        if owns_loop and hasattr(harness, 'runtime'):
            harness.runtime.claim_loop(loop)
        if background:
            threading.Thread(target=server.serve_forever, daemon=True, name='harness-http').start()
    except BaseException:
        # 只撤回本次启动拥有的监听和循环，不关闭宿主原有运行。
        if context is not None:
            context.stop_event_bridge()
        if server is not None:
            server.server_close()
        if owns_loop:
            if thread.is_alive():
                loop.call_soon_threadsafe(loop.stop)
                thread.join(timeout=5)
                if thread.is_alive():
                    raise RuntimeError(tr('web.startup.cleanup_failed'))
            _drain_loop(loop)
        raise
    url = f"http://{host}:{port}"
    if assets is None:
        logger.warning(tr('log.03145d9ff9cc'), "web/dist")
    logger.info(tr('log.11d729b0eab8'), url)

    if background:
        return server

    mode = tr('python.server.b4ffc645fafe') if assets is None else tr('python.server.def64a04e8f8')
    print(tr('python.server.0a0f1697c4e9', p0=f'{url}'))
    print(tr('python.server.3b6e12959102', p0=f'{mode}'))
    print(tr('python.server.e58a8c208a81'))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(tr('python.server.22bd90589eac'))
    finally:
        stop(server)
    return server


def stop(server: Any, *, timeout: float = 5.0) -> None:
    """彻底关闭 :func:`serve` 返回的服务器。

    ``ThreadingHTTPServer.shutdown()`` 只停 ``serve_forever``，不释放监听
    socket；直接返回会让 fd 一直挂着。这里把「停循环 → 关监听 → 断长连接
    → 停事件循环」四步补齐。
    """
    errors: list[BaseException] = []

    def attempt(operation) -> None:
        """某项失败后继续其他资源，最后统一传播原异常。"""
        try:
            operation()
        except BaseException as exc:
            errors.append(exc)

    loop = getattr(server, "_harness_loop", None)
    context = getattr(server, "_harness_ctx", None)
    if context is not None:
        context.shutting_down = True
        if getattr(context, 'mcp_service', None) is not None:
            attempt(context.mcp_service.stop)
    attempt(server.shutdown)
    attempt(server.server_close)
    if context is not None:
        attempt(context.stop_event_bridge)
        attempt(lambda: loop.call_soon_threadsafe(context.hub.close_all)
                if loop and loop.is_running() else context.hub.close_all())
    if loop is not None and not loop.is_closed() and getattr(server, "_harness_owns_loop", True):
        harness = getattr(context, 'harness', None)
        # 所属事件循环排空前，其服务必须释放进程。
        for service in (getattr(harness, '_superagent_workbench', None), getattr(harness, 'runtime', None), getattr(harness, 'automations', None),
                        getattr(harness, 'browser', None)):
            if service is None or not loop.is_running():
                continue
            attempt(lambda service=service: asyncio.run_coroutine_threadsafe(service.close(), loop).result(timeout=timeout))
        if loop.is_running():
            attempt(lambda: loop.call_soon_threadsafe(loop.stop))
        loop_thread = getattr(server, "_harness_loop_thread", None)
        if loop_thread is not None and loop_thread.is_alive():
            attempt(lambda: loop_thread.join(timeout=timeout))
        # 仍在运行时不能跨线程取消任务或关闭循环，保留句柄供显式重试。
        if loop.is_running() or (loop_thread is not None and loop_thread.is_alive()):
            errors.append(RuntimeError(tr('web.shutdown.loop_alive')))
        else:
            attempt(lambda: _drain_loop(loop))
    raise_cleanup_errors(errors)
    logger.debug(tr('log.b3b50a10987b'))


__all__ = [
    "Handler",
    "WebSocketConnection",
    "serve",
    "stop",
    "SEND_TIMEOUT",
    "MAX_CLIENT_MESSAGE",
    "MAX_REQUEST_BODY_BYTES",
    "bytes_response",
    "CLOSE_GOING_AWAY",
]

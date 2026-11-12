"""HTTP 传输层：只用标准库实现的流式 SSE 客户端。

为什么不用 ``httpx`` / ``aiohttp``？
* 目标是"体量最小"，``urllib`` 足够；
* :class:`Transport` 允许调用方提供独立的传输实现。

:class:`Transport` 抽象出"发一个 POST 拿一个字节流"的能力：
默认实现为 :class:`UrllibTransport`。
"""

from __future__ import annotations

from harness.core.text_encoding import diagnostic_text
from harness.core.cleanup import join_cleanup, raise_cleanup_errors

from harness.i18n import tr

import asyncio
import codecs
import errno
import http.client
import json
import ssl
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from ..core.errors import LlmError, LlmRequestError, LlmResponseError, LlmTimeoutError
from ..net import IPv4PreferredHTTPSConnection, ResponseProxy
from ..net.http import controlled_urlopen
from ..net.request_control import RequestControl
from .retry import parse_retry_after


@dataclass
class HttpResponse:
    """一次 HTTP 响应的最小描述。"""

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


# 诊断不能等待代理传完长度不受限的错误正文。
ERROR_BODY_BYTES = 4096
ERROR_BODY_SECONDS = 1.0


def network_failure(exc: Exception, *, phase: str) -> LlmError:
    """分类套接字故障，不重试 TLS 信任或配置错误。"""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, TimeoutError):
        return LlmTimeoutError(tr('python.transport.538cf9177df0'), phase=phase)
    transient = isinstance(reason, (ConnectionError, http.client.RemoteDisconnected,
                                    http.client.IncompleteRead, ssl.SSLEOFError)) or (
        isinstance(reason, OSError) and reason.errno in {
            errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE, errno.ETIMEDOUT,
            errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH,
        })
    if isinstance(reason, socket.gaierror):
        transient = reason.errno == socket.EAI_AGAIN
    if isinstance(reason, ssl.SSLCertVerificationError):
        transient = False
    return LlmRequestError(tr('python.transport.ee3219b35c35') + str(reason)[:500], transient=transient,
                           phase=phase, network_error=type(reason).__name__,
                           errno=getattr(reason, 'errno', None))


class Transport(ABC):
    """传输抽象。实现者只需提供 :meth:`post_stream`。"""

    @abstractmethod
    async def post_stream(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        timeout: float,
    ) -> AsyncIterator[bytes]:
        """发送 POST 请求，逐块产出响应体字节。

        :raises LlmError: 连接失败、超时或返回非 2xx。
        """


class UrllibTransport(Transport):
    """基于 ``urllib`` 的真实传输实现（放在线程里跑，避免阻塞事件循环）。"""

    def __init__(
        self,
        *,
        max_body_bytes: int | None = 32 * 1024 * 1024,
        prefer_ipv4: bool = True,
    ) -> None:
        if max_body_bytes is not None and (type(max_body_bytes) is not int or max_body_bytes <= 0):
            raise ValueError(tr('transport.invalid_body_limit'))
        self.max_body_bytes = max_body_bytes
        self.prefer_ipv4 = prefer_ipv4

    def _open_response(self, url, headers, body, timeout):
        """打开一个 POST 请求，返回可读流式字节的 response 对象。

        默认走 :class:`IPv4PreferredHTTPSConnection`：本机 DNS 常把 AAAA 记录排在
        前面，而很多宽带/企业网络的 IPv6 出口是"通但极慢"（实测 TTFB 40s+，
        甚至握手直接超时），而同一主机的 IPv4 只需 0.4s。

        这里只影响传输层的选路，不改变任何 HTTP 语义；``prefer_ipv4=False``
        可以退回标准库默认行为（例如在纯 IPv6 环境里）。
        """
        parsed = urllib.parse.urlsplit(url)
        if not self.prefer_ipv4 or parsed.scheme != "https":
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            return controlled_urlopen(request, timeout=timeout)

        conn = IPv4PreferredHTTPSConnection(
            parsed.hostname or "",
            port=parsed.port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        try:
            conn.request("POST", path, body=body, headers=headers)
            response = conn.getresponse()
        except Exception:
            conn.close()
            raise
        # 用代理包装，让 ``close()`` 同时释放底层 HTTPS 连接，
        # 否则每轮会泄一个 SSL 套接字（ResourceWarning）。
        return ResponseProxy(response, conn)

    async def post_stream(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        timeout: float,
    ) -> AsyncIterator[bytes]:
        async with aclosing(self._stream_response(
            lambda: self._open_response(url, headers, body, timeout), timeout=timeout)) as stream:
            async for chunk in stream:
                yield chunk

    async def _stream_response(
        self,
        opener: Callable[[], Any],
        *,
        timeout: float,
        on_response: Callable[[Any], None] | None = None,
        raise_for_status: bool = True,
    ) -> AsyncIterator[bytes]:
        """模型 POST 请求和有界网页请求共享套接字归属管理。"""
        loop = asyncio.get_running_loop()
        # 上下文交给执行器里的线程，用于在 finally 中关闭响应体。
        holder: dict[str, Any] = {}
        holder_lock = threading.Lock()
        cancelled = threading.Event()
        control = RequestControl(cancelled)

        def _open() -> None:
            """在执行器线程中发起请求，只负责拿到响应头（不读取整个体）。"""
            try:
                with control.scope():
                    response = opener()
            except urllib.error.HTTPError as exc:
                # 读取诊断前先取得套接字归属，让取消可以像中断成功流一样，
                # 中断缓慢响应或错误流。
                response = exc
            except urllib.error.URLError as exc:
                reason = exc.reason
                if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
                    raise LlmTimeoutError(tr('python.transport.86e3b977b52d', p0=f'{timeout}')) from exc
                raise network_failure(exc, phase="connect") from exc
            except TimeoutError as exc:
                raise LlmTimeoutError(tr('python.transport.86e3b977b52d', p0=f'{timeout}')) from exc
            with holder_lock:
                if not cancelled.is_set():
                    holder["response"] = response
                    return
            response.close()


        def _read_chunk(size: int) -> bytes:
            """从响应体读一小块字节（阻塞调用，只能在执行器线程运行）。

            必须用 ``read1`` 而不是 ``read``：``read(n)`` 会一直等到凑满 n 字节
            或流结束才返回，那会让首字延迟退化成"等整个回复生成完"；
            ``read1(n)`` 只要底层有数据就立刻返回，是真流式的关键。
            老式/包装过的响应对象可能没有 ``read1``，此时退回 ``read``。
            """
            response = holder["response"]
            read1 = getattr(response, "read1", None)
            if read1 is None:
                return response.read(size)
            return read1(size)

        def _close() -> None:
            """关闭响应体，释放连接。"""
            control.close()
            with holder_lock:
                response = holder.pop("response", None)
            if response is not None:
                try:
                    # shutdown 先解除另一个线程的阻塞读取，再关闭 BufferedReader。
                    reader = response.fp if isinstance(response, urllib.error.HTTPError) else response
                    active_socket = reader.fp.raw._sock
                    active_socket.shutdown(socket.SHUT_RDWR)
                    # Windows 的 shutdown 不取消已等待的 socket 读取；关闭句柄。
                    descriptor = active_socket.detach()
                    if descriptor >= 0:
                        socket.close(descriptor)
                except (AttributeError, OSError):
                    pass
                response.close()

        primary: BaseException | None = None
        try:
            try:
                # 在本任务内处理取消，避免嵌套超时等待改变调用方的取消归属。
                async with asyncio.timeout(timeout + 5):
                    # 连接错误在线程中解析资源，继承本次请求的语言上下文。
                    await asyncio.to_thread(_open)
            except asyncio.TimeoutError as exc:
                raise LlmTimeoutError(tr('python.transport.86e3b977b52d', p0=f'{timeout}')) from exc

            status = holder["response"].status
            if on_response is not None:
                on_response(holder["response"])
            if raise_for_status and (status < 200 or status >= 300):
                detail = bytearray()
                deadline = loop.time() + min(ERROR_BODY_SECONDS, timeout / 4)
                while len(detail) < min(ERROR_BODY_BYTES, self.max_body_bytes or ERROR_BODY_BYTES):
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    try:
                        async with asyncio.timeout(remaining):
                            chunk = await loop.run_in_executor(None, _read_chunk,
                                min(ERROR_BODY_BYTES, self.max_body_bytes or ERROR_BODY_BYTES) - len(detail))
                    except (TimeoutError, OSError, http.client.HTTPException):
                        # 状态码已经证明失败，部分诊断内容
                        # 不能改变已知 HTTP 错误是否重试的判断。
                        break
                    if not chunk:
                        break
                    detail.extend(chunk)
                message = diagnostic_text(detail)[:4000]
                raise LlmError(
                    tr('runtime.validation.d6cd15a48971', p0=status, p1=message or getattr(holder['response'], 'reason', '')),
                    transient=status >= 500 or status in {408, 429},
                    status=status,
                    retry_after=parse_retry_after(holder['response'].headers.get('Retry-After')),
                )

            # 真正的流式：每次只读一小块，读到什么就向上游交付什么。
            # 这样才能保证首字延迟（TTFB）受模型生成速度限制，
            # 也不会在内存里留下整个回复。
            total = 0
            while True:
                chunk = await loop.run_in_executor(None, _read_chunk, 4096)
                if not chunk:
                    break
                total += len(chunk)
                if self.max_body_bytes is not None and total > self.max_body_bytes:
                    raise LlmResponseError(
                        tr('python.transport.99b6f8371073', p0=f'{self.max_body_bytes}'),
                        code='llm/response-byte-limit', status=status, limit=self.max_body_bytes,
                    )
                yield chunk
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            primary = network_failure(exc, phase='read' if 'response' in holder else 'connect')
            raise primary from exc
        except BaseException as exc:
            primary = exc
            raise
        finally:
            cancelled.set()
            # 同一清理任务抵抗观察者重复取消，关闭失败不能伪装为正常结束。
            cleanup = loop.run_in_executor(None, _close)
            try:
                await join_cleanup(cleanup)
            except BaseException as failure:
                if primary is not None and not isinstance(failure, asyncio.CancelledError):
                    raise_cleanup_errors([primary, failure])
                raise


class SseParser:
    """把 SSE 字节流解析成 ``data:`` 行。

    规则遵循 ``text/event-stream``：
    * 空行表示一个事件结束；
    * ``data:`` 前缀后的内容（去掉一个前导空格）是事件数据；
    * ``[DONE]`` 是 OpenAI 约定的流结束标记。
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._data_lines: list[str] = []
        self._skip_lf = False
        # 增量解码器：SSE 字节块的边界可能正好切在多字节字符中间
        # （中文回复几乎必然发生），逐块独立解码会产生乱码。
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")

    def feed(self, chunk: bytes | str) -> list[str]:
        """喂入一块数据，返回本次可解析出的完整 ``data`` 字符串列表。"""
        try:
            text = self._decoder.decode(chunk, False) if isinstance(chunk, bytes) else chunk
        except UnicodeDecodeError as exc:
            raise LlmResponseError(tr('python.transport.a2128ac50ea4'),
                                   code='llm/invalid-utf8', transient=True) from exc
        # CR、LF、CRLF 均为合法行尾，CRLF 还可能跨网络分片。
        if self._skip_lf and text:
            if text.startswith("\n"):
                text = text[1:]
            self._skip_lf = False
        if text:
            self._skip_lf = text.endswith("\r")
        self._buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        events: list[str] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if not line:
                if self._data_lines:
                    events.append("\n".join(self._data_lines))
                    self._data_lines.clear()
            elif line.startswith("data:"):
                value = line[5:]
                self._data_lines.append(value[1:] if value.startswith(" ") else value)
        return events

    def finish(self) -> None:
        """流结束时拒绝不完整字符，避免静默丢失。"""
        try:
            self._decoder.decode(b'', final=True)
        except UnicodeDecodeError as exc:
            raise LlmResponseError(tr('python.transport.b5d1548778f7'),
                                   code='llm/invalid-utf8', transient=True) from exc



async def parse_sse_stream(
    stream: AsyncIterator[bytes],
    *,
    on_event: Callable[[str], Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """把字节流解析成 JSON 事件字典流。

    ``on_event`` 是可选的同步回调，用于记录原始事件（调试用）。
    """
    parser = SseParser()
    async for chunk in stream:
        for data in parser.feed(chunk):
            if on_event is not None:
                on_event(data)
            if data.strip() == "[DONE]":
                return
            if not data.strip():
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError as exc:
                raise LlmResponseError(tr('python.transport.a8d05569d57a', p0=f'{data[:200]!r}')) from exc
            if not isinstance(payload, dict):
                raise LlmResponseError(tr('python.transport.a8d05569d57a', p0=f'{data[:200]!r}'),
                                       code='llm/invalid-sse-event')
            yield payload
    parser.finish()


__all__ = [
    "HttpResponse",
    "Transport",
    "UrllibTransport",
    "SseParser",
    "parse_sse_stream",
]

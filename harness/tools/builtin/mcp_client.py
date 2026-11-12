"""MCP 客户端。

让模型把外部 Model Context Protocol 服务器的工具当成原生工具来调用，
工具名固定为 ``mcp__<serverName>__<rawToolName>``。

契约：

* **服务端限定的身份**——公开名只由 ``(serverName, rawName)`` 纯函数决定，
  绝不使用远端 ``serverInfo.name``（不可信、不唯一、会随升级变化）；
* **裸名才是线上名**——``tools/call`` 永远收到裸名，公开名从不发往服务端；
* **要么整代要么不变**——同步失败时保留上一代工具表；
* **退避重连**——500ms 起指数退避，30s 封顶，连续失败 10 次后放弃。
"""

from __future__ import annotations

from harness.i18n import tr
from harness.core.text_encoding import TextDecoder

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from concurrent.futures import Future
from typing import Any, Callable

from ...core.errors import LlmError
from ...core.cleanup import raise_cleanup_errors
from ..process_env import stdio_env
from ...core.jsonutil import content_hash
from ...llm.transport import SseParser, UrllibTransport

logger = logging.getLogger(__name__)

#: MCP 协议版本（取本实现支持的最新一档）。
PROTOCOL_VERSION = "2025-06-18"
#: 客户端标识，写进 ``initialize`` 的 ``clientInfo``。
CLIENT_NAME = "harness"
CLIENT_VERSION = "1.0.0"
#: 单次 ``tools/call`` / 资源请求的默认超时（毫秒）。
DEFAULT_TOOL_CALL_TIMEOUT_MS = 60_000
#: 公开工具名的最大长度，超出即视为有损。
MAX_PUBLIC_NAME_LEN = 64
#: 有损改名时追加的哈希长度。
HASH_SUFFIX_LEN = 12

_INVALID_NAME_CHARS = re.compile(r"[^0-9a-zA-Z_-]")
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class McpError(RuntimeError):
    """MCP 客户端的运行时错误（连接、协商、协议、调用）。"""


class McpConnectionLost(McpError):
    """底层连接已经断了——需要丢弃连接并触发重连。

    与普通的 :class:`McpError` 区分开：远端工具自己返回 ``isError`` 只是
    那一次调用失败，连接仍然是好的，既不该重连、也不该消耗重连预算。
    """


@dataclass
class McpServerConfig:
    """一个外部 MCP 服务器的连接配置。"""

    #: 命名空间，必须匹配 ``[A-Za-z0-9_-]{1,32}`` 且在同一范围内唯一。
    server_name: str
    #: ``stdio`` 或 ``streamable-http``。
    transport: str = "stdio"
    #: stdio：可执行文件。
    command: str = ""
    #: stdio：参数列表。
    args: list[str] = field(default_factory=list)
    #: stdio：额外环境变量，合并在被清洗过的环境之上。
    env: dict[str, str] = field(default_factory=dict)
    #: stdio：工作目录。
    cwd: str | None = None
    #: streamable-http：端点 URL。
    url: str = ""
    #: streamable-http：额外请求头。
    headers: dict[str, str] = field(default_factory=dict)
    #: 每次 ``tools/call`` 与资源请求的超时（毫秒）。
    tool_call_timeout_ms: int = DEFAULT_TOOL_CALL_TIMEOUT_MS
    #: 启动超时独立于工具调用；未声明时复用工具超时。
    startup_timeout_ms: int | None = None
    #: 服务声明允许及禁止的裸工具名称。
    enabled_tools: list[str] | None = None
    disabled_tools: list[str] = field(default_factory=list)
    #: 启动时连接/工具同步失败是否拒绝激活。
    fail_on_startup_error: bool = False
    #: 是否自动重连。
    reconnect_enabled: bool = True
    #: 首次重连延迟（毫秒），每次失败翻倍。
    reconnect_initial_delay_ms: int = 500
    #: 退避上限（毫秒），也是尝试预算的重置门槛。
    reconnect_max_delay_ms: int = 30_000
    #: 单次中断内允许的连续失败次数，超过则移除工具并停止重连。
    reconnect_max_attempts: int = 10

    def __post_init__(self) -> None:
        for name in ('server_name', 'transport', 'command', 'url'):
            if not isinstance(getattr(self, name), str):
                raise McpError(tr('mcp.config.invalid_field', field=name))
        if self.cwd is not None and not isinstance(self.cwd, str):
            raise McpError(tr('mcp.config.invalid_field', field='cwd'))
        for name in ('args', 'enabled_tools', 'disabled_tools'):
            value = getattr(self, name)
            if name == 'enabled_tools' and value is None:
                continue
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise McpError(tr('mcp.config.invalid_field', field=name))
        for name in ('env', 'headers'):
            value = getattr(self, name)
            if not isinstance(value, dict) or not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
                raise McpError(tr('mcp.config.invalid_field', field=name))
        for name in ('fail_on_startup_error', 'reconnect_enabled'):
            if type(getattr(self, name)) is not bool:
                raise McpError(tr('mcp.config.invalid_field', field=name))
        for name in ('tool_call_timeout_ms', 'startup_timeout_ms', 'reconnect_initial_delay_ms', 'reconnect_max_delay_ms', 'reconnect_max_attempts'):
            value = getattr(self, name)
            if name == 'startup_timeout_ms' and value is None:
                continue
            minimum = 1 if name in ('tool_call_timeout_ms', 'startup_timeout_ms') else 0
            if type(value) is not int or value < minimum:
                raise McpError(tr('mcp.config.invalid_field', field=name))
        if self.transport not in ("stdio", "streamable-http"):
            raise McpError(tr('mcp.config.invalid_transport', value=repr(self.transport)))
        if not _NAME_RE.fullmatch(self.server_name):
            raise McpError(tr('mcp.config.invalid_name', value=repr(self.server_name)))
        if self.transport == "stdio" and not self.command.strip():
            raise McpError(tr('mcp.config.command_required', server=self.server_name))
        if self.transport == "streamable-http" and not self.url.strip():
            raise McpError(tr('mcp.config.url_required', server=self.server_name))


def public_tool_name(server_name: str, raw_name: str) -> str:
    """把 ``(serverName, rawName)`` 映射成稳定的模型可见工具名。

    纯函数，且不做有损压缩：如果清洗后会与原文不同，追加 12 位 SHA-256
    摘要，保证不同身份永远不会塌缩成同一个名字。
    """
    raw_clean = _INVALID_NAME_CHARS.sub("_", raw_name)
    full = "mcp__" + server_name + "__" + raw_clean
    if raw_clean == raw_name and len(full) <= MAX_PUBLIC_NAME_LEN:
        return full
    seed = server_name + "\n" + raw_name
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:HASH_SUFFIX_LEN]
    stem = full[: MAX_PUBLIC_NAME_LEN - HASH_SUFFIX_LEN - 1]
    return stem + "_" + digest


# ---------------------------------------------------------------------------
# 传输层
# ---------------------------------------------------------------------------
class McpTransport:
    """JSON-RPC 2.0 传输的公共接口。"""

    async def start(self) -> None:
        """建立底层连接。"""

    async def close(self) -> None:
        """关闭底层连接，释放资源。"""

    async def send(self, message: dict[str, Any], *,
                   before_send: Callable[[], None] | None = None) -> None:
        """发送一条 JSON-RPC 消息（请求或通知）。"""
        raise NotImplementedError

    async def receive(self) -> dict[str, Any]:
        """接收一条 JSON-RPC 消息。"""
        raise NotImplementedError



class StdioTransport(McpTransport):
    """以子进程 + 行分隔 JSON 的方式跑一个 MCP 服务器。

    协议规定 stdio 上的消息是换行分隔的 JSON，不带 framing 头。stdout 只
    承载协议消息，因此 stderr 单独抽干到日志，避免管道写满后把子进程卡死。
    """

    def __init__(self, config: McpServerConfig) -> None:
        self._config = config
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()

    async def start(self) -> None:
        config = self._config
        try:
            creation = asyncio.create_task(asyncio.create_subprocess_exec(
                config.command,
                *config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=config.cwd or None,
                env=stdio_env(config.env),
            ))
            cancelled = False
            while True:
                try:
                    proc = await asyncio.shield(creation)
                    break
                except asyncio.CancelledError:
                    cancelled = True
        except OSError as exc:
            raise McpError(
                tr('python.mcp_client.03f460c04284', server=repr(config.server_name), command=repr(config.command), detail=str(exc))
            ) from exc
        self._proc = proc
        self._stderr_task = asyncio.create_task(self._drain_stderr(proc))
        if cancelled:
            await self.close()
            raise asyncio.CancelledError

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        """把子进程 stderr 逐行打进 debug 日志，并保证管道始终被抽干。"""
        decoder = TextDecoder()
        stream = proc.stderr
        if stream is None:
            return
        try:
            while True:
                line = await stream.readline()
                if not line:
                    decoder.decode(b'', final=True)
                    if decoder.error:
                        logger.debug(tr('encoding.protocol'))
                    break
                text = decoder.decode(line).rstrip()
                if text:
                    logger.debug(
                        "[mcp:%s] stderr: %s", self._config.server_name, text
                    )
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - stderr 抽干失败不应影响主流程
            logger.debug(tr('log.0dad06675e2d'), self._config.server_name)

    async def send(self, message: dict[str, Any], *,
                   before_send: Callable[[], None] | None = None) -> None:
        proc = self._require_proc()
        stdin = proc.stdin
        if stdin is None:
            raise McpError(tr('python.mcp_client.2b9cc781557f'))
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        if before_send is not None:
            before_send()
        try:
            stdin.write((payload + "\n").encode("utf-8"))
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
            raise McpConnectionLost(tr('python.mcp_client.5e804f0369f7', detail=str(exc))) from exc

    async def receive(self) -> dict[str, Any]:
        proc = self._require_proc()
        stdout = proc.stdout
        if stdout is None:
            raise McpError(tr('python.mcp_client.f266ea1cb840'))
        while True:
            try:
                line = await stdout.readline()
            except (asyncio.LimitOverrunError, ValueError):
                # 行过长：按协议这是畸形帧，放弃该连接而不是死循环。
                raise McpConnectionLost(tr('python.mcp_client.33832e966653'))
            if not line:
                raise McpConnectionLost(
                    tr('python.mcp_client.701a06716c37', code=proc.returncode)
                )
            try:
                text = line.decode("utf-8").strip()
            except UnicodeDecodeError as exc:
                raise McpConnectionLost(tr('encoding.protocol')) from exc
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                # 服务端偶尔会往 stdout 混入非协议日志，跳过而不是崩掉。
                logger.debug(tr('log.fe50cc172b62'), self._config.server_name, text[:200])
                continue
            if isinstance(parsed, dict):
                return parsed

    def _require_proc(self) -> asyncio.subprocess.Process:
        if self._proc is None:
            raise McpError(tr('python.mcp_client.9925aec06912'))
        return self._proc

    async def close(self) -> None:
        self._closed.set()
        proc = self._proc
        if proc is None:
            return
        task = self._stderr_task
        self._stderr_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if proc.stdin is not None:
            proc.stdin.close()
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
        try:
            async with asyncio.timeout(3):
                await proc.communicate()
        except asyncio.TimeoutError:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            try:
                async with asyncio.timeout(3):
                    await proc.communicate()
            except asyncio.TimeoutError as exc:
                raise McpError(tr('python.mcp_client.f28817e37533', pid=proc.pid)) from exc
        self._proc = None


class _McpHttpStream(UrllibTransport):
    """复用可取消的 HTTP 流，并公开本次事务的响应头。"""

    def __init__(self, method: str, max_body_bytes: int, *,
                 before_send: Callable[[], None] | None = None) -> None:
        super().__init__(max_body_bytes=max_body_bytes, prefer_ipv4=False)
        self.method = method
        self._before_send = before_send
        self._loop = asyncio.get_running_loop() if before_send is not None else None
        self.response_headers: dict[str, str] = {}

    def _authorize_open(self, timeout: float) -> None:
        """工作线程回到事件循环核验，不在线程中读取会话或工具状态。"""
        if self._before_send is None:
            return
        from ...net.request_control import current_request
        control = current_request()
        ready: Future[None] = Future()
        def check() -> None:
            if not ready.set_running_or_notify_cancel():
                return
            try:
                if control is not None:
                    control.check()
                self._before_send()
            except BaseException as exc:
                ready.set_exception(exc)
            else:
                ready.set_result(None)
        self._loop.call_soon_threadsafe(check)
        try:
            ready.result(timeout=timeout)
        finally:
            ready.cancel()
        if control is not None:
            control.check()

    def _open_response(self, url, headers, body, timeout):
        import urllib.error
        import urllib.request
        from ...net.http import controlled_urlopen

        self._authorize_open(timeout)
        try:
            response = controlled_urlopen(urllib.request.Request(
                url, data=body if self.method != "DELETE" else None, headers=headers, method=self.method,
            ), timeout=timeout)
        except urllib.error.HTTPError as exc:
            # 将响应纳入同一可取消流的归属管理，
            # 不能在注册前同步读取错误正文。
            response = exc
        self.response_headers = {key.lower(): value for key, value in response.headers.items()}
        return response


class StreamableHttpTransport(McpTransport):
    """以 Streamable HTTP 方式连接 MCP 服务器。

    每次请求都是一次 ``POST``，body 是单条 JSON-RPC 消息；``Accept`` 同时
    声明 JSON 与 SSE，因为规范允许服务端回 ``text/event-stream``。服务端
    在响应头 ``Mcp-Session-Id`` 里下发会话 id，后续请求必须原样带回。

    复用标准库流式 HTTP 执行器，逐条交付消息。收到对应响应就关闭流，
    超时或取消会解除阻塞读取并关闭套接字，通过内置网络传输实现。
    """

    #: 单次响应体上限（字节），防止恶意/失控服务器撑爆内存。
    MAX_RESPONSE_BYTES = 32 * 1024 * 1024

    def __init__(self, config: McpServerConfig) -> None:
        self._config = config
        self._session_id: str | None = None
        self._pending: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._initialized = False
        self._active_sends: set[asyncio.Task] = set()

    async def start(self) -> None:
        # HTTP 传输没有预连接阶段，真正的握手发生在第一次 initialize 请求。
        self._initialized = True

    async def send(self, message: dict[str, Any], *,
                   before_send: Callable[[], None] | None = None) -> None:
        if not self._initialized:
            raise McpError(tr('python.mcp_client.288498fc583d'))
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        headers.update(self._config.headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        task = asyncio.current_task()
        self._active_sends.add(task)
        stream = _McpHttpStream("POST", self.MAX_RESPONSE_BYTES, before_send=before_send)
        parser = SseParser()
        raw = bytearray()
        from contextlib import aclosing

        try:
            timeout = max(self._config.tool_call_timeout_ms, 1) / 1000
            async with asyncio.timeout(timeout), aclosing(stream.post_stream(
                self._config.url, headers=headers, body=body, timeout=timeout,
            )) as chunks:
                async for chunk in chunks:
                    session = stream.response_headers.get("mcp-session-id")
                    if session:
                        self._session_id = session
                    if "text/event-stream" in stream.response_headers.get("content-type", "").lower():
                        for data in parser.feed(chunk):
                            message_out = self._loads(data)
                            if message_out is None:
                                continue
                            await self._pending.put(message_out)
                            if ("id" in message and message_out.get("id") == message["id"]
                                    and "method" not in message_out):
                                return
                    else:
                        raw.extend(chunk)
                if raw:
                    message_out = self._loads(raw.decode("utf-8"))
                    if message_out is not None:
                        await self._pending.put(message_out)
        except LlmError as exc:
            if exc.details.get("status") is not None:
                raise McpError(str(exc)) from exc
            raise McpConnectionLost(tr('python.mcp_client.00cb7479f314', detail=str(exc))) from exc
        finally:
            self._active_sends.discard(task)

    def _loads(self, text: str) -> dict[str, Any] | None:
        if not text.strip():
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            logger.debug(tr('log.3eb3c4877674'), self._config.server_name)
            return None
        return parsed if isinstance(parsed, dict) else None

    async def receive(self) -> dict[str, Any]:
        if not self._initialized:
            raise McpError(tr('python.mcp_client.288498fc583d'))
        return await self._pending.get()

    async def close(self) -> None:
        """通知服务端会话结束；服务端不理会也不影响本地状态。"""
        self._initialized = False
        sends = [task for task in self._active_sends if task is not asyncio.current_task()]
        for task in sends:
            task.cancel()
        if sends:
            await asyncio.gather(*sends, return_exceptions=True)
        if not self._session_id:
            return
        session = self._session_id
        self._session_id = None
        headers = {
            "Mcp-Session-Id": session,
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        headers.update(self._config.headers)
        try:
            stream = _McpHttpStream("DELETE", 4096)
            from contextlib import aclosing
            timeout = min(3.0, max(self._config.tool_call_timeout_ms, 1) / 1000)
            async with asyncio.timeout(timeout), aclosing(stream.post_stream(
                self._config.url, headers=headers, body=b"", timeout=timeout,
            )) as chunks:
                async for _ in chunks:
                    pass
        except (OSError, LlmError, asyncio.TimeoutError):
            # DELETE 只是尽力而为，失败无需向调用方暴露。
            logger.debug(tr('log.13b0ac363fd3'), self._config.server_name)


def build_transport(config: McpServerConfig) -> McpTransport:
    """按配置构造传输层。"""
    if config.transport == "stdio":
        return StdioTransport(config)
    return StreamableHttpTransport(config)


# ---------------------------------------------------------------------------
# 连接与协议
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class McpRemoteTool:
    """远端服务器声明的一个工具。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)


def remote_tool_contract(tool: McpRemoteTool) -> str:
    """按发现时的完整声明冻结契约，不以连接代数代替权限依据。"""
    return content_hash({'name': tool.name, 'description': tool.description,
                         'input_schema': tool.input_schema, 'annotations': tool.annotations})


class McpConnection:
    """一条已协商好的 MCP 连接。

    生命周期是「start -> 若干 request -> close」。握手失败、协议违规、
    进程退出都抛 :class:`McpError`，由上层监督器决定是否重连。
    """

    def __init__(
        self, config: McpServerConfig, transport: McpTransport | None = None
    ) -> None:
        self._config = config
        self._transport = transport or build_transport(config)
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader: asyncio.Task[None] | None = None
        self._server_info: dict[str, Any] = {}
        self._protocol_version: str = PROTOCOL_VERSION
        self._instructions: str = ""
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._reply_tasks: set[asyncio.Task] = set()

    # -- 生命周期 --------------------------------------------------------
    async def open(self) -> None:
        """建连并完成 ``initialize`` 协商。"""
        if self._closed:
            raise McpConnectionLost(tr('python.mcp_client.98004566fa31'))
        if self._reader is not None:
            raise McpError(tr('python.mcp_client.4920711255b7'))
        try:
            async with asyncio.timeout(max(self._config.startup_timeout_ms or self._config.tool_call_timeout_ms, 1) / 1000):
                await self._transport.start()
                self._reader = asyncio.create_task(self._read_loop())
                await self._negotiate()
        except asyncio.TimeoutError as exc:
            await self.aclose()
            raise McpError(tr('python.mcp_client.015bcb4fab79', server=self._config.server_name)) from exc
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        """关闭连接，幂等。"""
        task = self._close_task
        if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
            self._closed = True
            task = self._close_task = asyncio.create_task(self._close())
        cancelled = False
        while True:
            try:
                await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    cancelled = True
                if task.cancelled():
                    # 关闭时可能取消所有任务，包括本次清理。
                    # 重试清理同一个传输句柄，不重启服务端，
                    # 也不在已取消任务上无限循环。
                    if self._close_task is task:
                        self._close_task = asyncio.create_task(self._close())
                    task = self._close_task
        if cancelled:
            raise asyncio.CancelledError

    async def _close(self) -> None:
        """所有调用方等待同一次清理，读取器失败后也如此。"""
        self._closed = True
        reader = self._reader
        self._reader = None
        if reader is not None:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
        for future in list(self._pending.values()):
            if not future.done():
                future.cancel()
        self._pending.clear()
        replies = list(self._reply_tasks)
        for task in replies:
            task.cancel()
        if replies:
            await asyncio.gather(*replies, return_exceptions=True)
        await self._transport.close()

    # -- 协议 ------------------------------------------------------------
    async def _negotiate(self) -> None:
        """走 ``initialize`` + ``notifications/initialized``。"""
        result = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        info = result.get("serverInfo")
        if isinstance(info, dict):
            self._server_info = info
        instructions = result.get("instructions")
        if isinstance(instructions, str):
            self._instructions = instructions
        await self.notify("notifications/initialized")

    async def _read_loop(self) -> None:
        """把服务端消息分派到等待中的请求。"""
        try:
            message_count = 0
            while True:
                message = await self._transport.receive()
                self._dispatch(message)
                message_count += 1
                if message_count % 64 == 0:
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except McpError:
            self._fail_all(McpConnectionLost(tr('python.mcp_client.7aad65c99e2e')))
            self._closed = True
        except Exception as exc:  # pragma: no cover - 传输层异常已被归一
            self._fail_all(McpConnectionLost(tr('python.mcp_client.301e317de0da', detail=str(exc))))
            self._closed = True

    def _dispatch(self, message: dict[str, Any]) -> None:
        message_id = message.get("id")
        if "method" in message:
            # 服务端请求标识与本客户端独立；通知没有标识，
            # 不得触发 HTTP 回复或生成发送任务。
            if "id" in message:
                method = message["method"]
                if method == "ping":
                    self._queue_reply({"jsonrpc": "2.0", "id": message_id, "result": {}})
                elif method == "roots/list":
                    from pathlib import Path
                    root = Path(self._config.cwd or os.getcwd()).resolve()
                    self._queue_reply({"jsonrpc": "2.0", "id": message_id,
                                       "result": {"roots": [{"uri": root.as_uri(), "name": root.name}]}})
                else:
                    self._reject_unsupported(message_id, method)
            return
        if not isinstance(message_id, int) or isinstance(message_id, bool):
            return
        future = self._pending.pop(message_id, None)
        if future is None or future.done():
            return
        if ("result" in message) == ("error" in message):
            future.set_exception(McpError(tr('python.mcp_client.4c231a68522c')))
            return
        error = message.get("error")
        if isinstance(error, dict):
            future.set_exception(McpError(_describe_error(error)))
            return
        result = message.get("result")
        if not isinstance(result, dict):
            future.set_exception(McpError(tr('python.mcp_client.641c0357083e')))
            return
        future.set_result(result)

    def _reject_unsupported(self, message_id: Any, method: Any) -> None:
        """对不支持的服务端请求回 -32601，避免对端一直等。"""
        logger.debug(
            tr('log.0e8b67780ca6'), self._config.server_name, method
        )
        reply = {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {"code": -32601, "message": tr('mcp.feedback.method_missing', method=method)},
        }
        self._queue_reply(reply)

    def _queue_reply(self, reply: dict[str, Any]) -> None:
        if self._closed:
            return
        if len(self._reply_tasks) >= 8:
            raise McpConnectionLost(tr('python.mcp_client.c8def60889df'))
        task = asyncio.create_task(self._safe_send(reply))
        self._reply_tasks.add(task)
        task.add_done_callback(self._reply_tasks.discard)

    async def _safe_send(self, message: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            async with asyncio.timeout(max(self._config.tool_call_timeout_ms, 1) / 1000):
                await self._transport.send(message)

    def _fail_all(self, error: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """发一条无 id 的通知。"""
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        try:
            async with asyncio.timeout(max(self._config.tool_call_timeout_ms, 1) / 1000):
                await self._transport.send(message)
        except asyncio.TimeoutError as exc:
            raise McpError(tr('python.mcp_client.227fbddfad99', method=method)) from exc

    async def request(
        self, method: str, params: dict[str, Any] | None = None, *,
        before_send: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """发一条请求并等它的响应，带超时。"""
        if self._closed:
            raise McpConnectionLost(tr('python.mcp_client.531814a5a49b'))
        self._next_id += 1
        request_id = self._next_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        timeout = max(self._config.tool_call_timeout_ms, 1) / 1000.0
        owner = asyncio.current_task()
        def authorize_send() -> None:
            # 子发送任务可能先于父任务收到取消；以调用方已请求的
            # 取消状态为准，避免排队的 HTTP 或 stdio 请求迟到发出。
            if owner.cancelling():
                raise asyncio.CancelledError
            if before_send is not None:
                before_send()
        send = asyncio.create_task(self._transport.send(message, before_send=authorize_send))
        try:
            async with asyncio.timeout(timeout):
                done, _ = await asyncio.wait({send, future}, return_when=asyncio.FIRST_COMPLETED)
                if future in done:
                    if not isinstance(future.exception(), McpConnectionLost):
                        await send  # 成功或远程错误时，都等待 HTTP 流关闭。
                    return future.result()
                await send
                return await future
        except asyncio.TimeoutError as exc:
            self._cancel_request(request_id, method, tr('mcp.feedback.timeout'))
            raise McpError(
                tr('mcp.feedback.call_timeout', timeout=int(self._config.tool_call_timeout_ms), method=method)
            ) from exc
        except asyncio.CancelledError:
            self._cancel_request(request_id, method, tr('mcp.feedback.cancelled'))
            raise
        finally:
            self._pending.pop(request_id, None)
            if not send.done():
                send.cancel()
            await asyncio.gather(send, return_exceptions=True)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                # 发送仍受阻时读取器可能失败；即使发送先失败，
                # 也须读取并处理读取器的终态异常。
                future.exception()

    def _cancel_request(self, request_id: int, method: str, reason: str) -> None:
        # 尽力发送协议取消，不重放 tools/call，也不延迟调用方。
        # 初始化不能以此方式取消。
        if method != "initialize" and not self._closed:
            with contextlib.suppress(McpError):
                self._queue_reply({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                   "params": {"requestId": request_id, "reason": reason}})

    # -- 能力 ------------------------------------------------------------
    async def list_tools(self) -> list[McpRemoteTool]:
        """发现远端工具，分页拉完。"""
        tools: list[McpRemoteTool] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            params = {"cursor": cursor} if cursor is not None else None
            result = await self.request("tools/list", params)
            if not isinstance(result, dict) or not isinstance(result.get('tools'), list):
                raise McpError(tr('mcp.catalog.invalid_page'))
            for item in result['tools']:
                if (not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name']
                        or not isinstance(item.get('description', ''), str)
                        or not isinstance(item.get('annotations', {}), dict)
                        or not isinstance(item.get('inputSchema'), dict)
                        or item['inputSchema'].get('type') != 'object'):
                    raise McpError(tr('mcp.catalog.invalid_tool', index=len(tools) + 1))
                tools.append(McpRemoteTool(name=item['name'], description=item.get('description', ''),
                    annotations=item.get('annotations', {}), input_schema=item['inputSchema']))
            if 'nextCursor' not in result:
                break
            cursor = result['nextCursor']
            if not isinstance(cursor, str):
                raise McpError(tr('mcp.catalog.invalid_cursor'))
            if cursor in seen:
                raise McpError(tr('mcp.catalog.repeated_cursor'))
            seen.add(cursor)
        return tools

    async def call_tool(self, raw_name: str, arguments: dict[str, Any], *,
                        before_send: Callable[[], None] | None = None) -> Any:
        """调用远端工具，**永远传裸名**。"""
        result = await self.request(
            "tools/call", {"name": raw_name, "arguments": arguments}, before_send=before_send
        )
        if isinstance(result.get("isError"), bool) and result["isError"]:
            raise McpError(_describe_tool_error(result))
        return result

    async def list_resources(self, *, cursor: str | None = None) -> dict[str, Any]:
        """列出服务端暴露的资源（``resources/list``）。"""
        params = {"cursor": cursor} if cursor else None
        return await self.request("resources/list", params)

    async def list_resource_templates(
        self, *, cursor: str | None = None
    ) -> dict[str, Any]:
        """列出资源模板（``resources/templates/list``）。"""
        params = {"cursor": cursor} if cursor else None
        return await self.request("resources/templates/list", params)

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """按 uri 读取单个资源的内容（``resources/read``）。"""
        return await self.request("resources/read", {"uri": uri})

    @property
    def server_info(self) -> dict[str, Any]:
        return dict(self._server_info)

    @property
    def instructions(self) -> str:
        """服务端说明，作为字面文本并入系统提示。"""
        return self._instructions

    @property
    def protocol_version(self) -> str:
        return self._protocol_version

    @property
    def closed(self) -> bool:
        """连接是否已关闭（含读循环因传输断开而自行结束的情况）。"""
        return self._closed


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []



def _describe_error(error: dict[str, Any]) -> str:
    code = error.get("code")
    message = error.get("message")
    data = error.get("data")
    if code is not None and message:
        text = tr('mcp.feedback.rpcCodeMessage', code=str(code), message=str(message))
    elif code is not None:
        text = tr('mcp.feedback.rpcCode', code=str(code))
    elif message:
        text = tr('mcp.feedback.rpcMessage', message=str(message))
    else:
        text = tr('python.mcp_client.6b07f132e6fd')
    if data is not None:
        text = tr('mcp.feedback.rpcData', error=text, data=json.dumps(data, ensure_ascii=False, default=str)[:400])
    return text


def _describe_tool_error(result: dict[str, Any]) -> str:
    """把 ``isError`` 的结果渲染成一句可读的失败原因。"""
    parts: list[str] = []
    for item in _as_list(result.get("content")):
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    detail = "\n".join(part for part in parts if part).strip()
    if detail:
        return tr('python.mcp_client.f3bfc8c8b8d6', detail=detail)
    return tr('python.mcp_client.0c27fb1f0fe5')


# ---------------------------------------------------------------------------
# 监督器：重连退避与整代切换
# ---------------------------------------------------------------------------
class McpServerSupervisor:
    """守住一台 MCP 服务器的连接，负责重连与工具表同步。

    对外只暴露「当前这一代工具」和「按公开名调用」。任何一次失败都不会让
    上一次成功同步的工具表变半截：要么整代替换，要么原样保留。
    """

    def __init__(self, config: McpServerConfig) -> None:
        self._config = config
        self._connection: McpConnection | None = None
        self._tools: dict[str, McpRemoteTool] = {}
        self._public_to_raw: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._reconnect_task: asyncio.Task[None] | None = None
        self._attempt = 0
        self._last_error: str = ""
        self._closing = False
        self._refresh_revision = 0

    # -- 属性 ------------------------------------------------------------
    @property
    def server_name(self) -> str:
        return self._config.server_name

    @property
    def connected(self) -> bool:
        connection = self._connection
        return connection is not None and not connection.closed

    @property
    def instructions(self) -> str:
        connection = self._connection
        return connection.instructions if connection is not None else ""

    def public_names(self) -> list[str]:
        """当前这一代公开工具名。"""
        return sorted(self._public_to_raw)

    def remote_tool(self, public_name: str) -> McpRemoteTool | None:
        raw = self._public_to_raw.get(public_name)
        return self._tools.get(raw) if raw else None

    def status(self) -> dict[str, Any]:
        """给 UI / 日志用的状态快照。"""
        return {
            "server": self._config.server_name,
            "transport": self._config.transport,
            "connected": self.connected,
            "tools": self.public_names(),
            "instructions": self.instructions,
            "reconnect_attempts": self._attempt,
            "last_error": self._last_error,
        }

    # -- 生命周期 --------------------------------------------------------
    async def connect(self) -> list[McpRemoteTool]:
        """首次连接并同步工具表。失败按 ``fail_on_startup_error`` 决定是否抛。"""
        try:
            return await self._connect_once()
        except McpError as exc:
            self._last_error = str(exc)
            if self._config.fail_on_startup_error:
                raise
            logger.warning(
                tr('log.a0c480058a3b'),
                self._config.server_name,
                exc,
            )
            self._schedule_reconnect()
            return []

    async def _connect_once(self) -> list[McpRemoteTool]:
        async with self._lock:
            if self._closing:
                raise McpError(tr('runtime.validation.4160f1087382'))
            await self._drop_connection()
            connection = McpConnection(self._config)
            try:
                await connection.open()
                remote_tools = await connection.list_tools()
                self._install_generation(remote_tools)
            except BaseException:
                await connection.aclose()
                raise
            self._connection = connection
            self._last_error = ""
            self._attempt = 0
            logger.info(
                tr('log.2113a0278156'),
                self._config.server_name,
                len(remote_tools),
                connection.protocol_version,
            )
            return remote_tools

    def _install_generation(self, remote_tools: list[McpRemoteTool]) -> None:
        """整代替换工具表；同名裸工具重复出现视为服务器非法。"""
        by_raw: dict[str, McpRemoteTool] = {}
        seen = set()
        for tool in remote_tools:
            if tool.name in seen:
                raise McpError(
                    tr('mcp.feedback.duplicateTool', server=repr(self._config.server_name), tool=repr(tool.name))
                )
            seen.add(tool.name)
            if tool.name in self._config.disabled_tools or (self._config.enabled_tools is not None and tool.name not in self._config.enabled_tools):
                continue
            by_raw[tool.name] = tool
        public: dict[str, str] = {}
        for raw in by_raw:
            name = public_tool_name(self._config.server_name, raw)
            if name in public:
                raise McpError(
                    tr('mcp.feedback.nameConflict', public=name, tool=raw)
                )
            public[name] = raw
        self._tools = by_raw
        self._public_to_raw = public

    async def refresh(self) -> list[McpRemoteTool]:
        """重新拉一次工具表（服务端工具列表变化之后）。"""
        if self._closing:
            raise McpError(tr('mcp.catalog_obsolete'))
        connection = self._connection
        if connection is None:
            return await self.connect()
        self._refresh_revision += 1
        revision = self._refresh_revision
        try:
            remote_tools = await connection.list_tools()
        except McpError as exc:
            async with self._lock:
                if self._connection is connection and not self._closing and revision == self._refresh_revision:
                    self._last_error = str(exc)
                    await self._on_failure()
            raise
        async with self._lock:
            if self._closing or self._connection is not connection or revision != self._refresh_revision:
                raise McpError(tr('mcp.catalog_obsolete'))
            self._install_generation(remote_tools)
        return remote_tools

    async def call(self, public_name: str, arguments: dict[str, Any], *, expected_contract: str,
                   execution_check: Callable[[], None] | None = None) -> Any:
        """按**公开名**调用远端工具。"""
        raw = self._public_to_raw.get(public_name)
        if raw is None:
            raise McpError(
                tr('mcp.feedback.unknownTool', tool=repr(public_name), server=self._config.server_name)
            )
        current = self.remote_tool(public_name)
        if current is None or remote_tool_contract(current) != expected_contract:
            raise McpError(tr('python.permissions.e6e4e44a4b91'))
        connection = self._connection
        if connection is None:
            async with self._lock:
                if self._connection is None and not self._closing:
                    self._last_error = tr('python.mcp_client.0ba92a350f8f')
                    await self._on_failure()
            raise McpError(
                tr('mcp.feedback.toolsUnavailable', server=repr(self._config.server_name))
            )
        def before_send() -> None:
            if execution_check is not None:
                execution_check()
            current = self.remote_tool(public_name)
            if (self._closing or self._connection is not connection or self._public_to_raw.get(public_name) != raw
                    or current is None or remote_tool_contract(current) != expected_contract):
                raise McpError(tr('python.permissions.e6e4e44a4b91'))
        try:
            return await connection.call_tool(raw, arguments, before_send=before_send)
        except McpConnectionLost as exc:
            # 旧调用的故障只能撤下其所属连接，不能关闭已经替换的新连接。
            async with self._lock:
                if self._connection is connection and not self._closing:
                    self._last_error = str(exc)
                    await self._drop_connection()
                    self._public_to_raw = {}
                    self._tools = {}
                    await self._on_failure()
            raise
        except McpError as exc:
            if self._connection is connection and not self._closing:
                self._last_error = str(exc)
            raise

    async def request_resource(
        self,
        method: str,
        *,
        cursor: str | None = None,
        uri: str | None = None,
    ) -> dict[str, Any]:
        """按方法名转发资源请求（``resources/list`` 等）。"""
        connection = self._connection
        if connection is None:
            raise McpError(
                tr('mcp.feedback.notConnected', server=repr(self._config.server_name))
            )
        if method == "resources/list":
            return await connection.list_resources(cursor=cursor)
        if method == "resources/templates/list":
            return await connection.list_resource_templates(cursor=cursor)
        if method == "resources/read":
            if not uri:
                raise McpError(tr('python.mcp_client.74a1165b710e'))
            return await connection.read_resource(uri)
        raise McpError(tr('python.mcp_client.311642c47e8f', method=method))

    async def _on_failure(self) -> None:
        """持有连接锁时记账；预算耗尽先回收原连接，不丢弃所属句柄。"""
        self._attempt += 1
        if not self._config.reconnect_enabled:
            return
        if self._attempt > self._config.reconnect_max_attempts:
            logger.warning(
                tr('log.80de53a2a5ee'),
                self._config.server_name,
                self._attempt - 1,
            )
            self._public_to_raw = {}
            self._tools = {}
            await self._drop_connection()
            return
        self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        if self._closing or not self._config.reconnect_enabled:
            return
        task = self._reconnect_task
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - 无事件循环时不排程
            return
        self._reconnect_task = loop.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """指数退避重连：500ms 起，30s 封顶。"""
        delay_ms = max(self._config.reconnect_initial_delay_ms, 1)
        ceiling_ms = max(self._config.reconnect_max_delay_ms, delay_ms)
        try:
            while not self._closing:
                if self._attempt > self._config.reconnect_max_attempts:
                    return
                await asyncio.sleep(delay_ms / 1000.0)
                if self._closing:
                    return
                try:
                    await self._connect_once()
                    return
                except McpError as exc:
                    self._last_error = str(exc)
                    self._attempt += 1
                    logger.info(
                        tr('log.5fef6fec0847'),
                        self._config.server_name,
                        self._attempt,
                        delay_ms,
                        exc,
                    )
                    delay_ms = min(delay_ms * 2, ceiling_ms)
        except asyncio.CancelledError:
            raise

    async def _drop_connection(self) -> None:
        connection = self._connection
        if connection is not None:
            await connection.aclose()
            if self._connection is connection:
                self._connection = None

    async def aclose(self) -> None:
        """停止重连并断开连接，幂等。"""
        self._closing = True
        task = self._reconnect_task
        self._reconnect_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        async with self._lock:
            self._public_to_raw = {}
            self._tools = {}
            await self._drop_connection()


class McpRegistry:
    """多台 MCP 服务器的容器，并提供 ``serverName -> 监督器`` 的解析。"""

    def __init__(self, configs: list[McpServerConfig] | None = None) -> None:
        self._supervisors: dict[str, McpServerSupervisor] = {}
        self._replacement_locks: dict[str, asyncio.Lock] = {}
        for config in configs or []:
            self.add(config)

    def add(self, config: McpServerConfig) -> McpServerSupervisor:
        """登记新服务器；已有连接必须经异步替换路径关闭。"""
        if config.server_name in self._supervisors:
            raise McpError(tr('runtime.validation.69818b441e74', p0=config.server_name))
        supervisor = McpServerSupervisor(config)
        self._supervisors[config.server_name] = supervisor
        return supervisor

    async def replace(self, config: McpServerConfig) -> McpServerSupervisor:
        """先关闭旧监督器，再发布新配置，不遗留重连任务。"""
        async with self._replacement_locks.setdefault(config.server_name, asyncio.Lock()):
            old = self._supervisors.get(config.server_name)
            if old is not None:
                await old.aclose()
                self._supervisors.pop(config.server_name, None)
            return self.add(config)

    def get(self, server_name: str) -> McpServerSupervisor:
        supervisor = self._supervisors.get(server_name)
        if supervisor is None:
            known = tr('ui.common.list_separator').join(sorted(self._supervisors)) or tr('python.mcp_client.c7bcc6d27f3a')
            raise McpError(
                tr('mcp.feedback.unknownServer', server=repr(server_name), known=known)
            )
        return supervisor

    @property
    def supervisors(self) -> list[McpServerSupervisor]:
        return list(self._supervisors.values())

    def instructions(self) -> list[str]:
        """所有在线服务器的字面说明，供系统提示拼接。"""
        return [s.instructions for s in self._supervisors.values() if s.instructions]

    def status(self) -> list[dict[str, Any]]:
        return [s.status() for s in self._supervisors.values()]

    async def connect_all(self) -> None:
        """并发连接所有服务器，慢的服务器不阻塞快的。"""
        if not self._supervisors:
            return
        results = await asyncio.gather(
            *(s.connect() for s in self._supervisors.values()),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def aclose(self) -> None:
        errors = []
        for supervisor in list(self._supervisors.values()):
            try:
                await supervisor.aclose()
            except BaseException as exc:
                errors.append(exc)
        raise_cleanup_errors(errors)


__all__ = [
    "CLIENT_NAME",
    "CLIENT_VERSION",
    "DEFAULT_TOOL_CALL_TIMEOUT_MS",
    "MAX_PUBLIC_NAME_LEN",
    "PROTOCOL_VERSION",
    "McpConnection",
    "McpConnectionLost",
    "McpError",
    "McpRegistry",
    "McpRemoteTool",
    "McpServerConfig",
    "McpServerSupervisor",
    "McpTransport",
    "StdioTransport",
    "StreamableHttpTransport",
    "build_transport",
    "public_tool_name",
]





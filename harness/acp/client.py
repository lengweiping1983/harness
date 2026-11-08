"""ACP v1 客户端，支持独立进程、双向请求和流式会话。

只声明实际支持的客户端能力。文件和终端由外部 Agent 自己管理，
权限请求必须由宿主回调明确选择；缺少回调时取消请求。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..i18n import tr
from ..tools.processes import stop_process
from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.text_encoding import TextDecoder
from ..tools.process_env import stdio_env


class AcpError(RuntimeError):
    """外部 Agent 启动、协商或请求失败。"""


def _reject_json_constant(_value: str) -> Any:
    """拒绝 Python JSON 解码器额外接受的非标准数值。"""
    raise AcpError(tr('acp.client.761c95d5aa14'))


def _validate_unicode(value: Any) -> None:
    """在协议边界拒绝孤立代理码点，避免外部消息破坏会话持久化。"""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode('utf-8')
            except UnicodeEncodeError as exc:
                raise AcpError(tr('acp.client.77f322a149f3')) from exc
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


@dataclass(frozen=True)
class AcpRunnerConfig:
    """一个外部进程的启动配置；不经 shell 拼接命令。"""

    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict, repr=False)
    request_timeout: float = 600.0
    max_frame_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        if not isinstance(self.command, str) or not self.command.strip() or '\0' in self.command:
            raise ValueError(tr('acp.client.4b0c651f5495'))
        if not isinstance(self.args, (list, tuple)) or any(not isinstance(v, str) or '\0' in v for v in self.args):
            raise ValueError(tr('acp.client.5fdc0b15dead'))
        if not isinstance(self.env, dict) or any(not isinstance(k, str) or not k or '=' in k or '\0' in k
                or not isinstance(v, str) or '\0' in v for k, v in self.env.items()):
            raise ValueError(tr('acp.client.fb979584b206'))
        if isinstance(self.request_timeout, bool) or not isinstance(self.request_timeout, (int, float)) or not math.isfinite(self.request_timeout) or self.request_timeout <= 0:
            raise ValueError(tr('acp.client.82f3faf6bc09'))
        if isinstance(self.max_frame_bytes, bool) or not isinstance(self.max_frame_bytes, int) or not 1024 <= self.max_frame_bytes <= 64 * 1024 * 1024:
            raise ValueError(tr('acp.client.9a01de889916'))


UpdateHandler = Callable[[dict[str, Any]], Awaitable[None]]
PermissionHandler = Callable[[dict[str, Any]], Awaitable[str | None]]


class AcpClient:
    """每个实例拥有一个 Agent 进程，可在其内建立多个独立会话。

同一会话不允许重叠 prompt，不同会话可并发。更新回调应快速完成，
权限回调在独立任务中等待，避免阻塞取消和其他会话的消息。
"""

    def __init__(self, config: AcpRunnerConfig, *, on_update: UpdateHandler | None = None,
                 on_permission: PermissionHandler | None = None) -> None:
        self.config = config
        self.on_update = on_update
        self.on_permission = on_permission
        self.capabilities: dict[str, Any] = {}
        self.stderr_tail = ''
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._stderr: asyncio.Task | None = None
        self._requests: dict[int, asyncio.Future] = {}
        self._permissions: dict[asyncio.Task, str] = {}
        self._sessions: set[str] = set()
        self._busy: set[str] = set()
        self._sequence = 0
        self._write_lock = asyncio.Lock()
        self._ready = False
        self._closed = False
        self._closing: asyncio.Task | None = None
        self._creation: asyncio.Task | None = None

    async def start(self, cwd: str | Path) -> None:
        """启动并协商协议；失败时收拢已创建的进程。"""
        if self._creation is not None or self._closed:
            raise AcpError(tr('acp.client.771abad7d347'))
        directory = Path(cwd).resolve(strict=True)
        if not directory.is_dir():
            raise AcpError(tr('acp.client.fc5d85b288b3'))
        creation = asyncio.create_task(asyncio.create_subprocess_exec(
            self.config.command, *self.config.args, cwd=str(directory), env=stdio_env(self.config.env),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=self.config.max_frame_bytes + 1, start_new_session=os.name != 'nt'))
        self._creation = creation
        cancelled = False
        try:
            while True:
                try:
                    self._proc = await asyncio.shield(creation)
                    break
                except asyncio.CancelledError:
                    if creation.cancelled():
                        raise
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError
            if self._closed:
                raise AcpError(tr('acp.client.55374f4cc69b'))
            self._reader = asyncio.create_task(self._read())
            self._stderr = asyncio.create_task(self._drain_stderr())
            result = await self._request('initialize', {
                'protocolVersion': 1, 'clientCapabilities': {},
                'clientInfo': {'name': 'harness', 'version': '1.0.0'},
            })
            if type(result.get('protocolVersion')) is not int or result['protocolVersion'] != 1:
                raise AcpError(tr('acp.client.f7cfb75d24d3'))
            capabilities = result.get('agentCapabilities', {})
            if not isinstance(capabilities, dict):
                raise AcpError(tr('acp.client.1bc0016e9bd8'))
            self.capabilities = capabilities
            self._ready = True
        except BaseException:
            await self.close()
            raise

    async def new_session(self, cwd: str | Path) -> str:
        """建立独立上下文，工作目录必须存在且为绝对路径。"""
        if not self._ready:
            raise AcpError(tr('acp.client.b1c79ed39c7e'))
        directory = Path(cwd)
        if not directory.is_absolute() or not directory.is_dir():
            raise AcpError(tr('acp.client.334a593b86d3'))
        result = await self._request('session/new', {'cwd': str(directory.resolve()), 'mcpServers': []})
        session_id = result.get('sessionId')
        if not isinstance(session_id, str) or not session_id or session_id in self._sessions:
            raise AcpError(tr('acp.client.d1e188024d1f'))
        self._sessions.add(session_id)
        return session_id

    async def prompt(self, session_id: str, text: str) -> dict[str, Any]:
        """发送一轮消息；超时或取消会终止连接以阻止后台继续修改文件。"""
        self._require_session(session_id)
        if not isinstance(text, str) or not text.strip():
            raise AcpError(tr('acp.client.ec0f3970f051'))
        if session_id in self._busy:
            raise AcpError(tr('acp.client.175506552530'))
        self._busy.add(session_id)
        try:
            return await self._request('session/prompt', {
                'sessionId': session_id, 'prompt': [{'type': 'text', 'text': text}],
            })
        except (asyncio.CancelledError, TimeoutError):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.cancel(session_id), 1)
            await self.close()
            raise
        finally:
            self._busy.discard(session_id)

    def _require_session(self, session_id: str) -> None:
        if not self._ready or session_id not in self._sessions:
            raise AcpError(tr('acp.client.ee0471f84523'))

    async def cancel(self, session_id: str) -> None:
        """按协议请求停止当前会话，并取消其待决权限选择。"""
        self._require_session(session_id)
        for task, owner in list(self._permissions.items()):
            if owner == session_id:
                task.cancel()
        await self._send({'jsonrpc': '2.0', 'method': 'session/cancel', 'params': {'sessionId': session_id}})

    async def _send(self, message: dict) -> None:
        proc = self._proc
        if self._closed or proc is None or proc.stdin is None:
            raise AcpError(tr('acp.client.3041b8ac6fcf'))
        encoded = (json.dumps(message, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
        if len(encoded) > self.config.max_frame_bytes:
            raise AcpError(tr('acp.client.796b0b1589d9'))
        async with self._write_lock:
            if self._closed or self._proc is not proc:
                raise AcpError(tr('acp.client.3041b8ac6fcf'))
            try:
                proc.stdin.write(encoded)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise AcpError(tr('acp.client.8f7f0c95ac78')) from exc

    async def _request(self, method: str, params: dict) -> dict:
        self._sequence += 1
        request_id = self._sequence
        future = asyncio.get_running_loop().create_future()
        self._requests[request_id] = future
        try:
            await self._send({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params})
            return await asyncio.wait_for(future, self.config.request_timeout)
        finally:
            self._requests.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def _read(self) -> None:
        """唯一读循环负责分发双向消息，协议损坏立即结束全部请求。"""
        try:
            assert self._proc is not None and self._proc.stdout is not None
            while True:
                try:
                    line = await self._proc.stdout.readline()
                except ValueError as exc:
                    # StreamReader 在超限时先于长度检查抛出 ValueError。
                    raise AcpError(tr('acp.client.65516b50779b')) from exc
                if not line:
                    raise AcpError(tr('acp.client.870782a7521e'))
                if len(line) > self.config.max_frame_bytes:
                    raise AcpError(tr('acp.client.65516b50779b'))
                # bytes 形式的 json.loads 会接受 UTF-16/32，ACP 只允许 UTF-8。
                try:
                    message = json.loads(line.decode('utf-8'), parse_constant=_reject_json_constant)
                except UnicodeDecodeError as exc:
                    raise AcpError(tr('acp.client.19582a180dbc')) from exc
                _validate_unicode(message)
                if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                    raise AcpError(tr('acp.client.761c95d5aa14'))
                if 'method' in message:
                    await self._dispatch(message)
                else:
                    request_id = message.get('id')
                    if type(request_id) is not int:
                        raise AcpError(tr('acp.client.0f3f4d08cd4f'))
                    future = self._requests.get(request_id)
                    if future is None or future.done():
                        continue
                    if 'error' in message:
                        future.set_exception(AcpError(tr('acp.client.request_failed', error=message['error'])))
                    elif isinstance(message.get('result'), dict):
                        future.set_result(message['result'])
                    else:
                        raise AcpError(tr('acp.client.1bc0016e9bd8'))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._ready = False
            for future in self._requests.values():
                if not future.done():
                    future.set_exception(AcpError(str(exc)))
            # 协议故障不能让失去监督的 Agent 留在后台。
            await self.close()

    async def _dispatch(self, message: dict) -> None:
        params = message.get('params', {})
        if not isinstance(params, dict):
            raise AcpError(tr('acp.client.3411b02b4588'))
        if 'id' in message:
            if message['method'] == 'session/request_permission' and params.get('sessionId') in self._busy:
                task = asyncio.create_task(self._permission(message['id'], params))
                self._permissions[task] = params['sessionId']
                task.add_done_callback(self._permissions.pop)
            else:
                await self._send({'jsonrpc': '2.0', 'id': message['id'],
                                  'error': {'code': -32601, 'message': tr('feedback.456ffbdf9049')}})
        elif message['method'] == 'session/update' and params.get('sessionId') in self._sessions:
            if self.on_update:
                await self.on_update(params)

    async def _permission(self, request_id: Any, params: dict) -> None:
        """只接受本次请求提供的 optionId，拒绝伪造选择或自动提权。"""
        outcome = {'outcome': 'cancelled'}
        try:
            if self.on_permission:
                selected = await self.on_permission(params)
                options = params.get('options', [])
                if selected is not None and any(isinstance(option, dict) and option.get('optionId') == selected for option in options):
                    outcome = {'outcome': 'selected', 'optionId': selected}
        except (Exception, asyncio.CancelledError):
            pass
        with contextlib.suppress(Exception):
            await self._send({'jsonrpc': '2.0', 'id': request_id, 'result': {'outcome': outcome}})

    async def _drain_stderr(self) -> None:
        """持续排空 stderr，保留有界诊断尾部，不写入协议输出。"""
        assert self._proc is not None and self._proc.stderr is not None
        decoder = TextDecoder()
        while chunk := await self._proc.stderr.read(4096):
            self.stderr_tail = (self.stderr_tail + decoder.decode(chunk))[-16384:]
        self.stderr_tail = (self.stderr_tail + decoder.decode(b'', final=True))[-16384:]
        if decoder.error:
            self.stderr_tail += tr('encoding.failure', channel='stderr', encoding=decoder.encoding, offset=decoder.error['offset'])

    async def close(self) -> None:
        """关闭连接并清理进程树；允许从读循环内部调用。"""
        if self._closing is None:
            self._closing = asyncio.create_task(self._finish_close(asyncio.current_task()))
        closing = self._closing
        try:
            await join_cleanup(closing)
        except BaseException:
            # 失败的清理可显式重试，仍在运行的原任务不允许被替换。
            if closing.done() and self._closing is closing and (closing.cancelled() or closing.exception() is not None):
                self._closing = None
            raise

    async def _finish_close(self, caller: asyncio.Task | None) -> None:
        """单个清理任务抵抗重复取消，不与发起清理的读循环互相等待。"""
        self._closed = True
        self._ready = False
        if self._creation is not None and not self._creation.cancelled():
            with contextlib.suppress(Exception):
                self._proc = await asyncio.shield(self._creation)
        tasks = [task for task in [self._reader, self._stderr, *self._permissions] if task is not None and task is not caller]
        for task in tasks:
            task.cancel()
        errors = []
        if self._proc is not None:
            try:
                await stop_process(self._proc)
            except BaseException as exc:
                errors.append(exc)
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            errors.extend(result for result in results if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError))
        self._reader = None
        self._stderr = None
        for future in self._requests.values():
            if not future.done():
                future.set_exception(AcpError(tr('acp.client.3041b8ac6fcf')))
        if self._proc is not None and self._proc.stdin is not None:
            try:
                self._proc.stdin.close()
            except BaseException as exc:
                errors.append(exc)
        raise_cleanup_errors(errors)

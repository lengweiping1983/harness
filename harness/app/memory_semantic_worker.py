"""所属进程内的本地语义检索；私有认证通道，不提供HTTP接口。"""
from __future__ import annotations

from harness.core.text_encoding import diagnostic_text

import asyncio
import contextlib
import hmac
import json
import os
import secrets
import re
import socket
import struct
import sys
from pathlib import Path

from ..core.cleanup import join_cleanup
from ..core.errors import HarnessError
from ..core.language import current_language
from ..i18n import tr
from ..tools.owned_process import spawn_owned
from ..tools.process_env import execution_env
from .config import DEFAULT_HOME

MAX_FRAME = 1048576


class _WorkerExited(Exception):
    """先完成所属进程与日志收尾，再构造对外诊断。"""


def frame(value):
    """通道仅接受有界JSON，不反序列化可执行对象。"""
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(raw) > MAX_FRAME:
        raise ValueError(tr('python.memory.semantic.frame_bound'))
    return struct.pack('!I', len(raw)) + raw


async def receive(reader):
    size = struct.unpack('!I', await reader.readexactly(4))[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError(tr('python.memory.semantic.frame_size'))
    return json.loads(await reader.readexactly(size))


class SemanticWorker:
    """每次只有一个请求拥有进程；取消排队者不打断其他会话的查询。"""

    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        self._gate = asyncio.Lock()
        self._cleanup = asyncio.Lock()
        self._process = None
        self._reader = self._writer = None
        self._drains = []
        self._tail = bytearray()
        self._closed = False
        self._active = None
        self._executable = None

    async def _drain(self, stream):
        while data := await stream.read(4096):
            self._tail.extend(data)
            del self._tail[:-8192]

    def _argv(self, port):
        return [sys.executable, '-m', __name__, str(port), str(self.cache_dir)]

    def _failure(self, *, exit_code=None, pid=None):
        detail = diagnostic_text(self._tail)[-2000:]
        diagnostic = {}
        if self._executable is not None:
            diagnostic.update(executable=self._executable, pid=pid, exit_code=exit_code,
                              exit_code_hex=f'0x{exit_code & 0xffffffff:08X}' if exit_code is not None else None)
        return HarnessError(tr('python.memory.semantic.exited', detail=detail),
                            code='memory/semantic-worker', **diagnostic)

    @staticmethod
    def _worker_environment(token: str, pythonpath: str) -> dict[str, str]:
        """构造语义 worker 环境：凭据不继承，导入路径显式注入。"""
        env = execution_env({
            'HARNESS_MEMORY_WORKER_KEY': token,
            'HF_HUB_OFFLINE': '1',
        })
        env['PYTHONPATH'] = pythonpath
        return env

    async def _start(self):
        token = secrets.token_hex(32)
        root = str(Path(__file__).resolve().parents[2])
        # 嵌入式宿主可能显式装配依赖路径，路径由可信宿主计算。
        paths = [root, *(str(Path(item or Path.cwd()).absolute()) for item in sys.path if isinstance(item, str))]
        env = self._worker_environment(token, os.pathsep.join(dict.fromkeys(paths)))
        if os.name == 'nt' and not any(key.casefold() == 'systemroot' for key in env):
            # 嵌入式调用方可能清空环境；从操作系统读取真实目录，不猜测安装盘。
            import ctypes
            buffer = ctypes.create_unicode_buffer(32768)
            size = ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
            if not 0 < size < len(buffer):
                raise ctypes.WinError()
            env['SystemRoot'] = buffer.value
        if os.name == 'nt' and not any(key.casefold() == 'userprofile' for key in env):
            if not (os.environ.get('HOMEDRIVE') and os.environ.get('HOMEPATH')):
                env['USERPROFILE'] = str(DEFAULT_HOME.parent)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            listener.setblocking(False)
            argv = self._argv(listener.getsockname()[1])
            self._executable = argv[0]
            self._process = await spawn_owned(argv, cwd=root, env=env)
            if self._closed:
                raise RuntimeError(tr('python.memory.semantic.closed'))
            self._drains = [asyncio.create_task(self._drain(stream))
                            for stream in (self._process.stdout, self._process.stderr)]
            accept = asyncio.create_task(asyncio.get_running_loop().sock_accept(listener))
            deadline = asyncio.get_running_loop().time() + 10
            connection = None
            try:
                while True:
                    try:
                        connection, _ = await asyncio.wait_for(asyncio.shield(accept), .1)
                        break
                    except TimeoutError:
                        if self._process.returncode is not None:
                            raise _WorkerExited from None
                        if asyncio.get_running_loop().time() >= deadline:
                            raise TimeoutError(tr('python.memory.semantic.startup_timeout')) from None
            finally:
                if not accept.done():
                    accept.cancel()
                await asyncio.gather(accept, return_exceptions=True)
                if connection is None and not accept.cancelled() and accept.exception() is None:
                    accept.result()[0].close()
            try:
                self._reader, self._writer = await asyncio.open_connection(sock=connection)
            except BaseException:
                connection.close()
                raise
            hello = await asyncio.wait_for(receive(self._reader), 10)
            if (not isinstance(hello, dict) or not isinstance(hello.get('token'), str)
                    or not re.fullmatch('[0-9a-f]{64}', hello['token'])
                    or not hmac.compare_digest(hello['token'], token)):
                raise ValueError(tr('python.memory.semantic.authentication'))
        finally:
            listener.close()

    async def _dispose(self):
        async with self._cleanup:
            process = self._process
            if process is None:
                return
            await process.stop_owned()
            if self._writer is not None:
                self._writer.close()
                # 已确认所属进程退出，通道复位是预期终止结果，不遮盖原始错误。
                with contextlib.suppress(ConnectionError):
                    await self._writer.wait_closed()
            if self._drains:
                await asyncio.gather(*self._drains)
            process.close()
            self._process = None
            self._reader = self._writer = None
            self._drains = []

    async def search(self, store, cwd, query, **options):
        async with self._gate:
            if self._closed:
                raise RuntimeError(tr('python.memory.semantic.closed'))
            self._active = asyncio.current_task()
            completed_error = None
            try:
                if self._process is None:
                    self._tail.clear()
                    await self._start()
                identity = secrets.token_hex(16)
                request = {'id': identity, 'path': str(store.path(cwd)), 'format': store.format,
                           'check_sources': store.check_sources, 'cwd': str(Path(cwd).resolve()), 'query': query,
                           'language': current_language.get(), **options}
                self._writer.write(frame(request))
                await self._writer.drain()
                response = await receive(self._reader)
                if not isinstance(response, dict) or response.get('id') != identity:
                    raise ValueError(tr('python.memory.semantic.identity'))
                if 'error' in response:
                    error = response['error']
                    if (not isinstance(error, dict) or set(error) != {'message', 'code'}
                            or not isinstance(error['message'], str) or not isinstance(error['code'], str)
                            or not error['code']):
                        raise ValueError(tr('python.memory.semantic.error_response'))
                    completed_error = HarnessError(error['message'], code=error['code'])
                elif not isinstance(response.get('result'), dict):
                    raise ValueError(tr('python.memory.semantic.result'))
            except (_WorkerExited, asyncio.IncompleteReadError, ConnectionError):
                # EOF 和进程退出可能先于管道读取完成，不能提前冻结空白诊断。
                process = self._process
                try:
                    # 停止前查询原根进程，不能把主动回收的退出码冒充崩溃原因。
                    exit_code = process.native.root_code() if process is not None else None
                finally:
                    await join_cleanup(asyncio.create_task(self._dispose()))
                raise self._failure(exit_code=exit_code, pid=process.pid if process is not None else None) from None
            except BaseException:
                await join_cleanup(asyncio.create_task(self._dispose()))
                raise
            finally:
                self._active = None
            # 匹配身份且完整接收的业务错误不表示进程损坏，不销毁其他会话的热缓存。
            if completed_error is not None:
                raise completed_error
            return response['result']

    async def aclose(self):
        """先封闭新请求，再回收当前归属进程，重复关闭不重复执行。"""
        self._closed = True
        if self._active is not None and self._active is not asyncio.current_task():
            self._active.cancel()
        await join_cleanup(asyncio.create_task(self._dispose()))
        async with self._gate:
            await join_cleanup(asyncio.create_task(self._dispose()))


def _receive_sync(channel):
    """工作进程使用阻塞套接字；父进程终止将连同原生推理一起结束。"""
    def exact(size):
        data = bytearray()
        while len(data) < size:
            part = channel.recv(size - len(data))
            if not part:
                raise EOFError
            data.extend(part)
        return data
    size = struct.unpack('!I', exact(4))[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError(tr('python.memory.semantic.frame_size'))
    return json.loads(exact(size))


def main():
    """只接受宿主启动时的一次性认证，向量与模型只留在所属进程内存。"""
    from .memory_semantic import LocalMemoryCandidates
    from .layered_memory import BoundMemory
    provider = LocalMemoryCandidates(sys.argv[2])
    token = os.environ.pop('HARNESS_MEMORY_WORKER_KEY')
    with socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=10) as channel:
        channel.settimeout(None)
        channel.sendall(frame({'token': token}))
        while True:
            try:
                request = _receive_sync(channel)
            except EOFError:
                return 0
            language = current_language.set(request['language'])
            try:
                store = BoundMemory(Path(request['path']), context_key='python.memory.context', format=request['format'])
                store.check_sources = request['check_sources']
                result = provider.search(store, request['cwd'], request['query'],
                    limit=request.get('limit', 5), offset=request.get('offset', 0), snapshot=request.get('snapshot'))
                response = {'id': request['id'], 'result': result}
            except Exception as exc:
                response = {'id': request['id'], 'error': {'message': str(exc)[:8192],
                    'code': exc.code if isinstance(exc, HarnessError) else 'memory/semantic-worker'}}
            finally:
                current_language.reset(language)
            channel.sendall(frame(response))


if __name__ == '__main__':
    raise SystemExit(main())

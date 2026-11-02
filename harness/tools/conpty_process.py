"""Windows ConPTY 的异步输入、持续输出与所属进程清理。"""
from __future__ import annotations

from harness.i18n import tr

import asyncio
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor

from .owned_process import AsyncPipe, create_owned, dispose
from .windows_conpty import WindowsConPTYProcess
from ..core.cleanup import join_cleanup


class ConPTYProcess:
    def __init__(self, native):
        self.native, self.pid = native, native.pid
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='harness-conpty')
        self.stdout = AsyncPipe(native.stdout, self.executor)
        self.stdin = self._transport = self
        self._pending = bytearray()
        self._write_lock = asyncio.Lock()
        self._control = asyncio.Lock()
        self._finalizer = weakref.finalize(self, dispose, native, self.executor)
        # 从创建时就观察整个 Job；自然退出也必须关闭控制台，否则输出不会 EOF。
        self._wait_future = self.executor.submit(self._wait_and_disconnect, native)

    @staticmethod
    def _wait_and_disconnect(native):
        code = native.wait()
        native.close_console()
        return code

    @property
    def cleanup_confirmed(self):
        return self.native.active_count() == 0 and self.native.root_code() is not None

    @property
    def returncode(self):
        return self.native.root_code() if self.cleanup_confirmed else None

    async def wait(self):
        return await asyncio.shield(asyncio.wrap_future(self._wait_future))

    def write(self, data: bytes):
        if not self._finalizer.alive or self.returncode is not None:
            raise BrokenPipeError(tr('terminal.transport.conpty_closed'))
        self._pending.extend(data)

    async def drain(self):
        async with self._write_lock:
            payload = bytes(self._pending)
            self._pending.clear()
            if not payload:
                return
            cancelled = threading.Event()
            def write():
                offset = 0
                while offset < len(payload) and not cancelled.is_set():
                    offset += self.native.stdin.write(payload[offset:offset + 4096])
            future = self.executor.submit(write)
            try:
                await asyncio.shield(asyncio.wrap_future(future))
            except BaseException:
                cancelled.set()
                self._pending.clear()
                # 写入前缀已不确定；终止所属会话，禁止超时后的后台输入继续执行。
                cleanup = asyncio.create_task(self.stop_owned())
                while True:
                    try:
                        await asyncio.shield(cleanup)
                        break
                    except asyncio.CancelledError:
                        continue
                raise

    def resize(self, cols: int, rows: int):
        self.native.resize(cols, rows)

    def kill(self):
        # 同步信号路径仅发出终止请求；异步关闭仍确认整个 Job 已退出。
        self.native._check(self.native.kernel.TerminateJobObject(self.native.job, 1))

    terminate = kill

    async def stop_owned(self):
        async def finish():
            if not self.cleanup_confirmed:
                await asyncio.wrap_future(self.executor.submit(self.native.stop))
            await self.wait()

        async with self._control:
            await join_cleanup(asyncio.create_task(finish()))

    def close(self):
        self._pending.clear()
        self._finalizer()


async def spawn_conpty(argv: list[str], *, cwd: str, env: dict[str, str]):
    """取消创建时也等待归属建立并清理，不留下未交付的终端。"""
    return await create_owned(lambda: ConPTYProcess(WindowsConPTYProcess(argv, cwd=cwd, env=env)))

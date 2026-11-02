"""可取消的所属进程执行器；句柄/未回收组长固定身份，不依赖事后 PID 查找。"""
from __future__ import annotations

from harness.i18n import tr

import asyncio
import contextlib
import contextvars
import os
import signal
import subprocess
import sys
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .processes import ProcessCleanupError
from ..core.cleanup import join_cleanup


class PosixGroupProcess:
    """在 waitid(WNOWAIT) 后保留组长，防止子进程存活期间复用进程组 ID。"""
    def __init__(self, argv: list[str], *, cwd: str, env: dict[str, str], shell: bool = False,
                 stdin: bool = False):
        self._proc = subprocess.Popen(argv[0] if shell else argv, cwd=cwd, env=env, shell=shell,
                                      stdin=subprocess.PIPE if stdin else subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
        self.pid, self.stdout, self.stderr = self._proc.pid, self._proc.stdout, self._proc.stderr
        self.stdin = self._proc.stdin
        self._code = None
        self._closed = False
        self._finished = False
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)

    def root_code(self) -> int | None:
        with self._lock:
            if self._code is None:
                result = os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if result is not None:
                    self._code = result.si_status if result.si_code == os.CLD_EXITED else -result.si_status
            return self._code

    def active_count(self) -> int:
        with self._lock:
            return self._scan_count()

    def _scan_count(self) -> int:
        raise NotImplementedError

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.monotonic() + timeout if timeout is not None else None
        interval = .02
        while True:
            with self._lock:
                if self._finished:
                    return self._code
                if self._scan_count() == 0:
                    if self.root_code() is None:
                        raise ProcessCleanupError(tr('python.owned_process.743d4d5f5360'), pid=self.pid)
                    self._code = self._proc.wait()
                    self._finished = True
                    self._changed.notify_all()
                    return self._code
                # 从 20ms 退避到 250ms；条件等待释放锁，停止操作可立即唤醒观察者。
                remaining = deadline - time.monotonic() if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    raise ProcessCleanupError(tr('python.owned_process.e27e67f232ba'), pid=self.pid)
                self._changed.wait(interval if remaining is None else min(interval, remaining))
                interval = min(interval * 2, .25)

    def stop(self, timeout: float = 5) -> None:
        with self._lock:
            if self._scan_count():
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.pid, signal.SIGKILL)
            self._changed.notify_all()
        self.wait(timeout)

    def close(self) -> None:
        if self._closed:
            return
        if self.active_count():
            self.stop()
        if self._proc.returncode is None:
            self.wait(5)
        for stream in (self.stdin, self.stdout, self.stderr):
            if stream is not None:
                stream.close()
        self._closed = True


class LinuxGroupProcess(PosixGroupProcess):
    """通过 Linux procfs 检查所属组内的活动进程。"""

    def _scan_count(self) -> int:
        if self._closed or self._finished:
            return 0
        count = 0
        # 不回收根进程：只要句柄仍持有归属，PID/PGID 就不能被新的组长复用。
        for directory in Path('/proc').iterdir():
            if not directory.name.isdigit():
                continue
            try:
                fields = (directory / 'stat').read_text(encoding='utf-8').rsplit(')', 1)[1].split()
            except (FileNotFoundError, ProcessLookupError):
                continue
            if int(fields[2]) == self.pid and fields[0] not in {'Z', 'X'}:
                count += 1
        return count


class AsyncPipe:
    def __init__(self, stream: Any, executor: ThreadPoolExecutor):
        self.stream, self.executor = stream, executor
        self.pending = None
        self.surplus = b''
        self.lock = asyncio.Lock()

    async def read(self, count: int) -> bytes:
        if count <= 0:
            if count == 0:
                return b''
            raise ValueError(tr('python.owned_process.598f9502880b'))
        async with self.lock:
            if self.surplus:
                result, self.surplus = self.surplus[:count], self.surplus[count:]
                return result
            if self.pending is None:
                self.pending = self.executor.submit(self.stream.read, count)
            # 取消观察者不取消内核读取；下一观察者接收同一结果，避免交接丢字节。
            data = await asyncio.shield(asyncio.wrap_future(self.pending))
            self.pending = None
            result, self.surplus = data[:count], data[count:]
            return result


def dispose(native, executor) -> None:
    try:
        native.close()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


class OwnedProcess:
    def __init__(self, native):
        self.native, self.pid = native, native.pid
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='harness-command')
        self.stdout, self.stderr = AsyncPipe(native.stdout, self.executor), AsyncPipe(native.stderr, self.executor)
        self._wait_future = None
        self._finalizer = weakref.finalize(self, dispose, native, self.executor)
        self._control = asyncio.Lock()

    @property
    def _transport(self):
        return self

    @property
    def cleanup_confirmed(self) -> bool:
        return self.native.active_count() == 0 and self.native.root_code() is not None

    @property
    def returncode(self) -> int | None:
        return self.native.root_code() if self.cleanup_confirmed else None

    async def wait(self) -> int:
        if self.cleanup_confirmed:
            return self.returncode
        if self._wait_future is None:
            self._wait_future = self.executor.submit(self.native.wait)
        return await asyncio.shield(asyncio.wrap_future(self._wait_future))

    async def stop_owned(self) -> None:
        async with self._control:
            if self.cleanup_confirmed:
                return
            await join_cleanup(asyncio.wrap_future(self.executor.submit(self.native.stop)))

    async def write_input(self, value: bytes) -> None:
        """写完一次匿名输入后关闭；调用方须持有等待直到所属进程收尾。"""
        stream = getattr(self.native, 'stdin', None)
        if stream is None:
            raise RuntimeError(tr('process.stdin.unavailable'))
        def write():
            with stream:
                pending = memoryview(value)
                while pending:
                    size = stream.write(pending)
                    if not size:
                        raise BrokenPipeError(tr('process.stdin.closed'))
                    pending = pending[size:]
        # 输入可能等待子进程读取，不能占满负责停止和输出的专属执行器。
        await asyncio.to_thread(write)

    def close(self) -> None:
        if not self._finalizer.alive:
            return
        if not self.cleanup_confirmed:
            raise ProcessCleanupError(tr('python.owned_process.76520cba567a'), pid=self.pid)
        self._finalizer()


async def create_owned(factory):
    """创建等待不注册为可被循环整体取消的子任务，保留迟到句柄归属。"""
    context = contextvars.copy_context()
    future = asyncio.get_running_loop().run_in_executor(None, context.run, factory)
    cancelled = False
    while True:
        try:
            process = await asyncio.shield(future)
            break
        except asyncio.CancelledError:
            cancelled = True
            if future.done():
                # 创建本身取消时传播原取消，不能在已取消结果上无限重试。
                process = future.result()
                break
    if cancelled:
        async def finish():
            await process.stop_owned()
            process.close()
        await join_cleanup(asyncio.create_task(finish()))
        raise asyncio.CancelledError
    return process


async def spawn_owned(argv: list[str], *, cwd: str, env: dict[str, str], shell: bool = False,
                      stdin: bool = False) -> OwnedProcess:
    if os.name == 'nt':
        from .windows_job import WindowsJobProcess
        factory = WindowsJobProcess
    elif sys.platform.startswith('linux') and hasattr(os, 'WNOWAIT'):
        factory = LinuxGroupProcess
    elif sys.platform == 'darwin':
        from .darwin_process import DarwinGroupProcess
        factory = DarwinGroupProcess
    else:
        raise ProcessCleanupError(tr('python.owned_process.32be47cfe23e'), platform=sys.platform)
    # 归属和子进程创建均在一个不可中断的同步段；取消时等创建收敛再清理。
    def create():
        options = {'stdin': True} if stdin else {}
        return OwnedProcess(factory(argv, cwd=cwd, env=env, shell=shell, **options))
    return await create_owned(create)

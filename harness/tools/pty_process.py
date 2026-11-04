"""仅依赖标准库的异步 POSIX 伪终端传输，支持背压。"""
from __future__ import annotations

from harness.i18n import tr
from ..core.cleanup import join_cleanup

import asyncio
import contextlib
import errno
import os
import signal
import struct
import sys
from pathlib import Path


class PtyProcess:
    def __init__(self, proc: asyncio.subprocess.Process, fd: int) -> None:
        self.proc = proc
        self.fd = fd
        self.stdout = self.stdin = self
        self._transport = self
        self._pending = bytearray()
        self._write_lock = asyncio.Lock()
        self._waiters: set[asyncio.Future] = set()
        self._eof = False
        os.set_blocking(fd, False)

    @property
    def pid(self):
        return self.proc.pid

    @property
    def returncode(self):
        return self.proc.returncode

    async def wait(self):
        return await self.proc.wait()

    def kill(self):
        self.proc.kill()

    async def _ready(self, *, writing=False):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._waiters.add(future)
        fd = self.fd
        add = loop.add_writer if writing else loop.add_reader
        remove = loop.remove_writer if writing else loop.remove_reader
        def wake():
            if not future.done():
                future.set_result(None)
        try:
            if fd < 0:
                raise BrokenPipeError(tr('terminal.transport.pty_closed'))
            add(fd, wake)
            await future
        finally:
            if fd >= 0 and self.fd == fd:
                remove(fd)
            self._waiters.discard(future)

    async def read(self, count: int) -> bytes:
        while self.fd >= 0 and not self._eof:
            try:
                data = os.read(self.fd, count)
                if not data:
                    self._eof = True
                return data
            except BlockingIOError:
                await self._ready()
            except OSError as exc:
                # Linux 在最后一个从端描述符关闭时返回 EIO，
                # macOS 返回流结束，两者均表示输出流正常结束。
                if exc.errno not in (errno.EIO, errno.EBADF):
                    raise
                self._eof = True
        return b''

    def write(self, data: bytes) -> None:
        if self.fd < 0:
            raise BrokenPipeError(tr('terminal.transport.pty_closed'))
        self._pending.extend(data)

    async def drain(self) -> None:
        async with self._write_lock:
            try:
                while self._pending:
                    if self.fd < 0:
                        raise BrokenPipeError(tr('terminal.transport.pty_closed'))
                    try:
                        size = os.write(self.fd, self._pending)
                        del self._pending[:size]
                    except BlockingIOError:
                        await self._ready(writing=True)
            except BaseException:
                # 输入超时或取消时，交付状态不确定。
                # 不能将未发送的尾部带入用户下一条已检查命令。
                self._pending.clear()
                raise

    def resize(self, cols: int, rows: int) -> None:
        import fcntl
        import termios
        if self.fd < 0:
            raise BrokenPipeError(tr('terminal.transport.pty_closed'))
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def signal_foreground(self, sig: int) -> None:
        if self.fd < 0:
            raise BrokenPipeError(tr('terminal.transport.pty_closed'))
        os.killpg(os.tcgetpgrp(self.fd), sig)

    async def stop_owned(self) -> None:
        # 交互式 Shell 将前台命令放入独立进程组。
        # 需同时停止该进程组和 Shell，进程退出后也要处理。
        # 后台任务也有独立进程组，应在终止 Shell 前记录后代进程，
        # 避免终止操作使它们成为孤儿进程。
        if self.returncode is None:
            if sys.platform == 'darwin':
                from ..core.macos_process import descendant_pids
                descendants = await asyncio.to_thread(descendant_pids, self.pid)
            else:
                scan = await asyncio.create_subprocess_exec(
                    '/bin/ps', '-axo', 'pid=,ppid=', stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    output, _ = await asyncio.wait_for(scan.communicate(), 2)
                except BaseException:
                    if scan.returncode is None:
                        scan.kill()
                    await scan.wait()
                    raise
                if scan.returncode != 0:
                    raise OSError(tr('terminal.transport.inspect_failed'))
                parents = {}
                for line in output.splitlines():
                    pid, parent = map(int, line.split())
                    parents.setdefault(parent, []).append(pid)
                descendants, pending = [], [self.pid]
                while pending:
                    children = parents.get(pending.pop(), [])
                    descendants.extend(children)
                    pending.extend(children)
            for pid in reversed(descendants):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
        if self.fd >= 0:
            with contextlib.suppress(ProcessLookupError, OSError):
                foreground = os.tcgetpgrp(self.fd)
                if foreground > 0 and os.getsid(foreground) == self.pid:
                    os.killpg(foreground, signal.SIGKILL)
        if self.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.pid, signal.SIGKILL)
            with contextlib.suppress(ProcessLookupError):
                self.proc.kill()
        await asyncio.wait_for(self.proc.wait(), 5)

    def close(self) -> None:
        if self.fd < 0:
            return
        fd, self.fd = self.fd, -1
        loop = asyncio.get_running_loop()
        loop.remove_reader(fd)
        loop.remove_writer(fd)
        os.close(fd)
        self._pending.clear()
        for future in self._waiters:
            if not future.done():
                future.set_result(None)


async def spawn_pty(argv: list[str], *, cwd: str, env: dict[str, str]) -> PtyProcess:
    """通过 exec 辅助程序启动，fork 与 exec 之间不运行 Python。"""
    import pty
    master, slave = pty.openpty()
    process = None
    spawn = None
    try:
        import fcntl
        import termios
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            sys.executable, str(Path(__file__).with_name('pty_exec.py')), *argv,
            stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env,
        ))
        process = await asyncio.shield(spawn)
        return PtyProcess(process, master)
    except BaseException:
        async def finish():
            nonlocal process
            try:
                if spawn is not None and process is None:
                    with contextlib.suppress(Exception):
                        process = await spawn
                if process is not None:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
            finally:
                os.close(master)
        await join_cleanup(asyncio.create_task(finish()))
        raise
    finally:
        os.close(slave)

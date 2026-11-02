"""取消时清理前台子进程树，不影响显式后台任务。"""
from __future__ import annotations

from harness.i18n import tr

import asyncio
import contextlib
import os
import signal
from typing import Any, Awaitable, Callable

from ..core.cleanup import join_cleanup

from ..core.errors import HarnessError


class ProcessCleanupError(HarnessError):
    code = "process/cleanup-failed"


async def stop_process(proc) -> None:
    owned = getattr(proc, 'stop_owned', None)
    if owned is not None:
        await owned()
        return
    if proc.returncode is not None:
        return
    if os.name == "nt":
        cleaner = await asyncio.create_subprocess_exec("taskkill", "/PID", str(proc.pid), "/T", "/F", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(cleaner.wait(), timeout=3)
        except BaseException:
            if cleaner.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    cleaner.kill()
                await asyncio.wait_for(cleaner.wait(), timeout=1)
            raise
        # taskkill /T /F 在子进程先于枚举退出（128）或恰在创建中尚未可终止（255）时
        # 也返回非零；退出码不能作为树仍存活的证据，统一以下方根进程的 kill+wait 为准。
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except TimeoutError as exc:
        raise ProcessCleanupError(tr('python.processes.d0d6da778004'), pid=proc.pid) from exc


async def start_process(spawn: Callable[[], Awaitable[Any]], *, stop: Callable[[Any], Awaitable[None]]) -> Any:
    """创建已开始后接管迟到句柄，清理完成才传播调用方的重复取消。"""
    owner = asyncio.current_task()

    async def begin():
        if owner.cancelling():
            raise asyncio.CancelledError
        return await spawn()

    startup = asyncio.create_task(begin())
    try:
        return await asyncio.shield(startup)
    except asyncio.CancelledError:
        async def reclaim():
            process = await startup
            await stop(process)
        await join_cleanup(asyncio.create_task(reclaim()))
        raise

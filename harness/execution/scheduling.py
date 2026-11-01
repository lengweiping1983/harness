"""执行器读写准入：允许读取重叠，等待中的独占调用优先。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class ExecutionGate:
    """同一资源域内共享读取；取消等待不遗留计数或占用。"""

    def __init__(self):
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    def locked(self):
        """是否存在已经进入执行区的调用。"""
        return self._writer or self._readers > 0

    @asynccontextmanager
    async def hold(self, *, shared=False):
        """独占调用排队后暂停新读取；既有读取结束后交接。"""
        async with self._condition:
            if shared:
                await self._condition.wait_for(lambda: not self._writer and not self._waiting_writers)
                self._readers += 1
            else:
                self._waiting_writers += 1
                try:
                    await self._condition.wait_for(lambda: not self._writer and not self._readers)
                    self._writer = True
                finally:
                    self._waiting_writers -= 1
                    self._condition.notify_all()
        try:
            yield
        finally:
            async with self._condition:
                if shared:
                    self._readers -= 1
                else:
                    self._writer = False
                self._condition.notify_all()

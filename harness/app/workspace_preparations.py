"""在同一事件循环内协调工作区准备与新任务启动。"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from harness.core.errors import HarnessError
from harness.i18n import tr


class WorkspacePreparations:
    """只持有短期宿主准备租约，不充当文件系统或跨进程锁。"""

    def __init__(self):
        self._leases: dict[str, tuple[Path, asyncio.Event]] = {}

    def _blocking(self, cwd):
        if not cwd or not self._leases:
            return []
        path = Path(cwd).resolve()
        return [event for root, event in self._leases.values()
                if path.is_relative_to(root) or root.is_relative_to(path)]

    def blocked(self, cwd) -> bool:
        return bool(self._blocking(cwd))

    def assert_available(self, cwd):
        if self.blocked(cwd):
            raise HarnessError(tr('workspace.preparation.busy'), code='git/environment-busy')

    def claim(self, cwd) -> str:
        # 宿主在取得租约前检查活动任务，检查与登记之间不得 await。
        asyncio.get_running_loop()
        self.assert_available(cwd)
        token = uuid.uuid4().hex
        self._leases[token] = (Path(cwd).resolve(), asyncio.Event())
        return token

    def release(self, token) -> bool:
        asyncio.get_running_loop()
        value = self._leases.pop(token, None)
        if value is None:
            return False
        value[1].set()
        return True

    async def wait(self, cwd):
        # 每个等待者拥有自己的事件等待句柄，取消不影响其他任务。
        while events := self._blocking(cwd):
            await events[0].wait()

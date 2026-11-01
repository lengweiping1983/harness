"""共用工具注册表内的声明路径互斥；不充当进程或操作系统沙箱。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from ..core.errors import ToolDeniedError
from ..i18n import tr
from .write_scope import declared_targets


class WriteCoordinator:
    """同一事件循环内，父子路径互斥，不相交路径保持并发。"""

    def __init__(self):
        self._active = {}
        self._changed = asyncio.Event()

    @staticmethod
    def targets(definition, args, ctx):
        """仅收集可信声明的写路径，不按工具名称推断。"""
        try:
            return frozenset(path for path, kind in declared_targets(definition, args, ctx) if kind == 'write')
        except Exception as exc:
            raise ToolDeniedError(tr('agent.workspace.write_invalid')) from exc

    def check(self, paths, definition, args, ctx):
        """异步授权完成后确认目标未变，拒绝无占用的目标替换。"""
        if paths is not None and paths != self.targets(definition, args, ctx):
            raise ToolDeniedError(tr('agent.workspace.write_invalid'))

    @asynccontextmanager
    async def hold(self, definition, args, ctx):
        """等待时不保留审批快照，取消或异常退出都释放占用。"""
        if definition.write_paths is None:
            yield None
            return
        token = object()
        while True:
            # 每次唤醒重新解析，不能复用等待前的符号链接目标。
            paths = self.targets(definition, args, ctx)
            conflict = any(left.is_relative_to(right) or right.is_relative_to(left)
                           for current in self._active.values() for left in paths for right in current)
            if not conflict:
                # 到登记之间没有 await，避免两个调用同时取得同一路径。
                self._active[token] = paths
                break
            await self._changed.wait()
        try:
            yield paths
        finally:
            # 释放路径不等待，连续取消也不能遗留占用。
            self._active.pop(token)
            changed, self._changed = self._changed, asyncio.Event()
            changed.set()

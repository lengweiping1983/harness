"""页签装饰标题在后台更新，不阻塞输入确认或控制权交接。"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class _Title:
    key: tuple[str, int]
    text: str = ''
    next_refresh: float = 0
    task: asyncio.Task | None = None


class BrowserTitles:
    def __init__(self, *, interval: float = .75, timeout: float = .5):
        self.interval, self.timeout = interval, timeout
        self.pages: dict[object, _Title] = {}
        self.tasks: set[asyncio.Task] = set()

    def snapshot(self, page, revision: int, *, blocked: bool = False) -> _Title:
        """按文档隔离缓存，同一页签只保留一个正在执行的刷新。"""
        key = (page.url, revision)
        value = self.pages.get(page)
        if value is None or value.key != key:
            self.forget(page)
            value = self.pages[page] = _Title(key)
        if blocked or page.is_closed():
            if value.task and not value.task.done():
                value.task.cancel()
            return value
        if (value.task is None or value.task.done()) and time.monotonic() >= value.next_refresh:
            value.next_refresh = time.monotonic() + self.interval
            value.task = asyncio.create_task(self._refresh(page, value))
            self.tasks.add(value.task)
            value.task.add_done_callback(self.tasks.discard)
        return value

    async def _refresh(self, page, value):
        try:
            title = await asyncio.wait_for(page.title(), self.timeout)
            if self.pages.get(page) is value and page.url == value.key[0] and not page.is_closed():
                value.text = title
        except asyncio.CancelledError:
            # 取消的是独立的装饰信息读取，不应把等待它的页签查询误判为用户取消。
            pass
        except Exception:
            # 同一文档短暂繁忙时保留上次标题，不把网络错误冒泡到输入操作。
            pass

    async def read(self, page, revision: int, *, blocked: bool = False) -> str:
        """代理主动查询时重新读取当前标题，仍共享正在执行的读取。"""
        value = self.pages.get(page)
        if value:
            value.next_refresh = 0
        value = self.snapshot(page, revision, blocked=blocked)
        if not blocked and value.task:
            await asyncio.shield(value.task)
        return value.text

    def forget(self, page):
        value = self.pages.pop(page, None)
        if value and value.task and not value.task.done():
            value.task.cancel()

    async def close(self):
        for page in list(self.pages):
            self.forget(page)
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

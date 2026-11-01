"""临时合成画面，每个被查看的页面最多保留一张 JPEG。

通过 CDP 确认机制将画面生成限制为每秒 10 帧。面板不再请求画面时，
查看器租约会过期，捕获任务不会无限期在后台运行。
"""
from __future__ import annotations

from harness.i18n import tr

import asyncio
import base64
import contextlib
import time

from ..core.operation_lock import OperationLock

FRAME_INTERVAL = .1
VIEWER_LEASE = 2.0


class CompositorFrames:
    def __init__(self, page, viewport):
        self.page, self.viewport = page, dict(viewport)
        self.client = None
        self.jpeg = None
        self.updated = asyncio.Event()
        self.last_viewed = time.monotonic()
        self.closed = False
        self.pending_ack = None
        self.ack_task = None
        self.lease_task = None
        self.last_ack = 0.0

    def received(self, frame):
        if self.closed:
            return
        self.pending_ack = frame['sessionId']
        metadata = frame.get('metadata', {})
        data = frame.get('data', '')
        # 调整尺寸后拒绝旧几何信息，并限制不可信图片负载大小。
        if (metadata.get('deviceWidth') == self.viewport['width'] and
                metadata.get('deviceHeight') == self.viewport['height'] and len(data) <= 4_000_000):
            try:
                self.jpeg = base64.b64decode(data, validate=True)
                self.updated.set()
            except ValueError:
                pass
        if self.ack_task is None or self.ack_task.done():
            self.ack_task = asyncio.create_task(self.acknowledge())

    async def acknowledge(self):
        try:
            while self.pending_ack is not None and not self.closed:
                await asyncio.sleep(max(0, FRAME_INTERVAL - (time.monotonic() - self.last_ack)))
                identity, self.pending_ack = self.pending_ack, None
                await self.client.send('Page.screencastFrameAck', {'sessionId': identity})
                self.last_ack = time.monotonic()
        except Exception:
            await self.close()

    async def start(self):
        self.client = await self.page.context.new_cdp_session(self.page)
        if self.closed:
            await self.client.detach()
            raise RuntimeError(tr('runtime.validation.e0b9af68e740'))
        # 持久运行的无界面 Chrome，其合成画面可能短于
        # Playwright 的视口；必须保持像素与输入坐标一致。
        await self.client.send('Emulation.setVisibleSize', self.viewport)
        self.client.on('Page.screencastFrame', self.received)
        await self.client.send('Page.startScreencast', {'format': 'jpeg', 'quality': 70,
            'maxWidth': self.viewport['width'], 'maxHeight': self.viewport['height'], 'everyNthFrame': 1})
        self.lease_task = asyncio.create_task(self.expire())

    async def expire(self):
        try:
            while not self.closed:
                await asyncio.sleep(VIEWER_LEASE / 2)
                if time.monotonic() - self.last_viewed >= VIEWER_LEASE:
                    await self.close()
        except asyncio.CancelledError:
            pass

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.updated.set()
        tasks = [task for task in (self.ack_task, self.lease_task) if task and task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.client:
            self.client.remove_listener('Page.screencastFrame', self.received)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.client.detach(), 1)
        self.jpeg = None


class BrowserFrames:
    def __init__(self):
        self.pages = {}
        self.fallback_until = {}
        self._locks = {}
        self._closed = False

    @contextlib.asynccontextmanager
    async def _owned(self, page):
        """同一页面的启动、取帧和关闭串行；等待者仍拥有同一把锁。"""
        lock = self._locks.setdefault(page, OperationLock())
        try:
            async with lock:
                yield
        finally:
            if not lock.busy and self._locks.get(page) is lock:
                self._locks.pop(page)

    async def get(self, page, viewport, *, document_revision=0):
        async with self._owned(page):
            if self._closed:
                return None
            return await self._get(page, viewport, document_revision=document_revision)

    async def _get(self, page, viewport, *, document_revision=0):
        if not hasattr(page.context, 'new_cdp_session'):
            return None  # SDK 适配器可以保留自身的截图实现。
        if self.fallback_until.get(page, 0) > time.monotonic():
            return None
        frames = self.pages.get(page)
        if frames and (frames.closed or frames.viewport != viewport or frames.document_revision != document_revision):
            await self._forget(page)
            frames = None
        if frames is None:
            frames = self.pages[page] = CompositorFrames(page, viewport)
            frames.document_revision = document_revision
            try:
                await asyncio.wait_for(frames.start(), 2)
            except asyncio.CancelledError:
                await self._forget(page)
                raise
            except Exception:
                await self._forget(page)
                self.fallback_until[page] = time.monotonic() + 30
                return None
        frames.last_viewed = time.monotonic()
        if frames.jpeg is None:
            try:
                await asyncio.wait_for(frames.updated.wait(), 1)
            except TimeoutError:
                await self._forget(page)
                self.fallback_until[page] = time.monotonic() + 30
                return None
        if frames.closed:
            await self._forget(page)
            self.fallback_until[page] = time.monotonic() + 30
            return None
        return frames.jpeg

    async def forget(self, page):
        async with self._owned(page):
            await self._forget(page)

    async def _forget(self, page):
        self.fallback_until.pop(page, None)
        frames = self.pages.pop(page, None)
        if frames:
            await frames.close()

    async def close(self):
        self._closed = True
        for page in list(set(self.pages) | set(self.fallback_until) | set(self._locks)):
            await self.forget(page)

"""合成画面的页面启动、取帧与关闭共享归属，其他页面不受阻塞。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.app.browser_frames import BrowserFrames, CompositorFrames


class Page:
    def __init__(self):
        self.context = SimpleNamespace(new_cdp_session=True)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()


class Frames:
    instances = []

    def __init__(self, page, viewport):
        self.page, self.viewport = page, viewport
        self.closed = False
        self.jpeg = None
        self.updated = asyncio.Event()
        self.instances.append(self)

    async def start(self):
        self.page.entered.set()
        await self.page.release.wait()
        self.jpeg = b'owned frame'
        self.updated.set()

    async def close(self):
        self.closed = True
        self.jpeg = None
        self.updated.set()


class BrowserFrameOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Frames.instances = []
        self.patch = patch('harness.app.browser_frames.CompositorFrames', Frames)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def test_viewport_change_waits_for_owned_startup(self):
        owner, page = BrowserFrames(), Page()
        first = asyncio.create_task(owner.get(page, {'width': 100, 'height': 100}))
        await page.entered.wait()
        second = asyncio.create_task(owner.get(page, {'width': 200, 'height': 100}))
        try:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(len(Frames.instances), 1)
            self.assertFalse(Frames.instances[0].closed)
        finally:
            page.release.set()
            results = await asyncio.gather(first, second)
            await owner.close()
        self.assertEqual(results, [b'owned frame', b'owned frame'])
        self.assertTrue(all(frame.closed for frame in Frames.instances))
        self.assertFalse(owner.pages)
        self.assertFalse(owner._locks)

    async def test_close_joins_startup_and_prevents_new_capture(self):
        owner, page = BrowserFrames(), Page()
        reading = asyncio.create_task(owner.get(page, {'width': 100, 'height': 100}))
        await page.entered.wait()
        closing = asyncio.create_task(owner.close())
        try:
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            self.assertFalse(Frames.instances[0].closed)
        finally:
            page.release.set()
            await asyncio.gather(reading, closing)
        self.assertTrue(Frames.instances[0].closed)
        self.assertIsNone(await owner.get(page, {'width': 100, 'height': 100}))
        self.assertEqual(len(Frames.instances), 1)
        self.assertFalse(owner.pages)
        self.assertFalse(owner._locks)

    async def test_other_page_runs_and_cancelled_waiter_cannot_close_owner(self):
        owner, page, other = BrowserFrames(), Page(), Page()
        reading = asyncio.create_task(owner.get(page, {'width': 100, 'height': 100}))
        await page.entered.wait()
        waiter = asyncio.create_task(owner.forget(page))
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        other.release.set()
        try:
            self.assertEqual(await owner.get(other, {'width': 100, 'height': 100}), b'owned frame')
            self.assertFalse(Frames.instances[0].closed)
        finally:
            page.release.set()
            await reading
            await owner.close()
        self.assertFalse(owner.pages)
        self.assertFalse(owner._locks)

    async def test_viewer_lease_joins_acknowledgement_and_detaches_once(self):
        page = Page()
        client = SimpleNamespace(remove_listener=Mock(), detach=AsyncMock())
        with patch('harness.app.browser_frames.time', SimpleNamespace(monotonic=lambda: 0)):
            frame = CompositorFrames(page, {'width': 100, 'height': 100})
        frame.client = client
        frame.jpeg = b'owned frame'
        frame.ack_task = asyncio.create_task(asyncio.Event().wait())
        with patch('harness.app.browser_frames.asyncio.sleep', new_callable=AsyncMock), patch('harness.app.browser_frames.time', SimpleNamespace(monotonic=lambda: 3)):
            await frame.expire()
        self.assertTrue(frame.closed)
        self.assertIsNone(frame.jpeg)
        self.assertTrue(frame.ack_task.done())
        self.assertTrue(frame.updated.is_set())
        client.detach.assert_awaited_once()
        await frame.close()
        client.detach.assert_awaited_once()

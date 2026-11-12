"""并发管理器关闭共享OAuth收尾，不把重复取消传入任务finally。"""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from harness.connectors.credentials import CredentialStore
from harness.connectors.manager import ConnectorManager
from harness.connectors.store import ConnectorRecord, ConnectorStore


class ConnectorCloseOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_close_and_repeated_cancel_wait_for_same_cleanup(self):
        manager = ConnectorManager(ConnectorStore(), Mock(), Mock(), Mock(), Mock(), CredentialStore(backend='memory'))
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        interrupted = []
        async def authorization():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    interrupted.append(True)
                    raise
        authorization_task = asyncio.create_task(authorization())
        manager._oauth_tasks['fixture'] = authorization_task
        await entered.wait()
        first = asyncio.create_task(manager.aclose())
        second = None
        try:
            await cleaning.wait()
            second = asyncio.create_task(manager.aclose())
            await asyncio.sleep(0)
            self.assertFalse(second.done(), '并发关闭必须等待已经开始的所属清理')
            first.cancel()
            await asyncio.sleep(0)
            first.cancel()
            await asyncio.sleep(0)
            self.assertEqual(interrupted, [])
            self.assertFalse(first.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await first
            await second
            self.assertTrue(authorization_task.done())
            self.assertEqual(manager._oauth_tasks, {})
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []), authorization_task, return_exceptions=True)

    async def test_close_failure_is_retained_and_new_authorization_is_rejected(self):
        from harness.core.errors import ConnectorError
        from harness.core.language import current_language
        from harness.i18n import tr
        manager = ConnectorManager(ConnectorStore(), Mock(), Mock(), Mock(), Mock(), CredentialStore(backend='memory'))
        entered = asyncio.Event()
        failure = OSError('Original cleanup failure')
        async def authorization():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                raise failure
        task = asyncio.create_task(authorization())
        manager._oauth_tasks['fixture'] = task
        await entered.wait()
        for _ in range(2):
            with self.assertRaises(OSError) as caught:
                await manager.aclose()
            self.assertIs(caught.exception, failure)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), self.assertRaises(ConnectorError) as caught:
                await manager.begin_oauth_login('fixture')
            self.assertEqual(caught.exception.code, 'connector/closed')
            with current_language.set(language):
                self.assertEqual(caught.exception.message, tr('connector.manager.closed'))
        self.assertEqual(manager._oauth_tasks, {})

    async def test_close_still_waits_for_authorization_removed_by_logout(self):
        manager = ConnectorManager(ConnectorStore(), Mock(), Mock(), Mock(), Mock(), CredentialStore(backend='memory'))
        manager.store.upsert(ConnectorRecord('fixture'))
        manager._emit_changed = AsyncMock()
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        interrupted = []
        async def authorization():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    interrupted.append(True)
                    raise
        task = asyncio.create_task(authorization())
        manager._oauth_tasks['fixture'] = task
        await entered.wait()
        await manager.oauth_logout('fixture')
        await cleaning.wait()
        closing = asyncio.create_task(manager.aclose())
        try:
            done, _ = await asyncio.wait({closing}, timeout=0.02)
            self.assertEqual(done, set())
            self.assertEqual(interrupted, [])
            release.set()
            await closing
            self.assertTrue(task.done())
        finally:
            release.set()
            await asyncio.gather(task, closing, return_exceptions=True)

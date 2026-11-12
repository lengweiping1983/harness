"""写入线程结束前重复取消不能释放插件锁，也不能重放操作。"""
import asyncio
import tempfile
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.plugins.service import PackageService


class PluginMutationCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_thread_mutations_keep_lock_through_repeated_cancellation(self):
        for mode in ('source', 'create', 'install'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                entered, release, finished = threading.Event(), threading.Event(), threading.Event()
                calls = []
                def write(*args, **kwargs):
                    calls.append(mode)
                    entered.set()
                    release.wait(5)
                    finished.set()
                    return {'name': 'fixture'}
                service = PackageService.__new__(PackageService)
                service._lock = asyncio.Lock()
                service.store = SimpleNamespace(root=root / 'managed', install=write)
                service.harness = SimpleNamespace(connector_store=SimpleNamespace(locked=nullcontext), refresh_skills=Mock())
                service._changed = AsyncMock()
                service.read_draft = AsyncMock(return_value={'name': 'fixture'})
                with patch('harness.plugins.authoring.create_plugin', side_effect=write):
                    if mode == 'source': operation = service._source_mutation(write)
                    elif mode == 'create': operation = service.create_draft(str(root / 'sources'), 'fixture', 'description', 'instructions')
                    else: operation = service.install('fixture')
                    owner = asyncio.create_task(operation)
                    contender = None
                    try:
                        self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                        for _ in range(3):
                            owner.cancel()
                            await asyncio.sleep(0)
                            self.assertFalse(owner.done())
                            self.assertTrue(service._lock.locked())
                        contender = asyncio.create_task(service._lock.acquire())
                        await asyncio.sleep(0)
                        self.assertFalse(contender.done())
                        release.set()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(owner, 3)
                        self.assertTrue(finished.is_set())
                        await asyncio.wait_for(contender, 2)
                        service._lock.release()
                        self.assertEqual(calls, [mode])
                        if mode == 'create': service.read_draft.assert_awaited_once()
                        if mode == 'install':
                            service.harness.refresh_skills.assert_called_once()
                            service._changed.assert_awaited_once_with('install', 'fixture')
                    finally:
                        release.set()
                        await asyncio.gather(owner, return_exceptions=True)
                        if contender and not contender.done():
                            contender.cancel()
                            await asyncio.gather(contender, return_exceptions=True)

    async def test_lifecycle_mutations_finish_before_releasing_lock_on_cancellation(self):
        for mode in ('enable', 'disable', 'uninstall'):
            for boundary in ('connection', 'notification'):
                with self.subTest(mode=mode, boundary=boundary):
                    entered, release = asyncio.Event(), asyncio.Event()
                    effects = []
                    async def pause():
                        entered.set()
                        await release.wait()
                    async def disconnect(name):
                        effects.append('unregistered')
                        if boundary == 'connection': await pause()
                        effects.append('closed')
                    async def resume(name):
                        effects.append('connecting')
                        if boundary == 'connection': await pause()
                        effects.append('connected')
                    async def changed(action, name):
                        effects.append('notifying')
                        if boundary == 'notification': await pause()
                        effects.append('notified')
                    service = PackageService.__new__(PackageService)
                    service._lock = asyncio.Lock()
                    service._disconnect, service._resume, service._changed = disconnect, resume, changed
                    service.store = SimpleNamespace(
                        set_enabled=Mock(side_effect=lambda *args: effects.append('persisted')),
                        uninstall=Mock(side_effect=lambda *args: effects.append('removed')))
                    service.harness = SimpleNamespace(refresh_skills=Mock(side_effect=lambda: effects.append('refreshed')))
                    service.catalog = Mock(return_value={'packages': []})
                    operation = service.uninstall('fixture') if mode == 'uninstall' else service.set_enabled('fixture', mode == 'enable')
                    owner = asyncio.create_task(operation)
                    contender = None
                    try:
                        await asyncio.wait_for(entered.wait(), 2)
                        for _ in range(3):
                            owner.cancel()
                            await asyncio.sleep(0)
                            self.assertFalse(owner.done())
                            self.assertTrue(service._lock.locked())
                        contender = asyncio.create_task(service._lock.acquire())
                        await asyncio.sleep(0)
                        self.assertFalse(contender.done())
                        release.set()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(owner, 2)
                        await asyncio.wait_for(contender, 2)
                        service._lock.release()
                        expected = {
                            'enable': ['persisted', 'refreshed', 'connecting', 'connected', 'notifying', 'notified'],
                            'disable': ['unregistered', 'closed', 'persisted', 'refreshed', 'notifying', 'notified'],
                            'uninstall': ['unregistered', 'closed', 'removed', 'refreshed', 'notifying', 'notified'],
                        }
                        self.assertEqual(effects, expected[mode])
                    finally:
                        release.set()
                        await asyncio.gather(owner, return_exceptions=True)
                        if contender and not contender.done():
                            contender.cancel()
                            await asyncio.gather(contender, return_exceptions=True)

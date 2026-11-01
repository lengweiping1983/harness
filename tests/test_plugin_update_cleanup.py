"""插件更新保存、断开、刷新与恢复失败保留原件，不自动重放写入。"""
import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from harness.plugins.service import PackageService


class PluginUpdateCleanupTests(unittest.IsolatedAsyncioTestCase):
    def service(self):
        service = PackageService.__new__(PackageService)
        service._lock = asyncio.Lock()
        service.preview = AsyncMock(return_value={'name':'fixture','digest':'new'})
        service.store = SimpleNamespace(entries=Mock(return_value=[(SimpleNamespace(name='fixture',servers={'one':{}}),{'digest':'old'})]),
                                        update=Mock(return_value={'name':'fixture','digest':'new'}))
        service.harness = SimpleNamespace(connector_store=SimpleNamespace(locked=nullcontext),refresh_skills=Mock())
        service._disconnect = AsyncMock();service._disconnect_names = AsyncMock()
        service._resume = AsyncMock();service._changed = AsyncMock()
        return service

    async def test_save_and_disconnect_failures_preserve_both_and_do_not_resume_unknown_connection(self):
        service = self.service()
        primary, cleanup = OSError('Original save failure'), OSError('Original disconnect failure')
        service.store.update.side_effect = primary;service._disconnect_names.side_effect = cleanup
        with self.assertRaises(BaseExceptionGroup) as error:await service.update('fixture','source','new','old')
        self.assertEqual(error.exception.exceptions,(primary,cleanup))
        service.store.update.assert_called_once();service.harness.refresh_skills.assert_called_once()
        service._resume.assert_not_awaited();service._changed.assert_not_awaited()
        self.assertFalse(service._lock.locked())

    async def test_committed_update_still_notifies_when_refresh_or_disconnect_fails(self):
        service = self.service()
        first, second = OSError('Original disconnect failure'), OSError('Original refresh failure')
        service._disconnect_names.side_effect = first;service.harness.refresh_skills.side_effect = second
        with self.assertRaises(BaseExceptionGroup) as error:await service.update('fixture','source','new','old')
        self.assertEqual(error.exception.exceptions,(first,second))
        service._changed.assert_awaited_once_with('update','fixture')
        service._resume.assert_not_awaited();service.store.update.assert_called_once()

    async def test_resume_failure_keeps_committed_notification_and_original_error(self):
        service = self.service();failure = OSError('Original resume failure');service._resume.side_effect = failure
        with self.assertRaises(OSError) as error:await service.update('fixture','source','new','old')
        self.assertIs(error.exception,failure)
        service._changed.assert_awaited_once_with('update','fixture');service.store.update.assert_called_once()

    async def test_repeated_cancel_waits_for_resume_before_unlock_and_does_not_replay_save(self):
        service = self.service();entered,release = asyncio.Event(),asyncio.Event()
        async def resume(name):entered.set();await release.wait()
        service._resume.side_effect = resume
        task = asyncio.create_task(service.update('fixture','source','new','old'))
        try:
            await asyncio.wait_for(entered.wait(),1)
            task.cancel();await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
            self.assertFalse(task.done());self.assertTrue(service._lock.locked())
        finally:release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        service.store.update.assert_called_once();service._resume.assert_awaited_once()
        service._changed.assert_awaited_once_with('update','fixture');self.assertFalse(service._lock.locked())

    async def test_resume_attempts_other_owned_connections_after_one_failure(self):
        service = self.service();failure = OSError('Original first connector failure')
        records=[SimpleNamespace(metadata={'plugin_id':'fixture'},enabled=True,bound=True) for _ in range(2)]
        service.harness.connector_store = SimpleNamespace(list=Mock(return_value=records))
        service.harness.connectors = SimpleNamespace(_connect_and_register=AsyncMock(side_effect=[failure,None]))
        with self.assertRaises(OSError) as error:await PackageService._resume(service,'fixture')
        self.assertIs(error.exception,failure)
        self.assertEqual([call.args[0] for call in service.harness.connectors._connect_and_register.await_args_list],records)

    async def test_replaced_manager_does_not_take_over_an_owned_resume(self):
        service = self.service()
        records=[SimpleNamespace(metadata={'plugin_id':'fixture'},enabled=True,bound=True) for _ in range(2)]
        service.harness.connector_store = SimpleNamespace(list=Mock(return_value=records))
        replacement = SimpleNamespace(_connect_and_register=AsyncMock())
        calls=[]
        async def connect(record):
            calls.append(record)
            service.harness.connectors = replacement
        service.harness.connectors = SimpleNamespace(_connect_and_register=connect)
        await PackageService._resume(service,'fixture')
        self.assertEqual(calls,records)
        replacement._connect_and_register.assert_not_awaited()

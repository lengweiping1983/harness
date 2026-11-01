"""调度器关闭等待任务收尾，失败仍释放自己的租约且不覆盖新所有者。"""
import asyncio
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harness.app.automations import AutomationService


class AutomationCloseOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def service(self):
        root=tempfile.TemporaryDirectory();self.addCleanup(root.cleanup)
        service=AutomationService(Path(root.name)/'automations.db',SimpleNamespace())
        with service.db() as db:
            db.execute('UPDATE automation_scheduler SET owner=?,expires=123',(service.owner,))
        return service

    async def test_repeated_cancel_waits_for_single_job_cleanup_before_releasing_lease(self):
        service=self.service();entered,cleaning,release=asyncio.Event(),asyncio.Event(),asyncio.Event()
        async def job():
            entered.set()
            try:await asyncio.Future()
            finally:cleaning.set();await release.wait()
        job_task=asyncio.create_task(job());service.tasks.add(job_task)
        await entered.wait();closing=asyncio.create_task(service.close())
        try:
            await asyncio.wait_for(cleaning.wait(),1)
            closing.cancel();await asyncio.sleep(0);closing.cancel();await asyncio.sleep(0)
            self.assertFalse(closing.done());self.assertFalse(job_task.done())
            with service.db() as db:self.assertEqual(db.execute('SELECT owner FROM automation_scheduler').fetchone()[0],service.owner)
        finally:release.set()
        with self.assertRaises(asyncio.CancelledError):await closing
        with service.db() as db:self.assertIsNone(db.execute('SELECT owner FROM automation_scheduler').fetchone()[0])

    async def test_job_and_lease_failures_preserve_original_exceptions(self):
        service=self.service();entered=asyncio.Event()
        primary,storage=OSError('Original job cleanup failure'),sqlite3.OperationalError('Original lease failure')
        async def job():
            entered.set()
            try:await asyncio.Future()
            finally:raise primary
        task=asyncio.create_task(job());service.tasks.add(task);await entered.wait()
        @contextmanager
        def broken():raise storage;yield
        with patch.object(service,'db',side_effect=broken):
            with self.assertRaises(BaseExceptionGroup) as error:await service.close()
        self.assertEqual(error.exception.exceptions,(primary,storage))
        await service.close()
        with service.db() as db:self.assertIsNone(db.execute('SELECT owner FROM automation_scheduler').fetchone()[0])

    async def test_other_scheduler_lease_is_never_released(self):
        service=self.service()
        with service.db() as db:db.execute("UPDATE automation_scheduler SET owner='other',expires=456")
        await service.close();await service.close()
        with service.db() as db:
            row=db.execute('SELECT owner,expires FROM automation_scheduler').fetchone()
        self.assertEqual(tuple(row),('other',456))

    async def test_closing_and_closed_scheduler_reject_all_producers_without_touching_storage(self):
        from harness.core.errors import HarnessError
        from harness.core.language import current_language
        from harness.i18n import tr
        service = self.service()
        entered, release = asyncio.Event(), asyncio.Event()
        async def job():
            try:
                await asyncio.Future()
            finally:
                entered.set()
                await release.wait()
        task = asyncio.create_task(job());service.tasks.add(task)
        await asyncio.sleep(0)
        closing = asyncio.create_task(service.close())
        await entered.wait()
        async def assert_refused():
            with patch.object(service, 'db', side_effect=AssertionError('closed producer accessed storage')):
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        for operation in (service.tick, lambda: service.run_now('fixture')):
                            with self.assertRaises(HarnessError) as caught:
                                await operation()
                            self.assertEqual(caught.exception.message, tr('automation.scheduler.closed'))
                        with self.assertRaises(HarnessError):
                            service.start()
        try:
            await assert_refused()
        finally:
            release.set()
            await closing
        await assert_refused()
        self.assertEqual(service.tasks, {task})
        with service.db() as db:
            self.assertIsNone(db.execute('SELECT owner FROM automation_scheduler').fetchone()[0])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM automation_runs').fetchone()[0], 0)

    async def test_due_producer_claims_once_and_other_scheduler_cannot_duplicate_run(self):
        from unittest.mock import Mock
        service = self.service()
        service.harness.get_session = Mock()
        row = service.save({'creation_id':'a'*32, 'name':'Original', 'prompt':'Original prompt',
            'session_id':'fixture', 'timezone':'UTC', 'mode':'session', 'enabled':True,
            'schedule':{'kind':'interval', 'seconds':60}})
        with service.db() as db:
            db.execute('UPDATE automations SET due=0 WHERE id=?', (row['id'],))
        other = AutomationService(service.path, service.harness)
        with patch.object(service, '_spawn') as first, patch.object(other, '_spawn') as second:
            self.assertEqual(await service.tick(), 1)
            self.assertEqual(await service.tick(), 0)
            self.assertEqual(await other.tick(), 0)
            first.assert_called_once()
            second.assert_not_called()
        with service.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM automation_runs').fetchone()[0], 1)
        await other.close()
        with service.db() as db:
            self.assertEqual(db.execute('SELECT owner FROM automation_scheduler').fetchone()[0], service.owner)
        await service.close()

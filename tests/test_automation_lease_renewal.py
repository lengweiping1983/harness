"""续租节流保留到期认领、数据库所有权核对和故障接管。"""
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from harness.app.automations import AutomationService


class AutomationLeaseRenewalTests(unittest.IsolatedAsyncioTestCase):
    def service(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        return AutomationService(Path(root.name) / 'automations.db', SimpleNamespace(get_session=Mock()))

    async def tick_at(self, service, now):
        with patch('harness.app.automations.time.time', return_value=now):
            return await service.tick()

    async def test_idle_checks_do_not_commit_changes_between_renewals(self):
        service = self.service()
        await self.tick_at(service, 100)
        observer = sqlite3.connect(service.path)
        self.addCleanup(observer.close)
        version = observer.execute('PRAGMA data_version').fetchone()[0]
        for now in (102, 104, 106, 108):
            self.assertEqual(await self.tick_at(service, now), 0)
            self.assertEqual(observer.execute('PRAGMA data_version').fetchone()[0], version)
            self.assertEqual(observer.execute('SELECT expires FROM automation_scheduler').fetchone()[0], 130)
        await self.tick_at(service, 110)
        self.assertNotEqual(observer.execute('PRAGMA data_version').fetchone()[0], version)
        self.assertEqual(observer.execute('SELECT expires FROM automation_scheduler').fetchone()[0], 140)

    async def test_due_task_is_claimed_before_renewal_and_other_instance_cannot_repeat(self):
        service = self.service()
        row = service.save({'creation_id': 'b' * 32, 'name': 'Due', 'prompt': 'Run',
            'session_id': 'fixture', 'timezone': 'UTC', 'mode': 'session', 'enabled': True,
            'schedule': {'kind': 'interval', 'seconds': 60}})
        with service.db() as db:
            db.execute('UPDATE automations SET due=104 WHERE id=?', (row['id'],))
        other = AutomationService(service.path, service.harness)
        with patch.object(service, '_spawn') as first, patch.object(other, '_spawn') as second:
            self.assertEqual(await self.tick_at(service, 100), 0)
            self.assertEqual(await self.tick_at(service, 104), 1)
            self.assertEqual(await self.tick_at(other, 104), 0)
            self.assertEqual(await self.tick_at(service, 106), 0)
            first.assert_called_once()
            second.assert_not_called()
        with service.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM automation_runs').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT expires FROM automation_scheduler').fetchone()[0], 130)

    async def test_expired_owner_is_replaced_at_boundary_without_waiting_for_renewal(self):
        service = self.service()
        other = AutomationService(service.path, service.harness)
        await self.tick_at(service, 100)
        await self.tick_at(other, 129)
        with service.db() as db:
            self.assertEqual(db.execute('SELECT owner FROM automation_scheduler').fetchone()[0], service.owner)
        await self.tick_at(other, 130)
        await self.tick_at(service, 132)
        with service.db() as db:
            row = db.execute('SELECT owner,expires FROM automation_scheduler').fetchone()
            self.assertEqual(tuple(row), (other.owner, 160))
        await service.close()
        with other.db() as db:
            self.assertEqual(db.execute('SELECT owner FROM automation_scheduler').fetchone()[0], other.owner)

    async def test_every_check_detects_another_owner_and_cancels_owned_work(self):
        service = self.service()
        await self.tick_at(service, 100)
        task = Mock()
        service.tasks.add(task)
        with service.db() as db:
            db.execute("UPDATE automation_scheduler SET owner='replacement',expires=200")
        self.assertEqual(await self.tick_at(service, 102), 0)
        task.cancel.assert_called_once()
        with service.db() as db:
            self.assertEqual(tuple(db.execute('SELECT owner,expires FROM automation_scheduler').fetchone()),
                             ('replacement', 200))

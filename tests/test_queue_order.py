"""验证队列排序的完整快照、持久提交与执行准入。"""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.app.runtime import RunController
from harness.core.errors import HarnessError, PersistenceError
from harness.core.runs import AgentRun


class QueueOrderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.session = SimpleNamespace(steering=SimpleNamespace(records={}))
        self.host = SimpleNamespace(config=SimpleNamespace(home=Path(directory.name)),
            get_session=Mock(return_value=self.session))
        self.controller = RunController(self.host)
        self.records = [AgentRun(id=key, session_id='session', text=key) for key in ('first', 'second')]
        self.controller._pending['session'] = {record.id: record for record in self.records}

    def snapshot(self):
        return [{key: row[key] for key in ('id', 'edit_version')} for row in self.controller.queue('session')]

    def test_order_is_persisted_before_waiters_are_notified(self):
        expected = self.snapshot()
        observed = []

        def notified(session_id):
            with self.controller.db() as db:
                stored = json.loads(db.execute('SELECT data FROM queue_orders WHERE session_id=?', (session_id,)).fetchone()[0])
            observed.append((stored, list(self.controller._pending[session_id])))

        with patch.object(self.controller, '_notify_queue', side_effect=notified):
            self.controller.reorder_queue('session', expected, ['second', 'first'])
        self.assertEqual(observed, [(['second', 'first'], ['second', 'first'])])
        self.assertFalse(self.controller._can_start(self.records[0]))
        self.assertTrue(self.controller._can_start(self.records[1]))

    def test_changed_input_or_new_submission_rejects_stale_snapshot(self):
        for change in ('edit', 'new'):
            with self.subTest(change=change):
                self.set_pending()
                expected = self.snapshot()
                if change == 'edit':
                    self.records[0].text = '其他标签页修改'
                else:
                    self.controller._pending['session']['third'] = AgentRun(id='third', session_id='session')
                before = list(self.controller._pending['session'])
                with self.assertRaises(HarnessError) as raised:
                    self.controller.reorder_queue('session', expected, ['second', 'first'])
                self.assertEqual(raised.exception.code, 'run/queue-order-changed')
                self.assertEqual(list(self.controller._pending['session']), before)

    def set_pending(self):
        for record in self.records:
            record.text = record.id
        self.controller._pending['session'] = {record.id: record for record in self.records}

    def test_failed_commit_preserves_order_and_does_not_wake_waiters(self):
        with patch.object(self.controller, 'db', side_effect=sqlite3.OperationalError('模拟磁盘写入失败')), patch.object(self.controller, '_notify_queue') as notify:
            with self.assertRaises(PersistenceError):
                self.controller.reorder_queue('session', self.snapshot(), ['second', 'first'])
        self.assertEqual(list(self.controller._pending['session']), ['first', 'second'])
        notify.assert_not_called()
        self.assertTrue(self.controller.storage_error)

    def test_goal_cannot_jump_ahead_of_user_input(self):
        self.records[1].goal_id = 'goal'
        with self.assertRaises(HarnessError) as raised:
            self.controller.reorder_queue('session', self.snapshot(), ['second', 'first'])
        self.assertEqual(raised.exception.code, 'run/invalid')
        self.assertEqual(list(self.controller._pending['session']), ['first', 'second'])

    async def test_cancelling_one_waiter_does_not_cancel_shared_wakeup(self):
        record = self.records[1]
        first = asyncio.create_task(self.controller._wait_queue_front(record))
        second = asyncio.create_task(self.controller._wait_queue_front(record))
        await asyncio.sleep(0)
        wake = self.controller._queue_wake['session']
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(wake.cancelled())
        self.controller.reorder_queue('session', self.snapshot(), ['second', 'first'])
        await asyncio.wait_for(second, 1)
        self.assertTrue(wake.done())

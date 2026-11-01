"""验证运行启动、取消与磁盘事实保持一致。"""
import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.app.runtime import RunController
from harness.core.agent import TurnResult
from harness.core.errors import PersistenceError
from harness.core.events import EventBus
from harness.core.runs import AgentRun


class RunCommitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        host = SimpleNamespace(config=SimpleNamespace(home=Path(self.directory.name), cwd=self.directory.name),
            jobs=SimpleNamespace(assert_ready=Mock()), bus=EventBus(),
            subagents=SimpleNamespace(shutdown=AsyncMock()))
        self.controller = RunController(host)
        self.controller.queue_control = Mock(return_value={})
        self.session = SimpleNamespace(id='session', open_turn=None, persistence_error=None,
            header=SimpleNamespace(cwd=self.directory.name))

    async def test_failed_start_commit_does_not_claim_execution_started(self):
        record = AgentRun(id='run', session_id=self.session.id)
        operation = AsyncMock()
        with patch.object(self.controller.storage, 'write', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaises(PersistenceError):
                await self.controller.execute(self.session, operation, record=record)
        operation.assert_not_awaited()
        self.assertEqual(record.status, 'queued')
        self.assertIsNone(record.started_at)
        self.assertEqual(self.controller.live_records(), ())
        self.assertEqual(self.controller.active, {})

    async def test_start_is_persisted_before_operation_and_identity_stays_shared(self):
        record = AgentRun(id='run', session_id=self.session.id)

        async def operation():
            with self.controller.db() as db:
                row = db.execute('SELECT data FROM runs WHERE id=?', (record.id,)).fetchone()
            persisted = self.controller.storage.decode(row[0], expected_id=record.id)
            self.assertEqual(persisted.status, 'running')
            self.assertIs(self.controller.records[record.id], record)
            self.assertIs(self.controller.current(self.session.id), record)
            return TurnResult(self.session.id, 1, 'completed', text='已核对')

        await self.controller.execute(self.session, operation, record=record)
        self.assertEqual(record.status, 'completed')
        self.assertIsNotNone(record.finished_at)
        self.assertEqual(self.controller.records[record.id].result['text'], '已核对')
        self.assertIsNone(self.controller.current(self.session.id))

    async def test_cancelled_execution_waits_for_child_cleanup_and_persists_interruption(self):
        record = AgentRun(id='run', session_id=self.session.id)
        entered = asyncio.Event()

        async def operation():
            entered.set()
            await asyncio.Event().wait()

        execution = asyncio.create_task(self.controller.execute(self.session, operation, record=record))
        await entered.wait()
        execution.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await execution
        self.controller.harness.subagents.shutdown.assert_awaited_once_with(self.session.id)
        self.assertEqual(self.controller.records[record.id].status, 'interrupted')
        self.assertTrue(record.settled)
        self.assertIsNone(self.controller.current(self.session.id))

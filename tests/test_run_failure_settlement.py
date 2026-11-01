"""运行失败与资源收尾失败共同保留，账本和运行状态不伪造成功。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.app.runtime import RunController, ExecutionBudget
from harness.core.errors import HarnessError
from harness.core.events import EventBus
from harness.core.language import current_language
from harness.core.runs import AgentRun
from harness.i18n import tr


class RunFailureSettlementTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_failure_still_persists_failed_direct_run_and_both_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            operation_error, cleanup_error = RuntimeError('Original operation'), OSError('Original cleanup')
            host = SimpleNamespace(config=SimpleNamespace(home=Path(directory), cwd=directory),
                jobs=SimpleNamespace(assert_ready=Mock()), bus=EventBus(),
                subagents=SimpleNamespace(shutdown=AsyncMock(side_effect=cleanup_error)))
            controller = RunController(host)
            session = SimpleNamespace(id='fixture', persistence_error=None, open_turn=None, header=SimpleNamespace(cwd=directory))
            host.get_session = lambda _: session
            record = AgentRun(id='direct', session_id=session.id)
            with self.assertRaises(ExceptionGroup) as caught:
                await controller.execute(session, AsyncMock(side_effect=operation_error), record=record)
            self.assertEqual(caught.exception.exceptions, (operation_error, cleanup_error))
            self.assertEqual(record.status, 'failed')
            self.assertIsNotNone(record.finished_at)
            self.assertEqual(controller.records[record.id].status, 'failed')
            self.assertEqual(controller.active, {})
            self.assertEqual(controller._executions, {})
            self.assertIsNone(record.session)
            host.subagents.shutdown.assert_awaited_once_with(session.id)

    async def test_repeat_cancellation_waits_for_owned_shutdown_and_settles_run(self):
        with tempfile.TemporaryDirectory() as directory:
            entered, release = asyncio.Event(), asyncio.Event()
            async def shutdown(_):
                entered.set()
                await release.wait()
            original = RuntimeError('Original operation')
            host = SimpleNamespace(config=SimpleNamespace(home=Path(directory), cwd=directory),
                jobs=SimpleNamespace(assert_ready=Mock()), bus=EventBus(),
                subagents=SimpleNamespace(shutdown=AsyncMock(side_effect=shutdown)))
            controller = RunController(host)
            session = SimpleNamespace(id='fixture', persistence_error=None, open_turn=None, header=SimpleNamespace(cwd=directory))
            host.get_session = lambda _: session
            record = AgentRun(id='direct', session_id=session.id)
            task = asyncio.create_task(controller.execute(session, AsyncMock(side_effect=original), record=record))
            await entered.wait()
            try:
                for _ in range(2):
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                self.assertEqual(record.status, 'failed')
            finally:
                release.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            self.assertIsInstance(result, BaseExceptionGroup)
            self.assertIs(result.exceptions[0], original)
            self.assertEqual(controller.records[record.id].status, 'failed')
            self.assertIsNotNone(record.finished_at)
            self.assertEqual(host.subagents.shutdown.await_count, 1)

    async def test_close_propagates_original_finalizer_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = RunController(SimpleNamespace(config=SimpleNamespace(home=Path(directory)), _agents={}))
            entered = asyncio.Event()
            error = OSError('Original finalizer failure')
            async def operation():
                try:
                    entered.set()
                    await asyncio.Event().wait()
                finally:
                    raise error
            task = asyncio.create_task(operation())
            controller.active['fixture'] = task
            await entered.wait()
            with self.assertRaises(OSError) as caught:
                await controller.close()
            self.assertIs(caught.exception, error)
            self.assertTrue(task.done())

    async def test_invalid_text_is_rejected_before_session_or_record_access(self):
        with tempfile.TemporaryDirectory() as directory:
            host = SimpleNamespace(config=SimpleNamespace(home=Path(directory)), get_session=Mock())
            controller = RunController(host)
            for value in (None, True, 1, [], {}):
                for operation in (lambda: controller.submit('fixture', value), lambda: controller.edit('missing', value)):
                    with self.assertRaises(HarnessError) as caught:
                        operation()
                    self.assertEqual(caught.exception.code, 'run/invalid')
            host.get_session.assert_not_called()

    async def test_invalid_budget_counts_preserve_reservation_and_spending(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for value in (True, False, -1, 1.5, float('nan'), float('inf'), '10', [], {}):
                    with self.assertRaises(HarnessError):
                        ExecutionBudget(value)
                    budget = ExecutionBudget(100)
                    for args in ((value, 10), (1, value)):
                        with self.assertRaises(HarnessError):
                            await budget.acquire(*args)
                        self.assertEqual(budget.snapshot(), {'limit':100, 'spent':0, 'reserved':0, 'available':100})
                    reservation = await budget.acquire(1, 10)
                    with self.assertRaises(HarnessError) as caught:
                        reservation.settle(value)
                    self.assertEqual(caught.exception.message, tr('runtime.budget.invalid', field='tokens'))
                    self.assertFalse(reservation.settled)
                    self.assertEqual(budget.reserved, 11)
                    reservation.settle(3)
                    self.assertEqual((budget.reserved, budget.spent), (0, 3))
                    reservation.settle(10)
                    self.assertEqual(budget.spent, 3)

    async def test_goal_checkpoint_failure_emits_browser_resource_without_replaying_run(self):
        """目标结算失败保留资源身份，原运行只执行一次。"""
        from harness.core.turn import TurnResult
        for language in ('en', 'zh-CN', 'en'):
            with tempfile.TemporaryDirectory() as directory, current_language.set(language):
                bus = EventBus()
                errors = []
                bus.on('error', errors.append)
                host = SimpleNamespace(config=SimpleNamespace(home=Path(directory)), bus=bus,
                    run_async=AsyncMock(return_value=TurnResult('fixture', 1, 'completed', text='Original result')),
                    goal_runtime=SimpleNamespace(finished=AsyncMock(side_effect=OSError('Original storage failure'))))
                controller = RunController(host)
                controller.queue_control = Mock(return_value={})
                record = AgentRun(id='run-fixture', session_id='fixture', text='Original request')
                controller.records[record.id] = record
                with self.assertLogs('harness.app.runtime', level='ERROR'):
                    controller._start_record(record)
                    await record.task
                self.assertEqual(errors, [{'session_id':'fixture','code':'goal/checkpoint-failed',
                    'message':tr('ui.run.goalCheckpointFailed'),'copy':{'key':'ui.run.goalCheckpointFailed'}}])
                self.assertEqual(record.status, 'completed')
                self.assertEqual(record.result['text'], 'Original result')
                host.run_async.assert_awaited_once_with('fixture', 'Original request')
                host.goal_runtime.finished.assert_awaited_once_with(record)

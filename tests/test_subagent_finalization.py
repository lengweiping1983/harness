"""验证成员取消与交付收尾期间的运行归属。"""
import asyncio
import unittest
from unittest.mock import AsyncMock

from harness.core.events import EventBus
from harness.core.runs import AgentRun, COMPLETED, FAILED, INTERRUPTED, PENDING
from harness.subagent.manager import SubagentManager


class SubagentFinalizationTests(unittest.IsolatedAsyncioTestCase):
    def manager(self):
        return SubagentManager(adapter=None, tools=None, prompts=None, bus=EventBus())

    async def test_cancel_before_first_schedule_still_finishes_delivery(self):
        manager = self.manager()
        delivered = []

        async def deliver(state):
            delivered.append(state.id)

        state = manager.spawn(prompt='测试', description='未启动取消', parent_session_id='parent')
        state._on_finished = deliver
        state.task.cancel()
        await asyncio.gather(state.task, return_exceptions=True)
        await manager.wait(state.id, timeout=1)
        self.assertEqual(state.status, INTERRUPTED)
        self.assertEqual(delivered, [state.id])
        self.assertTrue(state.settled)
        self.assertIsNotNone(state.finished_at)
        await manager.shutdown()

    async def test_completed_execution_keeps_context_owned_until_delivery_finishes(self):
        manager = self.manager()
        entered, release = asyncio.Event(), asyncio.Event()
        session = AsyncMock()
        state = AgentRun(id='member', status=COMPLETED, session=session)
        delivered = []

        async def deliver(record):
            entered.set()
            await release.wait()
            delivered.append(record.id)

        state._on_finished = deliver
        finalization = manager._finalization(state)
        await entered.wait()
        self.assertFalse(state.settled)
        self.assertIs(manager._finalization(state), finalization)
        release.set()
        await finalization
        self.assertTrue(state.settled)
        self.assertEqual(delivered, ['member'])
        session.close.assert_awaited_once()

    async def test_delivery_failure_overrides_execution_success(self):
        manager = self.manager()
        state = AgentRun(id='member', status=COMPLETED)

        async def fail_delivery(record):
            raise OSError('交付写盘失败')

        state._on_finished = fail_delivery
        with self.assertLogs('harness.subagent.manager', level='ERROR'):
            await manager._finalization(state)
        self.assertEqual(state.status, FAILED)
        self.assertIn('交付写盘失败', state.error_message)
        self.assertTrue(state.settled)

    async def test_waiting_timeout_does_not_cancel_owned_finalization(self):
        manager = self.manager()
        release = asyncio.Event()
        state = AgentRun(id='member', status=PENDING)
        state.task = asyncio.create_task(asyncio.sleep(0))
        await state.task

        async def deliver(record):
            await release.wait()

        state._on_finished = deliver
        manager._states[state.id] = state
        finalization = manager._finalization(state)
        await manager.wait(state.id, timeout=0.01)
        self.assertFalse(finalization.done())
        release.set()
        await finalization
        self.assertTrue(state.settled)

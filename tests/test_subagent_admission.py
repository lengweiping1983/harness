"""全树准入：名额、深度与让渡都必须可核验，父子不得互锁。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness.core.agent import AgentLoop
from harness.core.language import current_language
from harness.core.runs import FAILED, PENDING, run_error_message
from harness.core.turn import TurnResult
from harness.i18n import tr
from harness.subagent.admission import AdmissionGate, AgentDepthExceeded
from harness.subagent.manager import SubagentManager
from harness.tools.registry import ToolRegistry


def manager_with(**kwargs):
    """构造只依赖记录适配器的管理器，便于精确观察准入事实。"""
    return SubagentManager(adapter=SimpleNamespace(model='fixture'), tools=ToolRegistry(),
                           prompts=None, **kwargs)


class AdmissionGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_is_never_exceeded_and_release_is_idempotent(self):
        gate = AdmissionGate(capacity=2)
        first = await gate.acquire('a', 1)
        second = await gate.acquire('b', 1)
        pending = asyncio.create_task(gate.acquire('c', 1))
        await asyncio.sleep(0)
        self.assertEqual(gate.snapshot()['active'], 2)
        self.assertEqual(gate.snapshot()['queued'], 1)
        gate.release(first)
        self.assertIs(await pending, gate.permit_for('c'))
        self.assertEqual(gate.snapshot()['active'], 2)
        gate.release(second)
        gate.release(second)
        gate.release(None)
        self.assertEqual(gate.snapshot()['active'], 1)
        gate.release_run('c')
        gate.release_run('c')
        self.assertEqual(gate.snapshot(), {'capacity': 2, 'max_depth': None, 'active': 0,
                                           'queued': 0, 'lent': 0, 'deepest': 0})

    async def test_lend_and_recall_move_the_slot_without_losing_it(self):
        gate = AdmissionGate(capacity=1)
        holder = await gate.acquire('parent', 1)
        blocked = asyncio.create_task(gate.acquire('child', 2))
        await asyncio.sleep(0)
        self.assertTrue(gate.lend(holder))
        self.assertIs(await blocked, gate.permit_for('child'))
        self.assertEqual(gate.snapshot()['active'], 1)
        # 已让渡的名额不能重复让渡，避免同一名额被记账两次。
        self.assertFalse(gate.lend(holder))
        gate.release(gate.permit_for('child'))
        recall = asyncio.create_task(gate.recall(holder))
        await asyncio.sleep(0)
        await recall
        self.assertTrue(holder.held)
        self.assertEqual(gate.snapshot()['active'], 1)
        gate.release(holder)
        self.assertEqual(gate.snapshot()['active'], 0)

    async def test_cancelled_recall_leaves_the_permit_registered_for_the_run_teardown(self):
        gate = AdmissionGate(capacity=1)
        holder = await gate.acquire('parent', 1)
        self.assertTrue(gate.lend(holder))
        occupant = await gate.acquire('child', 2)
        recall = asyncio.create_task(gate.recall(holder))
        await asyncio.sleep(0)
        recall.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await recall
        # 召回被取消后凭证仍登记在闸门里，运行收尾按身份释放即可收敛。
        self.assertIsNotNone(gate.permit_for('parent'))
        self.assertEqual(gate.snapshot()['queued'], 1)
        gate.release(occupant)
        self.assertTrue(gate.permit_for('parent').held)
        gate.release_run('parent')
        self.assertEqual(gate.snapshot()['active'], 0)

    async def test_invalid_bounds_and_depth_are_rejected_before_any_permit(self):
        for capacity in (0, -1, True, 1.5, '2'):
            with self.assertRaises(ValueError):
                AdmissionGate(capacity=capacity)
        for depth in (0, -1, True, 2.5, '3'):
            with self.assertRaises(ValueError):
                AdmissionGate(max_depth=depth)
        gate = AdmissionGate(capacity=1, max_depth=1)
        for depth in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                gate.check_depth(depth)
        self.assertIsNone(gate.check_depth(1))
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                with self.assertRaises(AgentDepthExceeded) as caught:
                    gate.check_depth(2)
                expected = tr('subagent.depth_exceeded', depth=2, max_depth=1)
            self.assertEqual(caught.exception.code, 'agent/depth-exceeded')
            self.assertEqual((caught.exception.depth, caught.exception.max_depth), (2, 1))
            # 错误正文在抛出时按当次语言解析，因此必须与同一语言的资源一致。
            self.assertEqual(caught.exception.message, expected)
        self.assertEqual(gate.snapshot()['active'], 0)


class SubagentTreeAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_depth_is_recorded_and_capped_at_spawn_time(self):
        manager = manager_with(max_depth=1)
        try:
            outer = manager.spawn(prompt='outer task', description='outer', parent_session_id='root-session')
            self.assertEqual(outer.depth, 1)
            await asyncio.sleep(0)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    with self.assertRaises(AgentDepthExceeded) as caught:
                        manager.spawn(prompt='nested task', description='nested', parent_session_id=outer.session_id)
                    expected = tr('subagent.depth_exceeded', depth=2, max_depth=1)
                self.assertEqual(caught.exception.code, 'agent/depth-exceeded')
                self.assertEqual(caught.exception.message, expected)
            # 超限派生不得留下半成品运行记录。
            self.assertEqual([state.id for state in manager.list_states()], [outer.id])
            self.assertEqual(outer.to_dict()['depth'], 1)
        finally:
            await manager.shutdown()

    async def test_nested_spawns_occupy_the_same_tree_budget(self):
        manager = manager_with(max_concurrent=1)
        gate = asyncio.Event()

        async def prompt(agent, session, text, **kwargs):
            if text == 'holder':
                await gate.wait()
                return TurnResult(session.id, 1, 'completed', text='holder done')
            return TurnResult(session.id, 1, 'completed', text='nested done')

        with patch.object(AgentLoop, 'prompt', prompt):
            try:
                holder = manager.spawn(prompt='holder', description='holder', parent_session_id='root-session')
                await asyncio.sleep(0)
                self.assertEqual(manager._gate.snapshot()['active'], 1)
                nested = manager.spawn(prompt='nested', description='nested', parent_session_id=holder.session_id)
                await asyncio.sleep(0)
                # 后代同样占名额：容量已满时排队，不再绕过闸门无界膨胀。
                self.assertEqual(nested.status, PENDING)
                self.assertEqual(manager._gate.snapshot()['queued'], 1)
                gate.set()
                await manager.wait(holder.id, timeout=5)
                await manager.wait(nested.id, timeout=5)
                self.assertEqual(nested.status, 'completed', nested.error)
                self.assertEqual(manager._gate.snapshot()['active'], 0)
            finally:
                gate.set()
                await manager.shutdown()

    async def test_waiting_parent_lends_its_slot_so_the_descendant_can_run(self):
        manager = manager_with(max_concurrent=1)
        observations = []

        async def prompt(agent, session, text, **kwargs):
            if text == 'outer':
                child = manager.spawn(prompt='inner', description='inner', parent_session_id=session.id)
                waiting = asyncio.create_task(manager.wait(child.id, timeout=5))
                await asyncio.sleep(0)
                observations.append(manager._gate.snapshot())
                state = await waiting
                observations.append(manager._gate.snapshot())
                return TurnResult(session.id, 1, 'completed', text=state.status)
            return TurnResult(session.id, 1, 'completed', text='inner done')

        with patch.object(AgentLoop, 'prompt', prompt):
            try:
                outer = manager.spawn(prompt='outer', description='outer', parent_session_id='root-session')
                await manager.wait(outer.id, timeout=5)
                self.assertEqual(outer.status, 'completed', outer.error)
                self.assertEqual(outer.output, 'completed')
                # 等待期间名额已让渡给后代；召回后仍由父代理持有，总数不超过容量。
                self.assertEqual(observations[0], {'capacity': 1, 'max_depth': None, 'active': 1,
                                                   'queued': 0, 'lent': 1, 'deepest': 2})
                self.assertEqual(observations[1]['lent'], 0)
                self.assertEqual(manager._gate.snapshot()['active'], 0)
            finally:
                await manager.shutdown()

    async def test_manager_rejects_invalid_admission_limits_instead_of_disabling_them(self):
        invalid = (
            {'max_concurrent': 0},
            {'max_concurrent': True},
            {'max_concurrent': 1.5},
            {'max_depth': -1},
            {'max_depth': True},
            {'max_depth': 1.5},
            {'job_timeout': True},
            {'job_timeout': False},
            {'job_timeout': 0},
            {'job_timeout': -1},
            {'job_timeout': float('inf')},
            {'job_timeout': float('nan')},
            {'job_timeout': '1'},
        )
        for options in invalid:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    manager_with(**options)

        manager = manager_with(max_concurrent=2, max_depth=3, job_timeout=0.5)
        try:
            self.assertEqual(manager.max_concurrent, 2)
            self.assertEqual(manager.max_depth, 3)
            self.assertEqual(manager.job_timeout, 0.5)
            self.assertEqual(manager._gate.snapshot()['capacity'], 2)
            self.assertEqual(manager._gate.snapshot()['max_depth'], 3)
        finally:
            await manager.shutdown()

    async def test_job_timeout_produces_a_truthful_failure_instead_of_a_hung_run(self):
        manager = manager_with(job_timeout=0.05)

        async def prompt(agent, session, text, **kwargs):
            await asyncio.sleep(30)
            return TurnResult(session.id, 1, 'completed', text='late')

        with patch.object(AgentLoop, 'prompt', prompt):
            try:
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        state = manager.spawn(prompt='slow task', description='slow',
                                              parent_session_id='root-session')
                        await manager.wait(state.id, timeout=5)
                        expected = tr('runtime.validation.e121bb0c13e1')
                    self.assertEqual(state.status, FAILED)
                    self.assertEqual(state.language, language)
                    with current_language.set(language):
                        message = run_error_message(state.error)
                    # 时限失败必须留下可核验正文，不能只呈现空异常名。
                    self.assertIn(expected, message)
                    self.assertNotEqual(message, 'TimeoutError: ')
                    self.assertIn(expected, state.error['parts'][-1]['message'])
            finally:
                await manager.shutdown()


if __name__ == '__main__':
    unittest.main()

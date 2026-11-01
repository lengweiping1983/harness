"""压缩请求恢复、计费及成功节点复用回归。"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.app.runtime import ExecutionBudget
from harness.core.compaction import CompactionExecutor, fingerprint
from harness.core.errors import HarnessError


class CompactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        async def append(kind, data):
            self.events.append(SimpleNamespace(type=kind, data=data))
        self.session = SimpleNamespace(events=self.events, append=append)
        self.agent = SimpleNamespace(budget=ExecutionBudget(10000), _check_cancelled=Mock(),
            adapter=SimpleNamespace(supports_compaction_scope=False), summary_max_tokens=100,
            _summarize_transcript=AsyncMock(return_value='摘要'))
        self.config = SimpleNamespace(max_tokens=100)
        self.identity = [{'model': 'fixture'}, 'input']

    async def test_reserved_request_is_charged_once_across_recovery(self):
        executor = CompactionExecutor(self.agent, self.config, session=self.session)
        await executor.prepare(self.identity)
        executor.current, executor.reserved, executor.index = 'node', 123, 0
        await executor.before_request()
        budget = ExecutionBudget(10000)
        self.agent.budget = budget
        recovered = CompactionExecutor(self.agent, self.config, session=self.session)
        await recovered.prepare(self.identity)
        self.assertEqual(recovered.nodes, {'node': {1: 123}})
        before = budget.spent
        await recovered.prepare(self.identity)
        self.assertEqual(budget.spent, before)
        self.assertEqual(before, 123)

    async def test_successful_node_is_reused_without_model_request(self):
        piece = '需要缩减的历史内容。' * 100
        executor = CompactionExecutor(self.agent, self.config, session=self.session)
        await executor.prepare(self.identity)
        self.assertEqual(await executor.node(piece, 0), '摘要')
        recovered = CompactionExecutor(self.agent, self.config, session=self.session)
        await recovered.prepare(self.identity)
        recovered.load_stage_cache([piece])
        self.assertEqual(await recovered.node(piece, 0), '摘要')
        self.agent._summarize_transcript.assert_awaited_once()
        self.assertEqual(self.events[-1].type, 'compaction/reuse')

    async def test_corrupt_checkpoint_and_stale_context_do_not_call_model(self):
        self.events.append(SimpleNamespace(type='compaction/task', data={'version': 1, 'job_id': 'bad',
            'fingerprint': fingerprint('x'), 'context_fingerprint': fingerprint(self.identity[0])}))
        self.events.append(SimpleNamespace(type='compaction/node', data={'job_id': 'unknown'}))
        executor = CompactionExecutor(self.agent, self.config, session=self.session)
        with self.assertRaises(HarnessError):
            await executor.prepare(self.identity)
        executor.context_guard = lambda: False
        with self.assertRaises(HarnessError) as error:
            await executor.node('input', 0)
        self.assertEqual(error.exception.code, 'agent/compaction-stale')
        self.agent._summarize_transcript.assert_not_awaited()

    async def test_current_checkpoint_requires_both_exact_fingerprints(self):
        valid = {'version': 1, 'job_id': 'fixture', 'fingerprint': fingerprint(self.identity),
                 'context_fingerprint': fingerprint(self.identity[0])}
        invalid = [{key: value for key, value in valid.items() if key != 'context_fingerprint'}]
        invalid.extend({**valid, **changes} for changes in (
            {'context_fingerprint': None}, {'context_fingerprint': 'z' * 64},
            {'fingerprint': 'z' * 64}, {'version': True}, {'job_id': ''},
            {'context_fingerprint': fingerprint('different context')},
        ))
        for data in invalid:
            with self.subTest(data=data):
                self.events[:] = [SimpleNamespace(type='compaction/task', data=data)]
                executor = CompactionExecutor(self.agent, self.config, session=self.session)
                before = self.agent.budget.spent
                with self.assertRaises(HarnessError) as caught:
                    await executor.prepare(self.identity)
                self.assertEqual(caught.exception.code, 'agent/compaction-checkpoint')
                self.assertEqual(len(self.events), 1)
                self.assertEqual(self.agent.budget.spent, before)
        self.agent._summarize_transcript.assert_not_awaited()

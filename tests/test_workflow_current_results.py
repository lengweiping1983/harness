"""真实 Node 引擎使用当前成员运行结果；模型由本地确定性管理器代替。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from harness.core.language import current_language
from harness.core.runs import AgentRun
from harness.tools.builtin.workflow_host import WorkflowGuest, WorkflowLimits


class WorkflowCurrentResultTests(unittest.IsolatedAsyncioTestCase):
    async def run_script(self, script, output, language='en', status='completed'):
        state = AgentRun(id='fixture-run', status=status, result={'text': output})
        manager = SimpleNamespace(spawn=Mock(return_value=state),
            wait=lambda *args, **kwargs: asyncio.sleep(0), interrupt=Mock())
        with current_language.set(language):
            guest = WorkflowGuest(manager, script=script, meta={'name': 'fixture'}, args=None,
                limits=WorkflowLimits(), parent_session_id='fixture-parent')
            result = await asyncio.wait_for(guest.run(), 5)
        self.assertEqual(guest._proc.returncode, 0)
        self.assertEqual(guest._children, {})
        self.assertEqual(guest._rpc_tasks, set())
        manager.interrupt.assert_not_called()
        return result, manager

    async def test_plain_results_are_text_instead_of_structured_run_record(self):
        for language in ('en', 'zh-CN'):
            result, manager = await self.run_script('return await agent("Original 用户任务")', 'Original 回复😀', language)
            self.assertEqual(result['stopReason'], 'completed')
            self.assertEqual(result['value'], 'Original 回复😀')
            self.assertEqual(manager.spawn.call_args.kwargs['prompt'], 'Original 用户任务')
            self.assertEqual(manager.spawn.call_args.kwargs['parent_session_id'], 'fixture-parent')

    async def test_structured_result_uses_current_text_and_localized_instruction(self):
        script = 'return await agent("Original 用户任务", {schema: {type: "object", properties: {answer: {type: "string"}}, required: ["answer"]}})'
        for language in ('en', 'zh-CN', 'en'):
            result, manager = await self.run_script(script, '{"answer":"Original 原文😀"}', language)
            self.assertEqual(result['stopReason'], 'completed')
            self.assertEqual(result['value'], {'answer': 'Original 原文😀'})
            prompt = manager.spawn.call_args.kwargs['prompt']
            self.assertTrue(prompt.startswith('Original 用户任务\n\n'))
            self.assertIn('仅回复' if language == 'zh-CN' else 'Reply with ONLY', prompt)
            self.assertIn('JSON Schema', prompt)

    async def test_invalid_structured_output_and_failed_child_do_not_become_successful_data(self):
        result, _ = await self.run_script('return await agent("fixture", {schema: {type:"object", properties:{n:{type:"integer"}}, required:["n"]}})', '{"n":"wrong"}')
        self.assertEqual(result['stopReason'], 'error')
        self.assertIsNone(result['value'])
        result, _ = await self.run_script('return await agent("fixture")', 'original failure', status='failed')
        self.assertEqual(result['stopReason'], 'completed')
        self.assertIsNone(result['value'])

    async def test_model_override_reaches_current_manager_and_provider_rejection_starts_nothing(self):
        result, manager = await self.run_script('return await agent("fixture", {provider:"spawn", model:"fixture/model"})', 'reply')
        self.assertEqual(result['value'], 'reply')
        self.assertEqual(manager.spawn.call_args.kwargs['model'], 'fixture/model')
        for options in ('{provider:"unsupported"}', '{model:""}', '{model:"   "}'):
            result, manager = await self.run_script('return await agent("fixture", ' + options + ')', 'reply')
            self.assertEqual(result['stopReason'], 'error')
            manager.spawn.assert_not_called()

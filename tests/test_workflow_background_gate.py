"""工作流后台运行必须同时满足能力开关和作业表条件。"""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.types import ToolUseBlock
from harness.tools.builtin.workflow_tools import build_workflow_tools
from harness.tools.registry import ToolRegistry, ToolRunContext


class WorkflowBackgroundGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_background_is_rejected_before_guest_creation_in_both_entry_paths(self):
        args = {'script': 'return args;', 'meta': {'name': 'Original 原文', 'description': 'Original'},
                'args': {'Original': '{name}'}, 'run_in_background': True}
        original = copy.deepcopy(args)
        ctx = ToolRunContext('workflow', 'fixture', None, 1, 1)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for enabled, has_jobs in ((False, False), (True, False), (False, True)):
                    jobs = SimpleNamespace(spawn=AsyncMock()) if has_jobs else None
                    tool = build_workflow_tools(SimpleNamespace(), jobs=jobs, enable_run_in_background=enabled)[0]
                    registry = ToolRegistry()
                    registry.register(tool)
                    with patch('harness.tools.builtin.workflow_tools.WorkflowGuest') as guest:
                        with self.assertRaises(ToolExecutionError) as error:
                            await tool.execute(args, ctx)
                        self.assertEqual(error.exception.message, tr('workflow.background.unavailable'))
                        result = await registry.execute_call(ToolUseBlock(id='workflow', name='workflow', input=args), ctx)
                        self.assertTrue(result.is_error)
                        guest.assert_not_called()
                    if jobs is not None:
                        jobs.spawn.assert_not_awaited()
        self.assertEqual(args, original)

    async def test_foreground_and_explicitly_enabled_background_remain_available(self):
        args = {'script': 'return args;', 'meta': {'name': 'Original 原文', 'description': 'Original'},
                'args': {'Original': '{name}'}}
        ctx = ToolRunContext('workflow', 'fixture', None, 1, 1)
        for enabled in (False, True):
            jobs = SimpleNamespace(spawn=AsyncMock(return_value=SimpleNamespace(id='job-original')))
            tool = build_workflow_tools(SimpleNamespace(), jobs=jobs, enable_run_in_background=enabled)[0]
            guest = SimpleNamespace(run=AsyncMock(return_value={'stopReason': 'completed', 'agentsStarted': 0, 'value': args['args']}))
            factory = Mock(return_value=guest)
            with patch('harness.tools.builtin.workflow_tools.WorkflowGuest', factory):
                result = await tool.execute(args, ctx)
                self.assertEqual(result['kind'], 'foreground')
                self.assertEqual(result['result'], args['args'])
                guest.run.assert_awaited_once()
                jobs.spawn.assert_not_awaited()
                if enabled:
                    result = await tool.execute({**args, 'run_in_background': True}, ctx)
                    self.assertEqual(result['kind'], 'background')
                    self.assertEqual(result['jobId'], 'job-original')
                    jobs.spawn.assert_awaited_once()
                    self.assertEqual(guest.run.await_count, 1)

    async def test_enabled_background_runs_through_real_job_registry_once(self):
        from harness.tools.builtin.job_tools import JobRegistry

        jobs = JobRegistry()
        self.addAsyncCleanup(jobs.cancel_all)
        tool = build_workflow_tools(SimpleNamespace(), jobs=jobs)[0]
        ctx = ToolRunContext('workflow', 'fixture', None, 1, 1)
        args = {'script': 'return "Original";', 'meta': {'name': 'Original 原文', 'description': 'Original'},
                'run_in_background': True}
        guest = SimpleNamespace(run=AsyncMock(return_value={'stopReason': 'completed', 'agentsStarted': 0, 'value': 'Original 原文'}))
        with patch('harness.tools.builtin.workflow_tools.WorkflowGuest', return_value=guest):
            result = await tool.execute(args, ctx)
            job = jobs._require(result['jobId'], ctx.session_id)
            await job.task
        guest.run.assert_awaited_once()
        self.assertEqual(job.status, 'completed')
        self.assertIn('Original 原文', jobs.read(job.id, ctx.session_id)[0])

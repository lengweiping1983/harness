"""当前工具输出按调用语言呈现，保留工作流结果与用户名字原文。"""
import unittest
from types import SimpleNamespace

from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.workflow_tools import build_workflow_tools, render_result, stop_reason_error, job_outcome_of
from harness.tools.builtin.workspace_tools import _render
from harness.tools.builtin.subagent_tools import build_subagent_tools
from harness.tools.registry import ToolRunContext


class WorkflowOutputLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_tool_instances_resolve_runtime_language(self):
        manager = SimpleNamespace(list_states=lambda: [], visible_to=lambda *args: True)
        with current_language.set('zh-CN'):
            workflow = build_workflow_tools(manager)[0]
            listing = next(tool for tool in build_subagent_tools(manager) if tool.schema.name == 'list_agents')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                output = render_result('Original 名字', 2, {'original': '用户原文'})
                self.assertTrue(output.startswith(tr('workflow.completed', name='Original 名字', agents=tr('workflow.agent_many', count=2))))
                self.assertIn('用户原文', output)
                self.assertIn(tr('workflow.truncated', count=len(output)-5), render_result('Original 名字', 2, {'original': '用户原文'}, 5))
                self.assertEqual(stop_reason_error({'stopReason': 'completed'}), None)
                self.assertEqual(stop_reason_error({'stopReason': 'error'}), tr('workflow.failed', error=tr('workflow.unknown_error')))
                self.assertEqual(job_outcome_of({'stopReason': 'completed', 'agentsStarted': 1, 'value': '原文'}, 'name')['detail'], tr('workflow.agent_one', count=1))
                self.assertEqual(workflow.to_content({'meta': {'name': 'Original 名字'}}, {'kind': 'background', 'jobId': 'job', 'runId': 'run'}), tr('workflow.background', name='Original 名字', job='job', run='run'))
                data = {'python': {'version': '3.fixture', 'implementation': 'cpython', 'executable': '/fixture', 'site_packages_dirs': []}, 'node': []}
                self.assertIn(tr('workspace.python_runtime', version='3.fixture', implementation='cpython'), _render(data))
                result = await listing.execute({}, ToolRunContext('fixture', 'session', None, 1, 1))
                if language == 'en':
                    self.assertNotIn('。', result.content[0].text)
                    self.assertNotIn('（', _render(data))

"""可视化交付策略须进入真实模型请求，不能只注册工具或存在资源。"""
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.core.language import current_language
from harness.i18n import tr
from harness.prompt.tool_policies import build_tool_policies
from tests.agent_configuration_fixture import RecordingTransport


class VisualizationPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_policy_follows_capability_and_current_language(self):
        self.assertNotIn('tool:show_widget', {name for name, _, _ in build_tool_policies({'bash'})})
        provider = next(provider for name, _, provider in build_tool_policies({'show_widget'}) if name == 'tool:show_widget')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                text = provider()
                self.assertEqual(text, tr('policy.visualization'))
                for literal in ('show_widget', 'widget_code', 'html type="renderer"', 'read_image'):
                    self.assertIn(literal, text)

    async def test_same_host_delivers_localized_policy_and_schema_to_model_without_workspace_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / 'workspace'
            workspace.mkdir()
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            transport = RecordingTransport()
            host = Harness(HarnessConfig(api_key='fixture-only', home=root / 'state', cwd=workspace,
                model='fixture-model', max_steps=2, max_retries=0, **flags), transport=transport,
                codex_gateway_home=root / 'gateway')
            try:
                session = host.create_session()
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        before = len(transport.requests)
                        host.config.language = language
                        result = await host.run_async(session, 'Show an HTML visualization')
                        self.assertEqual(result.reason, 'completed', result.error)
                        self.assertEqual(len(transport.requests), before + 1)
                        request = transport.requests[-1]
                        instructions = '\n'.join(message['content'] for message in request['messages']
                                                 if message['role'] in ('system', 'developer') and isinstance(message['content'], str))
                        self.assertIn(tr('policy.visualization'), instructions)
                        schema = next(tool['function'] for tool in request['tools'] if tool['function']['name'] == 'show_widget')
                        self.assertEqual(schema['description'], tr('python.widget_tools.show.description'))
                        self.assertEqual(list(workspace.iterdir()), [])
            finally:
                await host.close()

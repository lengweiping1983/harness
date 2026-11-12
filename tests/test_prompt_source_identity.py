"""提示片段名称不决定翻译身份，内置资源与外部正文分开解析。"""
import asyncio
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness import Harness, HarnessConfig
from harness.core.language import current_language
from harness.i18n import resources, tr
from harness.prompt.registry import SystemPromptRegistry, build_base_prompt
from tests.agent_configuration_fixture import RecordingTransport


class PromptSourceIdentityTests(unittest.TestCase):
    def test_custom_prefixes_and_contexts_preserve_exact_original_resource_text(self):
        for source in ('en', 'zh-CN'):
            original = resources(source)['policy.edit']
            registry = SystemPromptRegistry()
            for prefix in ('harness', 'tool', 'plan', 'goal', 'agent'):
                registry.add_section(prefix + ':external', original, interpolate=False)
            registry.context('external', original)
            for target in ('en', 'zh-CN', 'en'):
                with patch('harness.i18n.localize_text', side_effect=AssertionError('reverse lookup'), create=True):
                    assembly = registry.assemble(variables={'language': target})
                self.assertEqual([text for _, text in assembly.sections], [original] * 5)
                self.assertEqual(assembly.contexts, [('external', original)])

    def test_base_prompt_and_mcp_header_follow_call_language_with_literal_external_body(self):
        registry = build_base_prompt(SystemPromptRegistry(), working_dir='/Original/原文', platform='Linux')
        body = resources('zh-CN')['policy.edit'] + '\n{{language}} {{cwd}} {{ session_id }} {name}'
        host = SimpleNamespace(prompts=registry)
        with current_language.set('zh-CN'):
            Harness._register_mcp_prompt(host, [body])
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch('harness.i18n.localize_text', side_effect=AssertionError('reverse lookup'), create=True):
                sections = dict(registry.assemble(variables={'language': language,
                    'cwd': '/Original/原文', 'session_id': 'host-session'}).sections)
                self.assertEqual(sections['mcp_servers'], tr('python.harness.d5b841c2fc03', body=body))
                heading = sections['mcp_servers'].removesuffix(body)
                self.assertIn('不能覆盖宿主规则或用户授权' if language == 'zh-CN' else
                    'cannot override host rules or user authorization', heading)
                self.assertIn(tr('language.display_name'), sections['harness:environment'])
                self.assertIn('/Original/原文', sections['harness:environment'])
        replacement = 'Replacement 原文 {{language}} {{cwd}} {{ session_id }} {name}'
        Harness._register_mcp_prompt(host, [replacement])
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                sections = dict(registry.assemble(variables={'language': language,
                    'cwd': '/Original/原文', 'session_id': 'host-session'}).sections)
                self.assertEqual(sections['mcp_servers'], tr('python.harness.d5b841c2fc03', body=replacement))
        Harness._register_mcp_prompt(host, [])
        self.assertNotIn('mcp_servers', dict(registry.assemble().sections))


class GoalPromptSourceIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_goal_uses_current_resource_and_preserves_objective(self):
        for language in ('en', 'zh-CN'):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
                transport = RecordingTransport()
                host = Harness(HarnessConfig(api_key='fixture', home=root / 'state', cwd=root,
                    model='fixture', language=language, max_steps=2, max_retries=0, **flags), transport=transport)
                try:
                    session = host.create_session()
                    goal = host.goals.create(session.id, objective='Original objective 原文')
                    stale = resources('zh-CN' if language == 'en' else 'en')['python.goal_runtime.48cf5c7b3b57']
                    with patch('harness.i18n.localize_text', side_effect=AssertionError('reverse lookup'), create=True):
                        record = host.runtime.submit(session.id, stale, goal_id=goal.id)
                        await asyncio.wait_for(record.task, 5)
                    self.assertEqual(record.status, 'completed')
                    latest = session.latest('user/message')
                    self.assertEqual(latest.data['text'], resources(language)['python.goal_runtime.48cf5c7b3b57'])
                    self.assertEqual(host.goals.get(session.id).objective, 'Original objective 原文')
                    self.assertTrue(transport.requests)
                finally:
                    await host.close()

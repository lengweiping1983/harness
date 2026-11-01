"""策略与提醒直接读取资源身份，调用方原文不能被策略名称误翻译。"""
import unittest
from unittest.mock import patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.prompt.registry import SystemPromptRegistry
from harness.prompt.tool_policies import build_tool_policies
from harness.tools.repeat_reminder import RepeatToolReminder


class PolicyResourceIdentityTests(unittest.TestCase):
    def test_same_providers_and_assembly_follow_explicit_language(self):
        names = {'read', 'write', 'edit', 'glob', 'grep', 'job_output', 'terminal_list',
                 'lsp', 'create_goal', 'todo_write', 'browser'}
        providers = build_tool_policies(names)
        registry = SystemPromptRegistry()
        for name, order, provider in providers:
            registry.add_section(name, lambda variables, provider=provider: provider(), order=order)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                expected = {name: provider() for name, _, provider in providers}
                self.assertEqual(expected['tool:edit'], tr('policy.edit'))
                self.assertEqual(expected['tool:write'], tr('policy.write_edit'))
                self.assertEqual(expected['tool:grep'], tr('policy.grep_read'))
                for value in expected.values():
                    self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in value), language == 'zh-CN')
            with current_language.set('zh-CN' if language == 'en' else 'en'):
                outer = current_language.get()
                # 不允许借助反向翻译修补已装配的策略。
                with patch('harness.i18n.localize_text', side_effect=AssertionError('reverse lookup'), create=True):
                    result = dict(registry.assemble(variables={'language': language}).sections)
                self.assertEqual(result, expected)
                self.assertEqual(current_language.get(), outer)
        plain = dict((name, provider()) for name, _, provider in build_tool_policies({'write', 'grep'}))
        self.assertEqual(plain['tool:write'], tr('policy.write'))
        self.assertEqual(plain['tool:grep'], tr('policy.grep'))

    def test_same_reminder_uses_current_language_without_reverse_lookup(self):
        reminder = RepeatToolReminder(thresholds=[2, 3])
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch('harness.i18n.localize_text', side_effect=AssertionError('reverse lookup'), create=True):
                reminder.reset('fixture')
                self.assertIsNone(reminder.observe('fixture', 'OriginalTool', {'原文': 'Original'}))
                self.assertEqual(reminder.observe('fixture', 'OriginalTool', {'原文': 'Original'}),
                                 tr('owned.repeat_reminder.b8da397db9f0'))
                detailed = reminder.observe('fixture', 'OriginalTool', {'原文': 'Original'})
                self.assertIn('OriginalTool', detailed)
                self.assertIn('原文', detailed)


class SummaryResourceIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_harness_summary_identity_and_query_guidance_use_runtime_keys(self):
        import tempfile
        from dataclasses import fields
        from pathlib import Path
        from harness import Harness, HarnessConfig
        from tests.web_fixture import FixtureTransport
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            flags['enable_session_query'] = True
            host = Harness(HarnessConfig(api_key='fixture-only', home=root / 'state', cwd=root,
                model='fixture-model', **flags), transport=FixtureTransport())
            try:
                session = host.create_session()
                agent = host._agent_for(session)
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language), patch('harness.i18n.localize_text', side_effect=AssertionError('reverse lookup'), create=True):
                        identity = agent._summary_identity(agent.config, session)
                        self.assertEqual(identity['policy'], tr('python.compaction_policy.86317a18f41a'))
                        sections = dict(host.prompts.assemble(variables={'language': language, 'session_id': session.id}).sections)
                        self.assertEqual(sections['harness:session-query'], tr('owned.session_query_tools.aa7040015ebe'))
            finally:
                await host.close()

    async def test_summary_policy_reaches_ordinary_and_zen_wire_requests_in_call_language(self):
        import tempfile
        from dataclasses import fields
        from pathlib import Path
        from harness import Harness, HarnessConfig
        from harness.llm.opencode import ZEN_URL
        from tests.agent_configuration_fixture import RecordingTransport
        transcript = 'Original transcript 原文 {language}'
        for base_url in ('https://offline.invalid/v1', ZEN_URL):
            with self.subTest(base_url=base_url), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
                transport = RecordingTransport()
                host = Harness(HarnessConfig(api_key='fixture-only', home=root / 'state', cwd=root,
                    base_url=base_url, model='fixture-model', max_retries=0, **flags), transport=transport)
                try:
                    session = host.create_session()
                    agent = host._agent_for(session)
                    for language in ('en', 'zh-CN', 'en'):
                        with self.subTest(language=language), current_language.set(language):
                            previous = len(transport.requests)
                            result = await agent._summarize_transcript(transcript, agent.config, session=session)
                            self.assertEqual(result, 'fixture response verified')
                            self.assertEqual(len(transport.requests), previous + 1)
                            messages = transport.requests[-1]['messages']
                            policy = tr('python.compaction_policy.86317a18f41a')
                            if base_url == ZEN_URL:
                                task = next(message['content'] for message in messages if message['role'] == 'user')
                                self.assertTrue(task.startswith(policy + '\n\n'))
                                self.assertIn(tr('context.compaction.zenInstructions', tokens=8192), task)
                            else:
                                self.assertEqual(messages[0], {'role': 'system', 'content': policy})
                            self.assertTrue(any(transcript in message['content'] for message in messages))
                    self.assertEqual(len(transport.requests), 3)
                finally:
                    await host.close()

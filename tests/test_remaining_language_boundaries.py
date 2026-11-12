"""真实适配器、注册表和成员背景入口使用显式语言资源。"""
import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from harness import Harness, HarnessConfig
from harness.core.language import current_language
from harness.core.errors import LlmError
from harness.i18n import tr
from harness.llm.adapter import OpenRouterAdapter
from harness.llm.types import LlmCallConfig
from harness.web.registry import WorkspaceRegistry
from tests.agent_configuration_fixture import RecordingTransport


class RemainingLanguageBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_subscription_fails_before_transport_in_each_language(self):
        transport = RecordingTransport()
        adapter = OpenRouterAdapter('fixture', transport=transport)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), self.assertRaises(LlmError) as caught:
                async for _ in adapter.stream([], config=LlmCallConfig(model='chatgpt/fixture')):
                    self.fail('Unexpected model event')
            with current_language.set(language):
                self.assertEqual(caught.exception.message, tr('chatgpt.login_required'))
            self.assertEqual(caught.exception.code, 'llm/chatgpt-auth')
        self.assertEqual(transport.requests, [])

    async def test_unavailable_subscription_authorization_never_sends_request(self):
        from harness.llm.chatgpt_auth import ChatGPTAccount
        with tempfile.TemporaryDirectory() as directory:
            transport = RecordingTransport()
            adapter = OpenRouterAdapter('fixture', transport=transport, max_retries=0)
            adapter._chatgpt_endpoint = ChatGPTAccount(Path(directory))
            adapter._chatgpt_epoch = adapter._chatgpt_endpoint.epoch
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language), self.assertRaises(LlmError) as caught:
                    async for _ in adapter.stream([], config=LlmCallConfig(model='fixture')):
                        self.fail('Unexpected model event')
                with current_language.set(language):
                    self.assertEqual(caught.exception.message, tr('llm.chatgpt.auth_unavailable'))
                self.assertEqual(caught.exception.code, 'llm/chatgpt-auth')
            self.assertEqual(transport.requests, [])

    def test_invalid_registry_preserves_bytes_in_each_language(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'workspaces.json'
            original = b'{"version":0,"workspaces":[]}'
            path.write_bytes(original)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language), self.assertRaises(ValueError) as caught:
                    WorkspaceRegistry(directory).list()
                with current_language.set(language):
                    self.assertEqual(str(caught.exception), tr('workspace.registry.current_format'))
                self.assertEqual(path.read_bytes(), original)

    async def test_member_context_notice_uses_spawn_language_and_preserves_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            app = Harness(HarnessConfig(api_key='fixture', home=root/'state', cwd=root, max_steps=2, max_retries=0, **flags), transport=RecordingTransport())
            try:
                parent = app.create_session()
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        member = app.subagents.spawn(prompt='fixture task', description='fixture', parent_session_id=parent.id, fork_turns='all')
                    await app.subagents.wait(member.id, timeout=5)
                    self.assertEqual(member.status, 'completed', member.error)
                    events = [event for event in member.session.events_of_type('developer/message') if event.data.get('source') == 'agent-inheritance']
                    self.assertEqual(len(events), 1)
                    text = events[0].data['text']
                    with current_language.set(language):
                        prefix = tr('agent.inheritance.context_notice')
                    self.assertTrue(text.startswith(prefix))
                    payload = json.loads(text[len(prefix):])
                    self.assertEqual(payload['source_session_id'], parent.id)
                    self.assertEqual(payload['fork_turns'], 'all')
            finally:
                await app.close()

"""计划模式的敏感引用与机器选项身份回归。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.app.plan_mode import PlanModeController, contains_sensitive_literal, is_sensitive_reference
from harness.core.events import EventBus
from harness.core.errors import ToolExecutionError
from harness.interaction.questions import QuestionBroker
from harness.session.store import SessionStore
from harness.tools.builtin.plan_tools import build_exit_plan_mode_tool
from harness.tools.registry import ToolRunContext
from harness.tools.builtin.fs_tools import build_fs_tools


class PlanSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = SessionStore(Path(self.directory.name))
        self.session = self.store.create()
        self.controller = PlanModeController(self.store)
        await self.controller.set(self.session, True)
        self.ctx = ToolRunContext(call_id='plan-security', session_id=self.session.id,
                                  cwd=self.directory.name, turn=1, step=1,
                                  services={'session': self.session})

    async def test_sensitive_references_are_withheld_but_safe_examples_remain_readable(self):
        for value in ('.env', 'cat .env', '--path=.env', 'credentials.txt',
                      'config/oauth-credentials.json', 'keys/id_ed25519', 'certs/app.pem', '.ssh/config'):
            self.assertTrue(is_sensitive_reference(value), value)
        for value in ('.env.example', 'cat .env.example', 'docs/credentials.template', 'config.sample.yaml'):
            self.assertFalse(is_sensitive_reference(value), value)

        read = SimpleNamespace(schema=SimpleNamespace(name='read'), effect='read', dangerous=False, approval_required=False)
        self.assertIsNotNone(self.controller.guard(read, {'path': '.env'}, self.ctx))
        self.assertIsNone(self.controller.guard(read, {'path': '.env.example'}, self.ctx))
        terminal = SimpleNamespace(schema=SimpleNamespace(name='terminal_read'), effect='read', dangerous=False, approval_required=False)
        self.assertIsNotNone(self.controller.guard(terminal, {}, self.ctx))
        self.assertTrue(contains_sensitive_literal('Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345'))
        self.assertTrue(contains_sensitive_literal('-----BEGIN PRIVATE KEY-----'))
        self.assertFalse(contains_sensitive_literal('api_key = <redacted>'))

    async def test_content_search_does_not_treat_member_names_as_file_paths(self):
        tools = {tool.schema.name: tool for tool in build_fs_tools(self.directory.name)}
        for arguments in ({'pattern': 'event.key', 'path': '.', 'glob': '*.js'},
                          {'pattern': '.env', 'path': '.', 'glob': '*.md'}):
            self.assertIsNone(self.controller.guard(tools['grep'], arguments, self.ctx))
        for name, arguments in (('grep', {'pattern': 'event.key', 'path': '.env'}),
                                ('grep', {'pattern': 'event.key', 'glob': '**/*.key'}),
                                ('glob', {'pattern': '**/*.key'})):
            self.assertIsNotNone(self.controller.guard(tools[name], arguments, self.ctx))

    async def test_question_labels_produce_stable_option_ids(self):
        broker = QuestionBroker(EventBus())
        task = asyncio.create_task(broker.ask(
            questions=[{'id': 'plan-review', 'question': 'Approve?',
                        'options': [{'id': 'approve', 'label': '批准'}, {'id': 'keep-planning', 'label': '继续规划'}]}],
            session_id=self.session.id))
        await asyncio.sleep(0)
        question_id = broker.pending()[0]['question_id']
        await broker.answer_and_notify(question_id, [{'id': 'plan-review', 'selected': ['批准']}], session_id=self.session.id)
        answers = await task
        self.assertEqual(answers[0]['selected'], ['批准'])
        self.assertEqual(answers[0]['selected_ids'], ['approve'])

    async def test_plan_with_secret_literal_is_rejected_before_persistence(self):
        questions = SimpleNamespace(ask=AsyncMock())
        tool = build_exit_plan_mode_tool(self.controller, questions)
        with self.assertRaises(ToolExecutionError):
            await tool.execute({'plan': '# 计划\napi_key = abcdefghijklmnop'}, self.ctx)
        self.assertEqual(questions.ask.await_count, 0)

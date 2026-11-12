"""真实宿主的计划命令通知和资源边界错误使用调用时语言。"""
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace

from harness import Harness, HarnessConfig
from harness.core.errors import HarnessError, ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from tests.web_fixture import FixtureTransport


class HarnessCommandLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        flags = {field.name: False for field in fields(HarnessConfig) if field.name.startswith('enable_')}
        flags['enable_plan_mode'] = True
        self.host = Harness(HarnessConfig(api_key='fixture-only', home=root/'state', cwd=root,
            model='fixture', **flags), transport=FixtureTransport())
        self.addAsyncCleanup(self.host.close)

    async def test_plan_notices_localize_full_sentence_and_outcome_without_changing_task(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                session = self.host.create_session()
                for command, outcome, task in [('/plan on Original task 中文', 'committed', 'Original task 中文'),
                        ('/plan', 'noop', ''), ('/plan off', 'committed', ''), ('/plan off', 'noop', '')]:
                    text, notice = await self.host._apply_slash_command(session, command)
                    self.assertEqual(text, task)
                    self.assertIn(tr('plan.mode.outcome.'+outcome), notice)
                    self.assertEqual('（' in notice, language == 'zh-CN')
                    self.assertEqual('Entered' in notice or 'Exited' in notice, language == 'en')
                await session.start_turn()
                text, notice = await self.host._apply_slash_command(session, '/plan on preserved task')
                self.assertEqual(text, 'preserved task')
                self.assertIn(tr('plan.mode.outcome.queued'), notice)
                self.assertFalse(self.host.plan.projection(session).active)
                self.assertTrue(self.host.plan.projection(session).pending)
                self.assertTrue(self.host.plan.is_active_session_id(session.id))
                await self.host.plan.settle(session)
                self.assertTrue(self.host.plan.is_active_session_id(session.id))
                text, notice = await self.host._apply_slash_command(session, '/plan off')
                self.assertEqual(text, '')
                self.assertIn(tr('plan.mode.outcome.queued'), notice)
                self.assertTrue(self.host.plan.projection(session).active)
                self.assertFalse(self.host.plan.projection(session).pending)
                await self.host.plan.settle(session)
                self.assertFalse(self.host.plan.is_active_session_id(session.id))

    async def test_active_terminal_error_is_bilingual_and_does_not_delete_session(self):
        session = self.host.create_session()
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch.object(self.host.terminals, 'list', return_value=[{'fixture': True}]):
                with self.assertRaises(HarnessError) as caught:
                    self.host.delete_session(session.id)
                self.assertEqual(caught.exception.code, 'session/resources-active')
                self.assertEqual(caught.exception.message, tr('harness.session.resources_active'))
                self.assertIn('delete_session_async', caught.exception.message)
                self.assertIs(self.host.get_session(session.id), session)

    async def test_plan_busy_deletion_preserves_real_session_and_localizes_error(self):
        for language in ('en', 'zh-CN', 'en'):
            session = self.host.create_session()
            lock = self.host.plan.review_lock(session.id)
            await lock.acquire()
            try:
                with current_language.set(language), self.assertRaises(HarnessError) as caught:
                    self.host.delete_session(session.id)
                self.assertEqual(caught.exception.code, 'plan/busy')
                with current_language.set(language):
                    self.assertEqual(caught.exception.message, tr('ui.collaboration.error.plan/busy'))
                self.assertIs(self.host.get_session(session.id), session)
            finally:
                lock.release()
            self.host.delete_session(session.id)

    async def test_permission_busy_deletion_preserves_session_and_localizes_error(self):
        for language in ('en', 'zh-CN', 'en'):
            session = self.host.create_session()
            policy = self.host.permissions
            await policy.set_policy(session.id, None, expected_version=policy.snapshot(session.id)['version'])
            lock = policy._locks[session.id]
            await lock.acquire()
            try:
                with current_language.set(language), self.assertRaises(HarnessError) as caught:
                    self.host.delete_session(session.id)
                self.assertEqual(caught.exception.code, 'permissions/busy')
                with current_language.set(language):
                    self.assertEqual(caught.exception.message, tr('ui.collaboration.error.permissions/busy'))
                self.assertIs(self.host.get_session(session.id), session)
            finally:
                lock.release()
            self.host.delete_session(session.id)

    async def test_subagent_rejections_localize_before_any_spawn(self):
        session = self.host.create_session()
        context = SimpleNamespace(session_id=session.id, call_id='fixture')
        external = SimpleNamespace(metadata={'runner': {'command': 'fixture'}})
        registry = SimpleNamespace(get=lambda name: external)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch.object(self.host, '_agent_persona_for_subagent', return_value='Original persona 中文'), patch.object(self.host, 'agents_for', return_value=registry), patch.object(self.host.subagents, 'spawn', Mock()) as spawn:
                for override in [{'model': 'fixture'}, {'reasoning_effort': 'low'}, {'fork_turns': 'all'}]:
                    with self.assertRaises(ToolExecutionError) as caught:
                        await self.host._spawn_configured_subagent({'agent': 'fixture', 'prompt': 'original task', **override}, context)
                    self.assertEqual(caught.exception.message, tr('harness.subagent.runner_context_unsupported'))
                for previous in [SimpleNamespace(prompt_input={'member': 'fixture'}, parent_session_id=session.id), SimpleNamespace(prompt_input={}, scope='old-run')]:
                    with self.assertRaises(ValueError) as caught:
                        await self.host._followup_subagent(previous, 'Original task 中文', {}, context)
                    self.assertEqual(str(caught.exception), tr('harness.subagent.team_followup_unavailable'))
                spawn.assert_not_called()

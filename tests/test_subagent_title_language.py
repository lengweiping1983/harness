"""子对话标题按派工语言生成，恢复上下文时保留原始标题。"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.core.agent import AgentLoop
from harness.core.language import current_language
from harness.core.runs import AgentRun
from harness.core.turn import TurnResult
from harness.session.store import SessionStore
from harness.subagent.manager import SubagentManager
from harness.tools.registry import ToolRegistry


class SubagentTitleLanguageTests(unittest.IsolatedAsyncioTestCase):
    def manager(self, contexts=None):
        return SubagentManager(adapter=SimpleNamespace(model='fixture'), tools=ToolRegistry(), prompts=None, contexts=contexts)

    async def prompt(self, session, text, **kwargs):
        return TurnResult(session.id, 1, 'completed', text='Original result 原文')

    async def test_new_titles_follow_parent_language_and_reused_context_keeps_original_title(self):
        description = 'Original-{name} 原文'
        for managed in (False, True):
            for language in ('en', 'zh-CN', 'en'):
                contexts = SessionStore() if managed else None
                manager = self.manager(contexts)
                manager.parent_language = Mock(return_value=language)
                outer = 'zh-CN' if language == 'en' else 'en'
                expected = ('Subagent: ' if language == 'en' else '子智能体：') + description
                with self.subTest(managed=managed, language=language), current_language.set(outer), patch.object(AgentLoop, 'prompt', side_effect=self.prompt):
                    try:
                        state = manager.spawn(prompt='Original task', description=description, parent_session_id='owner')
                        self.assertEqual(state.session.header.title, expected)
                        self.assertEqual(state.language, language)
                        self.assertEqual(current_language.get(), outer)
                        manager.parent_language.assert_called_once_with('owner')
                        await manager.wait(state.id, timeout=1)
                        self.assertEqual(state.status, 'completed')
                        self.assertTrue(state.settled)
                        if contexts is not None:
                            manager.parent_language.return_value = outer
                            reused = manager.spawn(prompt='Next task 原文', description='New description', parent_session_id='owner', context_session_id=state.session_id)
                            self.assertEqual(reused.session.header.title, expected)
                            self.assertEqual(reused.language, outer)
                            await manager.wait(reused.id, timeout=1)
                            self.assertEqual(reused.status, 'completed')
                    finally:
                        await manager.shutdown()

    async def test_direct_spawn_and_missing_session_use_runtime_language(self):
        for language in ('en', 'zh-CN', 'en'):
            manager = self.manager()
            with current_language.set(language), patch.object(AgentLoop, 'prompt', side_effect=self.prompt):
                try:
                    state = manager.spawn(prompt='Original-{value} 原文', description='', parent_session_id='owner')
                    expected = ('Subagent: ' if language == 'en' else '子智能体：') + 'Original-{value} 原文'
                    self.assertEqual(state.session.header.title, expected)
                    await manager.wait(state.id, timeout=1)
                    self.assertEqual(state.status, 'completed')
                finally:
                    await manager.shutdown()
            manager = self.manager()
            state = AgentRun(id='fallback', session_id='fallback-session', description='Fallback-{value} 原文', text='Task', parent_session_id='owner', language=language)
            with current_language.set('en' if language == 'zh-CN' else 'zh-CN'), patch.object(AgentLoop, 'prompt', side_effect=self.prompt):
                await manager._run(state, None)
                self.assertEqual(state.session.header.title, ('Subagent: ' if language == 'en' else '子智能体：') + 'Fallback-{value} 原文')
                self.assertEqual(state.status, 'completed')
                self.assertTrue(state.settled)
                self.assertTrue(state.session._closed)

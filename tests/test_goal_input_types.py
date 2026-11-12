"""目标正文和阻塞原因拒绝强制转换，非法输入不得产生持久变更。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness.core.errors import ToolError
from harness.core.language import current_language
from harness.i18n import tr
from harness.session.store import SessionStore
from harness.tools.builtin.goal_tools import GoalStore, build_goal_tools


class GoalInputTypeTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_objectives_are_rejected_by_sdk_and_tool_without_writing(self):
        for language in ('en', 'zh-CN', 'en'):
            with tempfile.TemporaryDirectory() as folder, current_language.set(language):
                sessions = SessionStore(folder)
                session = sessions.create()
                store = GoalStore(sessions=sessions)
                create = next(tool for tool in build_goal_tools(store) if tool.schema.name == 'create_goal')
                ctx = SimpleNamespace(session_id=session.id, services={})
                header = Path(folder) / 'sessions' / session.id / 'header.json'
                original = header.read_bytes()
                for value in (None, True, 42, [], {}, '', '  '):
                    with self.subTest(language=language, value=value):
                        for sdk in (True, False):
                            with self.assertRaises(ToolError) as failure:
                                if sdk:
                                    store.create(session.id, objective=value)
                                else:
                                    await create.execute({'objective': value}, ctx)
                            self.assertEqual(failure.exception.code, 'GOAL_INVALID_OBJECTIVE')
                            self.assertEqual(failure.exception.message, tr('python.goal_tools.07cfd33c7c6f'))
                            self.assertIsNone(store.get(session.id))
                            self.assertEqual(header.read_bytes(), original)

    async def test_invalid_block_reason_preserves_goal_and_persistent_header(self):
        for language in ('en', 'zh-CN', 'en'):
            with tempfile.TemporaryDirectory() as folder, current_language.set(language):
                sessions = SessionStore(folder)
                session = sessions.create()
                store = GoalStore(sessions=sessions)
                goal = store.create(session.id, objective='原始目标😀')
                cached = store._goals[session.id]
                header = Path(folder) / 'sessions' / session.id / 'header.json'
                original = header.read_bytes()
                for reason in (None, False, 42, [], {}, '', '  '):
                    with self.subTest(language=language, reason=reason):
                        with self.assertRaises(ToolError) as failure:
                            store.block(session.id, goal_id=goal.id, revision=goal.revision, reason=reason)
                        self.assertEqual(failure.exception.code, 'GOAL_TOOL_INVALID_UPDATE')
                        self.assertEqual(failure.exception.message, tr('python.goal_tools.1a419835edf0'))
                        self.assertEqual(store.get(session.id), goal)
                        self.assertIs(store._goals[session.id], cached)
                        self.assertEqual(header.read_bytes(), original)
                blocked = store.block(session.id, goal_id=goal.id, revision=goal.revision, reason=' 原始原因😀 ')
                self.assertEqual(blocked.blocked_reason['message'], '原始原因😀')
                self.assertEqual(GoalStore(sessions=SessionStore(folder)).get(session.id).blocked_reason, blocked.blocked_reason)

    async def test_default_round_limit_rejects_values_that_cannot_be_restored(self):
        from harness.tools.builtin.goal_tools import MAX_GOAL_ROUNDS_CEILING
        for language in ('en', 'zh-CN', 'en'):
            with tempfile.TemporaryDirectory() as folder, current_language.set(language):
                sessions = SessionStore(folder)
                session = sessions.create()
                header = Path(folder) / 'sessions' / session.id / 'header.json'
                original = header.read_bytes()
                for limit in (None, True, False, 1.5, float('inf'), '2', [], {}, 0, -1,
                              MAX_GOAL_ROUNDS_CEILING + 1):
                    with self.subTest(language=language, limit=limit):
                        with self.assertRaises(ValueError) as failure:
                            GoalStore(default_max_goal_rounds=limit, sessions=sessions)
                        self.assertIn('default_max_goal_rounds', str(failure.exception))
                        self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in str(failure.exception)), language == 'zh-CN')
                        self.assertEqual(header.read_bytes(), original)
                for limit in (1, MAX_GOAL_ROUNDS_CEILING):
                    store = GoalStore(default_max_goal_rounds=limit, sessions=sessions)
                    created = store.create(session.id, objective='Original objective')
                    restored = GoalStore(sessions=SessionStore(folder)).get(session.id)
                    self.assertEqual(restored.max_goal_rounds, limit)
                    self.assertEqual(restored.objective, created.objective)
                    store.clear(session.id)

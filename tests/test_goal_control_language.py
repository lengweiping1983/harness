"""目标控制消息在写入时使用调用语言，并保留目标原文及关联身份。"""
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from harness.app.goal_runtime import GoalRuntime
from harness.core.events import EventBus
from harness.core.language import current_language
from harness.session.store import SessionStore
from harness.tools.builtin.goal_tools import GoalView


class GoalControlLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_controls_and_edit_branches_use_current_language_with_exact_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            sessions = SessionStore(directory)
            session = sessions.create()
            previous = GoalView('goal-fixture', 1, 'Original objective', 'active', None)
            selected = previous
            bus = EventBus()
            emitted = []
            bus.on('user/message', emitted.append)
            harness = SimpleNamespace(bus=bus, _agents={}, goals=SimpleNamespace(get=lambda _: selected))
            runtime = GoalRuntime(harness)
            for language in ('en', 'zh-CN', 'en'):
                for action, updated in [('pause', previous), ('resume', previous), ('clear', previous),
                        ('edit', replace(previous, objective='Updated objective')),
                        ('edit', replace(previous, max_goal_rounds=3, token_budget=700))]:
                    selected = updated
                    before = session.event_count
                    with current_language.set(language):
                        await runtime._record_control(session, action, previous)
                    self.assertEqual(session.event_count, before + 1)
                    event = session.events[-1]
                    self.assertEqual(event.type, 'user/message')
                    self.assertEqual(event.data['source'], 'goal-control')
                    self.assertEqual(event.data['goal_action'], action)
                    self.assertEqual(event.data['goal_reference_id'], previous.id)
                    text = event.data['text']
                    self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in text), language == 'zh-CN')
                    if updated.objective != previous.objective:
                        self.assertIn('Updated objective', text)
                    if updated.token_budget is not None:
                        self.assertIn('700', text)
                    self.assertEqual(emitted[-1]['source_seq'], event.seq)
                selected = previous
                before = session.event_count
                with current_language.set(language):
                    await runtime._record_control(session, 'edit', previous)
                self.assertEqual(session.event_count, before)
            await session.close()

    def test_same_goal_views_resolve_every_phase_without_changing_original_fields(self):
        from harness.i18n import tr
        phases = {'active': 'python.goal_tools.dc9591e56d50', 'paused': 'python.goal_tools.eb0c326b60ae',
                  'blocked': 'python.goal_tools.ece5ef215809', 'complete': 'python.goal_tools.f28461bb49c8'}
        views = [GoalView('Original-id', 3, 'Original objective 原文 {phase}', phase, None) for phase in phases]
        originals = [view.to_value() for view in views]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for view in views:
                    self.assertEqual(view.describe(), tr('python.goal_tools.27a0b997804a',
                        p0=view.id, p1=str(view.revision), p2=tr(phases[view.phase]), p3=view.objective))
        self.assertEqual([view.to_value() for view in views], originals)

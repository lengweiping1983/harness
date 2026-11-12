"""目标主状态提交后的失败保留原件，不宣称控制已整体完成。"""
import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.app.goal_runtime import GoalRuntime
from harness.core.errors import HarnessError
from harness.core.events import EventBus
from harness.core.language import current_language
from harness.i18n import tr
from harness.session.events import AppendOp
from harness.session.store import SessionStore
from harness.tools.builtin.goal_tools import GoalStore


class GoalControlCommitReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_commit_failures_preserve_saved_state_and_original_error(self):
        for language in ('en', 'zh-CN', 'en'):
            for stage in ('append', 'broadcast', 'header', 'publish', 'schedule'):
                with self.subTest(language=language, stage=stage), tempfile.TemporaryDirectory() as directory:
                    sessions = SessionStore(directory)
                    session = sessions.create()
                    bus = EventBus()
                    goals = GoalStore(sessions=sessions, bus=bus)
                    h = SimpleNamespace(config=SimpleNamespace(enable_goal=True), bus=bus, goals=goals,
                        sessions=sessions, get_session=sessions.get, _agents={},
                        runtime=SimpleNamespace(claim_loop=lambda loop: loop, _pending={}))
                    runtime = GoalRuntime(h)
                    original = OSError('Original follow-up failure')
                    payload = {'action': 'create', 'objective': 'Original objective 原文'}
                    if stage == 'append':
                        target = patch.object(session, 'append', AsyncMock(side_effect=original))
                    elif stage == 'broadcast':
                        emit = bus.emit
                        async def fail(event_type, payload):
                            if event_type == 'user/message':
                                raise original
                            await emit(event_type, payload)
                        target = patch.object(bus, 'emit', side_effect=fail)
                    elif stage == 'header':
                        event = await session.append('user/message', {'text': 'Original user'}, surface_op=AppendOp())
                        payload['source_seq'] = event.seq
                        update = sessions.update_header
                        def update_header(*args, **kwargs):
                            if 'goal_messages' in kwargs.get('metadata', {}):
                                raise original
                            return update(*args, **kwargs)
                        target = patch.object(sessions, 'update_header', side_effect=update_header)
                    elif stage == 'publish':
                        target = patch.object(goals, 'publish', AsyncMock(side_effect=original))
                    else:
                        target = patch.object(runtime, 'schedule', side_effect=original)
                    with current_language.set(language), target:
                        with self.assertRaises(HarnessError) as caught:
                            await runtime.control(session.id, payload)
                        self.assertEqual(caught.exception.code, 'goal/control-incomplete')
                        self.assertEqual(caught.exception.message, tr('goal.control.incomplete'))
                        self.assertIs(caught.exception.__cause__, original)
                    saved = goals.get(session.id)
                    self.assertEqual(saved.objective, payload['objective'])
                    restored = GoalStore(sessions=SessionStore(directory)).get(session.id)
                    self.assertEqual(restored.id, saved.id)
                    self.assertEqual(restored.revision, saved.revision)
                    count = session.event_count
                    with self.assertRaises(HarnessError):
                        await runtime.control(session.id, payload)
                    self.assertEqual(session.event_count, count)
                    self.assertEqual(goals.get(session.id).id, saved.id)

    async def test_cancellation_after_commit_keeps_original_cancellation_and_saved_goal(self):
        sessions = SessionStore()
        session = sessions.create()
        goals = GoalStore(sessions=sessions)
        h = SimpleNamespace(config=SimpleNamespace(enable_goal=True), bus=EventBus(), goals=goals,
            sessions=sessions, get_session=sessions.get, _agents={},
            runtime=SimpleNamespace(claim_loop=lambda loop: loop, _pending={}))
        runtime = GoalRuntime(h)
        cancellation = asyncio.CancelledError('Original cancellation')
        with patch.object(session, 'append', AsyncMock(side_effect=cancellation)):
            with self.assertRaises(asyncio.CancelledError) as caught:
                await runtime.control(session.id, {'action': 'create', 'objective': 'Original goal'})
        self.assertIs(caught.exception, cancellation)
        self.assertEqual(goals.get(session.id).objective, 'Original goal')

    async def test_http_receipt_marks_post_commit_error_as_unavailable(self):
        from harness.web.api.sessions import build_router
        from harness.web.errors import ServiceUnavailable
        from harness.web.router import Request
        for code in ('goal/control-incomplete', 'goal/invalid-source'):
            original = HarnessError('Original control failure', code=code, action='create')
            ctx = SimpleNamespace(harness=SimpleNamespace(goal_runtime=SimpleNamespace(control=Mock(return_value=None))),
                                  call=Mock(side_effect=original))
            path = '/api/v1/sessions/fixture/goal'
            handler, params = build_router(ctx).resolve('POST', path)
            request = Request('POST', path, {}, {}, b'{"action":"create","objective":"Original goal"}')
            if code == 'goal/control-incomplete':
                with self.assertRaises(ServiceUnavailable) as caught:
                    handler(request, **params)
                self.assertEqual(caught.exception.status, 503)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.details, original.details)
                self.assertIs(caught.exception.__cause__, original)
            else:
                with self.assertRaises(HarnessError) as caught:
                    handler(request, **params)
                self.assertIs(caught.exception, original)

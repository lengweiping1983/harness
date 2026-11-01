"""目标控制引导晚于新任务应用时，目录仍使用人工任务正文。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness.session.events import AppendOp, ReplaceOp
from harness.session.store import SessionStore
from harness.web.views import session_listing


class SessionListingControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_goal_control_does_not_replace_task_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            for persistent in (False, True):
                for compacted in (False, True):
                    with self.subTest(persistent=persistent, compacted=compacted):
                        store = SessionStore(Path(directory) if persistent else None)
                        session = store.create()
                        app = SimpleNamespace(sessions=store, list_sessions=lambda: [session.header.to_dict()])
                        first = await session.append('user/message', {'text': 'old task'}, surface_op=AppendOp())
                        if compacted:
                            await session.append('system/message', {'text': 'old context'},
                                surface_op=ReplaceOp(first.seq, first.seq))
                        await session.append('note', {'kind': 'steering/accepted', 'request_id': 'old-clear',
                            'turn': 1, 'text': 'late clear control', 'goal_action': 'clear', 'goal_id': 'old-goal'}, ignorable=True)
                        await session.append('user/message', {'text': 'new blocked task', 'source': 'goal-command'}, surface_op=AppendOp())
                        await session.append('user/message', {'text': 'late clear control', 'source': 'steer',
                            'request_id': 'old-clear', 'turn': 1}, surface_op=AppendOp())
                        self.assertEqual(session_listing(app)[0]['summary'], 'new blocked task')
                        await session.append('user/message', {'text': 'late clear control', 'source': 'steer',
                            'request_id': 'ordinary-guidance', 'turn': 1}, surface_op=AppendOp())
                        self.assertEqual(session_listing(app)[0]['summary'], 'late clear control')
                        await session.append('user/message', {'text': 'internal pause', 'source': 'goal-control'}, surface_op=AppendOp())
                        self.assertEqual(session_listing(app)[0]['summary'], 'late clear control')
                        store.update_header(session.id, title='explicit user title')
                        self.assertEqual(session_listing(app)[0]['title'], 'explicit user title')
                        if persistent:
                            restored = SessionStore(Path(directory))
                            restored_app = SimpleNamespace(sessions=restored, list_sessions=lambda: [restored.get(session.id).header.to_dict()])
                            self.assertEqual(session_listing(restored_app)[0]['summary'], 'late clear control')

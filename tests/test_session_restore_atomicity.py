"""恢复投影失败不得清空或部分覆盖已经可用的对话。"""
import unittest

from harness.core.errors import SessionInvariantError
from harness.session.events import AppendOp, ReplaceOp, SessionEvent
from harness.session.session import Session


class SessionRestoreAtomicityTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_surface_or_mailbox_retains_all_existing_indexes(self):
        for malformed in ('surface', 'mailbox'):
            with self.subTest(malformed=malformed):
                session = Session()
                await session.append('user/message', {'text': 'Original 原文'}, surface_op=AppendOp())
                observed = []
                session.subscribe(observed.append)
                original = (session.events, session.surface, session._history_generation,
                            session.steering, session.agent_messages, session.tool_results)
                event = SessionEvent(time=0, seq=0, type='user/message', data={'text': 'Replacement'},
                                     surface_op=ReplaceOp(91, 92)) if malformed == 'surface' else SessionEvent(
                                         time=0, seq=0, type='agent/message', data={})
                with self.assertRaises(SessionInvariantError):
                    session.restore([event])
                self.assertEqual((session.events, session.surface, session._history_generation,
                                  session.steering, session.agent_messages, session.tool_results), original)
                self.assertEqual(observed, [])
                self.assertEqual(session.event_at(0), original[0][0])
                await session.append('user/message', {'text': 'After 原文'}, surface_op=AppendOp())
                self.assertEqual(session.event_count, 2)
                self.assertEqual(len(observed), 1)

    async def test_successful_restore_keeps_owner_and_listener_without_notifications(self):
        session = Session()
        observed = []
        session.subscribe(observed.append)
        await session.append('user/message', {'text': 'Original'}, surface_op=AppendOp())
        generation = session._history_generation
        event = SessionEvent(time=0, seq=0, type='user/message', data={'text': 'Current 原文'}, surface_op=AppendOp())
        session.restore(iter([event]))
        self.assertEqual(session.events, (event,))
        self.assertEqual(session.surface, (event,))
        self.assertEqual(session._history_generation, generation + 1)
        self.assertIs(session.agent_messages.session, session)
        self.assertEqual(len(observed), 1)
        await session.append('user/message', {'text': 'Next'}, surface_op=AppendOp())
        self.assertEqual(len(observed), 2)

    async def test_failed_restore_preserves_pending_listener_and_file_observations(self):
        import asyncio
        session = Session()
        entered, released = asyncio.Event(), asyncio.Event()
        async def listener(_event):
            entered.set()
            await released.wait()
        session.subscribe(listener)
        await session.append('user/message', {'text': 'Original'}, surface_op=AppendOp())
        session.file_observations.records['fixture'] = ('present', 'original')
        pending = tuple(session._pending_awaitables)
        with self.assertRaises(SessionInvariantError):
            session.restore([SessionEvent(time=0, seq=0, type='agent/message', data={})])
        self.assertEqual(tuple(session._pending_awaitables), pending)
        self.assertEqual(session.file_observations.records, {'fixture': ('present', 'original')})
        draining = asyncio.create_task(session.drain())
        await entered.wait()
        self.assertFalse(draining.done())
        released.set()
        await draining
        self.assertEqual(session._pending_awaitables, [])

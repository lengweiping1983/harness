"""验证会话提交、投影和损坏数据的业务边界。"""
import tempfile
import unittest
from pathlib import Path

from harness.core.errors import PersistenceError, SessionInvariantError
from harness.session.events import AppendOp, ReplaceOp
from harness.session.session import Session
from harness.session.store import SessionStore


class SessionCommitTests(unittest.IsolatedAsyncioTestCase):
    async def test_committed_event_is_durable_before_observers_read_it(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            session = store.create()
            observed = []

            def read_committed(event):
                restored = SessionStore(directory).get(session.id)
                observed.append(restored.event_at(event.seq).data['text'])

            session.subscribe(read_committed)
            await session.append('user/message', {'text': '已提交'}, surface_op=AppendOp())
            self.assertEqual(observed, ['已提交'])

    async def test_failed_commit_never_changes_projection_or_notifies(self):
        session = Session()
        await session.append('user/message', {'text': '原内容'}, surface_op=AppendOp())
        events, surface = session.events, session.surface
        observed = []
        session.subscribe(observed.append)

        def fail_commit(event):
            raise OSError('模拟磁盘写入失败')

        session.commit_event = fail_commit
        with self.assertRaises(PersistenceError):
            await session.append('user/message', {'text': '替换'}, surface_op=ReplaceOp(0, 0))
        self.assertEqual(session.events, events)
        self.assertEqual(session.surface, surface)
        self.assertEqual(observed, [])
        session.commit_event = None
        with self.assertRaises(PersistenceError):
            await session.append('note', {'text': '后续写入'})

    async def test_invalid_replacement_is_rejected_before_disk_commit(self):
        session = Session()
        commits = []
        session.commit_event = commits.append
        with self.assertRaises(SessionInvariantError):
            await session.append('user/message', {'text': '无效'}, surface_op=ReplaceOp(0, 9))
        self.assertEqual(commits, [])
        self.assertEqual(session.event_count, 0)

    async def test_replacement_and_replay_keep_same_surface_order(self):
        session = Session()
        for text in ('第一条', '第二条', '第三条'):
            await session.append('user/message', {'text': text}, surface_op=AppendOp())
        await session.append('user/message', {'text': '摘要'}, surface_op=ReplaceOp(0, 1))
        replay = Session()
        replay.restore(session.events)
        self.assertEqual([event.data['text'] for event in replay.surface], ['摘要', '第三条'])
        self.assertEqual(replay.surface, session.surface)
        self.assertEqual(replay.event_count, 4)

    async def test_corrupt_existing_log_is_preserved_and_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SessionStore(directory).create()
            log = Path(directory) / 'sessions' / session.id / 'events.jsonl'
            content = b'{"broken":\n'
            log.write_bytes(content)
            restored = SessionStore(directory).get(session.id)
            self.assertTrue(restored.persistence_error)
            with self.assertRaises(PersistenceError):
                await restored.append('note', {'text': '禁止继续写入'})
            self.assertEqual(log.read_bytes(), content)

"""分页与分叉游标必须为整数，非法输入不产生写入。"""
import tempfile
import unittest
from pathlib import Path

from harness.core.errors import SessionInvariantError
from harness.core.language import current_language
from harness.i18n import tr
from harness.session.store import SessionStore


class SessionCursorContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_cursors_preserve_memory_and_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            session = store.create()
            await session.append('note', {'kind': 'fixture'})
            root = Path(directory)
            snapshot = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    for value in (True, False, 0.5, '0', None, [], float('inf')):
                        for reader in (store, SessionStore(directory)):
                            for kwargs in ({'after': value}, {'limit': value}):
                                with self.subTest(language=language, kwargs=kwargs):
                                    with self.assertRaises(SessionInvariantError) as error:
                                        reader.read_events(session.id, **kwargs)
                                    self.assertEqual(error.exception.message, tr('python.store.0fd418ff18a1'))
                        if value is not None:
                            with self.assertRaises(SessionInvariantError) as error:
                                store.fork(session.id, through_seq=value)
                            self.assertEqual(error.exception.message, tr('python.store.8e5bc7c3f7e3'))
            self.assertEqual(len(store.list_sessions()), 1)
            self.assertEqual(snapshot, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()})

    async def test_integer_boundaries_and_omitted_fork_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            session = store.create()
            await session.append('note', {'kind': 'fixture'})
            for reader in (store, SessionStore(directory)):
                self.assertEqual(len(reader.read_events(session.id, after=-1, limit=1)), 1)
                self.assertEqual(reader.read_events(session.id, after=0, limit=1000), [])
                for kwargs in ({'after': -2}, {'limit': 0}, {'limit': 1001}):
                    with self.assertRaises(SessionInvariantError):
                        reader.read_events(session.id, **kwargs)
            self.assertEqual(store.fork(session.id, through_seq=-1).event_count, 0)
            self.assertEqual(store.fork(session.id, through_seq=0).event_count, 1)
            self.assertEqual(store.fork(session.id).event_count, 1)

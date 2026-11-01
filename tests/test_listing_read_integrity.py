"""对话列表读取失败不能伪装成空统计，损坏日志不改写原件。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness.core.errors import SessionInvariantError
from harness.session.events import AppendOp
from harness.session.store import SessionStore
from harness.web import views


class ListingReadIntegrityTests(unittest.IsolatedAsyncioTestCase):
    def test_event_path_distinguishes_missing_file_and_unreadable_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            session = store.create()
            self.assertIsNone(store.events_path(session.id))
            error = PermissionError('Original metadata failure')
            with patch.object(Path, 'stat', side_effect=error):
                with self.assertRaises(PermissionError) as caught:
                    store.events_path(session.id)
            self.assertIs(caught.exception, error)

    async def test_disk_failures_and_exact_projection_errors_are_preserved(self):
        for stage in ('stat', 'open', 'get', 'events_path'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                store = SessionStore(directory)
                session = store.create()
                await session.append('user/message', {'text': 'Original 原文'}, surface_op=AppendOp())
                app = SimpleNamespace(sessions=store, list_sessions=lambda: [session.header.to_dict()])
                path = store.events_path(session.id)
                original = path.read_bytes()
                error = PermissionError('fixture unreadable log')
                views.clear_listing_cache()
                if stage in ('stat', 'open'):
                    target, name = Path, stage
                else:
                    target, name = store, stage
                with patch.object(target, name, side_effect=error):
                    with self.assertRaises(PermissionError) as caught:
                        if stage == 'get':
                            views._exact_list_stats(app, session.id)
                        else:
                            views.session_listing(app)
                self.assertIs(caught.exception, error)
                self.assertEqual(path.read_bytes(), original)

    async def test_corrupt_tail_uses_store_prefix_and_preserves_write_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            session = store.create()
            await session.append('user/message', {'text': 'Original 原文'}, surface_op=AppendOp())
            path = store.events_path(session.id)
            original = path.read_bytes() + b'{"broken":\n'
            path.write_bytes(original)
            restored = SessionStore(directory)
            app = SimpleNamespace(sessions=restored, list_sessions=lambda: [session.header.to_dict()])
            with self.assertLogs('harness.session.store', level='WARNING'):
                rows = views.session_listing(app)
            self.assertEqual(rows[0]['message_count'], 1)
            self.assertEqual(rows[0]['summary'], 'Original 原文')
            self.assertTrue(restored.get(session.id).persistence_error)
            self.assertEqual(path.read_bytes(), original)

    async def test_invalid_sequence_is_not_counted_as_valid_message(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            session = store.create()
            await session.append('user/message', {'text': 'Original 原文'}, surface_op=AppendOp())
            path = store.events_path(session.id)
            raw = json.loads(path.read_text(encoding='utf-8'))
            raw['seq'] = 3
            original = json.dumps(raw).encode() + b'\n'
            path.write_bytes(original)
            restored = SessionStore(directory)
            app = SimpleNamespace(sessions=restored, list_sessions=lambda: [session.header.to_dict()])
            with self.assertRaises(SessionInvariantError):
                views.session_listing(app)
            self.assertEqual(path.read_bytes(), original)

    async def test_empty_in_memory_and_persistent_sessions_remain_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            for root in (None, directory):
                store = SessionStore(root)
                session = store.create()
                app = SimpleNamespace(sessions=store, list_sessions=lambda: [session.header.to_dict()])
                self.assertEqual(views.session_listing(app)[0]['message_count'], 0)

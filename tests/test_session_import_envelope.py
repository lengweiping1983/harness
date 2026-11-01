"""导入必须提供完整的当前格式封装，不能默认为空历史。"""
import tempfile
import unittest
from pathlib import Path

from harness.core.errors import SessionInvariantError
from harness.core.language import current_language
from harness.i18n import tr
from harness.session.store import SessionStore


class SessionImportEnvelopeTests(unittest.TestCase):
    def test_missing_or_invalid_events_are_rejected_without_publish(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), tempfile.TemporaryDirectory() as directory:
                store = SessionStore(directory)
                original = store.create(title='Original 原文 {x}')
                header = {**original.header.to_dict(), 'id': 'new-import'}
                root = Path(directory)
                before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                for data in (None, [], {}, {'header': header},
                             {'header': header, 'events': None},
                             {'header': header, 'events': {}},
                             {'header': header, 'events': ''},
                             {'header': header, 'events': ()},
                             {'header': header, 'events': [None]},
                             {'header': [], 'events': []}):
                    for persist in (True, False):
                        with self.subTest(language=language, data=data, persist=persist):
                            with self.assertRaises(SessionInvariantError) as error:
                                store.import_session(data, persist=persist)
                            self.assertEqual(error.exception.message, tr('python.store.invalid_import'))
                            self.assertEqual([s.id for s in store.list_sessions()], [original.id])
                            self.assertEqual(before, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_explicit_empty_history_and_existing_snapshot_remain_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            original = store.create(title='Original 原文 {x}')
            snapshot = store.export_session(original.id)
            self.assertIs(store.import_session(snapshot), original)
            empty = {'header': {**snapshot['header'], 'id': 'new-import'}, 'events': []}
            preview = store.import_session(empty, persist=False)
            self.assertEqual(preview.event_count, 0)
            self.assertFalse(store.has(preview.id))
            imported = store.import_session(empty)
            self.assertEqual(store.export_session(imported.id), empty)
            self.assertEqual(SessionStore(directory).export_session(imported.id), empty)

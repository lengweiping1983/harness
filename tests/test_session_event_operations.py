"""损坏的表面操作必须拒绝，不得静默改成追加或改写原件。"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.core.errors import SessionInvariantError
from harness.session.events import SessionEvent
from harness.session.store import SessionStore


def event(operation):
    return {'type': 'user/message', 'seq': 0, 'time': 0,
            'data': {'text': 'fixture'}, 'surface_op': operation}


class SessionEventOperationTests(unittest.TestCase):
    def test_current_operations_roundtrip(self):
        for op in ({'op': 'append'}, {'op': 'replace', 'start_seq': 0, 'end_seq': 1}):
            self.assertEqual(SessionEvent.from_dict(event(op)).to_dict(), event(op))

    def test_invalid_operations_rejected(self):
        for op in ({}, {'op': 'unknown'}, {'op': []}, [], 'append',
                   {'op': 'replace'}, {'op': 'replace', 'start_seq': True, 'end_seq': 1},
                   {'op': 'replace', 'start_seq': 2, 'end_seq': 1}):
            with self.subTest(operation=op), self.assertRaises(SessionInvariantError):
                SessionEvent.from_dict(event(op))

    def test_load_page_and_import_preserve_invalid_original(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            session = store.create()
            path = Path(directory) / 'sessions' / session.id / 'events.jsonl'
            raw = json.dumps(event({'op': 'unknown'})) + '\n'
            path.write_text(raw)
            for read in (lambda: SessionStore(directory).get(session.id),
                         lambda: SessionStore(directory).read_events(session.id),
                         lambda: SessionStore().import_session({'header': session.header.to_dict(),
                                                               'events': [json.loads(raw)]})):
                with self.assertRaises(SessionInvariantError):
                    read()
                self.assertEqual(path.read_text(), raw)


class SessionEventSourceTests(unittest.TestCase):
    def test_optional_sources_and_integer_lists_roundtrip(self):
        raw = event({'op': 'append'})
        self.assertIsNone(SessionEvent.from_dict(raw).source_event_seqs)
        for sources in (None, [], [0, 2]):
            with self.subTest(sources=sources):
                restored = SessionEvent.from_dict({**raw, 'source_event_seqs': sources})
                self.assertEqual(restored.source_event_seqs, None if sources is None else tuple(sources))
                self.assertEqual(SessionEvent.from_dict(restored.to_dict()), restored)

    def test_load_page_and_import_reject_invalid_sources_without_writing(self):
        from harness.core.language import current_language
        from harness.i18n import tr

        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), tempfile.TemporaryDirectory() as directory:
                store = SessionStore(directory)
                session = store.create()
                path = Path(directory) / 'sessions' / session.id / 'events.jsonl'
                for sources in ('0', 0, False, {}, [True], [None], ['0'], [1.5], [-1]):
                    with self.subTest(language=language, sources=sources):
                        payload = {**event({'op': 'append'}), 'source_event_seqs': sources}
                        raw = (json.dumps(payload) + '\n').encode('utf-8')
                        path.write_bytes(raw)
                        for read in (lambda: SessionStore(directory).get(session.id),
                                     lambda: SessionStore(directory).read_events(session.id),
                                     lambda: SessionStore().import_session({'header': session.header.to_dict(),
                                                                           'events': [payload]})):
                            with self.assertRaises(SessionInvariantError) as error:
                                read()
                            self.assertEqual(error.exception.message, tr('session.event.invalidSources'))
                            self.assertEqual(path.read_bytes(), raw)

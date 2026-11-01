"""轮次提交和恢复使用相同状态校验，拒绝后保留原历史。"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.core.errors import SessionInvariantError
from harness.core.language import current_language
from harness.i18n import tr
from harness.session.events import SessionEvent, TurnEndReason
from harness.session.store import SessionStore


class SessionTurnContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_starts_and_ends_never_commit_or_notify(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), tempfile.TemporaryDirectory() as directory:
                store = SessionStore(directory)
                session = store.create()
                observed = []
                session.subscribe(observed.append)
                for value in (True, False, 0, -1, 1.5, '1', [], None):
                    with self.subTest(language=language, value=value):
                        with self.assertRaises(SessionInvariantError) as error:
                            await session.append('turn/start', {'turn': value})
                        self.assertEqual(error.exception.message, tr('session.turn.invalid_lifecycle'))
                        if value is not None:
                            with self.assertRaises(SessionInvariantError):
                                await session.start_turn(value)
                self.assertEqual(session.event_count, 0)
                self.assertEqual(observed, [])
                await session.start_turn()
                log = Path(directory) / 'sessions' / session.id / 'events.jsonl'
                original = log.read_bytes()
                for event_type, data in [('turn/start', {'turn': 2}), ('turn/end', {}),
                                         ('turn/end', {'turn': 2}), ('turn/end', {'turn': True}),
                                         ('turn/start', []), ('turn/end', None)]:
                    with self.assertRaises(SessionInvariantError):
                        await session.append(event_type, data)
                    self.assertEqual(log.read_bytes(), original)
                    self.assertEqual(session.open_turn, 1)
                    self.assertEqual(len(observed), 1)
                await session.end_turn(TurnEndReason(kind='completed'))
                with self.assertRaises(SessionInvariantError):
                    await session.start_turn(1)
                await session.start_turn(2)
                restored = SessionStore(directory).get(session.id)
                self.assertEqual((restored.current_turn, restored.open_turn), (2, 2))

    async def test_invalid_restore_load_and_import_preserve_originals(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), tempfile.TemporaryDirectory() as directory:
                store = SessionStore(directory)
                session = store.create()
                await session.start_turn()
                before = session.events
                log = Path(directory) / 'sessions' / session.id / 'events.jsonl'
                for kind, data in [('turn/start', {'turn': '1'}), ('turn/start', {}),
                                   ('turn/end', {'turn': 1}), ('turn/start', {'turn': 1.5})]:
                    event = SessionEvent(type=kind, seq=0, time=0, data=data)
                    raw = json.dumps(event.to_dict()).encode() + b'\n'
                    log.write_bytes(raw)
                    for operation in (lambda: session.restore([event]),
                                      lambda: SessionStore(directory).get(session.id),
                                      lambda: store.import_session({'header': session.header.to_dict(), 'events': [event.to_dict()]}, persist=False)):
                        with self.assertRaises(SessionInvariantError) as error:
                            operation()
                        self.assertEqual(error.exception.message, tr('session.turn.invalid_lifecycle'))
                        self.assertEqual(log.read_bytes(), raw)
                        self.assertEqual(session.events, before)
                        self.assertEqual((session.current_turn, session.open_turn), (1, 1))
                self.assertFalse(session.persistence_error)

"""历史游标绑定原对话与已确认事件位置，不猜测失效位置。"""
import base64
import json
import unittest

from harness.core.language import current_language
from harness.i18n import tr
from harness.session.store import SessionStore
from harness.web.conversation import _cursor, _decode, project_page
from harness.web.errors import BadRequest, Conflict


class ConversationCursorIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def test_cursor_rejects_wrong_identity_type_range_and_encoding(self):
        def encode(value):
            return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
        valid = {'session': 'fixture', 'through': 4, 'before': 2}
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                self.assertEqual(_decode(encode(valid), 'fixture'), (4, 2))
                for token in ('not base64!', 'x'*2049, encode([]), encode({**valid, 'session':'other'}),
                              encode({**valid, 'through':True}), encode({**valid, 'before':'2'}),
                              encode({**valid, 'before':-1}), encode({**valid, 'before':5}),
                              encode({**valid, 'through':2**53}), encode({**valid, 'extra':0})):
                    with self.assertRaises(BadRequest) as caught:
                        _decode(token, 'fixture')
                    self.assertEqual(caught.exception.code, 'conversation/invalid-cursor')
                    self.assertEqual(caught.exception.message, tr('python.conversation.2ede74cc56fa'))

    async def test_removed_or_non_display_event_positions_are_not_silently_rebased(self):
        session = SessionStore().create()
        await session.append('note', {'kind': 'Original non-display event'})
        original = list(session.events)
        with self.assertRaises(Conflict) as caught:
            project_page(session, original, cursor=_cursor(session.id, 3, 0))
        self.assertEqual(caught.exception.code, 'conversation/history-changed')
        with self.assertRaises(BadRequest) as caught:
            project_page(session, original, cursor=_cursor(session.id, 0, 0))
        self.assertEqual(caught.exception.code, 'conversation/invalid-cursor')
        self.assertEqual(list(session.events), original)

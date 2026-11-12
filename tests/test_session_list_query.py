"""会话列表限制在读取前校验，并保留权限过滤及现有排序。"""
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.session.store import SessionStore
from harness.web.api.sessions import build_router
from harness.web.errors import BadRequest
from harness.web.router import Request


class SessionListQueryTests(unittest.TestCase):
    def test_invalid_limits_fail_before_listing(self):
        handler, _ = build_router(SimpleNamespace()).resolve('GET', '/api/v1/sessions')
        for language in ('en', 'zh-CN'):
            for values in (['0'], ['-5'], ['abc'], ['99999'], [''], ['1.5'], ['1', '2']):
                with self.subTest(language=language, values=values), current_language.set(language):
                    with patch('harness.web.api.sessions.session_listing') as listing:
                        with self.assertRaises(BadRequest) as caught:
                            handler(Request('GET', '/api/v1/sessions', {'limit': values}, {}, b''))
                        listing.assert_not_called()
                    self.assertEqual(caught.exception.status, 400)
                    self.assertEqual(caught.exception.code, 'request/invalid-query')
                    self.assertEqual(caught.exception.message, tr('web.query_invalid'))

    def test_limit_applies_after_visibility_and_activity_sorting(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            sessions = [store.create() for _ in range(3)]
            rows = [session.header.to_dict() for session in sessions]
            for index, row in enumerate(rows):
                row['created_at'] = index + 1
            app = SimpleNamespace(sessions=store, list_sessions=lambda: rows)
            handler, _ = build_router(SimpleNamespace(harness=app)).resolve('GET', '/api/v1/sessions')

            def read(query, **kwargs):
                return json.loads(handler(Request('GET', '/api/v1/sessions', query, {}, b'', **kwargs)).body)['sessions']

            self.assertEqual([row['id'] for row in read({})], [row['id'] for row in reversed(rows)])
            self.assertEqual([row['id'] for row in read({'limit': ['1']})], [rows[2]['id']])
            self.assertEqual(len(read({'limit': ['1000']})), 3)
            visible = frozenset({rows[0]['id'], rows[1]['id']})
            result = read({'limit': ['1']}, subject='guest', visible_session_ids=visible)
            self.assertEqual([row['id'] for row in result], [rows[1]['id']])
            self.assertNotIn('cwd', result[0])

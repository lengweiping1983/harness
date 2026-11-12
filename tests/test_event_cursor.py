"""事件游标必须明确有效，不能将错误输入降级为全量补发。"""
import asyncio
import unittest
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.web.api.sessions import build_router
from harness.web.errors import BadRequest
from harness.web.query import event_cursor
from harness.web.router import Request
from harness.core.language import current_language
from harness.web.server import Handler


class EventCursorTests(unittest.TestCase):
    def test_cursor_bounds_and_types(self):
        for value, expected in ((0, 0), ('0', 0), ('7', 7), (2**53 - 1, 2**53 - 1)):
            self.assertEqual(event_cursor(value), expected)
        for value in ('abc', '', '-1', '1.2', '1e2', 2**53, True, False, None, 1.0, [], '9' * 5000):
            with self.subTest(value=str(value)[:30]), self.assertRaises(BadRequest) as caught:
                event_cursor(value)
            self.assertEqual(caught.exception.code, 'request/invalid-query')

    def test_live_events_reject_invalid_query_without_replay(self):
        hub = Mock()
        router = build_router(SimpleNamespace(harness=Mock(), hub=hub))
        path = '/api/v1/sessions/s/live-events'
        handler, params = router.resolve('GET', path)
        for query in ({'since': ['abc']}, {'since': ['']}, {'since': ['-1']},
                      {'since': [str(2**53)]}, {'since': ['1', '2']}, {'unknown': ['1']}):
            with self.subTest(query=query), self.assertRaises(BadRequest):
                handler(Request('GET', path, query, {}, b''), **params)
        hub.replay.assert_not_called()

    def test_live_events_use_exact_valid_cursor_and_default(self):
        hub = Mock()
        hub.replay.return_value = []
        hub.last_seq.return_value = 9
        router = build_router(SimpleNamespace(harness=Mock(), hub=hub))
        path = '/api/v1/sessions/s/live-events'
        handler, params = router.resolve('GET', path)
        for query, expected in (({}, 0), ({'since': ['7']}, 7)):
            handler(Request('GET', path, query, {}, b''), **params)
            hub.replay.assert_called_with('s', expected)

    def test_invalid_handshake_cursor_cannot_upgrade(self):
        handler = SimpleNamespace(send_response=Mock())
        with self.assertRaises(BadRequest):
            Handler.do_GET_ws(handler, 's', 'invalid')
        handler.send_response.assert_not_called()

    def test_invalid_resume_cannot_read_or_replay_events(self):
        hub = Mock()
        handler = SimpleNamespace(ctx=SimpleNamespace(shared_access=None, hub=hub))
        conn = SimpleNamespace(send_json=AsyncMock())
        for value in ('invalid', -1, True, None, 2**53):
            asyncio.run(Handler._handle_ws_payload_in_language(handler, conn, 's', {'action': 'resume', 'since': value}))
            payload = conn.send_json.call_args.args[0]
            self.assertEqual(payload['type'], 'error')
            self.assertEqual(payload['code'], 'request/invalid-query')
            self.assertEqual(payload['action'], 'resume')
        self.assertEqual(hub.mock_calls, [])

    def test_origin_rejection_remains_forbidden_before_upgrade(self):
        handler = object.__new__(Handler)
        handler.harness = SimpleNamespace(config=SimpleNamespace(language=current_language.get()))
        handler.path = '/ws?session=s'
        handler._origin_allowed = Mock(return_value=False)
        handler._error = Mock()
        handler.do_GET_ws = Mock()
        with patch.object(BaseHTTPRequestHandler, 'parse_request', return_value=True):
            self.assertFalse(handler.parse_request())
        error = handler._error.call_args.args[0]
        self.assertEqual(error.status, 403)
        self.assertEqual(error.code, 'request/origin')
        self.assertTrue(handler.close_connection)
        handler.do_GET_ws.assert_not_called()

    def test_duplicate_ws_identity_and_view_are_rejected_before_authorization(self):
        from harness.core.language import current_language
        from harness.i18n import tr
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for query, parameter in (('session=s&session=other', 'session'),
                                         ('session=s&view=chat&view=full', 'view'),
                                         ('session=s&since=0&since=1', 'since')):
                    with self.subTest(language=language, query=query):
                        handler = object.__new__(Handler)
                        handler.harness = SimpleNamespace(config=SimpleNamespace(language=current_language.get()))
                        handler.path = '/ws?' + query
                        handler._origin_allowed = Mock(return_value=True)
                        handler._error = Mock()
                        handler.do_GET_ws = Mock()
                        access = SimpleNamespace(identity=Mock(), require_read=Mock())
                        handler._web_ctx = SimpleNamespace(shared_access=access, call=Mock())
                        with patch.object(BaseHTTPRequestHandler, 'parse_request', return_value=True):
                            self.assertFalse(handler.parse_request())
                        handler._error.assert_called_once()
                        error = handler._error.call_args.args[0]
                        self.assertEqual(error.code, 'request/invalid-query')
                        self.assertEqual(error.details, {'parameter':parameter})
                        self.assertEqual(error.message, tr('web.query_invalid'))
                        handler.do_GET_ws.assert_not_called()
                        handler.ctx.call.assert_not_called()
                        self.assertTrue(handler.close_connection)

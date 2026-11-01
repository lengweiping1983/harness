"""认证拒绝只展示安全分类，不重放授权，也不回显上游材料。"""
import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.chatgpt_auth import ChatGPTAccountError, request_json
from harness.llm.chatgpt_callback import render_callback


class ChatGPTDiagnosticsTests(unittest.TestCase):
    def test_http_errors_are_classified_closed_and_body_is_released(self):
        cases = [
            ({'error': {'code': 'unsupported_country_region_territory', 'message': 'private'}}, {}, 'chatgpt.http_region'),
            ({'error': 'invalid_grant', 'error_description': 'private'}, {}, 'chatgpt.http_grant'),
            ({'error': {'code': 'token_expired'}}, {}, 'chatgpt.http_grant'),
            ({'error': 'invalid_client'}, {}, 'chatgpt.http_client'),
            ({'error': 'access_denied'}, {}, 'chatgpt.http_denied'),
            ({'error': 'private'}, {}, 'chatgpt.http_failed'),
            ({'error': ['private']}, {}, 'chatgpt.http_failed'),
            (['private'], {}, 'chatgpt.http_failed'),
            (None, {}, 'chatgpt.http_failed'),
            (None, {'cf-mitigated': 'challenge'}, 'chatgpt.http_challenge'),
        ]
        for language in ('en', 'zh-CN', 'en'):
            for payload, headers, key in cases:
                with self.subTest(language=language, key=key), current_language.set(language):
                    body = io.BytesIO(json.dumps(payload).encode() if payload is not None else b'<html>private</html>')
                    metadata = Message()
                    for name, value in headers.items():
                        metadata[name] = value
                    failure = urllib.error.HTTPError('https://fixture.invalid', 403, 'private', metadata, body)
                    with patch('urllib.request.build_opener') as opener:
                        opener.return_value.open.side_effect = failure
                        with self.assertRaises(ChatGPTAccountError) as caught:
                            request_json('https://fixture.invalid', data=b'private')
                        opener.return_value.open.assert_called_once()
                    self.assertEqual(caught.exception.key, key)
                    self.assertEqual(str(caught.exception), tr(key, status=403))
                    self.assertNotIn('private', str(caught.exception))
                    self.assertTrue(body.closed)

    def test_unreadable_and_oversized_errors_preserve_http_status(self):
        for body in (io.BytesIO(b'x' * 20000), io.BytesIO(b'\xff')):
            failure = urllib.error.HTTPError('https://fixture.invalid', 403, 'private', Message(), body)
            with patch('urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect = failure
                with self.assertRaises(ChatGPTAccountError) as caught:
                    request_json('https://fixture.invalid')
            self.assertEqual(caught.exception.values, {'status': 403})
            self.assertTrue(body.closed)

    def test_callback_escapes_text_and_has_no_external_resources_or_credentials(self):
        for language in ('en', 'zh-CN'):
            for success in (False, True):
                with current_language.set(language):
                    page = render_callback('<script>private</script>', success=success, nonce='fixture')
                self.assertIn('&lt;script&gt;private&lt;/script&gt;', page)
                self.assertNotIn('<script>private', page)
                self.assertIn('nonce="fixture"', page)
                self.assertIn('prefers-color-scheme:dark', page)
                self.assertIn('name="viewport"', page)
                self.assertNotIn('src=', page)
                self.assertNotIn('href=', page)


if __name__ == '__main__':
    unittest.main()

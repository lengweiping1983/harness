"""账号响应和JWT载荷只接受UTF-8，失败不重放或回显授权材料。"""
import base64
import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.chatgpt_auth import ChatGPTAccountError, claims, request_json


class ChatGPTResponseEncodingTests(unittest.TestCase):
    def request(self, body):
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = body
            try:
                return request_json('https://fixture.invalid')
            finally:
                opener.return_value.open.assert_called_once()
                self.assertTrue(body.closed)

    def test_success_response_rejects_implicit_encodings_and_keeps_utf8_text(self):
        payload = {'access_token': 'private', 'description': '原文 English'}
        text = json.dumps(payload, ensure_ascii=False)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                self.assertEqual(self.request(io.BytesIO(text.encode('utf-8'))), payload)
                for encoded in [text.encode(codec) for codec in ('utf-16', 'utf-16-le', 'utf-32', 'utf-32-be')] + [b'{"private":"\xff"}']:
                    with self.subTest(language=language, encoded=encoded):
                        with self.assertRaises(ChatGPTAccountError) as caught:
                            self.request(io.BytesIO(encoded))
                        self.assertEqual(caught.exception.key, 'chatgpt.network_failed')
                        self.assertEqual(str(caught.exception), tr('chatgpt.network_failed'))
                        self.assertNotIn('private', str(caught.exception))

    def test_http_failure_preserves_status_without_decoding_other_encodings(self):
        text = '{"error":"invalid_grant"}'
        for language in ('en', 'zh-CN', 'en'):
            for codec in ('utf-8', 'utf-16', 'utf-32'):
                with self.subTest(language=language, codec=codec), current_language.set(language):
                    body = io.BytesIO(text.encode(codec))
                    error = urllib.error.HTTPError('https://fixture.invalid', 401, 'private', Message(), body)
                    with patch('urllib.request.build_opener') as opener:
                        opener.return_value.open.side_effect = error
                        with self.assertRaises(ChatGPTAccountError) as caught:
                            request_json('https://fixture.invalid')
                        opener.return_value.open.assert_called_once()
                    key = 'chatgpt.http_grant' if codec == 'utf-8' else 'chatgpt.http_failed'
                    self.assertEqual(caught.exception.key, key)
                    self.assertEqual(str(caught.exception), tr(key, status=401))
                    self.assertTrue(body.closed)

    def test_claims_decode_utf8_only_and_preserve_original_values(self):
        payload = {'chatgpt_account_id': 'fixture', 'email': '原文@example.invalid'}
        text = json.dumps(payload, ensure_ascii=False)
        for codec in ('utf-8', 'utf-16', 'utf-32'):
            encoded = base64.urlsafe_b64encode(text.encode(codec)).decode().rstrip('=')
            self.assertEqual(claims('header.' + encoded + '.signature'), payload if codec == 'utf-8' else {})

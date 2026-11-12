"""附件产品拒绝携带显式资源身份，不从错误正文反推参数。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.web.api.attachments import build_router, decode_canonical_base64
from harness.web.attachments import AttachmentRejected, admit, admit_batch
from harness.web.errors import ApiError
from harness.web.responses import error_response
from harness.web.router import Request


class AttachmentErrorResourceTests(unittest.TestCase):
    def test_domain_rejections_keep_identity_and_parameters(self):
        cases = [
            (lambda: admit('invalid'), 'bytesRequired'),
            (lambda: admit(b''), 'empty'),
            (lambda: admit(b'12', max_bytes=1), 'fileTooLarge'),
            (lambda: admit(b'a', name='Original 原文.exe'), 'extensionDenied'),
            (lambda: admit(b'a', name='file.txt', declared_type='application/x-executable'), 'mediaDenied'),
            (lambda: admit(b'\x00', name='Original 原文.dat'), 'unsupported'),
            (lambda: admit_batch([(b'a', 'a.txt', '')] * 2, max_files=1), 'tooMany'),
            (lambda: admit_batch([(b'ab', 'a.txt', '')], max_total_bytes=1), 'batchTooLarge'),
        ]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for action, suffix in cases:
                    with self.subTest(language=language, suffix=suffix):
                        with self.assertRaises(AttachmentRejected) as failure:
                            action()
                        copy = failure.exception.copy
                        self.assertEqual(copy['key'], 'ui.attachments.server.' + suffix)
                        self.assertEqual(str(failure.exception), tr(copy['key'], **copy['params']))
                        self.assertTrue(failure.exception.code.startswith('attachment/'))

    def test_api_rejections_preserve_wire_fields_and_resource_details(self):
        upload, _ = build_router(SimpleNamespace()).resolve('POST', '/api/v1/attachments')
        fetch, params = build_router(SimpleNamespace()).resolve('GET', '/api/v1/attachments/invalid-id')
        cases = [
            (lambda: fetch(Request('GET', '/', {}, {}, b''), **params), 'notFound'),
            (lambda: decode_canonical_base64(None), 'dataRequired'),
            (lambda: decode_canonical_base64('a'), 'base64Length'),
            (lambda: decode_canonical_base64('!!!!'), 'base64Decode'),
            (lambda: decode_canonical_base64('Zh=='), 'base64Canonical'),
            (lambda: upload(Request('POST', '/', {}, {}, b'{"files":[]}')), 'filesRequired'),
            (lambda: upload(Request('POST', '/', {}, {}, b'{"files":[null]}')), 'entryObject'),
            (lambda: upload(Request('POST', '/', {}, {}, b'{"files":[{}]}')), 'nameRequired'),
            (lambda: upload(Request('POST', '/', {}, {}, b'{"files":[{"name":"a.txt","data":"YQ==","extra":1}]}')), 'invalidPayload'),
            (lambda: upload(Request('POST', '/', {}, {}, b'{"files":[{"name":"a.txt","data":""}]}')), 'empty'),
        ]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for action, suffix in cases:
                    with self.subTest(language=language, suffix=suffix):
                        with self.assertRaises(ApiError) as failure:
                            action()
                        error = failure.exception
                        copy = error.details['copy']
                        self.assertEqual(copy['key'], 'ui.attachments.server.' + suffix)
                        self.assertEqual(error.message, tr(copy['key'], **copy['params']))
                        wire = json.loads(error_response(error.message, code=error.code, status=error.status, details=error.details).body)
                        self.assertEqual(wire['error'], error.message)
                        self.assertEqual(wire['code'], error.code)
                        self.assertEqual(wire['details']['copy'], copy)

    def test_request_limit_retains_existing_numeric_details(self):
        upload, _ = build_router(SimpleNamespace()).resolve('POST', '/api/v1/attachments')
        with patch('harness.web.api.attachments.MAX_REQUEST_BYTES', 0), self.assertRaises(ApiError) as failure:
            upload(Request('POST', '/', {}, {}, b'{"files":[{"name":"a.txt","data":"YQ=="}]}'))
        self.assertEqual(failure.exception.status, 413)
        self.assertEqual(failure.exception.details['bytes'], 1)
        self.assertEqual(failure.exception.details['maxBytes'], 0)
        self.assertEqual(failure.exception.details['copy']['key'], 'ui.attachments.server.requestTooLarge')

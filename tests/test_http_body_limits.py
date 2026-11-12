"""请求体上限在读取前按接口实施，附件保留领域批次容量。"""
import asyncio
import json
from email.message import Message
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from harness.web.attachments import MAX_BATCH_BYTES
from harness.web.errors import BadRequest, PayloadTooLarge
from harness.web.api.v1 import build_router
from harness.web.server import Handler, MAX_ATTACHMENT_REQUEST_BODY_BYTES, MAX_REQUEST_BODY_BYTES


class RequestBodyLimitTests(unittest.TestCase):
    def handler(self, path, length, method="POST"):
        headers = Message()
        headers["Content-Length"] = str(length)
        return SimpleNamespace(
            path=path, command=method, headers=headers, close_connection=False,
            rfile=Mock(), connection=Mock(), client_address=("127.0.0.1", 1234),
        )

    def test_oversized_requests_are_rejected_without_reading(self):
        cases = [
            ("/api/v1/sessions", "POST", MAX_REQUEST_BODY_BYTES),
            ("/api/v1/sessions/fixture/runs", "POST", MAX_REQUEST_BODY_BYTES),
            ("/api/v1/attachments", "GET", MAX_REQUEST_BODY_BYTES),
            ("/api/v1/attachments/other", "POST", MAX_REQUEST_BODY_BYTES),
            ("/api/v1/attachments?session=x", "POST", MAX_ATTACHMENT_REQUEST_BODY_BYTES),
            ("/api/v1/attachments/", "POST", MAX_ATTACHMENT_REQUEST_BODY_BYTES),
            ("/api/v1//attachments//", "POST", MAX_ATTACHMENT_REQUEST_BODY_BYTES),
        ]
        for path, method, limit in cases:
            with self.subTest(path=path, method=method):
                handler = self.handler(path, limit + 1, method)
                with self.assertRaises(PayloadTooLarge) as caught:
                    Handler._build_request(handler)
                self.assertEqual(caught.exception.details["maxBytes"], limit)
                self.assertTrue(handler.close_connection)
                handler.rfile.read1.assert_not_called()

    def test_large_run_text_remains_bounded_and_only_reaches_a_stub_submission(self):
        runtime = Mock()
        runtime.submit.return_value = SimpleNamespace(to_dict=lambda: {'id': 'run-fixture'})
        ctx = SimpleNamespace(harness=SimpleNamespace(runtime=runtime), call=asyncio.run)
        path = '/api/v1/sessions/fixture/runs'
        submit, params = build_router(ctx).resolve('POST', path)
        for text in ('x' * 500000, '😀' * 500000):
            payload = json.dumps({'text': text}, ensure_ascii=False).encode('utf-8')
            self.assertLess(len(payload), MAX_REQUEST_BODY_BYTES)
            handler = self.handler(path, len(payload))
            handler.rfile = BytesIO(payload)
            request = Handler._build_request(handler)
            response = submit(request, **params)
            self.assertEqual(response.status, 202)
            self.assertEqual(runtime.submit.call_args.args, ('fixture', text))
        calls = runtime.submit.call_count
        handler = self.handler(path, MAX_REQUEST_BODY_BYTES + 1)
        with self.assertRaises(PayloadTooLarge):
            submit(Handler._build_request(handler), **params)
        handler.rfile.read1.assert_not_called()
        self.assertEqual(runtime.submit.call_count, calls)

    def test_attachment_capacity_and_exact_boundaries(self):
        self.assertEqual(MAX_REQUEST_BODY_BYTES, 4 * 1024 * 1024)
        self.assertGreater(MAX_ATTACHMENT_REQUEST_BODY_BYTES, 4 * ((MAX_BATCH_BYTES + 2) // 3))
        for path, limit in [
            ("/api/v1/sessions", MAX_REQUEST_BODY_BYTES),
            ("/api/v1/attachments", MAX_ATTACHMENT_REQUEST_BODY_BYTES),
            ("/api/v1/attachments/", MAX_ATTACHMENT_REQUEST_BODY_BYTES),
        ]:
            handler = self.handler(path, limit)
            # 用可识别的读取异常确认边界被接受，不分配百兆测试数据。
            handler.rfile.read1.side_effect = EOFError("读取边界")
            with self.assertRaises(EOFError):
                Handler._build_request(handler)
            handler.rfile.read1.assert_called_once()

    def test_empty_transfer_encoding_header_is_rejected_before_body_read(self):
        handler = self.handler('/api/v1/sessions', 0)
        handler.headers['Transfer-Encoding'] = ''
        with self.assertRaises(BadRequest) as caught:
            Handler._build_request(handler)
        self.assertEqual(caught.exception.code, 'request/invalid-length')
        self.assertTrue(handler.close_connection)
        handler.rfile.read1.assert_not_called()

    def test_small_body_is_preserved(self):
        handler = self.handler("/api/v1/attachments", 2)
        handler.rfile = BytesIO(b"{}")
        request = Handler._build_request(handler)
        self.assertEqual(request.body, b"{}")

"""传输大小配置不得通过布尔值或浮点数隐式改变读取上限。"""
import unittest
from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.transport import UrllibTransport


class TransportBodyLimitTests(unittest.TestCase):
    def test_invalid_limits_fail_before_network_resources_exist(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for value in (True, False, 0, -1, 1.5, 1.0, float('inf'), float('nan'), '1024', [], {}):
                    with self.subTest(language=language, value=value), self.assertRaises(ValueError) as caught:
                        UrllibTransport(max_body_bytes=value)
                    self.assertEqual(str(caught.exception), tr('transport.invalid_body_limit'))
        for value in (None, 1, 4096, 32 * 1024 * 1024):
            with self.subTest(value=value):
                self.assertEqual(UrllibTransport(max_body_bytes=value).max_body_bytes, value)


class TransportBodyReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_limit_and_unlimited_keep_bytes_over_limit_rejects_and_closes(self):
        import io
        from harness.core.errors import LlmResponseError
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for limit, body, rejected in ((3, b'abc', False), (None, b'abc', False), (2, b'abc', True)):
                    with self.subTest(language=language, limit=limit):
                        response = io.BytesIO(body)
                        response.status = 200
                        response.headers = {}
                        transport = UrllibTransport(max_body_bytes=limit)
                        if rejected:
                            with self.assertRaises(LlmResponseError) as caught:
                                _ = [chunk async for chunk in transport._stream_response(lambda: response, timeout=1)]
                            self.assertEqual(caught.exception.code, 'llm/response-byte-limit')
                            self.assertEqual(caught.exception.message, tr('python.transport.99b6f8371073', p0=str(limit)))
                        else:
                            result = b''.join([chunk async for chunk in transport._stream_response(lambda: response, timeout=1)])
                            self.assertEqual(result, body)
                        self.assertTrue(response.closed)

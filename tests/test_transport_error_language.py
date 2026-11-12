"""传输线程继承实际请求语言，连接失败保留分类与取消收尾。"""
import threading
import unittest
import urllib.error

from harness.core.errors import LlmRequestError, LlmTimeoutError
from harness.core.language import current_language
from harness.llm.transport import UrllibTransport


class TransportErrorLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_failures_use_caller_language_in_worker_thread(self):
        transport = UrllibTransport()
        for language in ('en', 'zh-CN', 'en'):
            for error, expected in (
                (TimeoutError('fixture timeout'), LlmTimeoutError),
                (urllib.error.URLError(TimeoutError('fixture timeout')), LlmTimeoutError),
                (urllib.error.URLError(ConnectionResetError('fixture reset')), LlmRequestError),
            ):
                seen = []
                def opener():
                    seen.append((threading.get_ident(), current_language.get()))
                    raise error
                with self.subTest(language=language, error=error), current_language.set(language):
                    with self.assertRaises(expected) as caught:
                        _ = [chunk async for chunk in transport._stream_response(opener, timeout=1)]
                    self.assertEqual(seen[0][1], language)
                    self.assertNotEqual(seen[0][0], threading.get_ident())
                    self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in caught.exception.message), language == 'zh-CN')

    async def test_concurrent_requests_keep_their_own_language(self):
        import asyncio
        barrier = threading.Barrier(2)
        async def request(language):
            def opener():
                barrier.wait(timeout=5)
                raise urllib.error.URLError(ConnectionResetError('fixture reset'))
            with current_language.set(language):
                with self.assertRaises(LlmRequestError) as caught:
                    _ = [chunk async for chunk in UrllibTransport()._stream_response(opener, timeout=2)]
                return caught.exception.message
        english, chinese = await asyncio.gather(request('en'), request('zh-CN'))
        self.assertFalse(any('\u4e00' <= c <= '\u9fff' for c in english))
        self.assertTrue(any('\u4e00' <= c <= '\u9fff' for c in chinese))

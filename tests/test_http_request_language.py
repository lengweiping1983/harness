"""长连接等待读取期间切换语言，实际请求仍须消费最新课程资源。"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from harness.academy.service import AcademyService
from harness.core.language import current_language
from harness.web.server import Handler


class HttpRequestLanguageTests(unittest.TestCase):
    def test_same_handler_resolves_language_after_waiting_for_request_bytes(self):
        with TemporaryDirectory(prefix='harness-request-language-') as directory:
            service = AcademyService(Path(directory))
            expected = {}
            for language in ('en', 'zh-CN'):
                with current_language.set(language):
                    expected[language] = service.course('harness-foundations')['lessons'][0]['goal']
            config = SimpleNamespace(language='zh-CN')
            observed = []

            class RequestHandler(Handler):
                def do_GET(self):
                    """消费实际课程资源，不能只核对语言变量自身。"""
                    observed.append(service.course('harness-foundations')['lessons'][0]['goal'])

            class WaitingInput(BytesIO):
                def readline(self, *args, **kwargs):
                    """模拟连接进入等待后，另一个设置连接改变了全局语言。"""
                    config.language = self.next_language
                    return super().readline(*args, **kwargs)

            handler = object.__new__(RequestHandler)
            handler.harness = SimpleNamespace(config=config)
            handler.wfile = BytesIO()
            handler.client_address = ('127.0.0.1', 12345)
            with current_language.set('zh-CN'):
                for language in ('en', 'zh-CN', 'en'):
                    stream = WaitingInput(b'GET /api/v1/academy/courses/harness-foundations HTTP/1.1\r\nHost: localhost\r\n\r\n')
                    stream.next_language = language
                    handler.rfile = stream
                    handler.handle_one_request()
                    self.assertEqual(observed[-1], expected[language])
                    self.assertEqual(current_language.get(), 'zh-CN')
            self.assertNotEqual(expected['en'], expected['zh-CN'])

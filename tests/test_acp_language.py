"""ACP 本机校验与连接错误遵循调用语言，协议名称不变。"""
import unittest

from harness.acp.client import AcpClient, AcpRunnerConfig, AcpError, _validate_unicode
from harness.core.language import current_language


class AcpLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_validation_and_closed_connection_are_localized(self):
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                for options in ({'command': ''}, {'command': 'fixture', 'args': [1]}, {'command': 'fixture', 'request_timeout': 0}):
                    with self.assertRaises(ValueError) as caught:
                        AcpRunnerConfig(**options)
                    self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in str(caught.exception)), language == 'zh-CN')
                client = AcpClient(AcpRunnerConfig('fixture'))
                with self.assertRaises(AcpError) as caught:
                    await client._send({})
                self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in str(caught.exception)), language == 'zh-CN')
                with self.assertRaises(AcpError):
                    _validate_unicode('\ud800')

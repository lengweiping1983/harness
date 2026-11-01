"""联网工具摘要与来源失败按调用语言呈现，外部原文保持。"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.web_tools import build_web_tools
from harness.tools.registry import ToolRunContext


class WebOutputLanguageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.dict('harness.tools.builtin.web_tools._ENGINE_UNREACHABLE_UNTIL', {}, clear=True))

    def context(self):
        return ToolRunContext('web-language', 'fixture', None, 1, 1)

    async def test_same_fetch_tool_renders_complete_and_truncated_summaries(self):
        fetch = build_web_tools()[0]
        url = 'https://example.test/Original-{count}'
        original = 'Original 原文 {mime}😀'
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for truncated in (False, True):
                    response = {'text': original, 'final_url': url, 'status': 200, 'content_type': 'text/plain',
                                'truncated': truncated, 'error': None}
                    with patch('harness.tools.builtin.web_tools._http_get', AsyncMock(return_value=response)) as get:
                        value = await fetch.execute({'url': url}, self.context())
                    if language == 'en':
                        suffix = ', truncated)' if truncated else ')'
                        expected = f'URL: {url} (HTTP 200, text/plain, {len(original)} characters{suffix}\n\n{original}'
                    else:
                        suffix = '，已截断）' if truncated else '）'
                        expected = f'URL：{url}（HTTP 200，text/plain，{len(original)} 字符{suffix}\n\n{original}'
                    self.assertEqual(fetch.to_content({}, value)[0].text, expected)
                    self.assertEqual(value['content'], original)
                    get.assert_awaited_once()

    async def test_engine_and_provider_failures_preserve_order_and_external_diagnostics(self):
        failure = ToolExecutionError('Original failure {source}')
        tools = [build_web_tools()[1], build_web_tools(provider_settings=lambda: {
            'deepseek': (True, 'fixture-key'), 'brave': (True, 'fixture-key')})[1]]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for index, tool in enumerate(tools):
                    with patch('harness.tools.builtin.web_tools._http_get', AsyncMock(side_effect=failure)) as get, \
                         patch('harness.tools.builtin.web_tools._http_request', AsyncMock(side_effect=failure)) as provider:
                        with self.assertRaises(ToolExecutionError) as error:
                            await tool.execute({'query': 'Original query', 'engine': 'bing'}, self.context())
                    self.assertIn('Original failure {source}', error.exception.message)
                    if language == 'en':
                        self.assertNotIn('；', error.exception.message)
                        self.assertNotIn('：', error.exception.message)
                    if index:
                        self.assertLess(error.exception.message.index('deepseek'), error.exception.message.index('brave'))
                        self.assertLess(error.exception.message.index('brave'), error.exception.message.index('default'))
                        self.assertEqual(provider.await_count, 2)
                    else:
                        self.assertEqual(error.exception.message, tr('web.search.failed', errors=tr('web.source.error', source='bing', error=str(failure))))
                    get.assert_awaited_once()

    async def test_provider_cancellation_never_falls_back(self):
        tool = build_web_tools(provider_settings=lambda: {'deepseek': (True, 'fixture-key'), 'brave': (True, 'fixture-key')})[1]
        with patch('harness.tools.builtin.web_tools._http_request', AsyncMock(side_effect=asyncio.CancelledError())) as provider, \
             patch('harness.tools.builtin.web_tools._http_get', AsyncMock()) as get:
            with self.assertRaises(asyncio.CancelledError):
                await tool.execute({'query': 'Original query', 'engine': 'bing'}, self.context())
        provider.assert_awaited_once()
        get.assert_not_awaited()

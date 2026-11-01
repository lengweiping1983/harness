"""网页请求协商与默认搜索地域按调用语言选择，不改写查询原文。"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from harness.core.language import current_language
from harness.tools.builtin.web_tools import _http_get, build_web_tools
from harness.tools.registry import ToolRunContext


class WebRequestLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_accept_language_is_resolved_for_each_call_in_worker(self):
        async def request(opener, *args, **kwargs):
            return await asyncio.to_thread(opener)

        with patch('harness.tools.builtin.web_tools._check_url'), patch('harness.tools.builtin.web_tools._http_request', side_effect=request), patch('harness.tools.builtin.web_tools.ipv4_preferred_get', return_value='original response') as get:
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    result = await _http_get('https://example.test/Original', timeout=2, max_bytes=10, accept='text/plain')
                self.assertEqual(result, 'original response')
                headers = get.call_args.kwargs['headers']
                self.assertEqual(headers['Accept-Language'], 'en' if language == 'en' else 'zh-CN,zh;q=0.9,en;q=0.8')
                self.assertEqual(headers['Accept'], 'text/plain')
                self.assertEqual(get.call_args.args, ('https://example.test/Original',))

    async def test_same_tool_uses_matching_search_region_without_changing_query(self):
        tool = build_web_tools()[1]
        query = 'Original 原文 {query} & code'
        ctx = ToolRunContext('owner', 'call', None, 1, 1)
        response = {'text': '<html></html>', 'final_url': 'https://example.test/', 'status': 200, 'content_type': 'text/html', 'error': None}
        for language in ('en', 'zh-CN', 'en'):
            for engine in ('duckduckgo', 'bing'):
                with self.subTest(language=language, engine=engine), current_language.set(language), patch('harness.tools.builtin.web_tools._http_get', AsyncMock(return_value=response)) as get:
                    await tool.execute({'query': query, 'engine': engine}, ctx)
                    parsed = urlsplit(get.call_args.args[0])
                    params = parse_qs(parsed.query)
                    self.assertEqual(params['q'], [query])
                    if engine == 'duckduckgo':
                        self.assertEqual(params['kl'], ['us-en' if language == 'en' else 'cn-zh'])
                    else:
                        self.assertEqual(parsed.hostname, 'www.bing.com' if language == 'en' else 'cn.bing.com')
                        self.assertEqual(params['setlang'], ['en' if language == 'en' else 'zh-hans'])
                    get.assert_awaited_once()

    async def test_engine_health_is_scoped_to_actual_endpoint_host(self):
        from harness.tools.builtin.web_tools import _engine_is_usable, _mark_engine_failed, _mark_engine_ok
        with patch.dict('harness.tools.builtin.web_tools._ENGINE_UNREACHABLE_UNTIL', {}, clear=True):
            with current_language.set('zh-CN'):
                _mark_engine_failed('bing', 100)
                self.assertFalse(_engine_is_usable('bing', 101))
            with current_language.set('en'):
                self.assertTrue(_engine_is_usable('bing', 101))
                _mark_engine_failed('bing', 101)
                _mark_engine_ok('bing')
                self.assertTrue(_engine_is_usable('bing', 102))
            with current_language.set('zh-CN'):
                self.assertFalse(_engine_is_usable('bing', 102))
                self.assertTrue(_engine_is_usable('bing', 1001))

    async def test_openrouter_without_credentials_reports_both_supported_variables(self):
        from harness.core.errors import ToolExecutionError
        tool = build_web_tools()[1]
        ctx = ToolRunContext('web-language', 'fixture', None, 1, 1)
        with patch.dict('os.environ', {}, clear=True), \
             patch('harness.tools.builtin.web_tools._http_request', new=AsyncMock()) as request:
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language), self.assertRaises(ToolExecutionError) as caught:
                    await tool.execute({'query': 'Original 原文 {query}', 'engine': 'openrouter'}, ctx)
                self.assertIn('OPENROUTER_API_KEY', caught.exception.message)
                self.assertIn('HARNESS_API_KEY', caught.exception.message)
                self.assertNotIn('default duckduckgo', caught.exception.message)
                self.assertNotIn('默认的 duckduckgo', caught.exception.message)
            request.assert_not_awaited()

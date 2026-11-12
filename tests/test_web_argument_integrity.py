"""联网工具在提供方之前拒绝错误类型，保留合法查询和外部正文。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.web_tools import build_web_tools


class WebArgumentIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_direct_inputs_never_read_provider_settings_or_start_network(self):
        cases = [('web_search', {'query': value}) for value in (None, True, 1, [], {}, '')]
        cases += [('web_fetch', {'url': value}) for value in (None, True, 1, [], {}, '')]
        for field in ('max_results', 'timeout'):
            for value in (None, True, '8', [], 0, -1, float('inf'), float('nan')):
                cases.append(('web_search', {'query':'Original 原文',field:value}))
        for field in ('max_length', 'max_bytes', 'timeout'):
            for value in (None, False, '8', {}, 0, -1, float('inf'), float('nan')):
                cases.append(('web_fetch', {'url':'https://fixture.invalid',field:value}))
        cases.append(('web_search', {'query':'Original','max_results':1.5}))
        cases += [('web_search', {'query':'Original','engine':value}) for value in (None, True, 1, [], 'unknown')]
        cases += [('web_fetch', {'url':'https://fixture.invalid','raw':value}) for value in (None, 1, 'false', [])]
        for language in ('en','zh-CN','en'):
            settings = Mock(return_value={'deepseek':(True,'fixture')})
            tools = {tool.schema.name:tool for tool in build_web_tools(provider_settings=settings)}
            for name, args in cases:
                with self.subTest(language=language,name=name,args=args), current_language.set(language), patch('harness.tools.builtin.web_tools._http_request',new_callable=AsyncMock) as network:
                    with self.assertRaises(ToolExecutionError):
                        await tools[name].execute(args, SimpleNamespace(session_id='owner'))
                    network.assert_not_awaited()
            settings.assert_not_called()

    async def test_valid_deepseek_query_keeps_original_unicode_and_integer_limit(self):
        tools={tool.schema.name:tool for tool in build_web_tools(provider_settings=lambda:{'deepseek':(True,'fixture')})}
        raw={'results':[{'title':'Original 原文','url':'https://fixture.invalid','summary':'Snippet 原文'}]}
        async def request(opener, url, **options):
            return {'text':json.dumps(raw),'error':None,'truncated':False}
        with patch('harness.tools.builtin.web_tools._http_request',side_effect=request) as network:
            result=await tools['web_search'].execute({'query':'  Original 原文  ','max_results':2,'timeout':1.5},SimpleNamespace(session_id='owner'))
        self.assertEqual(result['query'],'Original 原文')
        self.assertEqual(result['results'],[{'title':'Original 原文','url':'https://fixture.invalid','snippet':'Snippet 原文'}])
        self.assertEqual(network.call_count,1)

    async def test_openrouter_malformed_structures_return_localized_error_without_retry(self):
        tool=next(tool for tool in build_web_tools() if tool.schema.name=='web_search')
        for payload in ([],None,1,{'data':{}},{'data':[None]},{'results':[{'title':True}]}):
            for language in ('en','zh-CN','en'):
                with self.subTest(payload=payload,language=language), current_language.set(language), patch.dict('os.environ',{'OPENROUTER_API_KEY':'fixture'}), patch('harness.tools.builtin.web_tools._http_request',new_callable=AsyncMock,return_value={'text':json.dumps(payload),'error':None,'truncated':False}) as network:
                    with self.assertRaises(ToolExecutionError) as error:
                        await tool.execute({'query':'Original','engine':'openrouter'},SimpleNamespace(session_id='owner'))
                    self.assertEqual(error.exception.message,tr('web.deepseek.invalidResponse'))
                    network.assert_awaited_once()

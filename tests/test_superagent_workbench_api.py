"""本机工作台实际路由、协议、作用域与无模型读取验证。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from urllib.parse import urlsplit,parse_qs
import unittest

from harness.app.superagent_workbench import SuperagentWorkbench
from harness.core.language import current_language
from harness.web.api.superagent import build_router
from harness.web.context import WebContext
from harness.web.errors import ApiError
from harness.web.router import Request
from harness.web.server import Handler
from harness.tools.schema import validate
from tests.superagent_product_fixture import product_config


class WorkbenchApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.path,_ = product_config(self.root)
        self.created=[]
        async def forbidden(path):
            self.created.append(path)
            raise AssertionError('读取不得创建宿主')
        self.manager = SuperagentWorkbench(service_factory=forbidden)
        self.addAsyncCleanup(self.manager.close)
        self.ctx = WebContext(SimpleNamespace(superagent_workbench=self.manager),asyncio.get_running_loop())
        self.router = build_router(self.ctx)
        self.ctx._router = self.router
        self.handler = Handler.__new__(Handler)
        self.handler._web_ctx = self.ctx
        self.handler._origin_allowed = lambda: True

    async def request(self,method,url,body=None,*,headers=None,ip='127.0.0.1',raw=None):
        parsed = urlsplit(url)
        request = Request(method,parsed.path,parse_qs(parsed.query,keep_blank_values=True),
            {'Host':'127.0.0.1:8765','Origin':'http://127.0.0.1:8765',**(headers or {})},
            raw if raw is not None else json.dumps(body).encode() if body is not None else b'',client_ip=ip)
        response = await asyncio.to_thread(self.handler._dispatch,request)
        self.assertEqual(response.headers.get('Cache-Control'),'no-store')
        return json.loads(response.body)

    async def connect(self):
        return await self.request('POST','/api/v1/superagents/connect',{'config_path':str(self.path)})

    async def test_registered_routes_read_existing_state_and_commit_managed_source_without_models(self):
        connection = await self.connect(); key=connection['connection_id']
        base='/api/v1/superagents/'+key
        self.assertFalse((self.root/'state').exists())
        write = await self.request('POST',base+'/commands',{'request_id':'submit','action':'submit','payload':{'text':'Fix the typo.'}})
        self.assertEqual(write['instance_id'],'product')
        for resource in ('missions','devices','leases','audit','mandates','channels','sources','effects','attentions','events','evaluations','policy','local_options'):
            with self.subTest(resource=resource):
                result = await self.request('GET',base+'/read?resource='+resource)
                self.assertEqual(result['connection_id'],key)
                self.assertEqual(result['resource'],resource)
                self.assertEqual(result['data']['instance_id'],'product')
        disabled = await self.request('POST',base+'/commands',{'request_id':'source','action':'source',
            'payload':{'source_id':'local','expected_revision':1,'enabled':False}})
        self.assertFalse(disabled['receipt']['receipt']['enabled'])
        receipt = await self.request('GET',base+'/read?resource=receipt&identifier=source')
        self.assertEqual(receipt['data']['state'],'completed')
        self.assertEqual(self.created,[])

    async def test_remote_shared_and_cross_origin_requests_cannot_connect_or_read_paths(self):
        for headers,ip in (({'Origin':'https://foreign.invalid'},'127.0.0.1'),({},'10.0.0.2'),
                ({'Host':'foreign.invalid'},'127.0.0.1'),({'Sec-Fetch-Site':'cross-site'},'127.0.0.1'),
                ({'Origin':'http://127.0.0.1:8765/forged'},'127.0.0.1'),
                ({'Origin':'http://user@127.0.0.1:8765'},'127.0.0.1'),
                ({'Sec-Fetch-Site':'same-origin, Same-Site'},'127.0.0.1')):
            with self.subTest(headers=headers,ip=ip),self.assertRaises(ApiError) as caught:
                await self.request('POST','/api/v1/superagents/connect',{'config_path':str(self.path)},headers=headers,ip=ip)
            self.assertEqual(caught.exception.status,403)
            self.assertEqual(caught.exception.details['copy']['key'],'ui.sg.error.local')
        def authorize(request,params):
            return SimpleNamespace(subject='shared'),frozenset()
        self.ctx.shared_access=SimpleNamespace(browser_endpoint=lambda request:False,authorize_http=authorize)
        with self.assertRaises(ApiError) as caught: await self.connect()
        self.assertEqual(caught.exception.code,'superagent/local-only')
        self.assertEqual(self.manager._connections,{})
        self.assertFalse((self.root/'state').exists())

    async def test_malformed_and_duplicate_parameters_are_rejected_before_business_writes(self):
        connection=await self.connect(); base='/api/v1/superagents/'+connection['connection_id']
        for query in ('resource=missions&limit=0','resource=missions&limit=1&limit=2','resource=policy&cursor=x',
                'resource=missions&unknown=x','resource=foreign'):
            with self.subTest(query=query),self.assertRaises(ApiError):
                await self.request('GET',base+'/read?'+query)
        with self.assertRaises(ApiError):
            await self.request('POST',base+'/commands',raw=b'{"request_id":"one","request_id":"two","action":"start","payload":{}}')
        self.assertEqual(self.created,[])
        self.assertFalse((self.root/'state').exists())

    async def test_api_error_keeps_resource_identity_in_english_chinese_english(self):
        connection=await self.connect(); base='/api/v1/superagents/'+connection['connection_id']
        messages=[]
        for language in ('en','zh-CN','en'):
            with current_language.set(language),self.assertRaises(ApiError) as caught:
                await self.request('GET',base+'/read?resource=missions')
            messages.append(str(caught.exception))
            self.assertEqual(caught.exception.code,'superagent/status-unavailable')
            self.assertEqual(caught.exception.details['copy']['key'],'ui.sg.error.uninitialized')
        self.assertEqual(messages[0],messages[2]); self.assertNotEqual(messages[0],messages[1])

    async def test_new_connection_keeps_local_options_without_initializing_state_or_models(self):
        connection=await self.connect(); base='/api/v1/superagents/'+connection['connection_id']
        self.assertEqual(await self.request('GET',base),connection)
        options=await self.request('GET',base+'/read?resource=local_options')
        self.assertEqual(options['data']['sources'],[{'source_id':'local','actors':['human:1'],'threads':['work']}])
        with self.assertRaises(ApiError) as caught:
            await self.request('GET',base+'/read?resource=missions&limit=32')
        self.assertEqual(caught.exception.details['copy']['key'],'ui.sg.error.uninitialized')
        self.assertFalse((self.root/'state').exists())
        self.assertEqual(self.created,[])

    async def test_generated_protocol_describes_the_actual_connect_and_command_receipts(self):
        document=json.loads((Path(__file__).resolve().parents[1]/'docs/openapi-v1.json').read_text())
        schemas=document['components']['schemas']
        for name in ('SuperagentConnection','SuperagentRead','SuperagentWrite','SuperagentCommand'):
            self.assertIn(name,schemas)
        result=await self.connect()
        self.assertEqual(validate(result,schemas['SuperagentConnection']),[])
        self.assertIn('/api/v1/superagents/{connection_id}/read',document['paths'])
        media=document['paths']['/api/v1/superagents/{connection_id}/media/{media_id}']['get']['responses']['200']['content']
        self.assertIn('image/png',media); self.assertIn('application/octet-stream',media)

    async def test_media_route_binds_existing_connection_and_returns_safe_binary_headers(self):
        from unittest.mock import patch
        connection=await self.connect(); key=connection['connection_id']
        await self.request('POST','/api/v1/superagents/'+key+'/commands',{'request_id':'seed','action':'submit','payload':{'text':'Fix typo.'}})
        path='/api/v1/superagents/'+key+'/media/media_'+'a'*64
        request=Request('GET',path,{}, {'Host':'127.0.0.1:8765'},b'',client_ip='127.0.0.1')
        with patch('harness.web.api.superagent.SourceMedia.cached',return_value=({'content_type':'application/octet-stream'},b'<script>original</script>',{'name':'user.html'})) as cached:
            response=await asyncio.to_thread(self.handler._dispatch,request)
        cached.assert_called_once_with('media_'+'a'*64)
        self.assertEqual(response.body,b'<script>original</script>')
        self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
        self.assertEqual(response.headers['Content-Security-Policy'],"default-src 'none'; sandbox")
        self.assertTrue(response.headers['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(response.headers['Cache-Control'],'no-store')

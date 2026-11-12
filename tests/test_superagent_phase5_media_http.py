"""媒体使用真实回环 HTTP 验证认证、有界读取、撤权与禁止重定向。"""
import asyncio
import json
import unittest

from harness.app.superagent_feishu_http import FeishuClient
from harness.core.errors import HarnessError


class Phase5MediaHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests=[]; self.revoked=False
        async def handler(reader,writer):
            try:
                head=await reader.readuntil(b'\r\n\r\n')
                lines=head.decode('ascii').split('\r\n'); method,path,_=lines[0].split(' ')
                headers=dict(line.split(': ',1) for line in lines[1:] if ': ' in line)
                data=await reader.readexactly(int(headers.get('Content-Length','0')))
                self.requests.append((method,path,headers,data))
                if path.endswith('/internal'):
                    self.revoked=True
                    body=json.dumps({'code':0,'expire':3600,'tenant_access_token':'fixture-token'}).encode()
                    status,extra='200 OK','Content-Type: application/json\r\n'
                elif path=='/redirect':
                    body=b''; status='302 Found'; extra='Location: '+self.root+'/foreign\r\n'
                else:
                    body=b'\x00\xffsafe-binary'; status='200 OK'; extra='Content-Type: image/png; charset=binary\r\n'
                writer.write(('HTTP/1.1 '+status+'\r\n'+extra+'Content-Length: '+str(len(body))+'\r\nConnection: close\r\n\r\n').encode()+body)
                await writer.drain()
            finally:
                writer.close(); await writer.wait_closed()
        self.server=await asyncio.start_server(handler,'127.0.0.1',0)
        self.addAsyncCleanup(self.server.wait_closed); self.addCleanup(self.server.close)
        self.root='http://127.0.0.1:'+str(self.server.sockets[0].getsockname()[1])
        self.client=FeishuClient({'api_url':self.root,'app_id':'fixture'},'fixture-secret')

    async def test_binary_read_uses_refreshed_token_and_enforces_size_and_redirect_boundary(self):
        result=await self.client.request_bytes('/resource')
        self.assertEqual(result,{'data':b'\x00\xffsafe-binary','content_type':'image/png'})
        self.assertEqual(self.requests[0][0],'POST')
        self.assertEqual(json.loads(self.requests[0][3]),{'app_id':'fixture','app_secret':'fixture-secret'})
        self.assertEqual(self.requests[1][2]['Authorization'],'Bearer fixture-token')
        with self.assertRaises(HarnessError): await self.client.request_bytes('/large',maximum=2)
        with self.assertRaises(HarnessError): await self.client.request_bytes('/redirect')
        self.assertNotIn('/foreign',[item[1] for item in self.requests])
        self.assertEqual(len(self.requests),4)

    async def test_guard_after_token_refresh_prevents_resource_get_when_authority_changes(self):
        def guard():
            if self.revoked: raise PermissionError('fixture source revision revoked')
        with self.assertRaises(PermissionError): await self.client.request_bytes('/resource',before_request=guard)
        self.assertEqual([item[1] for item in self.requests],['/open-apis/auth/v3/tenant_access_token/internal'])

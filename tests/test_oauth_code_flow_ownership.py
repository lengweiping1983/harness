"""取消浏览器授权后，真实回调监听端口在返回前关闭。"""
import asyncio
import socket
import unittest
import urllib.parse
from unittest.mock import patch

from harness.connectors import oauth


class OAuthCodeFlowOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_closes_callback_server_without_token_exchange(self):
        server = oauth.AuthorizationServer('https://fixture.invalid', 'https://fixture.invalid/auth', 'https://fixture.invalid/token')
        resource = oauth.ProtectedResource('https://fixture.invalid/resource', (server.issuer,))
        pending = asyncio.get_running_loop().create_future()
        loop = asyncio.get_running_loop()
        def opened(value):
            loop.call_soon_threadsafe(pending.set_result, value)
        with patch.object(oauth, '_discover_all', return_value=(resource, server)), patch.object(
                oauth, 'resolve_client_id', return_value='client'), patch.object(oauth, '_http_json') as exchange:
            task = asyncio.create_task(oauth.run_authorization(resource.resource, 'fixture', {},
                on_pending=opened, open_browser=False, timeout=30))
            try:
                value = await asyncio.wait_for(pending, 2)
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(value.authorize_url).query)
                port = urllib.parse.urlsplit(query['redirect_uri'][0]).port
                task.cancel('Original cancellation')
                await asyncio.sleep(0)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
                with socket.socket() as probe:
                    probe.settimeout(1)
                    self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0)
                exchange.assert_not_called()
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

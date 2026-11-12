"""设备授权轮询的异步调度回归。"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.connectors import oauth


class DevicePollingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server = oauth.AuthorizationServer(
            issuer="https://example.test", authorization_endpoint="https://example.test/auth",
            token_endpoint="https://example.test/token",
            device_authorization_endpoint="https://example.test/device",
        )
        self.device = {"device_code": "device", "interval": 1}

    async def authorize(self, timeout=60):
        return await oauth.run_authorization(
            "https://example.test/resource", "test", {}, open_browser=False,
            timeout=timeout,
        )

    def discovery(self):
        return patch.object(oauth, "_discover_all", return_value=(
            oauth.ProtectedResource("https://example.test/resource", (self.server.issuer,)),
            self.server,
        ))

    async def test_wait_releases_executor_and_cancellation_stops_polling(self):
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        waiting = asyncio.Event()
        release = asyncio.Event()

        async def wait(interval):
            waiting.set()
            await release.wait()

        with self.discovery(), patch.object(oauth, "resolve_client_id", return_value="client"), \
                patch.object(oauth, "_http_json", return_value=self.device) as http, \
                patch.object(oauth.asyncio, "sleep", side_effect=wait):
            task = asyncio.create_task(self.authorize())
            try:
                await asyncio.wait_for(waiting.wait(), 2)
                self.assertEqual(await asyncio.wait_for(asyncio.to_thread(lambda: 42), 2), 42)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertEqual(http.call_count, 1)

    async def test_pending_and_slow_down_preserve_poll_intervals(self):
        sleep = AsyncMock()
        with self.discovery(), patch.object(oauth, "resolve_client_id", return_value="client"), \
                patch.object(oauth, "_http_json", side_effect=[
                    self.device, {"error": "authorization_pending"},
                    {"error": "slow_down"}, {"access_token": "token"},
                ]), patch.object(oauth.asyncio, "sleep", sleep):
            tokens, client, issuer = await self.authorize()
        self.assertEqual((tokens.access_token, client, issuer), ("token", "client", self.server.issuer))
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [1, 1, 6])

    async def test_expired_wait_does_not_send_token_request(self):
        with self.discovery(), patch.object(oauth, "resolve_client_id", return_value="client"), \
                patch.object(oauth, "_http_json", return_value=self.device) as http, \
                patch.object(oauth, "time", SimpleNamespace(monotonic=Mock(side_effect=[0, 0, 2]))), \
                patch.object(oauth.asyncio, "sleep", AsyncMock()):
            with self.assertRaises(oauth.OAuthError) as raised:
                await self.authorize(timeout=1)
        self.assertEqual(raised.exception.code, "connector/oauth-timeout")
        self.assertEqual(http.call_count, 1)

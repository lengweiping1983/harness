"""OAuth HTTP线程取消须等待响应关闭，不重放令牌请求。"""
import asyncio
import threading
import unittest
from unittest.mock import Mock, patch

from harness.connectors import oauth


class OAuthHttpOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_device_request_waits_for_response_close(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                closed.set()
            def read(self):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError('Fixture release missing')
                return b'{"device_code":"original"}'
        server = oauth.AuthorizationServer('https://fixture.invalid', 'https://fixture.invalid/auth',
            'https://fixture.invalid/token', device_authorization_endpoint='https://fixture.invalid/device')
        pending = Mock()
        with patch('urllib.request.urlopen', return_value=Response()) as request:
            task = asyncio.create_task(oauth._run_device_flow(server, 'client', [],
                on_pending=pending, open_browser=False, timeout=1))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=0.02)
                self.assertEqual(done, set())
                task.cancel()
                self.assertFalse(closed.is_set())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(closed.is_set())
                pending.assert_not_called()
                request.assert_called_once()
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

    async def test_cancel_and_network_failure_preserve_both_original_exceptions(self):
        entered, release = threading.Event(), threading.Event()
        failure = OSError('Original HTTP read failure')
        def request():
            entered.set()
            if not release.wait(3):
                raise TimeoutError('Fixture release missing')
            raise failure
        task = asyncio.create_task(oauth._http_work(request))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            task.cancel('Original cancellation')
            await asyncio.sleep(0)
            release.set()
            with self.assertRaises(BaseExceptionGroup) as caught:
                await task
            cancelled, network = caught.exception.exceptions
            self.assertIsInstance(cancelled, asyncio.CancelledError)
            self.assertEqual(cancelled.args, ('Original cancellation',))
            self.assertIs(network, failure)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

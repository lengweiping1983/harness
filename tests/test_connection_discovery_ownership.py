"""连接状态请求等待并回收能力发现，缓存失败不能被后台回调吞掉。"""
import asyncio
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness.llm.capabilities import CapabilityRegistry
from harness.llm.routes import ModelRoutes
from harness.web.api.connection import build_router
from harness.web.router import Request


def endpoint(registry, call, models=None):
    pool = ModelRoutes(models or [{'model': 'fixture', 'base_url': 'https://example.test/v1', 'keys': [{'value': 'fixture'}]}])
    pool.capabilities = registry
    host = SimpleNamespace(chatgpt=None, adapter=SimpleNamespace(route_pool=pool, capabilities=registry))
    router = build_router(SimpleNamespace(harness=host, call=call))
    handler, params = router.resolve('GET', '/api/v1/connection')
    return lambda: json.loads(handler(Request('GET', '/api/v1/connection', {}, {}, b''), **params).body)


class ConnectionDiscoveryOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_status_requests_share_fetch_and_return_discovered_snapshot(self):
        registry = CapabilityRegistry()
        loop = asyncio.get_running_loop()
        # HTTP 请求线程与能力发现线程分离，低核数运行器同样保留发现线程的执行机会。
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        entered, all_started = asyncio.Event(), asyncio.Event()
        release = threading.Event()
        started = 0

        async def submit(coro):
            nonlocal started
            started += 1
            if started == 8:
                all_started.set()
            return await coro

        def fetch(*args):
            loop.call_soon_threadsafe(entered.set)
            release.wait(5)
            return {'context_window': 12345}

        invoke = endpoint(registry, lambda coro: asyncio.run_coroutine_threadsafe(submit(coro), loop).result())
        with ThreadPoolExecutor(max_workers=8) as requests, patch.object(registry, '_fetch', side_effect=fetch) as request:
            pending = [loop.run_in_executor(requests, invoke) for _ in range(8)]
            try:
                await asyncio.wait_for(asyncio.gather(entered.wait(), all_started.wait()), 3)
                self.assertTrue(all(not task.done() for task in pending))
                release.set()
                results = await asyncio.wait_for(asyncio.gather(*pending), 3)
            finally:
                release.set()
                await asyncio.gather(*pending, return_exceptions=True)
            self.assertEqual(request.call_count, 1)
        self.assertEqual(registry.pending, {})
        self.assertTrue(all(result['models'][0]['capabilities']['context_window'] == 12345 for result in results))

    async def test_cache_failure_reaches_request_and_retry_remains_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = CapabilityRegistry(Path(directory))
            loop = asyncio.get_running_loop()
            invoke = endpoint(registry, lambda coro: asyncio.run_coroutine_threadsafe(coro, loop).result())
            failure = OSError('fixture disk failure')
            with patch.object(registry, '_fetch', return_value={'context_window': 12345}), patch(
                    'harness.llm.capabilities.atomic_json', side_effect=failure):
                with self.assertRaises(ExceptionGroup) as caught:
                    await asyncio.to_thread(invoke)
            self.assertIs(caught.exception.exceptions[0], failure)
            self.assertEqual(registry.pending, {})
            self.assertEqual(registry.cache, {})
            self.assertEqual(registry.checked, {})
            with patch.object(registry, '_fetch', return_value={'context_window': 12345}):
                result = await asyncio.to_thread(invoke)
            self.assertEqual(result['models'][0]['capabilities']['context_window'], 12345)

    async def test_remote_discovery_failure_still_returns_existing_capabilities(self):
        registry = CapabilityRegistry()
        registry.learn('https://example.test/v1', 'fixture', context_window=321)
        loop = asyncio.get_running_loop()
        invoke = endpoint(registry, lambda coro: asyncio.run_coroutine_threadsafe(coro, loop).result())
        with patch.object(registry, '_fetch', side_effect=OSError('fixture offline')):
            result = await asyncio.to_thread(invoke)
        self.assertEqual(result['models'][0]['capabilities']['context_window'], 321)
        self.assertEqual(registry.pending, {})

    async def test_failed_endpoint_waits_for_sibling_cleanup(self):
        registry = CapabilityRegistry()
        entered, settled = asyncio.Event(), asyncio.Event()
        failure = OSError('fixture failure')

        async def resolve(url, model, manual=None):
            if model == 'slow':
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    settled.set()
            else:
                await entered.wait()
                raise failure

        loop = asyncio.get_running_loop()
        models = [{'model': model, 'base_url': 'https://example.test/v1', 'keys': [{'value': 'fixture'}]}
                  for model in ('slow', 'failed')]
        invoke = endpoint(registry, lambda coro: asyncio.run_coroutine_threadsafe(coro, loop).result(), models)
        with patch.object(registry, 'resolve', side_effect=resolve):
            with self.assertRaises(ExceptionGroup) as caught:
                await asyncio.to_thread(invoke)
        self.assertTrue(settled.is_set())
        self.assertIs(caught.exception.exceptions[0], failure)

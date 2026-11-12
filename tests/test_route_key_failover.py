"""验证完整凭据遍历、冷却隔离和部分输出不得重放。"""
import unittest
import tempfile
from contextlib import aclosing
from pathlib import Path
from unittest.mock import AsyncMock, patch

from harness.core.errors import LlmError
from harness.llm.adapter import OpenRouterAdapter
from harness.llm.routes import ModelRoutes
from harness.llm.types import UserMessage


class ControlledTransport:
    """按槽位注入错误并经过真实 SSE 解析器返回成功结果。"""
    def __init__(self, failures, *, partial=False):
        self.failures = failures
        self.partial = partial
        self.calls = []

    async def post_stream(self, url, *, headers, body, timeout):
        key = headers['Authorization'].removeprefix('Bearer ')
        self.calls.append(key)
        if self.partial:
            yield b'data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n'
        if key in self.failures:
            status, delay = self.failures[key]
            raise LlmError('controlled failure', status=status, transient=status >= 429,
                           **({'retry_after': delay} if delay is not None else {}))
        yield b'data: {"choices":[{"index":0,"delta":{"content":"OK"}}]}\n\n'
        yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        yield b'data: [DONE]\n\n'


class RouteKeyFailoverTests(unittest.IsolatedAsyncioTestCase):
    def create(self, failures, *, size=9, partial=False):
        transport = ControlledTransport(failures, partial=partial)
        adapter = OpenRouterAdapter('fixture-1', model='fixture', transport=transport, max_retries=0)
        adapter.route_pool = ModelRoutes([{'id':'fixture', 'model':'fixture',
            'base_url':'https://fixture.invalid/v1',
            'keys':[{'id':str(i), 'value':f'fixture-{i}'} for i in range(1, size + 1)]}])
        return adapter, transport

    async def consume(self, adapter):
        return [event async for event in adapter.stream([UserMessage('hi')])]

    async def test_last_of_nine_succeeds_after_eight_failures(self):
        for status in (401, 402, 403, 429, 503):
            with self.subTest(status=status):
                adapter, transport = self.create({f'fixture-{i}':(status, None) for i in range(1,9)})
                events = await self.consume(adapter)
                self.assertEqual(transport.calls, [f'fixture-{i}' for i in range(1,10)])
                self.assertEqual(''.join(e.text for e in events if e.kind == 'text'), 'OK')
                self.assertTrue(adapter.route_pool.models[0]['enabled'])
                self.assertFalse(adapter.route_pool.in_use)
                keys = adapter.route_pool.snapshot()['models'][0]['keys']
                self.assertTrue(all(not key['available'] for key in keys[:8]))
                self.assertTrue(keys[8]['available'])

    async def test_429_disables_only_current_key_and_persists(self):
        adapter, transport = self.create({'fixture-1':(429,3600)})
        pool = adapter.route_pool
        with tempfile.TemporaryDirectory() as folder:
            pool.home = Path(folder)
            await self.consume(adapter)
            self.assertEqual(transport.calls, ['fixture-1', 'fixture-2'])
            snapshot = pool.snapshot()['models'][0]
            self.assertFalse(snapshot['keys'][0]['available'])
            self.assertTrue(all(key['available'] for key in snapshot['keys'][1:]))
            restored = ModelRoutes(pool.models, pool.home)
            self.assertFalse(restored.snapshot()['models'][0]['keys'][0]['available'])

    async def test_all_failed_status_survives_reads_and_restart_until_next_request(self):
        adapter, _ = self.create({f'fixture-{i}':(429,3600) for i in range(1,10)})
        pool = adapter.route_pool
        with tempfile.TemporaryDirectory() as folder:
            pool.home = Path(folder)
            with self.assertRaises(LlmError):
                await self.consume(adapter)
            restored = ModelRoutes(pool.models, pool.home)
            for _ in range(2):
                self.assertTrue(all(not key['available'] for key in restored.snapshot()['models'][0]['keys']))
            with patch('harness.llm.routes.time.monotonic', return_value=0):
                selected = restored.select()
            self.assertEqual(selected[1]['value'], 'fixture-1')
            self.assertEqual(restored.cycle, 2)
            self.assertEqual(restored.wait_until, 5)

    async def test_all_nine_fail_once_then_stop(self):
        for status, delay in ((403, None), (429, 3600)):
            with self.subTest(status=status):
                adapter, transport = self.create({f'fixture-{i}':(status,delay) for i in range(1,10)})
                with self.assertRaises(LlmError) as caught:
                    await self.consume(adapter)
                self.assertEqual(transport.calls, [f'fixture-{i}' for i in range(1,10)])
                self.assertTrue(caught.exception.details['route_exhausted'])
                self.assertEqual(caught.exception.details['route_attempts'], 9)
                self.assertTrue(all(not key['available'] for key in adapter.route_pool.snapshot()['models'][0]['keys']))
                self.assertTrue(adapter.route_pool.models[0]['enabled'])
                self.assertFalse(adapter.route_pool.in_use)

    async def test_long_cooldown_does_not_block_other_credentials(self):
        adapter, transport = self.create({f'fixture-{i}':(429,3600) for i in range(1,9)})
        with patch('harness.llm.adapter.asyncio.sleep', new_callable=AsyncMock) as sleep:
            events = await self.consume(adapter)
        sleep.assert_not_awaited()
        self.assertEqual(len(transport.calls), 9)
        self.assertEqual(''.join(e.text for e in events if e.kind == 'text'), 'OK')

    async def test_prior_failure_marks_do_not_repeat_attempted_credentials(self):
        adapter, transport = self.create({f'fixture-{i}':(401,None) for i in range(1,10)})
        pool = adapter.route_pool
        for slot in pool.slots()[6:8]:
            pool.fail(slot[2], pool.generation)
        with self.assertRaises(LlmError):
            await self.consume(adapter)
        self.assertEqual(set(transport.calls), {f'fixture-{i}' for i in range(1,10)})
        self.assertEqual(len(transport.calls), 9)

    async def test_single_credential_still_obeys_long_cooldown(self):
        adapter, transport = self.create({'fixture-1':(429,3600)}, size=1)
        with self.assertRaises(LlmError) as caught:
            await self.consume(adapter)
        self.assertEqual(caught.exception.code, 'llm/retry-delay-limit')
        self.assertEqual(transport.calls, ['fixture-1'])
        self.assertFalse(adapter.route_pool.snapshot()['models'][0]['keys'][0]['available'])

    async def test_previous_failure_recovers_only_after_success(self):
        adapter, transport = self.create({'fixture-1':(429,3600), 'fixture-2':(429,3600)}, size=3)
        pool = adapter.route_pool
        pool.fail(pool.slots()[2][2], pool.generation)
        async with aclosing(adapter.stream([UserMessage('hi')])) as stream:
            async for event in stream:
                if event.kind == 'done':
                    break
        self.assertEqual(transport.calls, ['fixture-1', 'fixture-2', 'fixture-3'])
        self.assertEqual([key['available'] for key in pool.snapshot()['models'][0]['keys']], [False, False, True])

    async def test_late_success_does_not_override_manual_status(self):
        adapter, _ = self.create({})
        pool = adapter.route_pool
        identity = pool.slots()[0][2]
        generation = pool.generation
        pool.set_available('fixture', '1', False)
        pool.succeed(identity, generation)
        self.assertFalse(pool.snapshot()['models'][0]['keys'][0]['available'])

    async def test_partial_output_never_replays_on_another_key(self):
        adapter, transport = self.create({'fixture-1':(503,None)}, partial=True)
        with self.assertRaises(LlmError) as caught:
            await self.consume(adapter)
        self.assertTrue(caught.exception.details['partial_output'])
        self.assertEqual(transport.calls, ['fixture-1'])
        self.assertFalse(adapter.route_pool.in_use)

    async def test_parameter_error_does_not_disable_any_key(self):
        adapter, transport = self.create({'fixture-1':(400,None)})
        with self.assertRaises(LlmError):
            await self.consume(adapter)
        self.assertEqual(transport.calls, ['fixture-1'])
        self.assertFalse(adapter.route_pool.failed)

    async def test_route_contract_rejects_empty_topology_before_first_acquisition(self):
        from copy import deepcopy
        from harness.core.language import current_language
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                adapter, transport = self.create({}, size=1)
                pool = adapter.route_pool
                original = deepcopy(pool.models)
                disabled = deepcopy(original)
                disabled[0]['enabled'] = False
                no_keys = deepcopy(original)
                no_keys[0]['keys'] = []
                for invalid in ([], disabled, no_keys):
                    with self.assertRaises(ValueError):
                        ModelRoutes(invalid)
                    with self.assertRaises(ValueError):
                        pool.update(invalid)
                    self.assertEqual(pool.models, original)
                acquired = await pool.acquire(exclude=set())
                self.assertIsNotNone(acquired)
                identity = acquired[2]
                pool.release(identity)
                self.assertIsNone(await pool.acquire(exclude={identity}))
                self.assertFalse(pool.in_use)
                self.assertFalse(transport.calls)

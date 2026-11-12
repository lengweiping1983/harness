"""模型流任一层关闭失败不能跳过已拥有的后续句柄。"""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.adapter import OpenRouterAdapter


class Stream:
    def __init__(self, name, attempts, failure=None, read_failure=None):
        self.name, self.attempts, self.failure = name, attempts, failure
        self.read_failure = read_failure

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.read_failure is not None:
            raise self.read_failure
        raise StopAsyncIteration

    async def aclose(self):
        self.attempts.append(self.name)
        if self.failure is not None:
            raise self.failure


class AdapterStreamCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_stream_keeps_network_accounting_without_response_cap(self):
        adapter = OpenRouterAdapter('fixture-only')
        self.assertIsNone(adapter.transport.max_body_bytes)
        observations = []
        closed = []
        async def raw():
            try:
                for _ in range(34):
                    yield b'x' * 1024 * 1024
            finally:
                closed.append(True)
        async def consume(source, **_):
            async for chunk in source:
                yield len(chunk)
        adapter._consume = consume
        sizes = [size async for size in adapter._bounded_consume(raw(), on_attempt=AsyncMock(side_effect=observations.append))]
        self.assertEqual(sum(sizes), 34 * 1024 * 1024)
        self.assertEqual(closed, [True])
        self.assertGreater(observations[0]['network_bytes'], 0)

    async def test_network_timeout_still_closes_the_stream(self):
        from harness.core.errors import LlmTimeoutError
        closed = []
        async def raw():
            try:
                await asyncio.Event().wait()
                yield b''
            finally:
                closed.append(True)
        adapter = OpenRouterAdapter('fixture-only', network_timeout=0.02)
        with self.assertRaises(LlmTimeoutError) as caught:
            _ = [event async for event in adapter._bounded_consume(raw())]
        self.assertEqual(caught.exception.code, 'llm/timeout')
        self.assertEqual(closed, [True])

    async def test_native_bridge_does_not_cap_cumulative_event_bytes(self):
        import json
        from types import SimpleNamespace
        from harness.llm.provider_runtime import bridge
        raw = (json.dumps({'type': 'event', 'event': {'text': 'x' * 1024 * 1024}}) + '\n').encode()
        process = SimpleNamespace(stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()),
            stdout=SimpleNamespace(readline=AsyncMock(side_effect=[raw] * 9 + [b'', b''])), wait=AsyncMock(return_value=0))
        with patch('harness.llm.provider_runtime._start_process', AsyncMock(return_value=process)), \
                patch('harness.llm.provider_runtime._stop_stream_process', AsyncMock()) as stop:
            events = [event async for event in bridge({'operation': 'stream'})]
        self.assertEqual(len(events), 9)
        stop.assert_awaited_once_with(process)

    async def test_all_owned_layers_close_and_multiple_errors_keep_originals(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                attempts = []
                failures = [OSError('parsed'), RuntimeError('wire'), ValueError('raw')]
                parsed, wire, raw = [Stream(name, attempts, error) for name, error in zip(('parsed', 'wire', 'raw'), failures)]
                adapter = OpenRouterAdapter('fixture-only')
                adapter._chatgpt_endpoint = object()
                adapter._consume = Mock(return_value=parsed)
                with patch('harness.llm.chatgpt.chat_chunks', return_value=wire):
                    with self.assertRaises(ExceptionGroup) as caught:
                        _ = [event async for event in adapter._bounded_consume(raw)]
                self.assertEqual(attempts, ['parsed', 'wire', 'raw'])
                self.assertEqual(list(caught.exception.exceptions), failures)
                self.assertEqual(caught.exception.message, tr('cleanup.failed'))

    async def test_single_failure_preserves_type_and_identity_after_raw_close(self):
        for failure in (OSError('parsed'), asyncio.CancelledError()):
            attempts = []
            adapter = OpenRouterAdapter('fixture-only')
            adapter._consume = Mock(return_value=Stream('parsed', attempts, failure))
            raw = Stream('raw', attempts)
            with self.assertRaises(type(failure)) as caught:
                _ = [event async for event in adapter._bounded_consume(raw)]
            self.assertIs(caught.exception, failure)
            self.assertEqual(attempts, ['parsed', 'raw'])

    async def test_normal_end_closes_raw_once_without_turning_success_into_failure(self):
        attempts = []
        adapter = OpenRouterAdapter('fixture-only')
        adapter._consume = Mock(return_value=Stream('parsed', attempts))
        self.assertEqual([event async for event in adapter._bounded_consume(Stream('raw', attempts))], [])
        self.assertEqual(attempts, ['parsed', 'raw'])

    async def test_trace_failure_preserves_generation_failure_in_exception_chain(self):
        from harness.core.errors import LlmError
        for language in ('en', 'zh-CN', 'en'):
            for source in (LlmError('Original provider error', code='llm/fixture'), asyncio.CancelledError()):
                with current_language.set(language):
                    attempts = []
                    observer_error = OSError('Original trace error')
                    adapter = OpenRouterAdapter('fixture-only')
                    adapter._consume = Mock(return_value=Stream('parsed', attempts, read_failure=source))
                    observer = AsyncMock(side_effect=['trace-fixture', observer_error])
                    with self.assertRaises(OSError) as caught:
                        _ = [event async for event in adapter._traced_consume(Stream('raw', attempts),
                            on_attempt=None, on_request=observer, body=b'{}', url='https://fixture.invalid')]
                    self.assertIs(caught.exception, observer_error)
                    self.assertIs(caught.exception.__context__, source)
                    self.assertEqual(attempts, ['parsed', 'raw'])
                    self.assertEqual(observer.await_count, 2)

    async def test_trace_only_failure_keeps_its_original_identity(self):
        attempts = []
        adapter = OpenRouterAdapter('fixture-only')
        adapter._consume = Mock(return_value=Stream('parsed', attempts))
        failure = OSError('Original trace error')
        observer = AsyncMock(side_effect=['trace-fixture', failure])
        with self.assertRaises(OSError) as caught:
            _ = [event async for event in adapter._traced_consume(Stream('raw', attempts),
                on_attempt=None, on_request=observer, body=b'{}', url='https://fixture.invalid')]
        self.assertIs(caught.exception, failure)
        self.assertEqual(attempts, ['parsed', 'raw'])

    async def test_close_failures_keep_generation_failure_in_the_exception_chain(self):
        from harness.core.errors import LlmError
        source = LlmError('Original provider error', code='llm/fixture')
        attempts = []
        failures = [OSError('parsed close'), OSError('raw close')]
        adapter = OpenRouterAdapter('fixture-only')
        adapter._consume = Mock(return_value=Stream('parsed', attempts, failures[0], read_failure=source))
        with self.assertRaises(ExceptionGroup) as caught:
            _ = [event async for event in adapter._bounded_consume(Stream('raw', attempts, failures[1]))]
        self.assertEqual(list(caught.exception.exceptions), failures)
        self.assertIs(caught.exception.__context__, source)
        self.assertEqual(attempts, ['parsed', 'raw'])

    async def test_native_stream_preserves_initial_text_and_current_tool_deltas(self):
        import json
        chunks = [
            {'type':'content_block_start', 'index':0, 'content_block':{'type':'text', 'text':'Initial 原文 {name}'}},
            {'type':'content_block_delta', 'index':0, 'delta':{'type':'text_delta', 'text':' tail'}},
            {'type':'content_block_stop', 'index':0},
            {'type':'content_block_start', 'index':1, 'content_block':{'type':'tool_use', 'id':'call-fixture', 'name':'read', 'input':{}}},
            {'type':'content_block_delta', 'index':1, 'delta':{'type':'input_json_delta', 'partial_json':'{"path":"Original 文件"}'}},
            {'type':'content_block_stop', 'index':1},
            {'type':'message_delta', 'delta':{'stop_reason':'tool_use'}},
            {'type':'message_stop'},
        ]
        async def raw():
            for chunk in chunks:
                yield ('data: '+json.dumps(chunk, ensure_ascii=False)+'\n\n').encode('utf-8')
        for initial in ('Initial 原文 {name}', ''):
            chunks[0]['content_block']['text'] = initial
            events = [event async for event in OpenRouterAdapter('fixture-only')._bounded_consume(raw())]
            self.assertEqual(''.join(event.text for event in events if event.kind == 'text'), initial + ' tail')
            calls = [event.tool_use for event in events if event.kind == 'tool_use']
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].input, {'path':'Original 文件'})
            self.assertEqual(calls[0].id, 'call-fixture')

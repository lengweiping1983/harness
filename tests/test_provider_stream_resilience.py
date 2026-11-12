"""发行桥接的真实网络活动、断流恢复及不可重放边界。"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import fields
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from harness import Harness, HarnessConfig
from harness.core.errors import LlmError
from harness.core.language import current_language
from harness.llm.adapter import OpenRouterAdapter
from harness.llm.provider_runtime import bridge
from harness.llm.providers import ProviderConnections
from harness.llm.types import LlmCallConfig, ToolSchema, UserMessage
from harness.tools.registry import ToolDefinition
from tests.test_provider_connections import provider_fixture


def sse(value, kind=None):
    """保留各供应商的真实流式帧格式。"""
    return ((f'event: {kind}\n' if kind else '') + 'data: ' + json.dumps(value) + '\n\n').encode()


def chat(delta, finish=None):
    """构造完整且可被供应商 SDK 接受的 Completions 帧。"""
    return sse({'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'remote-a',
                'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]})


HEADERS = b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n'
IDENTITY = 'harness/supplier/endpoint/model-a'


async def wait_for_peer_disconnect(reader, disconnected):
    """EOF 和连接重置都证明对端断开；其他读取故障仍向上传播。"""
    try:
        await reader.read()
    except ConnectionResetError:
        pass
    disconnected.set()


@asynccontextmanager
async def endpoint(respond):
    """本轮独占回环监听与连接，退出前等待每个处理协程收尾。"""
    requests, writers, tasks = [], [], []
    async def handle(reader, writer):
        writers.append(writer)
        try:
            try:
                raw = await reader.readuntil(b'\r\n\r\n')
            except asyncio.IncompleteReadError as exc:
                if not exc.partial:
                    return
                raise
            headers = {key.lower(): value for key, value in
                       (line.split(b':', 1) for line in raw.split(b'\r\n')[1:] if b':' in line)}
            requests.append(json.loads(await reader.readexactly(int(headers[b'content-length']))))
            await respond(len(requests), reader, writer)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
    def connected(reader, writer):
        tasks.append(asyncio.create_task(handle(reader, writer)))
    server = await asyncio.start_server(connected, '127.0.0.1', 0)
    original = None
    try:
        yield f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1', requests
    except BaseException as exc:
        original = exc
        raise
    finally:
        server.close()
        await server.wait_closed()
        for writer in writers:
            writer.close()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
        errors = [value for value in results if isinstance(value, BaseException)]
        if errors:
            raise BaseExceptionGroup('回环请求处理失败', [*([original] if original else []), *errors])


class ProviderStreamResilienceTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, root, url, protocol='openai-completions', timeout=0.3):
        """只配置隔离接口及一个测试 Key，禁止读取在线账号。"""
        store = ProviderConnections(root)
        value = provider_fixture(url, protocol)
        value['connections'][0].update(keys=value['connections'][0]['keys'][:1], max_retries=0)
        store.save([value], store.revision)
        adapter = OpenRouterAdapter('unused', network_timeout=timeout)
        adapter.provider_connections = store
        return adapter

    async def test_heartbeats_and_fragmented_tools_outlive_the_idle_timeout(self):
        for protocol in ('openai-completions', 'anthropic-messages', 'openai-responses'):
            async def respond(number, reader, writer):
                writer.write(HEADERS)
                # 只有网络心跳，没有可供模型事件消费者接收的正文或思考。
                for _ in range(5):
                    writer.write(b': heartbeat\n\n')
                    await writer.drain()
                    await asyncio.sleep(0.08)
                if protocol == 'openai-completions':
                    frames = [chat({'tool_calls': [{'index': 0, 'id': 'call', 'type': 'function',
                               'function': {'name': 'lookup', 'arguments': ''}}]})]
                    frames += [chat({'tool_calls': [{'index': 0, 'function': {'arguments': part}}]})
                               for part in ('{"value":', '42', '}')]
                    frames += [chat({}, 'tool_calls'), b'data: [DONE]\n\n']
                elif protocol == 'anthropic-messages':
                    values = [('message_start', {'message': {'id': 'msg', 'role': 'assistant', 'model': 'remote-a', 'content': [], 'usage': {'input_tokens': 1, 'output_tokens': 0}}}),
                              ('content_block_start', {'index': 0, 'content_block': {'type': 'tool_use', 'id': 'call', 'name': 'lookup', 'input': {}}})]
                    values += [('content_block_delta', {'index': 0, 'delta': {'type': 'input_json_delta', 'partial_json': part}})
                               for part in ('{"value":', '42', '}')]
                    values += [('content_block_stop', {'index': 0}), ('message_delta', {'delta': {'stop_reason': 'tool_use'}, 'usage': {'output_tokens': 2}}), ('message_stop', {})]
                    frames = [sse({'type': kind, **value}, kind) for kind, value in values]
                else:
                    call = {'id': 'fc', 'type': 'function_call', 'call_id': 'call', 'name': 'lookup', 'arguments': '{"value":42}', 'status': 'completed'}
                    values = [('response.created', {'response': {'id': 'resp', 'status': 'in_progress', 'output': []}}),
                              ('response.output_item.added', {'output_index': 0, 'item': {**call, 'arguments': ''}})]
                    values += [('response.function_call_arguments.delta', {'output_index': 0, 'delta': part})
                               for part in ('{"value":', '42', '}')]
                    values += [('response.output_item.done', {'output_index': 0, 'item': call}),
                               ('response.completed', {'response': {'id': 'resp', 'status': 'completed', 'output': [call], 'usage': {'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3}}})]
                    frames = [sse({'type': kind, **value}, kind) for kind, value in values]
                for frame in frames:
                    writer.write(frame)
                    await writer.drain()
                    await asyncio.sleep(0.08)
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as root:
                async with endpoint(respond) as (url, requests):
                    adapter = self.adapter(root, url, protocol)
                    progress, traces = [], []
                    async def attempted(value):
                        progress.append(value)
                    async def traced(value):
                        traces.append(value)
                        return 'trace'
                    tool = ToolSchema(name='lookup', description='fixture', parameters={'type': 'object', 'properties': {'value': {'type': 'integer'}}})
                    events = [event async for event in adapter.stream([UserMessage('test')], config=LlmCallConfig(model=IDENTITY), tools=[tool], on_attempt=attempted, on_request=traced)]
                    self.assertEqual(len(requests), 1)
                    self.assertEqual([event.tool_use.input for event in events if event.kind == 'tool_use'], [{'value': 42}])
                    self.assertEqual(events[-1].kind, 'done')
                    self.assertTrue(any(value.get('network_bytes', 0) > 0 for value in progress))
                    self.assertIsNotNone(traces[-1]['observation']['first_event_ms'])
                    self.assertIsNone(traces[-1]['observation']['error_code'])
                    self.assertEqual(adapter.provider_connections.leases, {})

    async def test_no_headers_times_out_with_recoverable_diagnostics(self):
        disconnected = asyncio.Event()
        async def respond(number, reader, writer):
            await wait_for_peer_disconnect(reader, disconnected)
        with tempfile.TemporaryDirectory() as root:
            async with endpoint(respond) as (url, requests):
                adapter = self.adapter(root, url)
                traces = []
                async def traced(value):
                    traces.append(value)
                    return 'trace'
                with self.assertRaises(LlmError) as caught:
                    _ = [event async for event in adapter.stream([UserMessage('test')], config=LlmCallConfig(model=IDENTITY), on_request=traced)]
                self.assertEqual(caught.exception.code, 'llm/timeout')
                self.assertTrue(caught.exception.transient)
                await asyncio.wait_for(disconnected.wait(), 3)
                self.assertEqual(len(requests), 1)
                self.assertEqual(traces[-1]['observation']['error_code'], 'llm/timeout')
                self.assertIn(traces[-1]['observation']['error_details']['phase'], {'provider', 'network'})
                self.assertEqual(adapter.provider_connections.leases, {})

    async def test_agent_recovers_truncated_stream_without_replaying_completed_tool(self):
        async def respond(number, reader, writer):
            writer.write(HEADERS)
            if number == 1:
                writer.write(chat({'tool_calls': [{'index': 0, 'id': 'call', 'type': 'function', 'function': {'name': 'record', 'arguments': '{}'}}]}, 'tool_calls') + b'data: [DONE]\n\n')
            elif number == 2:
                writer.write(chat({'content': 'Original partial text'}))
                await writer.drain()
                await asyncio.sleep(0.05)
                writer.transport.abort()
            else:
                writer.write(chat({'content': 'Continued answer'}, 'stop') + b'data: [DONE]\n\n')
        with tempfile.TemporaryDirectory() as root:
            async with endpoint(respond) as (url, requests):
                flags = {field.name: False for field in fields(HarnessConfig) if field.name.startswith('enable_')}
                app = Harness(HarnessConfig(api_key='fixture', model=IDENTITY, home=Path(root)/'state', cwd=root,
                    network_timeout=2, max_retries=0, permission_policy='full_access', **flags))
                calls = []
                app.tools.register(ToolDefinition({'name': 'record', 'description': 'fixture', 'parameters': {'type': 'object'}},
                    lambda args, context: calls.append(context.call_id) or 'recorded', effect='workspace_write'))
                adapter = self.adapter(Path(root)/'providers', url, timeout=2)
                app.adapter.provider_connections = adapter.provider_connections
                try:
                    session = app.create_session()
                    result = await app._agent_for(session).prompt(session, 'Complete this task')
                    self.assertEqual(result.reason, 'completed', result.error)
                    self.assertEqual(calls, ['call'])
                    self.assertEqual(len(requests), 3)
                    self.assertTrue(any(message.get('content') == 'Original partial text' for message in requests[-1]['messages']))
                    self.assertEqual(len([event for event in session.events if event.type == 'tool/result']), 1)
                    self.assertIsNone(session.open_turn)
                finally:
                    await app.close()

    async def test_failure_identity_keeps_permanent_errors_non_retryable(self):
        cases = [(0, 'ECONNRESET', True), (200, 'UND_ERR_SOCKET', True),
                 (0, 'CERT_HAS_EXPIRED', False), (401, 'ECONNRESET', False),
                 (400, 'ECONNRESET', False)]
        for status, code, transient in cases:
            for language in ('en', 'zh-CN', 'en'):
                failure = {'type': 'failure', 'status': status, 'message': 'connection reset secret', 'error_type': 'APIConnectionError', 'error_code': code,
                           'provider_trace_id': 'trace-secret'}
                process = SimpleNamespace(stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()),
                    stdout=SimpleNamespace(readline=AsyncMock(side_effect=[(json.dumps(failure)+'\n').encode(), b''])), wait=AsyncMock(return_value=0))
                with self.subTest(status=status, code=code, language=language), current_language.set(language), \
                        patch('harness.llm.provider_runtime._start_process', AsyncMock(return_value=process)), \
                        patch('harness.llm.provider_runtime._stop_stream_process', AsyncMock()):
                    with self.assertRaises(LlmError) as caught:
                        _ = [value async for value in bridge({'operation': 'stream', 'credential': {'key': 'secret'}})]
                    self.assertEqual(caught.exception.transient, transient)
                    self.assertNotIn('secret', str(caught.exception))
                    self.assertEqual(caught.exception.details['provider_trace_id'], 'trace-[redacted]')
                    self.assertEqual(caught.exception.details['error_code'], code)
                    self.assertIn('模型请求失败' if language == 'zh-CN' else 'Model request failed', str(caught.exception))

    async def test_model_call_failure_preserves_trace_without_rotating_or_disabling_keys(self):
        for language, nested, thinking_format in (('en', False, 'openai'), ('zh-CN', True, 'openrouter'), ('en', False, 'openai')):
            async def respond(number, reader, writer):
                error = {'code': 'MODEL_CALL_FAILED', 'message': 'upstream model failed'}
                body = json.dumps({'error': error, 'traceId': 'trace-fixture'} if nested else
                                  {**error, 'traceId': 'trace-fixture'}).encode()
                writer.write(b'HTTP/1.1 409 Conflict\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n' + body)
            with self.subTest(language=language, nested=nested), current_language.set(language), tempfile.TemporaryDirectory() as root:
                async with endpoint(respond) as (url, requests):
                    store = ProviderConnections(root)
                    value = provider_fixture(url)
                    value['connections'][0]['models'][0].update(model='egg-alpha', reasoning_efforts={'high': 'high', 'max': 'max'}, compat={'thinkingFormat': thinking_format})
                    store.save([value], store.revision)
                    adapter = OpenRouterAdapter('fixture', network_timeout=3)
                    adapter.provider_connections = store
                    traces = []
                    async def traced(value):
                        traces.append(value)
                        return 'local-trace'
                    with self.assertRaises(LlmError) as caught:
                        _ = [event async for event in adapter.stream([UserMessage('test')],
                            config=LlmCallConfig(model=IDENTITY, reasoning_effort='max'), on_request=traced)]
                    error = caught.exception
                    self.assertEqual(error.code, 'llm/provider-model-call-failed')
                    self.assertFalse(error.transient)
                    self.assertEqual(error.details['status'], 409)
                    self.assertEqual(error.details['error_code'], 'MODEL_CALL_FAILED')
                    self.assertEqual(error.details['provider_trace_id'], 'trace-fixture')
                    self.assertIn('模型请求失败' if language == 'zh-CN' else 'Model request failed', str(error))
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(requests[0]['model'], 'egg-alpha')
                    if thinking_format == 'openrouter':
                        self.assertEqual(requests[0]['reasoning'], {'effort': 'max'})
                        self.assertNotIn('reasoning_effort', requests[0])
                    else:
                        self.assertEqual(requests[0]['reasoning_effort'], 'max')
                        self.assertNotIn('reasoning', requests[0])
                    self.assertEqual(traces[-1]['observation']['error_details']['provider_trace_id'], 'trace-fixture')
                    self.assertFalse(any(key['failed'] for key in store.providers[0]['connections'][0]['keys']))
                    self.assertEqual(store.leases, {})

    async def test_model_call_failure_keeps_completed_tool_and_turn_evidence(self):
        async def respond(number, reader, writer):
            if number == 1:
                writer.write(HEADERS + chat({'tool_calls': [{'index': 0, 'id': 'call', 'type': 'function',
                    'function': {'name': 'record', 'arguments': '{}'}}]}, 'tool_calls') + b'data: [DONE]\n\n')
            else:
                body = json.dumps({'code': 'MODEL_CALL_FAILED', 'message': 'upstream model failed', 'traceId': 'trace-after-tool'}).encode()
                writer.write(b'HTTP/1.1 409 Conflict\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n' + body)
        with tempfile.TemporaryDirectory() as root:
            async with endpoint(respond) as (url, requests):
                flags = {field.name: False for field in fields(HarnessConfig) if field.name.startswith('enable_')}
                app = Harness(HarnessConfig(api_key='fixture', model=IDENTITY, home=Path(root)/'state', cwd=root,
                    network_timeout=3, max_retries=0, permission_policy='full_access', **flags))
                calls = []
                app.tools.register(ToolDefinition({'name': 'record', 'description': 'fixture', 'parameters': {'type': 'object'}},
                    lambda args, context: calls.append(context.call_id) or 'recorded', effect='workspace_write'))
                app.adapter.provider_connections = self.adapter(Path(root)/'providers', url, timeout=3).provider_connections
                try:
                    session = app.create_session()
                    with self.assertLogs('harness.core.agent', level='ERROR'):
                        result = await app._agent_for(session).prompt(session, 'Complete this task')
                    self.assertEqual(result.reason, 'error')
                    self.assertEqual(calls, ['call'])
                    self.assertEqual(len(requests), 2)
                    self.assertEqual(len([event for event in session.events if event.type == 'tool/result']), 1)
                    error = session.latest('turn/end').data['reason']['error']
                    self.assertEqual(error['code'], 'llm/provider-model-call-failed')
                    self.assertEqual(error['details']['provider_trace_id'], 'trace-after-tool')
                    self.assertEqual(error['details']['recoveries'], 0)
                    self.assertIsNone(session.open_turn)
                    self.assertEqual(app.adapter.provider_connections.leases, {})
                finally:
                    await app.close()

    async def test_agent_retries_a_real_header_timeout_with_a_finite_limit(self):
        async def respond(number, reader, writer):
            await reader.read()
        with tempfile.TemporaryDirectory() as root:
            async with endpoint(respond) as (url, requests):
                flags = {field.name: False for field in fields(HarnessConfig) if field.name.startswith('enable_')}
                app = Harness(HarnessConfig(api_key='fixture', model=IDENTITY, home=Path(root)/'state', cwd=root,
                    network_timeout=0.3, max_retries=0, **flags))
                app.adapter.provider_connections = self.adapter(Path(root)/'providers', url).provider_connections
                try:
                    session = app.create_session()
                    with self.assertLogs('harness.core.agent', level='ERROR'):
                        result = await app._agent_for(session).prompt(session, 'Complete this task')
                    self.assertEqual(result.reason, 'error')
                    self.assertEqual(len(requests), 4)
                    error = session.latest('turn/end').data['reason']['error']
                    self.assertEqual(error['code'], 'llm/timeout')
                    self.assertEqual(error['details']['recoveries'], 3)
                    self.assertTrue(error['details']['request_recovery_exhausted'])
                    self.assertIsNone(session.open_turn)
                    self.assertEqual(app.adapter.provider_connections.leases, {})
                finally:
                    await app.close()

    async def test_protocol_and_process_failures_keep_their_cause_without_retrying(self):
        for raw, returncode, phase in ((b'{broken\n', 0, 'bridge'), (b'', 7, 'process')):
            process = SimpleNamespace(stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()),
                stdout=SimpleNamespace(readline=AsyncMock(side_effect=[raw, b''])), wait=AsyncMock(return_value=returncode))
            with self.subTest(phase=phase), patch('harness.llm.provider_runtime._start_process', AsyncMock(return_value=process)), \
                    patch('harness.llm.provider_runtime._stop_stream_process', AsyncMock()) as stop:
                with self.assertRaises(LlmError) as caught:
                    _ = [value async for value in bridge({'operation': 'stream'})]
                self.assertEqual(caught.exception.code, 'llm/provider-runtime')
                self.assertFalse(caught.exception.transient)
                self.assertEqual(caught.exception.details['phase'], phase)
                if phase == 'bridge':
                    self.assertIsNotNone(caught.exception.__cause__)
                else:
                    self.assertEqual(caught.exception.details['returncode'], 7)
                stop.assert_awaited_once()

    async def test_unknown_credential_refresh_is_not_retried_as_a_network_failure(self):
        values = [{'type': 'request'}, {'type': 'credential_pending'},
                  {'type': 'failure', 'status': 0, 'message': 'timeout', 'error_type': 'APIConnectionTimeoutError'}]
        process = SimpleNamespace(stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()),
            stdout=SimpleNamespace(readline=AsyncMock(side_effect=[*(json.dumps(value).encode()+b'\n' for value in values), b''])), wait=AsyncMock(return_value=0))
        commit = AsyncMock()
        with patch('harness.llm.provider_runtime._start_process', AsyncMock(return_value=process)), \
                patch('harness.llm.provider_runtime._stop_stream_process', AsyncMock()):
            with self.assertRaises(LlmError) as caught:
                _ = [value async for value in bridge({'operation': 'stream', 'credential': {'access': 'fixture'}}, commit=commit)]
        self.assertFalse(caught.exception.transient)
        commit.assert_awaited_once_with({'access': 'fixture', 'harness_refresh_unknown': True})

    async def test_cancellation_closes_the_owned_network_request(self):
        active, disconnected = asyncio.Event(), asyncio.Event()
        async def respond(number, reader, writer):
            writer.write(HEADERS + b': heartbeat\n\n')
            await writer.drain()
            active.set()
            await wait_for_peer_disconnect(reader, disconnected)
        with tempfile.TemporaryDirectory() as root:
            async with endpoint(respond) as (url, requests):
                adapter = self.adapter(root, url, timeout=5)
                async def consume():
                    return [event async for event in adapter.stream([UserMessage('test')], config=LlmCallConfig(model=IDENTITY))]
                task = asyncio.create_task(consume())
                await asyncio.wait_for(active.wait(), 5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
                await asyncio.wait_for(disconnected.wait(), 3)
                self.assertEqual(len(requests), 1)
                self.assertEqual(adapter.provider_connections.leases, {})

    async def test_disconnect_observer_accepts_reset_but_preserves_other_read_failures(self):
        """确定性注入断连形式，不能把连接重置误报为取消未释放。"""
        for failure in (None, ConnectionResetError('对端重置连接'), OSError('原始读取故障')):
            with self.subTest(failure=failure):
                disconnected = asyncio.Event()
                reader = SimpleNamespace(read=AsyncMock(return_value=b'', side_effect=failure))
                if failure is not None and not isinstance(failure, ConnectionResetError):
                    with self.assertRaises(OSError) as caught:
                        await wait_for_peer_disconnect(reader, disconnected)
                    self.assertIs(caught.exception, failure)
                    self.assertFalse(disconnected.is_set())
                else:
                    await wait_for_peer_disconnect(reader, disconnected)
                    self.assertTrue(disconnected.is_set())
                reader.read.assert_awaited_once_with()

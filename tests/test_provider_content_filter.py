"""内容审核拒绝的真实桥接、凭据与轮次证据回归。"""
import json
from dataclasses import fields
from pathlib import Path
import tempfile
import unittest

from harness import Harness, HarnessConfig
from harness.core.errors import LlmError
from harness.core.language import current_language
from harness.llm.adapter import OpenRouterAdapter
from harness.llm.provider_runtime import _provider_failure
from harness.llm.providers import ProviderConnections
from harness.llm.types import LlmCallConfig, UserMessage
from harness.tools.registry import ToolDefinition
from tests.test_provider_connections import provider_fixture
from tests.test_provider_stream_resilience import HEADERS, IDENTITY, chat, endpoint, sse


def provider_adapter(root, url):
    """隔离两个可用 Key，并显式开启重试以验证拒绝边界。"""
    store = ProviderConnections(root)
    providers = [provider_fixture(url)]
    providers[0]['connections'][0]['max_retries'] = 2
    store.save(providers, store.revision)
    adapter = OpenRouterAdapter('unused', network_timeout=3)
    adapter.provider_connections = store
    return adapter


class ProviderContentFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_inspection_rejection_preserves_diagnostics_and_keys_without_retry(self):
        for language, status in (('en', 200), ('zh-CN', 403), ('en', 503)):
            async def respond(number, reader, writer):
                error = {'code': 'data_inspection_failed', 'message': 'Output data may contain inappropriate content.',
                         'traceId': 'inspection-fixture'}
                if status == 200:
                    writer.write(HEADERS + sse({'error': error}))
                else:
                    writer.write(f'HTTP/1.1 {status} Rejected\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n'.encode()
                                 + json.dumps({'error': error}).encode())
            with self.subTest(language=language, status=status), current_language.set(language), tempfile.TemporaryDirectory() as root:
                async with endpoint(respond) as (url, requests):
                    adapter = provider_adapter(root, url)
                    store = adapter.provider_connections
                    before = store.snapshot()
                    traces = []
                    async def traced(value):
                        traces.append(value)
                        return 'request-fixture'
                    with self.assertRaises(LlmError) as caught:
                        _ = [event async for event in adapter.stream([UserMessage('Sports animation')],
                            config=LlmCallConfig(model=IDENTITY), on_request=traced)]
                    error = caught.exception
                    self.assertEqual(error.code, 'llm/content-filter')
                    self.assertFalse(error.transient)
                    self.assertEqual(error.details['status'], status)
                    self.assertEqual(error.details['error_code'], 'data_inspection_failed')
                    self.assertIn('Output data may contain inappropriate content.', error.message)
                    self.assertIn('模型请求失败' if language == 'zh-CN' else 'Model request failed', error.message)
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(store.snapshot(), before)
                    self.assertEqual(store.leases, {})
                    self.assertEqual(traces[-1]['observation']['error_code'], 'llm/content-filter')
                    self.assertEqual(traces[-1]['observation']['error_details']['error_code'], 'data_inspection_failed')

    async def test_inspection_rejection_keeps_completed_tool_and_partial_response(self):
        for partial in (False, True):
            async def respond(number, reader, writer):
                if number == 1:
                    writer.write(HEADERS + chat({'tool_calls': [{'index': 0, 'id': 'call', 'type': 'function',
                        'function': {'name': 'record', 'arguments': '{}'}}]}, 'tool_calls') + b'data: [DONE]\n\n')
                else:
                    writer.write(HEADERS)
                    if partial:
                        writer.write(chat({'content': 'Original partial response'}))
                    writer.write(sse({'error': {'code': 'data_inspection_failed',
                        'message': 'Output data may contain inappropriate content.'}}))
            with self.subTest(partial=partial), tempfile.TemporaryDirectory() as root:
                async with endpoint(respond) as (url, requests):
                    flags = {field.name: False for field in fields(HarnessConfig) if field.name.startswith('enable_')}
                    app = Harness(HarnessConfig(api_key='fixture', model=IDENTITY, home=Path(root) / 'state', cwd=root,
                        network_timeout=3, max_retries=2, permission_policy='full_access', **flags))
                    calls = []
                    app.tools.register(ToolDefinition({'name': 'record', 'description': 'fixture', 'parameters': {'type': 'object'}},
                        lambda args, context: calls.append(context.call_id) or 'recorded', effect='workspace_write'))
                    app.adapter.provider_connections = provider_adapter(Path(root) / 'providers', url).provider_connections
                    try:
                        session = app.create_session()
                        with self.assertLogs('harness.core.agent', level='ERROR'):
                            result = await app._agent_for(session).prompt(session, 'Complete the sports animation')
                        self.assertEqual(result.reason, 'error')
                        self.assertEqual(calls, ['call'])
                        self.assertEqual(len(requests), 2)
                        self.assertEqual(len([event for event in session.events if event.type == 'tool/result']), 1)
                        error = session.latest('turn/end').data['reason']['error']
                        self.assertEqual(error['code'], 'llm/content-filter')
                        self.assertEqual(error['details']['recoveries'], 0)
                        self.assertEqual(error['details']['no_text_progress'], not partial)
                        preserved = [event.data for event in session.events
                                     if event.type == 'note' and event.data.get('kind') == 'assistant_partial']
                        self.assertEqual([value['text'] for value in preserved], ['Original partial response'] if partial else [])
                        self.assertIsNone(session.open_turn)
                        self.assertEqual(app.adapter.provider_connections.leases, {})
                    finally:
                        await app.close()

    def test_unknown_errors_are_not_classified_by_message_text(self):
        for code, message, transient in (('unknown', 'Output data may contain inappropriate content.', False),
                                         ('unknown', 'service unavailable', True),
                                         ('data_inspection_failed', 'service unavailable', False)):
            with self.subTest(code=code, message=message):
                error = _provider_failure({'status': 200, 'message': message, 'error_code': code}, {})
                self.assertEqual(error.code, 'llm/content-filter' if code == 'data_inspection_failed' else 'llm/provider')
                self.assertEqual(error.transient, transient)

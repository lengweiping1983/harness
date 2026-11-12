"""输出能力与请求预算分离，并核对真实协议正文。"""
import json
import tempfile
import unittest

from harness.core.errors import LlmContextLengthError
from harness.llm.adapter import OpenRouterAdapter
from harness.llm.capabilities import CapabilityRegistry
from harness.llm.request_limits import normalize_request
from harness.llm.providers import ProviderConnections
from harness.llm.types import LlmCallConfig, UserMessage
from tests.test_provider_connections import provider_fixture
from tests.test_provider_stream_resilience import IDENTITY, endpoint, chat, sse, HEADERS


class RequestBudgetTests(unittest.TestCase):
    def test_unspecified_budget_stays_unspecified_with_known_capacity(self):
        for capacity in (None, 128000, 384000):
            body, info = normalize_request({}, {'context_window': 1000000, 'max_output_tokens': capacity}, input_tokens=100)
            self.assertNotIn('max_tokens', body)
            self.assertIsNone(info['requested_max_tokens'])
            self.assertIsNone(info['max_tokens'])
            self.assertEqual(info['model_max_output_tokens'], capacity)
            self.assertEqual(info['adjustments'], [])

    def test_explicit_aliases_share_capacity_and_context_constraints(self):
        for field in ('max_tokens', 'max_completion_tokens', 'max_output_tokens'):
            body, info = normalize_request({field: 128000}, {'context_window': 10000, 'max_output_tokens': 8000}, input_tokens=3000)
            self.assertEqual(body, {'max_tokens': 6900})
            self.assertEqual(info['requested_max_tokens'], 128000)
        body, _ = normalize_request({'max_tokens': 512, 'max_completion_tokens': 8192}, {'max_output_tokens': 4096})
        self.assertEqual(body, {'max_tokens': 512})

    def test_input_preflight_and_explicit_reasoning_budget_remain_enforced(self):
        with self.assertRaises(LlmContextLengthError):
            normalize_request({}, {'context_window': 1000}, input_tokens=1000)
        body, _ = normalize_request({'reasoning': {'max_tokens': 2000}}, {'max_output_tokens': 1500})
        self.assertEqual(body, {'reasoning': {'max_tokens': 1499}})


class AdapterBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_completions_omit_default_and_clamp_explicit_aliases(self):
        async def respond(number, reader, writer):
            writer.write(HEADERS + chat({'content': 'OK'}, 'stop') + b'data: [DONE]\n\n')
        async with endpoint(respond) as (url, requests):
            for config, expected in ((LlmCallConfig(model='egg-alpha'), None),
                    (LlmCallConfig(model='egg-alpha', max_tokens=200000), 128000),
                    (LlmCallConfig(model='egg-alpha', max_tokens=64, extra_body={'max_completion_tokens': 256}), 64),
                    (LlmCallConfig(model='egg-alpha', extra_body={'max_output_tokens': 256}), 256)):
                adapter = OpenRouterAdapter('fixture', base_url=url, capabilities=CapabilityRegistry(discover=False),
                    model_info={'context_window': 1000000, 'max_output_tokens': 128000})
                traces = []
                async def traced(value):
                    if value['phase'] == 'started':
                        traces.append(json.loads(value['body']))
                events = [event async for event in adapter.stream([UserMessage('Reply OK')], config=config, on_request=traced)]
                self.assertEqual(''.join(event.text for event in events if event.kind == 'text'), 'OK')
                self.assertEqual(traces, [requests[-1]])
                self.assertEqual(requests[-1].get('max_tokens'), expected)
                self.assertNotIn('max_completion_tokens', requests[-1])
                self.assertNotIn('max_output_tokens', requests[-1])

    async def test_responses_default_explicit_budgets_and_messages_required_limit(self):
        cases = [('openai-responses', None, {}, None),
                 ('openai-responses', 200000, {}, 128000),
                 ('openai-responses', 64, {'max_output_tokens': 256}, 64),
                 ('openai-responses', None, {'max_output_tokens': 256}, 256),
                 ('openai-completions', None, {'max_completion_tokens': 200000}, 128000),
                 ('openai-completions', 64, {'max_tokens': 256, 'max_completion_tokens': 512}, 64),
                 ('anthropic-messages', None, {}, 128000),
                 ('anthropic-messages', 64, {}, 64)]
        for protocol, requested, extra, expected in cases:
            async def respond(number, reader, writer):
                writer.write(HEADERS)
                if protocol == 'openai-completions':
                    writer.write(chat({'content': 'OK'}, 'stop') + b'data: [DONE]\n\n')
                elif protocol == 'openai-responses':
                    writer.write(sse({'type': 'response.completed', 'response': {'id': 'resp', 'status': 'completed', 'output': [],
                        'usage': {'input_tokens': 1, 'output_tokens': 0, 'total_tokens': 1}}}, 'response.completed'))
                else:
                    writer.write(sse({'type': 'message_start', 'message': {'id': 'msg', 'role': 'assistant', 'model': 'remote-a',
                        'content': [], 'usage': {'input_tokens': 1, 'output_tokens': 0}}}, 'message_start'))
                    writer.write(sse({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 0}}, 'message_delta'))
                    writer.write(sse({'type': 'message_stop'}, 'message_stop'))
            with self.subTest(protocol=protocol, requested=requested, extra=extra), tempfile.TemporaryDirectory() as root:
                async with endpoint(respond) as (url, requests):
                    store = ProviderConnections(root)
                    value = provider_fixture(url, protocol)
                    value['connections'][0]['max_retries'] = 0
                    value['connections'][0]['models'][0].update(context_window=1000000, max_output_tokens=128000)
                    store.save([value], store.revision)
                    adapter = OpenRouterAdapter('fixture', network_timeout=3)
                    adapter.provider_connections = store
                    traces = []
                    async def traced(value):
                        if value['phase'] == 'started':
                            traces.append(json.loads(value['body']))
                    events = [event async for event in adapter.stream([UserMessage('Reply OK')],
                        config=LlmCallConfig(model=IDENTITY, max_tokens=requested, extra_body=extra), on_request=traced)]
                    self.assertEqual(events[-1].kind, 'done')
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(traces, requests)
                    field = {'openai-responses': 'max_output_tokens', 'openai-completions': 'max_completion_tokens',
                             'anthropic-messages': 'max_tokens'}[protocol]
                    self.assertEqual(requests[0].get(field), expected)
                    for alias in {'max_tokens', 'max_completion_tokens', 'max_output_tokens'} - {field}:
                        self.assertNotIn(alias, requests[0])

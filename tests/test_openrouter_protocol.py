"""OpenRouter 目录外模型的协议选择及真实发行桥接回归。"""
import asyncio
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from harness.core.errors import LlmError
from harness.llm.adapter import OpenRouterAdapter
from harness.llm.providers import ProviderConnections, directory
from harness.llm.types import LlmCallConfig, ToolSchema, UserMessage
from tests.test_provider_connections import provider_fixture


MODEL = 'stealth/crouton-alpha'
IDENTITY = 'harness/supplier/endpoint/model-a'


class OpenRouterProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                """隔离测试不输出凭据。"""

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, body, self.headers.get('Authorization')))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                if self.path.endswith('/chat/completions'):
                    values = [
                        {'id': 'fixture', 'choices': [{'index': 0, 'delta': {'content': '完成'}, 'finish_reason': None}]},
                        {'id': 'fixture', 'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'id': 'call', 'type': 'function',
                            'function': {'name': 'lookup', 'arguments': '{"value":42}'}}]}, 'finish_reason': 'tool_calls'}]},
                        {'id': 'fixture', 'choices': [], 'usage': {'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10}},
                    ]
                    for value in values:
                        self.wfile.write(('data: ' + json.dumps(value) + '\n\n').encode())
                    self.wfile.write(b'data: [DONE]\n\n')
                    return
                if self.path.split('?', 1)[0].endswith('/messages'):
                    values = [
                        ('message_start', {'message': {'id': 'msg', 'type': 'message', 'role': 'assistant', 'model': body['model'],
                            'content': [], 'usage': {'input_tokens': 7, 'output_tokens': 0}}}),
                        ('content_block_start', {'index': 0, 'content_block': {'type': 'text', 'text': ''}}),
                        ('content_block_delta', {'index': 0, 'delta': {'type': 'text_delta', 'text': '完成'}}),
                        ('content_block_stop', {'index': 0}),
                        ('message_delta', {'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 3}}),
                        ('message_stop', {}),
                    ]
                else:
                    message = {'id': 'msg', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text': '完成', 'annotations': []}]}
                    values = [
                        ('response.created', {'response': {'id': 'resp', 'status': 'in_progress', 'output': []}}),
                        ('response.output_item.added', {'output_index': 0, 'item': {**message, 'content': []}}),
                        ('response.content_part.added', {'output_index': 0, 'content_index': 0,
                            'part': {'type': 'output_text', 'text': '', 'annotations': []}}),
                        ('response.output_text.delta', {'output_index': 0, 'content_index': 0, 'delta': '完成'}),
                        ('response.output_item.done', {'output_index': 0, 'item': message}),
                        ('response.completed', {'response': {'id': 'resp', 'status': 'completed', 'output': [message],
                            'usage': {'input_tokens': 7, 'output_tokens': 3, 'total_tokens': 10}}}),
                    ]
                for kind, value in values:
                    self.wfile.write(('event: ' + kind + '\ndata: ' + json.dumps({'type': kind, **value}) + '\n\n').encode())

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'

    async def asyncTearDown(self):
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.thread.join()

    async def invoke(self, catalog, *, provider='openrouter', protocol='', model=MODEL, base_url=None):
        """保存并重开隔离配置，经过实际 Python 分发与随包 Node 桥接。"""
        with tempfile.TemporaryDirectory() as root:
            value = provider_fixture(base_url or self.url, protocol)
            value['type'] = provider
            connection = value['connections'][0]
            connection.update(keys=connection['keys'][:1], models=connection['models'][:1], max_retries=0)
            connection['models'][0].update(model=model, context_window=1048576)
            if catalog is not None:
                connection['model_catalog'] = catalog
            store = ProviderConnections(root)
            store.save([value], store.revision)
            original = store.path.read_bytes()
            adapter = OpenRouterAdapter('unused', network_timeout=3)
            adapter.provider_connections = ProviderConnections(root)
            traces = []
            async def traced(value):
                if value['phase'] == 'started':
                    traces.append(json.loads(value['body']))
            tool = ToolSchema(name='lookup', description='fixture', parameters={'type': 'object', 'properties': {'value': {'type': 'integer'}}})
            try:
                events = [event async for event in adapter.stream([UserMessage('原始问题')],
                    config=LlmCallConfig(model=IDENTITY), tools=[tool], on_request=traced)]
            finally:
                self.assertEqual(store.path.read_bytes(), original)
                self.assertEqual(adapter.provider_connections.leases, {})
            self.assertEqual(traces, [self.requests[-1][1]])
            self.assertEqual(self.requests[-1][1]['model'], model)
            self.assertEqual(''.join(event.text for event in events if event.kind == 'text'), '完成')
            self.assertEqual(next(event.usage.output_tokens for event in events if event.kind == 'usage'), 3)
            return events

    async def test_uncatalogued_chat_model_uses_openrouter_endpoint_protocol(self):
        catalogs = [None, [], [{'id': MODEL}],
                    [{'id': 'other', 'api': 'anthropic-messages'}],
                    [{'id': 'other', 'api': 'anthropic-messages'}, {'id': 'another', 'api': 'openai-completions'}]]
        for catalog in catalogs:
            with self.subTest(catalog=catalog):
                before = len(self.requests)
                events = await self.invoke(catalog)
                self.assertEqual(len(self.requests), before + 1)
                path, body, auth = self.requests[-1]
                self.assertEqual(path, '/v1/chat/completions')
                self.assertEqual(auth, 'Bearer fixture-first')
                self.assertEqual(body['messages'][-1], {'role': 'user', 'content': '原始问题'})
                self.assertEqual(body['tools'][0]['function']['name'], 'lookup')
                self.assertFalse({'max_tokens', 'max_completion_tokens', 'max_output_tokens'} & body.keys())
                self.assertEqual(next(event.tool_use.input for event in events if event.kind == 'tool_use'), {'value': 42})
                self.assertEqual(events[-1].stop_reason, 'tool_use')

    async def test_explicit_connection_and_model_protocols_keep_priority(self):
        for protocol, api, path in [('', 'anthropic-messages', '/v1/messages'),
                ('openai-completions', 'anthropic-messages', '/v1/chat/completions'),
                ('openai-responses', 'anthropic-messages', '/v1/responses')]:
            with self.subTest(protocol=protocol, api=api):
                base_url = self.url.removesuffix('/v1') if not protocol and api == 'anthropic-messages' else self.url
                await self.invoke([{'id': MODEL, 'api': api}], protocol=protocol, base_url=base_url)
                self.assertEqual(self.requests[-1][0].split('?', 1)[0], path)
        template = next(row for row in directory() if row['id'] == 'openrouter')
        known = next(row['id'] for row in template['models'] if row.get('api') == 'anthropic-messages')
        await self.invoke(None, model=known, base_url=self.url.removesuffix('/v1'))
        self.assertEqual(self.requests[-1][0].split('?', 1)[0], '/v1/messages')

    async def test_unrelated_mixed_provider_does_not_guess_a_protocol(self):
        before = len(self.requests)
        with self.assertRaises(LlmError) as caught:
            await self.invoke([{'id': 'first', 'api': 'openai-completions'}, {'id': 'second', 'api': 'openai-responses'}], provider='openai')
        self.assertEqual(caught.exception.code, 'llm/provider')
        self.assertFalse(caught.exception.transient)
        self.assertEqual(len(self.requests), before)

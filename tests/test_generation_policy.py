"""生成策略只保留显式轮次预算与网络无活动超时。"""
import asyncio
import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.llm.adapter import OpenRouterAdapter
from harness.llm.transport import Transport
from harness.tools.registry import ToolDefinition
from harness.web.protocol import SCHEMAS


class RepeatedReads(Transport):
    def __init__(self):
        self.count = 0

    async def post_stream(self, url, *, headers, body, timeout):
        self.count += 1
        if self.count <= 5:
            delta = {'tool_calls': [{'index': i, 'id': f'call-{self.count}-{i}',
                'function': {'name': 'lookup', 'arguments': '{}'}} for i in range(2)]}
            finish = 'tool_calls'
        else:
            delta, finish = {'content': '完成'}, 'stop'
        for item in ({'choices': [{'delta': delta, 'finish_reason': None}]},
                     {'choices': [{'delta': {}, 'finish_reason': finish}]}):
            yield ('data: ' + json.dumps(item) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


class GenerationPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_defaults_and_public_config_exclude_removed_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            config = HarnessConfig(home=Path(folder))
            self.assertIsNone(config.max_steps)
            self.assertIsNone(config.token_budget)
            self.assertEqual(config.network_timeout, 180)
            for name in ('max_output_chars', 'max_thinking_chars', 'response_progress_timeout'):
                self.assertFalse(hasattr(config, name))
                self.assertNotIn(name, config.to_dict())
                self.assertNotIn(name, SCHEMAS['SettingsPatch']['properties'])
                self.assertFalse(hasattr(OpenRouterAdapter('fixture'), name))

    def test_oversized_subagent_timeout_is_rejected_without_breaking_config_load(self):
        with tempfile.TemporaryDirectory() as folder:
            config = HarnessConfig(home=Path(folder), subagent_job_timeout=10 ** 400)
            self.assertIsNone(config.subagent_job_timeout)
            valid = HarnessConfig(home=Path(folder) / 'valid', subagent_job_timeout=0.5)
            self.assertEqual(valid.subagent_job_timeout, 0.5)

    async def test_heartbeat_stream_has_no_total_or_progress_deadline(self):
        adapter = OpenRouterAdapter('fixture', network_timeout=0.1)
        async def raw():
            for _ in range(5):
                await asyncio.sleep(0.03)
                yield b': heartbeat\n\n'
            yield b'data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}\n\n'
            yield b'data: [DONE]\n\n'
        events = [event async for event in adapter._bounded_consume(raw())]
        self.assertEqual(''.join(event.text for event in events), 'done')

    async def test_repeated_parallel_read_results_do_not_stop_the_turn(self):
        with tempfile.TemporaryDirectory() as folder:
            flags = {f.name: False for f in fields(HarnessConfig) if f.name.startswith('enable_')}
            transport = RepeatedReads()
            app = Harness(HarnessConfig(api_key='fixture', model='fixture', home=Path(folder)/'state',
                cwd=folder, max_steps=None, max_retries=0, **flags), transport=transport)
            calls = []
            def lookup(args, context):
                calls.append(context.session_id)
                return '相同结果'
            app.tools.register(ToolDefinition({'name': 'lookup', 'description': '读取固定证据',
                'parameters': {'type': 'object', 'properties': {}}}, lookup, effect='read', concurrency_safe=True))
            try:
                session = app.create_session()
                result = await app._agent_for(session).prompt(session, '继续读取直到完成')
                self.assertEqual(result.reason, 'completed', result.error)
                self.assertEqual(transport.count, 6)
                self.assertEqual(len(calls), 10)
                self.assertEqual(len([e for e in session.events if e.type == 'tool/result']), 10)
                self.assertIsNone(session.open_turn)
            finally:
                await app.close()

    async def test_completion_feedback_has_no_fixed_retry_limit(self):
        from tests.agent_configuration_fixture import RecordingTransport
        with tempfile.TemporaryDirectory() as folder:
            flags = {f.name: False for f in fields(HarnessConfig) if f.name.startswith('enable_')}
            transport = RecordingTransport()
            app = Harness(HarnessConfig(api_key='fixture', model='fixture', home=Path(folder)/'state',
                cwd=folder, max_steps=None, max_retries=0, **flags), transport=transport)
            try:
                session = app.create_session()
                agent = app._agent_for(session)
                checks = []
                async def feedback(*args):
                    checks.append(True)
                    return '继续完成任务' if len(checks) <= 5 else None
                agent.stop_guard = feedback
                result = await agent.prompt(session, '完成任务')
                self.assertEqual(result.reason, 'completed', result.error)
                self.assertEqual(len(checks), 6)
                self.assertEqual(len(transport.requests), 6)
                self.assertIsNone(session.open_turn)
            finally:
                await app.close()

    async def test_native_bridge_receives_global_network_timeout(self):
        from unittest.mock import patch
        from harness.llm.providers import ProviderConnections
        from harness.llm.types import LlmCallConfig, UserMessage
        from tests.test_provider_connections import provider_fixture
        with tempfile.TemporaryDirectory() as folder:
            store = ProviderConnections(folder)
            store.save([provider_fixture()], store.revision)
            requests = []
            async def bridge(request, **options):
                requests.append(request)
                yield {'type': 'event', 'event': {'kind': 'text', 'text': '完成'}}
                yield {'type': 'event', 'event': {'kind': 'done', 'stop_reason': 'stop'}}
            adapter = OpenRouterAdapter('fixture', network_timeout=180)
            adapter.provider_connections = store
            with patch('harness.llm.provider_runtime.bridge', bridge):
                for seconds in (180, 42):
                    adapter.network_timeout = seconds
                    _ = [event async for event in adapter.stream([UserMessage('test')],
                        config=LlmCallConfig(model='harness/supplier/endpoint/model-a'))]
                    self.assertEqual(requests[-1]['connection']['timeout_ms'], seconds * 1000)

"""自动标题通过真实总线和存储验证归属、语言与持久化。"""
import asyncio
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.app.session_titles import SessionTitles
from harness.core.events import EventBus
from harness.core.language import current_language
from harness.llm.types import LlmCallConfig
from harness.llm.adapter import OpenRouterAdapter
from harness.llm.providers import ProviderConnections
from harness.session.events import AppendOp
from harness.session.store import SessionStore
from harness.web.views import session_listing


class SessionTitleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.bus = EventBus()
        self.store = SessionStore(Path(self.directory.name), bus=self.bus)
        self.complete = AsyncMock(return_value=SimpleNamespace(text='实现 Trello 拖拽看板', has_tool_use=False))
        self.app = SimpleNamespace(bus=self.bus, sessions=self.store, config=SimpleNamespace(language='zh-CN'),
            has_model_credentials=True, adapter=SimpleNamespace(complete=self.complete),
            _session_llm_config=lambda session: LlmCallConfig(model=session.header.metadata.get('model', 'selected')),
            list_sessions=lambda: [header.to_dict() for header in self.store.list_sessions()])
        self.titles = SessionTitles(self.app)
        self.addAsyncCleanup(self.titles.close)

    def create(self, **kwargs):
        return self.store.create(metadata={'auto_title': 'awaiting', **kwargs})

    async def append(self, session, text, source='user'):
        return await session.append('user/message', {'text': text, 'source': source}, surface_op=AppendOp())

    async def settle(self):
        await asyncio.gather(*tuple(self.titles.tasks.values()))

    async def test_first_task_generated_without_changing_messages_and_survives_reopen(self):
        session = self.create(model='custom-model')
        text = '/plan 请用单文件原生 HTML/CSS/JS 实现 Trello 看板，支持跨列拖拽、排序和卡片编辑。' * 4
        await self.append(session, text)
        self.assertLessEqual(len(session.header.title), 50)
        self.assertFalse(session.header.title.startswith('/plan'))
        await self.settle()
        self.assertEqual(session.header.title, '实现 Trello 拖拽看板')
        messages, options = self.complete.call_args
        self.assertEqual(messages[0][1].text, text[:12000])
        self.assertEqual(options['config'].model, 'custom-model')
        self.assertEqual(options['tools'], [])
        await self.append(session, '继续增加筛选')
        self.complete.assert_awaited_once()
        self.assertEqual(session_listing(self.app)[0]['title'], session.header.title)
        reopened = SessionStore(Path(self.directory.name)).get(session.id)
        self.assertEqual(reopened.header.title, session.header.title)
        self.assertEqual(reopened.events[0].data['text'], text)
        self.assertEqual(reopened.event_count, 2)

    async def test_late_result_cannot_replace_manual_title_even_same_value(self):
        for same_value in (False, True):
            session = self.create()
            entered, release = asyncio.Event(), asyncio.Event()
            async def delayed(*args, **kwargs):
                entered.set()
                await release.wait()
                return SimpleNamespace(text='Generated title', has_tool_use=False)
            self.complete.side_effect = delayed
            await self.append(session, 'Original task')
            await entered.wait()
            chosen = session.header.title if same_value else '用户手动名称'
            self.store.update_header(session.id, title=chosen)
            manual_metadata = dict(session.header.metadata)
            release.set()
            await self.settle()
            self.assertEqual(session.header.title, chosen)
            self.assertEqual(session.header.metadata['auto_title'], 'manual')
            self.assertEqual(session.header.metadata, manual_metadata)

    async def test_title_disables_reasoning_only_when_selected_model_supports_off(self):
        base = LlmCallConfig(model='selected', max_tokens=4096, reasoning_effort='high',
            extra_body={'reasoning': {'effort': 'high'}, 'reasoning_effort': 'high', 'user': 'fixture'})
        self.app._session_llm_config = lambda session: base
        cases = [
            ({'supports_thinking': True, 'supported_efforts': ['off', 'high']}, 'off'),
            ({'supported_efforts': ['off', 'low']}, 'off'),
            ({'supports_thinking': True, 'supported_efforts': ['low', 'high']}, None),
            ({'supports_thinking': False, 'supported_efforts': ['off']}, None),
            ({'reasoning_mandatory': True, 'supported_efforts': ['off', 'high']}, None),
            ({}, None),
        ]
        for capabilities, expected in cases:
            with self.subTest(capabilities=capabilities):
                resolve = AsyncMock(return_value=capabilities)
                self.app.adapter.resolve_capabilities = resolve
                session = self.create()
                await self.append(session, 'Task')
                await self.settle()
                resolve.assert_awaited_once_with('selected')
                config = self.complete.call_args.kwargs['config']
                self.assertEqual((config.reasoning_effort, config.max_tokens), (expected, 256))
                self.assertEqual(config.extra_body, {'user': 'fixture'} if expected == 'off' else base.extra_body)
                self.assertEqual(session.header.metadata['auto_title'], 'generated')
        self.assertEqual((base.reasoning_effort, base.max_tokens), ('high', 4096))
        self.assertEqual(base.extra_body, {'reasoning': {'effort': 'high'}, 'reasoning_effort': 'high', 'user': 'fixture'})

    async def test_capability_lookup_shares_title_deadline_without_sending_or_replaying(self):
        self.titles.timeout = .01
        entered, cleaned = asyncio.Event(), asyncio.Event()
        async def resolve(model):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        self.app.adapter.resolve_capabilities = resolve
        session = self.create()
        with self.assertLogs('harness.app.session_titles', level='WARNING'):
            await self.append(session, 'Task')
            await self.settle()
        self.assertTrue(entered.is_set())
        self.assertTrue(cleaned.is_set())
        self.complete.assert_not_awaited()
        self.assertEqual(session.header.metadata['auto_title_reason'], 'timeout')
        self.assertEqual(self.store.request_traces.page(session.id)['requests'], [])
        await self.append(session, 'Next task')
        self.complete.assert_not_awaited()

    async def test_title_off_reaches_provider_bridge_and_archived_request_without_changing_settings(self):
        from tests.test_provider_connections import provider_fixture
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                """隔离请求不输出凭据。"""
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                chunks = [
                    {'id': 'fixture', 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'Generated title'}, 'finish_reason': None}]},
                    {'id': 'fixture', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 12, 'completion_tokens': 3}},
                ]
                for chunk in chunks:
                    self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for thinking_format, field, expected in [('openai', 'reasoning_effort', 'none'), ('openrouter', 'reasoning', {'effort': 'none'})]:
                with tempfile.TemporaryDirectory() as root, self.subTest(thinking_format=thinking_format):
                    value = provider_fixture(f'http://127.0.0.1:{server.server_port}/v1')
                    connection = value['connections'][0]
                    connection['models'] = connection['models'][:1]
                    connection['models'][0]['model'] = 'title-reasoner'
                    connection['compat'] = {'thinkingFormat': thinking_format}
                    connection['model_catalog'] = [{'id': 'title-reasoner', 'api': 'openai-completions',
                        'reasoningEfforts': ['off', 'high'], 'contextWindow': 32000, 'maxTokens': 4096,
                        **({'thinkingLevelMap': {'off': 'none', 'high': 'high'}} if thinking_format == 'openai' else {}),
                        'compat': {'supportsReasoningEffort': True, 'supportsStore': False}}]
                    providers = ProviderConnections(root)
                    providers.save([value], providers.revision)
                    before = providers.path.read_bytes()
                    adapter = OpenRouterAdapter('unused')
                    adapter.provider_connections = providers
                    self.app.adapter = adapter
                    base = LlmCallConfig(model='harness/supplier/endpoint/model-a', reasoning_effort='high',
                        max_tokens=4096, extra_body={'reasoning': {'effort': 'high'}, 'reasoning_effort': 'high'})
                    self.app._session_llm_config = lambda session: base
                    session = self.create()
                    await self.append(session, 'Original task')
                    await self.settle()
                    self.assertEqual(session.header.metadata['auto_title'], 'generated')
                    body = requests[-1]
                    self.assertEqual(body[field], expected)
                    self.assertNotIn('reasoning' if field == 'reasoning_effort' else 'reasoning_effort', body)
                    self.assertEqual(body['model'], 'title-reasoner')
                    self.assertEqual(body.get('max_completion_tokens', body.get('max_tokens')), 256)
                    trace = self.store.request_traces.page(session.id)['requests'][0]
                    archived = json.loads(self.store.request_traces.read(session.id, trace['id'])[1])
                    self.assertEqual(archived[field], expected)
                    self.assertEqual(trace['purpose'], 'title')
                    self.assertEqual(providers.path.read_bytes(), before)
                    self.assertEqual((base.reasoning_effort, base.max_tokens), ('high', 4096))
                    self.assertFalse(providers.leases)
            self.assertEqual(len(requests), 2)
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=5)

    async def test_failures_invalid_results_and_no_credentials_keep_stable_fallback(self):
        cases = [(RuntimeError('offline'), 'request-error'), ('', 'empty-output'),
                 ('a\nb', 'multiline-output'), ('x' * 51, 'title-too-long'), ('Candidate', 'tool-use')]
        for response, reason in cases:
            session = self.create()
            self.complete.side_effect = response if isinstance(response, Exception) else None
            self.complete.return_value = SimpleNamespace(text=response, has_tool_use=reason == 'tool-use')
            await self.append(session, '/goal ' + '原始任务内容' * 30)
            fallback = session.header.title
            await self.settle()
            await self.append(session, '后续请求')
            self.assertEqual(session.header.title, fallback)
            self.assertLessEqual(len(fallback), 50)
            self.assertEqual(session.header.metadata['auto_title'], 'fallback')
            self.assertEqual(session.header.metadata['auto_title_reason'], reason)
            self.assertTrue(session.header.metadata['auto_title_request_id'].startswith('title_'))
        self.app.has_model_credentials = False
        self.complete.reset_mock()
        session = self.create()
        await self.append(session, 'Offline task')
        await self.settle()
        self.complete.assert_not_awaited()
        self.assertEqual(session.header.metadata['auto_title_reason'], 'no-credentials')
        self.assertIsNone(session.header.metadata['auto_title_request_id'])

    async def test_attachment_only_uses_original_name_without_sending_private_body(self):
        session = self.create()
        await session.append('user/message', {'text': 'Private attachment body', 'original_text': '',
            'attachments': [{'name': '设计原文.txt'}]}, surface_op=AppendOp())
        self.assertEqual(session.header.title, '设计原文.txt')
        self.assertEqual(session.header.metadata['auto_title'], 'fallback')
        self.assertEqual(session.header.metadata['auto_title_reason'], 'attachment-only')
        self.complete.assert_not_awaited()
        self.assertFalse(self.titles.tasks)

    async def test_explicit_existing_and_internal_messages_do_not_generate(self):
        for session in (self.store.create(title='Manual'), self.store.create()):
            await self.append(session, 'Task')
        session = self.create()
        for source in ('goal', 'goal-control', 'plan-mode', 'steer'):
            await self.append(session, 'Internal instruction', source)
        self.complete.assert_not_awaited()
        self.assertFalse(self.titles.tasks)
        self.assertIsNone(session.header.title)

    async def test_languages_are_resolved_per_request_in_same_instance(self):
        prompts = []
        async def complete(messages, **kwargs):
            prompts.append((current_language.get(), messages[0].text))
            return SimpleNamespace(text='Title 原文', has_tool_use=False)
        self.complete.side_effect = complete
        for language in ('en', 'zh-CN', 'en'):
            self.app.config.language = language
            await self.append(self.create(), 'Task 原文 {x}')
            await self.settle()
        self.assertEqual([language for language, _ in prompts], ['en', 'zh-CN', 'en'])
        self.assertEqual(prompts[0][1], prompts[2][1])
        self.assertNotEqual(prompts[0][1], prompts[1][1])

    async def test_titles_accept_fifty_unicode_codepoints_and_share_the_prompt_limit(self):
        for language in ('en', 'zh-CN', 'en'):
            self.app.config.language = language
            for length in (37, 49, 50):
                candidate = '标题' * ((length - 1) // 2) + '题' * ((length - 1) % 2) + '😀'
                self.assertEqual(len(candidate), length)
                self.complete.return_value = SimpleNamespace(text=candidate, has_tool_use=False)
                session = self.create()
                await self.append(session, '/goal ' + '用户原始需求' * 20)
                self.assertEqual(len(session.header.title), 50)
                await self.settle()
                self.assertEqual(session.header.title, candidate)
                self.assertEqual(session.header.metadata['auto_title'], 'generated')
                self.assertIn('50', self.complete.call_args.args[0][0].text)
                self.assertNotIn('{limit}', self.complete.call_args.args[0][0].text)
                from tests.web_fixture import FIXTURE_TITLE, is_title_request
                self.assertGreater(len(FIXTURE_TITLE), 50)
                self.assertTrue(is_title_request([{'role': 'system', 'content': self.complete.call_args.args[0][0].text}]))
                reopened = SessionStore(Path(self.directory.name)).get(session.id)
                self.assertEqual(reopened.header.title, candidate)
                self.assertEqual(next(row for row in session_listing(self.app) if row['id'] == session.id)['title'], candidate)

    async def test_timeout_and_repeated_close_wait_for_request_cleanup(self):
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def complete(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
        self.complete.side_effect = complete
        session = self.create()
        await self.append(session, 'Task')
        await entered.wait()
        first = asyncio.create_task(self.titles.close())
        await cleaning.wait()
        second = asyncio.create_task(self.titles.close())
        first.cancel()
        await asyncio.sleep(0)
        self.assertFalse(first.done())
        self.assertFalse(second.done())
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertIsNone(results[1])
        self.assertEqual(session.header.metadata['auto_title'], 'fallback')
        self.assertEqual(session.header.metadata['auto_title_reason'], 'cancelled')
        self.complete.assert_awaited_once()

    async def test_timeout_retains_fallback_without_replaying_model_request(self):
        self.titles.timeout = .001
        async def complete(*args, **kwargs):
            await asyncio.Event().wait()
        self.complete.side_effect = complete
        session = self.create()
        await self.append(session, 'Task')
        await self.settle()
        self.assertEqual(session.header.title, 'Task')
        self.assertEqual(session.header.metadata['auto_title'], 'fallback')
        self.assertEqual(session.header.metadata['auto_title_reason'], 'timeout')
        self.assertEqual(session.header.metadata['auto_title_error_type'], 'TimeoutError')
        self.complete.assert_awaited_once()

    async def test_title_requests_are_archived_at_transport_boundary_with_fallback_diagnostics(self):
        for outcome in ('generated', 'empty', 'failed'):
            with self.subTest(outcome=outcome):
                bodies = []
                class Transport:
                    async def post_stream(self, url, *, headers, body, timeout):
                        bodies.append(json.loads(body))
                        if outcome == 'failed':
                            raise RuntimeError('Original provider failure')
                        chunks = [{'choices': [{'delta': {'content': 'Generated title' if outcome == 'generated' else ''}}]},
                                  {'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 12, 'completion_tokens': 3}}]
                        for chunk in chunks:
                            yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
                        yield b'data: [DONE]\n\n'
                self.app.adapter = OpenRouterAdapter('fixture-secret', model='fixture', transport=Transport(), max_retries=0)
                self.app._session_llm_config = lambda session: LlmCallConfig(model='fixture')
                session = self.create()
                await self.append(session, 'Original task')
                await self.settle()
                self.assertEqual(len(bodies), 1)
                rows = self.store.request_traces.page(session.id)['requests']
                self.assertEqual(len(rows), 1)
                trace = rows[0]
                self.assertEqual(trace['purpose'], 'title')
                self.assertEqual(trace['request_id'], session.header.metadata['auto_title_request_id'])
                self.assertEqual(trace['status'], 'completed' if outcome == 'generated' else 'failed')
                self.assertEqual(session.header.metadata['auto_title_reason'], {'generated': None, 'empty': 'empty-output', 'failed': 'request-error'}[outcome])
                self.assertEqual(session.header.metadata['auto_title'], 'generated' if outcome == 'generated' else 'fallback')
                archived = self.store.request_traces.read(session.id, trace['id'])[1]
                self.assertNotIn('fixture-secret', archived)
                self.assertEqual(json.loads(archived)['messages'][1]['content'], 'Original task')
                self.assertIsNone(self.store.request_traces.latest(session.id))
                reopened = SessionStore(Path(self.directory.name))
                self.assertEqual(reopened.request_traces.page(session.id)['requests'][0]['purpose'], 'title')
                self.assertEqual(reopened.get(session.id).header.metadata['auto_title_reason'], session.header.metadata['auto_title_reason'])
                await self.append(session, 'Next task')
                self.assertEqual(len(bodies), 1)

    async def test_deleted_conversation_does_not_regain_title_traces_from_late_transport_events(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def complete(messages, *, config, tools, on_request):
            body = json.dumps({'model': 'fixture', 'messages': [{'role': 'user', 'content': messages[1].text}]})
            trace_id = await on_request({'phase': 'started', 'body': body, 'url': 'https://example.invalid/v1'})
            entered.set()
            await release.wait()
            await on_request({'phase': 'completed', 'trace_id': trace_id})
            await on_request({'phase': 'started', 'body': body, 'url': 'https://example.invalid/v1'})
            return SimpleNamespace(text='Generated', has_tool_use=False)
        self.app.adapter.complete = complete
        session = self.create()
        await self.append(session, 'Original task')
        await entered.wait()
        self.store.delete(session.id)
        release.set()
        await self.settle()
        self.assertEqual(self.store.request_traces.page(session.id)['requests'], [])
        self.assertFalse(self.store.has(session.id))

    async def test_title_adapter_without_observer_parameter_keeps_its_existing_signature(self):
        calls = []
        async def complete(messages, *, config, tools):
            calls.append(messages)
            return SimpleNamespace(text='Generated', has_tool_use=False)
        self.app.adapter.complete = complete
        session = self.create()
        await self.append(session, 'Task')
        await self.settle()
        self.assertEqual(session.header.title, 'Generated')
        self.assertEqual(len(calls), 1)

    async def test_configuration_failure_records_reason_without_inventing_a_transport_request(self):
        def invalid_config(session):
            raise ValueError('Original configuration detail')
        self.app._session_llm_config = invalid_config
        session = self.create()
        with self.assertLogs('harness.app.session_titles', level='WARNING'):
            await self.append(session, 'Task')
            await self.settle()
        self.complete.assert_not_awaited()
        self.assertEqual(session.header.metadata['auto_title_reason'], 'request-error')
        self.assertEqual(session.header.metadata['auto_title_error_type'], 'ValueError')
        self.assertEqual(self.store.request_traces.page(session.id)['requests'], [])
        self.assertNotIn('Original configuration detail', json.dumps(session.header.metadata))

    async def test_parallel_sessions_delete_and_shutdown_keep_original_ownership(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def complete(*args, **kwargs):
            entered.set()
            await release.wait()
            return SimpleNamespace(text='Generated', has_tool_use=False)
        self.complete.side_effect = complete
        first, second = self.create(), self.create()
        await self.append(first, 'First')
        await self.append(second, 'Second')
        await entered.wait()
        self.store.delete(first.id)
        release.set()
        await self.settle()
        self.assertFalse(self.store.has(first.id))
        self.assertEqual(second.header.title, 'Generated')
        release.clear()
        third = self.create()
        await self.append(third, 'Third')
        await self.titles.close()
        self.assertFalse(self.titles.tasks)
        await self.append(self.create(), 'After close')
        self.assertFalse(self.titles.tasks)

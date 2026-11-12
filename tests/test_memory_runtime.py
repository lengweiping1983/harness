"""验证真实宿主每回合按当前会话召回三层资料。"""
import tempfile
import json
import unittest
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.session.events import AppendOp
from harness.app.memory_archive import MemoryArchive
from tests.web_fixture import FixtureTransport


class MemoryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_request_refreshes_after_revision_archive_and_restore(self):
        class CaptureTransport(FixtureTransport):
            def __init__(self):
                self.requests = []

            async def post_stream(self, url, *, headers, body, timeout):
                self.requests.append(json.loads(body))
                async for part in super().post_stream(url, headers=headers, body=body, timeout=timeout):
                    yield part

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            flags['enable_project_memory'] = True
            transport = CaptureTransport()
            app = Harness(HarnessConfig(api_key='fixture-only', home=root / 'home', cwd=root,
                          max_steps=2, max_retries=0, **flags), transport=transport)
            try:
                one, two = app.create_session(), app.create_session()
                app.layered_memory.user.save(root, 'USER-PREFERENCE-CURRENT', pinned=True)
                private = app.layered_memory.store('session', session_id=one.id).save(
                    root, 'SESSION-ONE-PRIVATE', pinned=True)
                row = app.memory.save(root, 'PROJECT-VERSION-ONE', pinned=True)

                async def system_request(session):
                    result = await app.run_async(session, '核对当前记忆上下文')
                    self.assertEqual(result.reason, 'completed')
                    messages = transport.requests[-1]['messages']
                    return json.dumps([item for item in messages if item['role'] == 'system'], ensure_ascii=False)

                initial = await system_request(one)
                self.assertIn(row['content'], initial)
                self.assertIn(private['content'], initial)
                self.assertIn('USER-PREFERENCE-CURRENT', initial)
                row = app.memory.save(root, 'PROJECT-VERSION-TWO', pinned=True,
                                      memory_id=row['id'], expected_revision=row['revision'])
                changed = await system_request(one)
                self.assertIn(row['content'], changed)
                self.assertNotIn('PROJECT-VERSION-ONE', changed)
                archive = MemoryArchive(app.memory)
                archive.archive(root, [{'id': row['id'], 'revision': row['revision']}])
                archived = await system_request(one)
                self.assertNotIn(row['content'], archived)
                self.assertIn(private['content'], archived)
                restored = archive.restore(root, row['id'], row['revision'])['memory']
                current = await system_request(two)
                self.assertIn(restored['content'], current)
                self.assertIn('USER-PREFERENCE-CURRENT', current)
                self.assertNotIn(private['content'], current)
                self.assertNotIn(private['id'], current)
                self.assertEqual(restored['revision'], row['revision'] + 1)
                self.assertEqual(archive.list(root)['archives'][0]['memory'], row)
            finally:
                await app.close()

    async def test_agent_discovers_rewrites_recovers_invalid_arguments_and_keeps_session_scope(self):
        class RecallTransport:
            def __init__(self):
                self.requests = []

            async def post_stream(transport, url, *, headers, body, timeout):
                """固定模型输出仅验证运行链路，不作为真实模型改写质量证据。"""
                request = json.loads(body)
                step = len(transport.requests)
                transport.requests.append(request)
                calls = [
                    ('search_tools', {'query': 'memory_check'}),
                    ('memory_recall', {'query': 'ambiguous write', 'queries': ['回查'], 'scopes': ['project']}),
                    ('memory_recall', {'queries': ['ambiguous write retry', '回查', '回查'], 'scopes': ['project']}),
                    ('memory_recall', {'queries': ['任务'], 'scopes': ['session']}),
                ]
                if step < len(calls):
                    name, args = calls[step]
                    delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'recall-{step}',
                        'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
                    reason = 'tool_calls'
                else:
                    delta, reason = {'role': 'assistant', 'content': '召回链路核对完成'}, 'stop'
                for chunk in ({'choices': [{'delta': delta, 'finish_reason': None}]},
                              {'choices': [{'delta': {}, 'finish_reason': reason}]}):
                    yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
                yield b'data: [DONE]\n\n'

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags['enable_project_memory'] = True
        transport = RecallTransport()
        app = Harness(HarnessConfig(api_key='fixture-only', home=root / 'home', cwd=root,
            model='fixture-model', max_steps=6, max_retries=0, **flags), transport=transport)
        self.addAsyncCleanup(app.close)
        one, two = app.create_session(), app.create_session()
        fact = app.memory.save(root, '未知写入先回查，不重放，核查标记PROJECT-RECEIPT')
        private = app.layered_memory.store('session', session_id=one.id).save(root, '任务私有标记SESSION-ONE-ONLY')
        before = app.memory.export(root)
        for session in (one, two):
            transport.requests = []
            result = await app.run_async(session, 'Should an ambiguous write be retried?')
            self.assertEqual(result.reason, 'completed')
            self.assertEqual(len(transport.requests), 5)
            initial_names = {item['function']['name'] for item in transport.requests[0]['tools']}
            self.assertIn('memory_recall', initial_names)
            self.assertNotIn('memory_check', initial_names)
            definitions = transport.requests[1]['tools']
            self.assertIn('memory_check', {item['function']['name'] for item in definitions})
            definition = next(item['function'] for item in definitions if item['function']['name'] == 'memory_recall')
            self.assertEqual(definition['parameters']['properties']['queries']['maxItems'], 4)
            self.assertNotIn('PROJECT-RECEIPT', json.dumps(transport.requests[0], ensure_ascii=False))
            events = session.events_of_type('tool/result')
            self.assertEqual([event.data['results'][0]['is_error'] for event in events], [False, True, False, False])
            recovery = json.dumps(transport.requests[2]['messages'], ensure_ascii=False)
            self.assertIn('query', recovery)
            self.assertIn('queries', recovery)
            self.assertIn('500', recovery)
            self.assertNotIn('4000', str(events[1].data['results'][0]['content']))
            feedback = json.dumps(transport.requests[3]['messages'], ensure_ascii=False)
            self.assertIn(fact['id'], feedback)
            self.assertIn('query_matches', feedback)
            final = json.dumps(transport.requests[-1]['messages'], ensure_ascii=False)
            if session is one:
                self.assertIn(private['id'], final)
                self.assertIn('SESSION-ONE-ONLY', final)
            else:
                self.assertNotIn(private['id'], final)
                self.assertNotIn('SESSION-ONE-ONLY', final)
        self.assertEqual(app.memory.export(root), before)

    async def test_prompt_refresh_and_session_binding(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags['enable_project_memory'] = True
        app = Harness(HarnessConfig(api_key='fixture-only', home=root / 'home', cwd=root, **flags),
                      transport=FixtureTransport())
        self.addAsyncCleanup(app.close)
        one, two = app.create_session(), app.create_session()
        app.layered_memory.user.save(root, '偏好中文沟通', pinned=True)
        app.memory.save(root, '队列需要比较完整快照')
        app.layered_memory.store('session', session_id=one.id).save(root, '会话一线索', pinned=True)
        await one.append('user/message', {'text': '队列'}, surface_op=AppendOp())
        context = app._memory_context({'cwd': str(root), 'session_id': one.id})
        self.assertIn('偏好中文沟通', context)
        self.assertIn('队列需要比较完整快照', context)
        self.assertIn('会话一线索', context)
        await two.append('user/message', {'text': '无关主题'}, surface_op=AppendOp())
        other = app._memory_context({'cwd': str(root), 'session_id': two.id})
        self.assertIn('偏好中文沟通', other)
        self.assertNotIn('队列需要比较完整快照', other)
        self.assertNotIn('会话一线索', other)
        names = {tool.name for tool in app.tools.schemas()}
        self.assertTrue({'memory_save', 'user_memory_save'} <= names)
        self.assertFalse(any(name.startswith('session_memory_') for name in names))

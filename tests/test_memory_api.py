"""验证 HTTP 记忆管理的作用域、格式和乐观并发约束。"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness.app.layered_memory import LayeredMemory
from harness.app.memory import ProjectMemory
from harness.web.api.memory import build_router
from harness.web.errors import BadRequest, Conflict, NotFound, ServiceUnavailable
from harness.core.errors import SessionNotFoundError
from harness.core.language import current_language
from harness.web.router import Request


class MemoryApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.memory = ProjectMemory()
        self.latest = None
        layered = LayeredMemory(self.root / 'state', self.memory)
        harness = SimpleNamespace(memory=self.memory, layered_memory=layered,
                                  get_session=lambda identity: SimpleNamespace(header=SimpleNamespace(cwd=str(self.root)),
                                      latest=lambda kind: self.latest))
        self.router = build_router(SimpleNamespace(harness=harness, call=asyncio.run))

    def request(self, method='GET', *, scope='project', identity='one', suffix='', body=None, **query):
        path = f'/api/v1/sessions/{identity}/memory{suffix}'
        request = Request(method, path, {'scope': [scope], **{key: [value] for key, value in query.items()}},
                          {}, json.dumps(body).encode() if body is not None else b'')
        handler, params = self.router.resolve(method, path)
        return json.loads(handler(request, **params).body)

    def test_all_scopes_round_trip_and_session_isolation(self):
        for scope in ('project', 'user', 'session'):
            row = self.request('POST', scope=scope, body={'content': scope})['memory']
            records = self.request(scope=scope)['memories']
            self.assertEqual([item['content'] for item in records], [scope])
            self.assertEqual(row['source'], 'chat:one')
        self.assertEqual(self.request(scope='session', identity='two')['memories'], [])
        self.assertEqual(self.request(scope='user', identity='two')['memories'][0]['content'], 'user')

    def test_failure_paths_preserve_localized_status_and_codes(self):
        """异常分支必须返回预期错误，不能因缺文案再次抛异常。"""
        for language in ('zh-CN', 'en'):
            with current_language.set(language):
                for kind, expected, code in (
                    ('session', NotFound, 'memory/session-not-found'),
                    ('storage', ServiceUnavailable, 'memory/storage-unavailable'),
                    ('disabled', BadRequest, 'memory/disabled'),
                ):
                    with self.subTest(language=language, kind=kind):
                        def session(identity):
                            if kind == 'session':
                                raise SessionNotFoundError('fixture')
                            return SimpleNamespace(header=SimpleNamespace(cwd=str(self.root)), latest=lambda name: None)

                        def store(*args, **kwargs):
                            raise OSError('private-storage-detail')

                        harness = SimpleNamespace(memory=None if kind == 'disabled' else self.memory,
                            get_session=session, layered_memory=SimpleNamespace(store=store))
                        router = build_router(SimpleNamespace(harness=harness, call=asyncio.run))
                        path = '/api/v1/sessions/missing/memory'
                        handler, params = router.resolve('GET', path)
                        with self.assertRaises(expected) as caught:
                            handler(Request('GET', path, {}, {}, b''), **params)
                        self.assertEqual(caught.exception.code, code)
                        self.assertNotIn('python.memory.', str(caught.exception))
                        self.assertNotIn('private-storage-detail', str(caught.exception))

    def test_wrong_revision_and_wrong_scope_do_not_change_record(self):
        row = self.request('POST', scope='session', body={'content': '笔记'})['memory']
        for scope, revision in [('session', 2), ('user', 1)]:
            with self.assertRaises(Conflict):
                self.request('PATCH', scope=scope, suffix='/' + row['id'],
                             body={'content': '覆盖', 'expected_revision': revision})
        self.assertEqual(self.request(scope='session')['memories'][0]['content'], '笔记')

    def test_import_rejects_cross_scope_and_root_override(self):
        self.request('POST', body={'content': '项目内部信息'})
        payload = self.request(suffix='/export')
        with self.assertRaises(BadRequest):
            self.request('POST', scope='user', suffix='/import', body=payload)
        with self.assertRaises(BadRequest):
            self.request(root='elsewhere')
        with self.assertRaises(BadRequest):
            self.request(scope='unknown')
        self.assertEqual(self.request(scope='user')['memories'], [])

    def test_preview_reports_scopes_without_copying_content(self):
        saved = self.request('POST', body={'content': 'private-fixture-content', 'pinned': True})['memory']
        report = self.request(suffix='/preview')
        self.assertEqual(report['session_id'], 'one')
        self.assertIsNone(report['source_seq'])
        project = next(layer for layer in report['layers'] if layer['scope'] == 'project')
        self.assertEqual(project['recall']['selected'], [
            {'id': saved['id'], 'revision': 1, 'reason': 'pinned', 'source_status': 'untracked', 'derivation_status': 'none'}])
        self.assertNotIn('private-fixture-content', json.dumps(report))

    def test_preview_uses_server_session_message_instead_of_query_override(self):
        self.latest = SimpleNamespace(seq=37, data={'text': '队列'})
        row = self.request('POST', body={'content': '队列决策'})['memory']
        self.request('POST', body={'content': '字体约定'})
        report = self.request(suffix='/preview', q='字体')
        self.assertEqual(report['source_seq'], 37)
        project = next(layer for layer in report['layers'] if layer['scope'] == 'project')
        self.assertEqual(project['recall']['selected'], [
            {'id': row['id'], 'revision': 1, 'reason': 'query', 'source_status': 'untracked', 'derivation_status': 'none'}])

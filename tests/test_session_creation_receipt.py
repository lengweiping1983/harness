"""创建请求身份随首个对话头提交，完成标志不能提前发布。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness.app.harness import Harness
from harness.session.store import SessionStore
from harness.web.api.sessions import build_router
from harness.web.errors import BadRequest
from harness.web.router import Request


class SessionCreationReceiptTests(unittest.TestCase):
    def test_invalid_cwd_is_rejected_before_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            file = root / 'file.txt'
            file.write_text('原文', encoding='utf-8')
            store, endpoint, params, _ = self.fixture(root)
            for cwd in (str(root / 'missing'), str(file), '\x00'):
                with self.subTest(cwd=cwd):
                    request = Request('POST', '/api/v1/sessions', {}, {},
                                      json.dumps({'cwd': cwd}).encode('utf-8'))
                    with patch.object(store, 'create') as create, self.assertRaises(BadRequest) as caught:
                        endpoint(request, **params)
                    self.assertEqual(caught.exception.code, 'session/invalid-cwd')
                    self.assertEqual(caught.exception.status, 400)
                    create.assert_not_called()
                    self.assertEqual(store.list_sessions(), [])

    def test_existing_cwd_and_default_creation_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / 'project'
            project.mkdir()
            _, endpoint, params, _ = self.fixture(root)
            for payload in ({}, {'cwd': None}, {'cwd': ''}, {'cwd': str(project)}):
                with self.subTest(payload=payload):
                    request = Request('POST', '/api/v1/sessions', {}, {},
                                      json.dumps(payload).encode('utf-8'))
                    record = json.loads(endpoint(request, **params).body)['session']
                    self.assertEqual(Path(record['cwd']), project.resolve() if payload.get('cwd') else root)
                    self.assertIsNone(record.get('title'))
                    self.assertEqual(record['metadata']['auto_title'], 'awaiting')

    def fixture(self, root):
        store = SessionStore(root)
        host = SimpleNamespace(sessions=store, config=SimpleNamespace(cwd=root), get_session=store.get)
        host.create_session = lambda **kwargs: Harness.create_session(host, **kwargs)
        endpoint, params = build_router(SimpleNamespace(harness=host)).resolve('POST', '/api/v1/sessions')
        request = Request('POST', '/api/v1/sessions', {}, {'X-Request-ID':'creation-original'},
                          json.dumps({'title':'Original 原文 {name}'}).encode('utf-8'))
        return store, endpoint, params, request

    def test_receipt_is_present_in_initial_commit_and_complete_only_in_final_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            store, endpoint, params, request = self.fixture(Path(folder))
            commits = []
            original = store._write_header
            def write(session, **kwargs):
                header = kwargs.get('header', session.header)
                commits.append(dict(header.metadata))
                return original(session, **kwargs)
            with patch.object(store, '_write_header', side_effect=write):
                response = endpoint(request, **params)
            record = json.loads(response.body)['session']
            self.assertEqual(commits, [
                {'creation_request_id':'creation-original', 'creation_completed':False},
                {'creation_request_id':'creation-original', 'creation_completed':True},
            ])
            self.assertEqual(record['metadata'], commits[-1])
            reopened = SessionStore(Path(folder)).get(record['id'])
            self.assertEqual(reopened.header.metadata, commits[-1])
            self.assertEqual(reopened.header.title, 'Original 原文 {name}')

    def test_final_commit_failure_keeps_initial_identity_and_incomplete_state(self):
        with tempfile.TemporaryDirectory() as folder:
            store, endpoint, params, request = self.fixture(Path(folder))
            failure = OSError('Original final commit failure')
            with patch.object(store, 'update_header', side_effect=failure), self.assertRaises(OSError) as caught:
                endpoint(request, **params)
            self.assertIs(caught.exception, failure)
            records = SessionStore(Path(folder)).list_sessions()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].metadata, {'creation_request_id':'creation-original', 'creation_completed':False})

    def test_initial_commit_failure_does_not_publish_a_session(self):
        with tempfile.TemporaryDirectory() as folder:
            store, endpoint, params, request = self.fixture(Path(folder))
            failure = OSError('Original initial commit failure')
            with patch.object(store, '_write_header', side_effect=failure), self.assertRaises(OSError) as caught:
                endpoint(request, **params)
            self.assertIs(caught.exception, failure)
            self.assertEqual(store.list_sessions(), [])

    def test_initial_metadata_is_copied_and_optional_for_sdk_creation(self):
        store = SessionStore()
        metadata = {'creation_request_id':'original'}
        session = store.create(metadata=metadata)
        metadata['creation_request_id'] = 'changed'
        self.assertEqual(session.header.metadata, {'creation_request_id':'original'})
        self.assertEqual(store.create().header.metadata, {})

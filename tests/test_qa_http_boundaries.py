"""热部署 QA 的 HTTP、写入准入与统计边界，只使用隔离数据。"""
import http.client
import json
import socket
import tempfile
import threading
import unittest
from email.message import Message
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.web.api.attachments import build_router as attachment_router
from harness.web.api.sessions import build_router as session_router
from harness.web.api.voice import build_router as voice_router
from harness.web.attachment_store import AttachmentStore, AttachmentStorageError
from harness.web.errors import ApiError, BadRequest
from harness.web.input_schemas import TITLE_MAX_LENGTH
from harness.web.responses import json_response
from harness.web.router import Request, Router
from harness.web.server import Handler, MAX_REQUEST_BODY_BYTES
from harness.web.static_assets import StaticAssets


class QaHttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / 'index.html').write_text('<html>fixture homepage</html>', encoding='utf-8')
        router = Router()
        router.add('GET', '/api/v1/status', lambda request: json_response({'ok': True}))
        self.write = Mock(return_value=json_response({'written': True}))
        router.add('POST', '/api/v1/write-only', self.write)
        context = SimpleNamespace(shared_access=None, router=lambda: router, assets=StaticAssets(root))

        class IsolatedHandler(Handler):
            harness = SimpleNamespace(config=SimpleNamespace(language='en'))
            _web_ctx = context

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), IsolatedHandler)
        self.worker = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.worker.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)
        self.assertFalse(self.worker.is_alive())

    def request(self, method, path, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        self.addCleanup(connection.close)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        return response, response.read()

    def test_head_matches_get_headers_without_body_or_write_execution(self):
        for path, status in (('/api/v1/status', 200), ('/api/missing', 404), ('/api', 404),
                             ('/api/v1/write-only', 405)):
            with self.subTest(path=path):
                response, body = self.request('HEAD', path)
                self.assertEqual(response.status, status)
                self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
                self.assertEqual(body, b'')
                self.assertIn('application/json', response.getheader('Content-Type'))
                self.assertGreater(int(response.getheader('Content-Length')), 0)
                self.assertTrue(response.getheader('X-Request-ID'))
        response, body = self.request('GET', '/api/v1/status')
        head, _ = self.request('HEAD', '/api/v1/status')
        self.assertEqual(head.getheader('Content-Length'), str(len(body)))
        self.write.assert_not_called()

    def test_origin_rejection_closes_the_http_connection(self):
        for method in ('GET', 'OPTIONS'):
            with self.subTest(method=method):
                response, body = self.request(method, '/api/v1/status', {'Origin': 'https://evil.example'})
                self.assertEqual(response.status, 403)
                self.assertEqual(response.getheader('Connection'), 'close')
                self.assertEqual(json.loads(body)['code'], 'request/origin')

    def test_method_not_allowed_includes_route_specific_allow_header(self):
        response, body = self.request('POST', '/api/v1/status')
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader('Allow'), 'GET, HEAD, OPTIONS')
        self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(json.loads(body)['code'], 'method-not-allowed')

        response, body = self.request('GET', '/api/v1/write-only')
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader('Allow'), 'OPTIONS, POST')
        self.assertEqual(json.loads(body)['code'], 'method-not-allowed')

    def test_unknown_ws_paths_cannot_serve_spa_or_upgrade(self):
        for headers, status in (({}, 404), ({'Origin': 'https://evil.example'}, 403)):
            response, body = self.request('GET', '/ws/nope?since=0', {
                'Upgrade': 'websocket', 'Connection': 'Upgrade', **headers})
            self.assertEqual(response.status, status)
            self.assertIn('application/json', response.getheader('Content-Type'))
            self.assertEqual(json.loads(body)['code'], 'route/not-found' if status == 404 else 'request/origin')
            self.assertNotIn(b'fixture homepage', body)

    def test_oversized_headers_receive_complete_413_without_upload(self):
        with socket.create_connection(self.server.server_address, timeout=3) as stream:
            stream.sendall((f'POST /api/v1/write-only HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                            f'Content-Length: {MAX_REQUEST_BODY_BYTES + 1}\r\n\r\n').encode())
            response = http.client.HTTPResponse(stream)
            response.begin()
            self.assertEqual(response.status, 413)
            self.assertEqual(response.getheader('Connection'), 'close')
            self.assertEqual(json.loads(response.read())['code'], 'request/body-too-large')
        self.write.assert_not_called()

    def test_oversized_upload_still_reads_413_after_a_write_failure(self):
        payload = b'x' * (MAX_REQUEST_BODY_BYTES + 512)
        with socket.create_connection(self.server.server_address, timeout=3) as stream:
            stream.sendall((f'POST /api/v1/write-only HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                            f'Content-Length: {len(payload)}\r\n\r\n').encode())
            try:
                for offset in range(0, len(payload), 65536):
                    stream.sendall(payload[offset:offset + 65536])
            except (BrokenPipeError, ConnectionResetError):
                # 写方向被关闭不能当作读方向为空，仍须实际读取响应。
                pass
            response = http.client.HTTPResponse(stream)
            response.begin()
            self.assertEqual(response.status, 413)
            self.assertEqual(json.loads(response.read())['code'], 'request/body-too-large')
        self.write.assert_not_called()

    def test_expect_continue_rejects_bad_lengths_without_authorizing_upload(self):
        for length, status in ((str(MAX_REQUEST_BODY_BYTES + 512), 413), ('-1', 400)):
            with self.subTest(length=length), socket.create_connection(self.server.server_address, timeout=3) as stream:
                stream.sendall((f'POST /api/v1/write-only HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                    f'Expect: 100-continue\r\nContent-Length: {length}\r\n\r\n').encode())
                chunks = []
                while chunk := stream.recv(4096):
                    chunks.append(chunk)
                raw = b''.join(chunks)
                self.assertTrue(raw.startswith(f'HTTP/1.1 {status} '.encode()), raw)
                self.assertNotIn(b'100 Continue', raw)
        self.write.assert_not_called()

    def test_expect_continue_sends_valid_body_once_and_resets_on_the_next_request(self):
        payload = b'{"original":"text"}'
        with socket.create_connection(self.server.server_address, timeout=3) as stream:
            stream.sendall((f'POST /api/v1/write-only HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                f'Expect: 100-continue\r\nContent-Length: {len(payload)}\r\n\r\n').encode())
            interim = b''
            while not interim.endswith(b'\r\n\r\n'):
                chunk = stream.recv(4096)
                self.assertTrue(chunk)
                interim += chunk
            self.assertEqual(interim, b'HTTP/1.1 100 Continue\r\n\r\n')
            stream.sendall(payload)
            response = http.client.HTTPResponse(stream)
            response.begin()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read()), {'written': True})
            self.write.assert_called_once()
            self.assertEqual(self.write.call_args.args[0].body, payload)
            stream.sendall(b'GET /api/v1/status HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n')
            chunks = []
            while chunk := stream.recv(4096):
                chunks.append(chunk)
            raw = b''.join(chunks)
            self.assertTrue(raw.startswith(b'HTTP/1.1 200 '), raw)
            self.assertNotIn(b'100 Continue', raw)


class QaAdmissionBoundaryTests(unittest.TestCase):
    def test_fetch_metadata_checks_every_header_and_origin_before_dispatch(self):
        handler = object.__new__(Handler)
        handler._web_ctx = SimpleNamespace(shared_access=None)
        handler.server = SimpleNamespace(server_address=('127.0.0.1', 8765))
        for origin in ('', 'http://127.0.0.1:8765'):
            for values, allowed in (([], True), (['none'], True), (['same-origin'], True),
                    (['cross-site'], False), (['SAME-SITE'], False), (['cross-site, same-origin'], False),
                    (['none', ' cross-SITE '], False), (['cross-site', 'none'], False)):
                with self.subTest(origin=origin, values=values):
                    handler.headers = Message()
                    handler.headers['Host'] = '127.0.0.1:8765'
                    if origin:
                        handler.headers['Origin'] = origin
                    for value in values:
                        handler.headers['Sec-Fetch-Site'] = value
                    self.assertEqual(handler._origin_allowed(), allowed)
                    response = handler._apply_cors(json_response({}))
                    self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), origin if allowed and origin else None)
                    self.assertIn('origin', {part.strip().lower() for part in response.headers.get('Vary', '').split(',')})

        handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:8765'
        response = json_response({})
        response.headers['Vary'] = 'Accept-Encoding'
        self.assertEqual(handler._apply_cors(response).headers['Vary'], 'Accept-Encoding, Origin')

    def test_origin_rejects_non_origin_components_and_duplicate_hosts(self):
        handler = object.__new__(Handler)
        handler._web_ctx = SimpleNamespace(shared_access=None)
        handler.server = SimpleNamespace(server_address=('127.0.0.1', 8765))
        invalid_origins = (
            'http://127.0.0.1:8765/evil',
            'http://127.0.0.1:8765/?x=1',
            'http://127.0.0.1:8765#fragment',
            'http://user@127.0.0.1:8765',
        )
        for origin in invalid_origins:
            with self.subTest(origin=origin):
                handler.headers = Message()
                handler.headers['Host'] = '127.0.0.1:8765'
                handler.headers['Origin'] = origin
                self.assertFalse(handler._origin_allowed())

        handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:8765'
        handler.headers['Host'] = 'evil.example'
        self.assertFalse(handler._origin_allowed())

        handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:8765'
        handler.headers['Origin'] = 'http://127.0.0.1:8765'
        handler.headers['Origin'] = 'https://evil.example'
        self.assertFalse(handler._origin_allowed())
        shared = SimpleNamespace(browser_origin='https://share.example', origin_allowed=Mock(return_value=True))
        handler._web_ctx = SimpleNamespace(shared_access=shared)
        self.assertFalse(handler._origin_allowed())
        shared.origin_allowed.assert_not_called()

    def test_session_rejections_happen_before_creation_or_update(self):
        host = Mock()
        host.config = SimpleNamespace()
        router = session_router(SimpleNamespace(harness=host))
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for method, path, bodies in (
                    ('POST', '/api/v1/sessions', [{'extra': True}, {'title': '😀' * (TITLE_MAX_LENGTH + 1)}]),
                    ('PATCH', '/api/v1/sessions/fixture', [{'title': {}}, {'model': []}, {'reasoning_effort': {}},
                        {'extra': True}, {'title': '😀' * (TITLE_MAX_LENGTH + 1)}])):
                    action, params = router.resolve(method, path)
                    for body in bodies:
                        with self.subTest(language=language, body=list(body)):
                            with self.assertRaises(ApiError) as error:
                                action(Request(method, path, {}, {}, json.dumps(body).encode()), **params)
                            self.assertEqual(error.exception.code, 'session/invalid-field')
                            if 'maxLength' in (error.exception.details or {}):
                                self.assertEqual(error.exception.message, tr('ui.session.titleTooLong', limit=TITLE_MAX_LENGTH))
        host.create_session.assert_not_called()
        host.get_session.assert_not_called()
        host.sessions.update_header.assert_not_called()

    def test_valid_title_boundary_preserves_original_unicode_and_creates_once(self):
        host = Mock()
        host.create_session.return_value = SimpleNamespace(id='fixture', header={'id': 'fixture'})
        title = '😀' * TITLE_MAX_LENGTH
        action, _ = session_router(SimpleNamespace(harness=host)).resolve('POST', '/api/v1/sessions')
        with patch('harness.web.api.sessions.session_header_view', return_value={'id': 'fixture'}):
            action(Request('POST', '/', {}, {}, json.dumps({'title': title}).encode()))
        self.assertEqual(host.create_session.call_args.kwargs['title'], title)
        host.create_session.assert_called_once()

    def test_attachment_structure_and_length_rejections_never_initialize_store(self):
        ctx = SimpleNamespace()
        action, _ = attachment_router(ctx).resolve('POST', '/api/v1/attachments')
        valid = {'name': 'original.txt', 'data': 'YQ=='}
        bodies = [{'files': [valid], 'extra': 1}, {'files': [{**valid, 'extra': 1}]},
                  {'files': [{**valid, 'name': 'x' * 5000}]}, {'files': [valid] * 21},
                  {'files': [{**valid, 'mediaType': {}}]}]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for body in bodies:
                    with self.assertRaises(ApiError) as error:
                        action(Request('POST', '/', {}, {}, json.dumps(body).encode()))
                    self.assertEqual(error.exception.code, 'attachment/bad-payload')
                    self.assertLess(len(error.exception.message), 200)
                    self.assertNotIn('_attachment_store', ctx.__dict__)

    def test_bad_json_is_bilingual_and_diagnostics_are_structured(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for body in (b'{not-json', b'\xff'):
                    with self.assertRaises(ApiError) as error:
                        Request('POST', '/', {}, {}, body).json()
                    self.assertEqual(error.exception.message, tr('ui.http.invalidJsonRequest'))
                    self.assertEqual(error.exception.code, 'bad-json')
                    self.assertIsInstance(error.exception, BadRequest)
                    self.assertNotIn('Expecting', error.exception.message)
                    self.assertTrue(set(error.exception.details) & {'lineno', 'start'})

    def test_voice_preparation_rejects_bad_json_before_engine(self):
        ctx = SimpleNamespace(harness=SimpleNamespace(hex_service=Mock()), call=Mock())
        action, _ = voice_router(ctx).resolve('POST', '/api/v1/voice/preparations')
        for body in (b'{not-json', b'{"extra":true}', b'[]'):
            with self.assertRaises(ApiError):
                action(Request('POST', '/', {}, {'Host': 'localhost'}, body, client_ip='127.0.0.1'))
        ctx.call.assert_not_called()
        ctx.harness.hex_service.prepare_transcription.assert_not_called()

    def test_live_events_invalid_query_precedes_missing_session(self):
        host = Mock()
        host.get_session.side_effect = AssertionError('不应读取不存在的对话')
        hub = Mock()
        action, params = session_router(SimpleNamespace(harness=host, hub=hub)).resolve('GET', '/api/v1/sessions/missing/live-events')
        with self.assertRaises(ApiError) as error:
            action(Request('GET', '/', {'since': ['-1']}, {}, b''), **params)
        self.assertEqual(error.exception.code, 'request/invalid-query')
        host.get_session.assert_not_called()
        hub.replay.assert_not_called()

    def test_attachment_statistics_never_report_partial_reads_as_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'store'
            store = AttachmentStore(root)
            self.assertEqual(store.stats()['count'], 0)
            bucket = root / 'ab'
            bucket.mkdir(parents=True)
            content = bucket / 'content'
            content.write_bytes(b'original')
            self.assertEqual(store.stats()['totalBytes'], 8)
            original_stat = Path.stat
            for target in (root, bucket, content):
                def stat(path, *args, **kwargs):
                    if path == target:
                        raise PermissionError('私有诊断')
                    return original_stat(path, *args, **kwargs)
                with patch.object(Path, 'stat', stat) if target != root else patch.object(Path, 'iterdir', side_effect=PermissionError('私有诊断')):
                    with self.assertRaises(AttachmentStorageError):
                        store.stats()
            self.assertEqual(content.read_bytes(), b'original')

    def test_upload_statistics_failure_retains_written_references_and_returns_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AttachmentStore(directory)
            ctx = SimpleNamespace(_attachment_store=store)
            action, _ = attachment_router(ctx).resolve('POST', '/api/v1/attachments')
            request = Request('POST', '/', {}, {}, b'{"files":[{"name":"original.txt","data":"YQ=="}]}')
            with patch.object(store, 'stats', side_effect=AttachmentStorageError('私有诊断')):
                with self.assertRaises(ApiError) as error:
                    action(request)
            self.assertEqual(error.exception.status, 503)
            self.assertEqual(error.exception.code, 'attachment/stats-unavailable')
            refs = error.exception.details['attachments']
            self.assertEqual(len(refs), 1)
            self.assertEqual(store.read(refs[0]['attachmentId']), b'a')
            self.assertNotIn('私有诊断', error.exception.message)

    def test_route_error_echo_is_bounded_without_changing_route_identity(self):
        with self.assertRaises(ApiError) as error:
            Router().resolve('GET', '/api/v1/' + 'a' * 8000)
        self.assertEqual(error.exception.code, 'route/not-found')
        self.assertLess(len(error.exception.message), 300)

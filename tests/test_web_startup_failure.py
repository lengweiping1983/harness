"""Web 启动失败只回收本次拥有资源，静态读取失败不缓存成功空正文。"""
import asyncio
import http.client
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.core.events import EventBus
from harness.core.language import current_language
from harness.i18n import tr
from harness.web.router import Router
from harness.web.server import serve, stop
from harness.web.static_assets import StaticAssets
from harness.web.errors import ServiceUnavailable


class WebStartupFailureTests(unittest.TestCase):
    def test_owned_loop_and_subscriptions_are_reclaimed_after_failed_boot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'index.html').write_text('Original frontend')
            for stage in ('assets', 'routes', 'bind'):
                with self.subTest(stage=stage):
                    bus = EventBus()
                    original = lambda *args: None
                    bus.on('turn/start', original)
                    baseline = {name: tuple(rows) for name, rows in bus._listeners.items()}
                    harness = SimpleNamespace(config=SimpleNamespace(language='en'), bus=bus)
                    loops = []
                    factory = asyncio.new_event_loop
                    def new_loop():
                        loop = factory()
                        loops.append(loop)
                        return loop
                    try:
                        with socket.socket() as listener:
                            listener.bind(('127.0.0.1', 0))
                            listener.listen()
                            with patch('asyncio.new_event_loop', side_effect=new_loop), patch('harness.web.api.build_router', side_effect=RuntimeError('fixture route failure') if stage == 'routes' else None, return_value=Router()), patch.object(StaticAssets, 'locate', side_effect=RuntimeError('fixture asset failure') if stage == 'assets' else None, return_value=StaticAssets(root)):
                                with self.assertRaises((OSError, RuntimeError)):
                                    serve(harness, port=listener.getsockname()[1], background=True, dist=root)
                    finally:
                        closed = [loop.is_closed() and not loop.is_running() for loop in loops]
                        # 即使回归失败，也回收测试自己的循环，保留清理前的断言证据。
                        for loop in loops:
                            if loop.is_running():
                                loop.call_soon_threadsafe(loop.stop)
                        for thread in threading.enumerate():
                            if getattr(thread, '_target', None) in [loop.run_forever for loop in loops]:
                                thread.join(2)
                        for loop in loops:
                            if not loop.is_closed() and not loop.is_running():
                                loop.close()
                    self.assertTrue(closed and all(closed))
                    current = {name: tuple(rows) for name, rows in bus._listeners.items() if rows}
                    self.assertEqual(current, baseline)
                    self.assertFalse(any(thread.name == 'harness-loop' and thread.is_alive() and getattr(thread, '_target', None) in [loop.run_forever for loop in loops] for thread in threading.enumerate()))

    def test_failure_does_not_close_host_loop_and_next_boot_can_serve(self):
        loop = asyncio.new_event_loop()
        started = threading.Event()
        loop.call_soon(started.set)
        worker = threading.Thread(target=loop.run_forever)
        worker.start()
        self.assertTrue(started.wait(2))
        runtime = SimpleNamespace(loop=loop, claim_loop=Mock())
        harness = SimpleNamespace(config=SimpleNamespace(language='en'), runtime=runtime, bus=EventBus())
        try:
            with tempfile.TemporaryDirectory() as folder, patch('harness.web.api.build_router', return_value=Router()):
                root = Path(folder)
                (root / 'index.html').write_text('Original frontend')
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1', 0))
                    listener.listen()
                    with self.assertRaises(OSError):
                        serve(harness, port=listener.getsockname()[1], background=True, dist=root)
                self.assertTrue(loop.is_running())
                self.assertEqual(asyncio.run_coroutine_threadsafe(asyncio.sleep(0, result='host alive'), loop).result(2), 'host alive')
                server = serve(harness, port=0, background=True, dist=root)
                try:
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
                    try:
                        with patch('harness.web.static_assets.os.fdopen', side_effect=PermissionError('private filename')):
                            connection.request('GET', '/')
                            response = connection.getresponse()
                            self.assertEqual(response.status, 503)
                            self.assertEqual(json.loads(response.read())['code'], 'static/unreadable')
                        for path in ('/api', '/api/unknown', '/api/v1/nonexistent'):
                            connection.request('GET', path)
                            response = connection.getresponse()
                            self.assertEqual(response.status, 404)
                            self.assertTrue(response.getheader('Content-Type').startswith('application/json'))
                            self.assertEqual(json.loads(response.read())['code'], 'route/not-found')
                        connection.request('GET', '/conversation/example')
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.read(), b'Original frontend')
                        connection.request('GET', '/')
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.read(), b'Original frontend')
                    finally:
                        connection.close()
                finally:
                    stop(server)
                self.assertTrue(loop.is_running())
                runtime.claim_loop.assert_not_called()
        finally:
            loop.call_soon_threadsafe(loop.stop)
            worker.join(2)
            loop.close()

    def test_static_read_errors_are_localized_and_do_not_poison_cache(self):
        for language in ('en', 'zh-CN'):
            with self.subTest(language=language), current_language.set(language), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source = root / 'index.html'
                source.write_bytes(b'Original frontend')
                assets = StaticAssets(root)
                with patch('harness.web.static_assets.os.fdopen', side_effect=PermissionError('private filename')):
                    with self.assertRaises(ServiceUnavailable) as failure:
                        assets.serve('/')
                self.assertEqual(failure.exception.status, 503)
                self.assertEqual(failure.exception.code, 'static/unreadable')
                self.assertEqual(failure.exception.message, tr('web.static.unreadable'))
                self.assertEqual(len(assets._encoded), 0)
                response = assets.serve('/')
                self.assertEqual((response.status, response.body), (200, b'Original frontend'))
                self.assertEqual(assets.serve('/', if_none_match=response.headers['ETag']).status, 304)
                self.assertEqual(source.read_bytes(), b'Original frontend')

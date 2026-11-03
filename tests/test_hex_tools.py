"""hex 语音转文字（STT）工具的离线回归。

不启动真实 hex 服务：用本地假 HTTP 端点 + 临时 discovery 文件
覆盖 discovery 解析、服务复用、模型准备、转录调用、错误分支与双语文案，
保证集成逻辑不依赖本机是否安装 Rust/CMake 或真实模型。
"""
from __future__ import annotations

import http.server
import io
import json
import os
import subprocess
import tempfile
import threading
import unittest
import asyncio
from pathlib import Path
from unittest import mock

from harness.core.language import current_language
from harness.tools.builtin.hex_tools import (
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    DiscoveryDocument,
    HexServiceManager,
    HexToolError,
    build_hex_tools,
    hex_binary_path,
    hex_discovery_path,
)


class FakeHexHandler(http.server.BaseHTTPRequestHandler):
    """按类变量配置行为的假 hex 本地 API。

    - ``prepare_body``：/models/<id>/prepare 返回的 SSE 文本（成功场景为 ok 事件）；
    - ``prepare_status``：/models/<id>/prepare 的 HTTP 状态码；
    - ``transcript``：/transcriptions 返回的 transcript 文本。
    """

    prepare_body = ': keep-alive\n\ndata: {"type":"loading"}\n\ndata: {"type":"ok"}\n\n'
    prepare_status = 200
    transcript = "hello from the fixture"

    def _reply(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 —— HTTP 动词按标准库命名
        if self.headers.get("Authorization") != "Bearer test-token":
            self._reply(401, b"", "text/plain")
            return
        self._reply(200, b'{"version":"fixture","apiVersion":"2"}', "application/json")

    def do_POST(self):  # noqa: N802 —— HTTP 动词按标准库命名
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length) if length else b""
        if self.path.startswith("/models/"):
            self._reply(
                self.prepare_status,
                self.prepare_body.encode("utf-8"),
                "text/event-stream",
            )
            return
        if self.path.startswith("/transcriptions"):
            payload = json.dumps({"transcript": self.transcript}).encode("utf-8")
            self._reply(200, payload, "application/json")
            return
        self._reply(404, b"not found", "text/plain")

    def log_message(self, *args):  # 静音假服务的访问日志
        pass


def _fake_endpoint(handler_type: type[FakeHexHandler]) -> tuple[int, threading.Thread, http.server.HTTPServer]:
    """启动假 HTTP 端点，返回 (port, thread, server)。"""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_type)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server.server_address[1], thread, server


class FakeHexServer:
    """测试用工具类：临时目录 + 假 HTTP 端点 + 指向它的 discovery 文件。"""

    def __init__(self, handler_type: type[FakeHexHandler] = FakeHexHandler):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.port, self._thread, self._server = _fake_endpoint(handler_type)
        self.discovery = self.root / "local-api.json"
        self.discovery.write_text(json.dumps({
            "port": self.port,
            "token": "test-token",
            "apiVersion": "2",
            "pid": os.getpid(),
        }), encoding="utf-8")

    def close(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()
        self._tmp.cleanup()


class DiscoveryDocumentTests(unittest.TestCase):
    def test_from_file_reads_valid_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local-api.json"
            path.write_text(json.dumps({"port": 4321, "token": "t", "apiVersion": "2", "pid": 99}), encoding="utf-8")
            document = DiscoveryDocument.from_file(path)
            self.assertIsNotNone(document)
            assert document is not None
            self.assertEqual((document.port, document.token, document.pid), (4321, "t", 99))

    def test_from_file_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(DiscoveryDocument.from_file(Path(tmp) / "missing.json"))

    def test_from_file_broken_json_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local-api.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(DiscoveryDocument.from_file(path))

    def test_from_file_missing_fields_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local-api.json"
            path.write_text(json.dumps({"port": 1}), encoding="utf-8")
            self.assertIsNone(DiscoveryDocument.from_file(path))


class HexPathTests(unittest.TestCase):
    def test_binary_path_has_platform_layout(self):
        binary = hex_binary_path()
        self.assertEqual(binary.name, "hex" if os.name != "nt" else "hex.exe")
        self.assertTrue('resources' in binary.parts or 'Hex.app' in binary.parts)

    def test_macos_signed_app_fallback_keeps_bundle_path(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch('harness.tools.builtin.hex_tools.sys.platform', 'darwin'), \
             mock.patch('harness.tools.builtin.hex_tools.Path.home', return_value=Path(tmp)), \
             mock.patch('harness.tools.builtin.hex_tools.__file__', str(Path(tmp) / 'tools/builtin/hex_tools.py')):
            binary = Path(tmp) / 'Applications/Hex.app/Contents/MacOS/hex'
            binary.parent.mkdir(parents=True)
            binary.touch()
            self.assertEqual(hex_binary_path(), binary)

    def test_discovery_path_respects_override(self):
        with mock.patch.dict(os.environ, {"HEX_APPLICATION_SUPPORT_DIR": "/tmp/fake-hex"}, clear=False):
            self.assertEqual(hex_discovery_path(), Path("/tmp/fake-hex") / "local-api.json")

    def test_discovery_path_defaults_to_hex_support_dir(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            path = hex_discovery_path()
            self.assertEqual(path.name, "local-api.json")
            self.assertIn("hex", path.parts)


class HexApiClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_recording_preparation_and_concurrent_audio_share_one_confirmed_model(self):
        """录音预热与并发转写串行收敛，第二段音频不再做预热推理。"""
        class Counting(FakeHexHandler):
            paths = []

            def do_POST(self):
                self.paths.append(self.path)
                super().do_POST()

        fake = FakeHexServer(Counting)
        manager = HexServiceManager(discovery=fake.discovery)
        try:
            await asyncio.gather(asyncio.to_thread(manager.prepare_transcription),
                                 asyncio.to_thread(manager.prepare_transcription))
            timings = {}
            results = await asyncio.gather(
                asyncio.to_thread(manager.transcribe_prepared, b'first', timings=timings),
                asyncio.to_thread(manager.transcribe_prepared, b'second'))
            self.assertEqual([result['transcript'] for result in results], [Counting.transcript] * 2)
            self.assertEqual(sum(path.startswith('/models/') for path in Counting.paths), 1)
            self.assertEqual(Counting.paths[0], f'/models/{DEFAULT_MODEL}/prepare?language=auto')
            self.assertTrue(all('language=auto' in path for path in Counting.paths if path.startswith('/transcriptions?')))
            self.assertEqual(sum(path.startswith('/transcriptions?') for path in Counting.paths), 2)
            self.assertEqual(set(timings), {'queue', 'prepare', 'inference'})
            self.assertTrue(all(value >= 0 for value in timings.values()))
            await asyncio.to_thread(manager.prepare_transcription, language='zh')
            self.assertTrue(Counting.paths[-1].endswith('/prepare?language=zh'))
            self.assertEqual(sum(path.startswith('/models/') for path in Counting.paths), 2)
        finally:
            manager.stop()
            fake.close()

    async def test_preparation_cache_is_bound_to_authenticated_endpoint_and_not_failed_requests(self):
        """端点替换或准备失败不能沿用旧成功，不自动重放失败请求。"""
        first, second = FakeHexServer(), FakeHexServer()
        manager = HexServiceManager(discovery=first.discovery)
        try:
            with mock.patch.object(manager, 'prepare_model', wraps=manager.prepare_model) as prepare:
                manager.prepare_transcription()
                manager.prepare_transcription()
                self.assertEqual(prepare.call_count, 1)
                manager.discovery_path = second.discovery
                manager.prepare_transcription()
                self.assertEqual(prepare.call_count, 2)
            failure = HexToolError('fixture inference failure')
            with mock.patch.object(manager, 'transcribe', side_effect=failure) as send:
                with self.assertRaises(HexToolError) as caught:
                    manager.transcribe_prepared(b'audio')
                self.assertIs(caught.exception, failure)
                send.assert_called_once()
            with mock.patch.object(manager, 'prepare_model', side_effect=failure) as prepare:
                with self.assertRaises(HexToolError):
                    manager.prepare_transcription()
                prepare.assert_called_once()
                self.assertIsNone(manager._prepared)
            manager.prepare_transcription()
            manager.stop()
            with self.assertRaises(HexToolError):
                manager.prepare_transcription()
        finally:
            manager.stop()
            first.close()
            second.close()

    async def test_invalid_audio_never_prepares_or_launches_engine(self):
        """无效音频在下载或进程启动前拒绝。"""
        manager = HexServiceManager()
        with mock.patch.object(manager, 'ensure_running') as start:
            for audio in (b'', b'0' * (64 * 1024 * 1024 + 1)):
                with self.assertRaises(HexToolError):
                    manager.transcribe_prepared(audio)
            start.assert_not_called()

    async def test_reuses_live_discovery_without_spawning(self):
        fake = FakeHexServer()
        try:
            manager = HexServiceManager(discovery=fake.discovery)
            base_url, token = manager.ensure_running()
            self.assertEqual(base_url, f"http://127.0.0.1:{fake.port}")
            self.assertEqual(token, "test-token")
            self.assertIsNone(manager._process)  # 复用已有服务，不启动子进程
        finally:
            fake.close()

    async def test_prepare_and_transcribe_roundtrip(self):
        fake = FakeHexServer()
        try:
            manager = HexServiceManager(discovery=fake.discovery)
            manager.prepare_model(DEFAULT_MODEL)
            result = manager.transcribe(b"\x00" * 128, model=DEFAULT_MODEL, language=DEFAULT_LANGUAGE)
            self.assertEqual(result["transcript"], FakeHexHandler.transcript)
        finally:
            fake.close()

    async def test_prepare_error_event_is_localized(self):
        class ErrorHandler(FakeHexHandler):
            prepare_body = 'data: {"type":"error","error":{"message":"boom"}}\n\n'

        fake = FakeHexServer(ErrorHandler)
        try:
            manager = HexServiceManager(discovery=fake.discovery)
            with current_language.set("zh-CN"):
                with self.assertRaises(HexToolError) as caught:
                    manager.prepare_model(DEFAULT_MODEL)
                self.assertIn("准备转录模型失败", str(caught.exception))
            with current_language.set("en"):
                with self.assertRaises(HexToolError) as caught:
                    manager.prepare_model(DEFAULT_MODEL)
                self.assertIn("Failed to prepare", str(caught.exception))
        finally:
            fake.close()

    async def test_http_error_status_is_localized(self):
        class ErrorHandler(FakeHexHandler):
            prepare_status = 500
            prepare_body = "internal error"

        fake = FakeHexServer(ErrorHandler)
        try:
            manager = HexServiceManager(discovery=fake.discovery)
            with current_language.set("zh-CN"):
                with self.assertRaises(HexToolError) as caught:
                    manager.prepare_model(DEFAULT_MODEL)
                self.assertIn("HTTP 500", str(caught.exception))
        finally:
            fake.close()

    async def test_missing_binary_reports_build_hint(self):
        fake = FakeHexServer()
        try:
            # discovery 指向假端点但二进制路径不存在：ensure_running 应优先读 discovery，
            # 因此这里故意让 discovery 也失效（指向不存在 pid 的文件）。
            broken = fake.root / "dead.json"
            broken.write_text(json.dumps({"port": 1, "token": "x", "apiVersion": "2", "pid": 2 ** 31 - 1}), encoding="utf-8")
            manager = HexServiceManager(binary=fake.root / "no-such-hex", discovery=broken)
            with current_language.set("zh-CN"):
                with self.assertRaises(HexToolError) as caught:
                    manager.ensure_running()
                self.assertIn("make hex-build", str(caught.exception))
        finally:
            fake.close()


class TranscribeAudioToolTests(unittest.IsolatedAsyncioTestCase):
    async def _tool(self, fake: FakeHexServer):
        tools = {tool.schema.name: tool for tool in build_hex_tools(HexServiceManager(discovery=fake.discovery))}
        return tools["transcribe_audio"]

    async def test_schema_carries_resource_identity(self):
        from harness.i18n import resources
        fake = FakeHexServer()
        try:
            tool = await self._tool(fake)
            self.assertEqual(tool.schema.name, "transcribe_audio")
            self.assertTrue(tool.schema.description_key.startswith("python.hex_tools."))
            for language in ("en", "zh-CN"):
                values = resources(language)
                for key in [tool.schema.description_key, *tool.schema.parameter_keys.values()]:
                    if isinstance(key, tuple):
                        key = key[0]
                    self.assertIn(key, values, f"{language} 缺少资源键 {key}")
        finally:
            fake.close()

    async def test_execute_transcribes_wav_and_returns_data(self):
        fake = FakeHexServer()
        try:
            audio = fake.root / "sample.wav"
            audio.write_bytes(b"\x00" * 1024)
            tool = await self._tool(fake)
            result = await tool.execute({"audio_path": str(audio)}, None)
            self.assertEqual(result.data["transcript"], FakeHexHandler.transcript)
            self.assertEqual(result.data["model"], DEFAULT_MODEL)
            self.assertIn("hello from the fixture", result.content[0].text)
        finally:
            fake.close()

    async def test_missing_audio_path_is_rejected(self):
        fake = FakeHexServer()
        try:
            tool = await self._tool(fake)
            with current_language.set("zh-CN"):
                with self.assertRaises(HexToolError) as caught:
                    await tool.execute({}, None)
                self.assertIn("audio_path", str(caught.exception))
        finally:
            fake.close()

    async def test_missing_audio_file_is_localized(self):
        fake = FakeHexServer()
        try:
            tool = await self._tool(fake)
            missing = fake.root / "absent.wav"
            with current_language.set("zh-CN"):
                with self.assertRaises(HexToolError) as caught:
                    await tool.execute({"audio_path": str(missing)}, None)
                self.assertIn("音频文件不存在", str(caught.exception))
        finally:
            fake.close()

    async def test_missing_binary_tool_error_includes_build_hint(self):
        fake = FakeHexServer()
        try:
            broken = fake.root / "dead.json"
            broken.write_text(json.dumps({"port": 1, "token": "x", "apiVersion": "2", "pid": 2 ** 31 - 1}), encoding="utf-8")
            audio = fake.root / "a.wav"
            audio.write_bytes(b"\x00" * 128)
            tools = {tool.schema.name: tool for tool in build_hex_tools(
                HexServiceManager(binary=fake.root / "no-such-hex", discovery=broken))}
            with current_language.set("zh-CN"):
                with self.assertRaises(HexToolError) as caught:
                    await tools["transcribe_audio"].execute({"audio_path": str(audio)}, None)
                self.assertIn("make hex-build", str(caught.exception))
        finally:
            fake.close()

class HexBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_rejects_coercion_duplicates_and_wrong_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'local-api.json'
            valid = {'port': 4321, 'pid': 99, 'token': 't', 'apiVersion': '2'}
            for field, values in {'port': [True, '4321', 0, 65536], 'pid': [True, -1, '99'],
                                  'token': ['', 3, 'a\nb'], 'apiVersion': ['1', 2]}.items():
                for value in values:
                    path.write_text(json.dumps({**valid, field: value}), encoding='utf-8')
                    self.assertIsNone(DiscoveryDocument.from_file(path))
            path.write_text('{"port":4321,"port":4322,"pid":99,"token":"t","apiVersion":"2"}', encoding='utf-8')
            self.assertIsNone(DiscoveryDocument.from_file(path))

    async def test_live_pid_with_unavailable_endpoint_is_not_reused(self):
        fake = FakeHexServer()
        try:
            raw = json.loads(fake.discovery.read_text(encoding='utf-8'))
            raw['token'] = 'wrong'
            fake.discovery.write_text(json.dumps(raw), encoding='utf-8')
            manager = HexServiceManager(discovery=fake.discovery, binary=fake.root / 'missing')
            with self.assertRaises(HexToolError):
                manager.ensure_running()
        finally:
            fake.close()

    async def test_prepare_rejects_malformed_events_and_missing_terminal_event(self):
        manager = HexServiceManager()
        for payload in (b'data: []\n\n', b'data: broken\n\n', b'data: {"type":"loading"}\n\n',
                        b'data: {"type":"ok","type":"error"}\n\n', b'\xff'):
            with self.subTest(payload=payload), mock.patch.object(manager, '_request', return_value=payload):
                with self.assertRaises(HexToolError):
                    manager.prepare_model(DEFAULT_MODEL)

    async def test_transcription_and_selection_do_not_coerce_invalid_values(self):
        manager = HexServiceManager()
        for payload in (b'{}', b'{"transcript":null}', b'{"transcript":3}', b'[]',
                        b'{"transcript":"one","transcript":"two"}'):
            with mock.patch.object(manager, '_request', return_value=payload):
                with self.assertRaises(HexToolError):
                    manager.transcribe(b'audio')
        for model, language in ((False, 'auto'), ('../bad', 'auto'), (DEFAULT_MODEL, 'en&model=other')):
            with mock.patch.object(manager, '_request') as send:
                with self.assertRaises(HexToolError):
                    manager.transcribe(b'audio', model=model, language=language)
                send.assert_not_called()

    async def test_cancel_waits_for_owned_thread_and_does_not_replay(self):
        import asyncio
        from harness.tools.builtin.hex_tools import run_hex_owned
        started, release = threading.Event(), threading.Event()
        calls = []
        def work():
            calls.append(True)
            started.set()
            release.wait(5)
            return 'done'
        task = asyncio.create_task(run_hex_owned(work))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(calls, [True])

    async def test_close_rejects_new_work_and_keeps_failed_cleanup_handle(self):
        manager = HexServiceManager()
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('hex', 5)] * 3 + [0]
        manager._process = process
        with self.assertRaises(subprocess.TimeoutExpired):
            manager.stop()
        self.assertIs(manager._process, process)
        with self.assertRaises(HexToolError):
            manager.ensure_running()
        manager.stop()
        self.assertIsNone(manager._process)


class HexEmbeddedTests(unittest.TestCase):
    def test_private_handshake_releases_no_discovery_and_closes_lease(self):
        fake = FakeHexServer()
        try:
            discovery = fake.root / 'not-published.json'
            binary = fake.root / 'hex'
            binary.touch()
            process = mock.Mock()
            process.pid = os.getpid()
            process.poll.return_value = None
            process.stdin = io.BytesIO()
            process.stdout = io.BytesIO(json.dumps({'type': 'ready', 'url': f'http://127.0.0.1:{fake.port}',
                'token': 'test-token', 'apiVersion': '2', 'pid': os.getpid()}).encode() + b'\n')
            manager = HexServiceManager(binary=binary, discovery=discovery)
            with mock.patch('harness.tools.builtin.hex_tools.subprocess.Popen', return_value=process) as spawn:
                self.assertEqual(manager.ensure_running(), (f'http://127.0.0.1:{fake.port}', 'test-token'))
                self.assertEqual(manager.ensure_running(), (f'http://127.0.0.1:{fake.port}', 'test-token'))
                spawn.assert_called_once()
                self.assertEqual(spawn.call_args.args[0], [str(binary), 'service', '--embedded'])
            self.assertFalse(discovery.exists())
            manager.stop()
            self.assertTrue(process.stdin.closed)
            self.assertTrue(process.stdout.closed)
            self.assertIsNone(manager._reader)
        finally:
            fake.close()

    def test_invalid_handshake_is_rejected_and_owned_process_is_reaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'hex'
            binary.touch()
            for data in (b'not JSON\n', b'{}\n', b'a'*8193,
                         b'{"type":"ready","url":"https://example.com","pid":1}\n'):
                process = mock.Mock(pid=os.getpid())
                process.stdin, process.stdout = io.BytesIO(), io.BytesIO(data)
                manager = HexServiceManager(binary=binary, discovery=Path(tmp)/'missing.json')
                with mock.patch('harness.tools.builtin.hex_tools.subprocess.Popen', return_value=process):
                    with self.assertRaises(HexToolError):
                        manager.ensure_running()
                process.wait.assert_called_once()
                self.assertTrue(process.stdin.closed)
                self.assertTrue(process.stdout.closed)
                self.assertIsNone(manager._process)


    def test_handshake_timeout_releases_reader_and_preserves_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'hex'
            binary.touch()
            discovery = Path(tmp) / 'local-api.json'
            discovery.write_bytes(b'{broken original')
            release = threading.Event()
            process = mock.Mock(pid=os.getpid())
            process.stdin.closed = False
            process.stdin.close.side_effect = release.set
            process.stdout.readline.side_effect = lambda limit: (release.wait(5), b'')[1]
            process.stdout.read.return_value = b''
            manager = HexServiceManager(binary=binary, discovery=discovery, start_timeout=0.01)
            with mock.patch('harness.tools.builtin.hex_tools.subprocess.Popen', return_value=process):
                with self.assertRaises(HexToolError):
                    manager.ensure_running()
            self.assertIsNone(manager._reader)
            self.assertEqual(discovery.read_bytes(), b'{broken original')
            process.stdin.close.assert_called_once()
            process.wait.assert_called_once()


if __name__ == "__main__":
    unittest.main()

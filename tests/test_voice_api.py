"""语音接口的本机边界、WAV 准入和原生入口回归。"""
from __future__ import annotations

import asyncio
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.core.language import current_language
from harness.web.api.voice import build_router
from harness.web.errors import BadRequest, ServiceUnavailable
from harness.web.router import Request
from harness.tools.builtin.hex_tools import HexToolError
from harness import voice


def wav_bytes():
    """生成隔离的单声道 PCM16 WAV。"""
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\x00\x00' * 1600)
    return stream.getvalue()


class VoiceApiTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.transcribe_prepared.return_value = {'transcript': 'Hello 中文'}
        self.ctx = SimpleNamespace(harness=SimpleNamespace(hex_service=self.service),
                                   call=lambda coro, **kwargs: asyncio.run(coro))
        self.path = '/api/v1/voice/transcriptions'
        self.handler, _ = build_router(self.ctx).resolve('POST', self.path)

    def request(self, data=None, *, host='localhost', ip='127.0.0.1', subject=None):
        return Request('POST', self.path, {}, {'Host': host, 'Content-Type': 'audio/wav'},
                       wav_bytes() if data is None else data, client_ip=ip, subject=subject)

    def test_transcription_is_localized_without_translating_speech(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                response = self.handler(self.request())
                self.assertEqual(json.loads(response.body), {'transcript': 'Hello 中文'})
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.service.transcribe_prepared.call_count, 3)

    def test_remote_shared_and_invalid_audio_never_start_engine(self):
        requests = [self.request(host='203.0.113.3'), self.request(ip='203.0.113.3'),
                    self.request(subject='guest'), self.request(b'not wav'), self.request(b'a' * (2*1024*1024+1)),
                    self.request(wav_bytes()[:-4])]
        for request in requests:
            with self.subTest(request=request.client_ip), self.assertRaises(BadRequest):
                self.handler(request)
        self.service.transcribe_prepared.assert_not_called()

    def test_engine_failure_preserves_resource_identity(self):
        self.service.transcribe_prepared.side_effect = HexToolError('diagnostic')
        with self.assertRaises(ServiceUnavailable) as caught:
            self.handler(self.request())
        self.assertEqual(caught.exception.details, {'copy': {'key': 'ui.voice.engineFailed'}, 'reason': 'diagnostic'})
        self.service.transcribe_prepared.assert_called_once()


    def test_missing_engine_exposes_specific_bilingual_resource(self):
        self.service.transcribe_prepared.side_effect = HexToolError('missing raw',
            copy={'key': 'ui.voice.binaryMissing', 'params': {'path': '/fixture/hex'}})
        with self.assertRaises(ServiceUnavailable) as caught:
            self.handler(self.request())
        self.assertEqual(caught.exception.details['copy'],
                         {'key': 'ui.voice.binaryMissing', 'params': {'path': '/fixture/hex'}})


class VoiceCommandTests(unittest.TestCase):
    def test_explicit_native_commands_are_forwarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'hex'
            binary.touch()
            for args in (['app'], ['settings', 'set', 'dictation.shortcut', 'option+space']):
                with patch.object(voice, 'hex_binary_path', return_value=binary), \
                     patch.object(voice.sys, 'argv', ['harness-voice', *args]), \
                     patch.object(voice.os, 'execv') as execute:
                    self.assertEqual(voice.main(), 0)
                    execute.assert_called_once_with(str(binary), [str(binary), *args])

    def test_native_command_has_same_arguments_on_windows_and_macos(self):
        with tempfile.TemporaryDirectory() as tmp:
            for platform, name in (('win32', 'hex.exe'), ('darwin', 'hex')):
                binary = Path(tmp) / name
                binary.touch()
                with patch.object(voice.sys, 'platform', platform), \
                     patch.object(voice, 'hex_binary_path', return_value=binary), \
                     patch.object(voice.sys, 'argv', ['harness-voice']), \
                     patch.object(voice, 'open_dictation') as execute:
                    self.assertEqual(voice.main(), 0)
                    execute.assert_called_once_with()

    def test_missing_binary_does_not_launch(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(voice, 'hex_binary_path', return_value=Path(tmp)/'missing'), \
             patch.object(voice.os, 'execv') as execute, patch.object(voice.sys, 'stderr', io.StringIO()):
            self.assertEqual(voice.main(), 2)
            execute.assert_not_called()


class VoiceDictationTests(unittest.TestCase):
    def test_capabilities_do_not_launch_or_claim_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'hex'
            for system, machine, platform, suffix in (
                    ('darwin', 'arm64', 'macos', 'macos-arm64-dmg'),
                    ('win32', 'AMD64', 'windows', 'windows-x64-setup'),
                    ('win32', 'ARM64', 'windows', 'windows-arm64-setup')):
                with self.subTest(system=system, machine=machine), \
                     patch.object(voice.sys, 'platform', system), \
                     patch.object(voice.platform, 'machine', return_value=machine), \
                     patch.object(voice, 'hex_binary_path', return_value=binary), \
                     patch.object(voice, 'open_dictation') as launch:
                    missing = voice.dictation_capabilities()
                    self.assertFalse(missing['available'])
                    self.assertEqual(missing['reason'], {'key': 'ui.voice.dictation.missing'})
                    binary.touch()
                    available = voice.dictation_capabilities()
                    self.assertTrue(available['available'])
                    self.assertIsNone(available['reason'])
                    self.assertEqual(available['platform'], platform)
                    self.assertTrue(available['download_url'].endswith(suffix))
                    self.assertNotIn('ready', available)
                    launch.assert_not_called()
                    binary.unlink()

    def test_macos_uses_bundle_identity_and_does_not_request_duplicate_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / 'Hex.app'
            binary = bundle / 'Contents' / 'MacOS' / 'hex'
            binary.parent.mkdir(parents=True)
            binary.touch()
            with patch.object(voice.sys, 'platform', 'darwin'), \
                 patch.object(voice, 'hex_binary_path', return_value=binary), \
                 patch.dict(voice.os.environ, {'HEX_APPLICATION_SUPPORT_DIR': str(Path(tmp) / 'isolated data')}, clear=True), \
                 patch.object(voice.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as launch:
                voice.open_dictation()
                voice.open_dictation()
            self.assertEqual(launch.call_count, 2)
            self.assertEqual(launch.call_args.args[0], ['/usr/bin/open', '--env',
                f'HEX_APPLICATION_SUPPORT_DIR={Path(tmp) / "isolated data"}', str(bundle)])
            self.assertEqual(launch.call_args.kwargs['timeout'], 10)

    def test_windows_uses_shell_and_preserves_native_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'hex.exe'
            binary.touch()
            shell = SimpleNamespace(startfile=Mock())
            with patch.object(voice.sys, 'platform', 'win32'), \
                 patch.object(voice, 'hex_binary_path', return_value=binary), patch.object(voice, 'os', shell):
                voice.open_dictation()
            shell.startfile.assert_called_once_with(str(binary), arguments='app')

    def test_source_binary_is_detached_and_reaped_without_duplicate_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'hex'
            binary.touch()
            process = Mock()
            process.poll.return_value = None
            with patch.object(voice.sys, 'platform', 'darwin'), \
                 patch.object(voice, 'hex_binary_path', return_value=binary), \
                 patch.object(voice, '_app_process', None), \
                 patch.object(voice.subprocess, 'Popen', return_value=process) as launch, \
                 patch.object(voice.threading, 'Thread') as waiter:
                voice.open_dictation()
                voice.open_dictation()
                launch.assert_called_once_with([str(binary), 'app'], stdin=voice.subprocess.DEVNULL, start_new_session=True)
                self.assertEqual(waiter.call_args.kwargs['target'], process.wait)
                waiter.return_value.start.assert_called_once_with()

    def test_open_failure_keeps_bilingual_copy_and_original_cause(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'Hex.app' / 'Contents' / 'MacOS' / 'hex'
            binary.parent.mkdir(parents=True)
            binary.touch()
            for language in ('en', 'zh-CN', 'en'):
                for failure, key in ((OSError('OS 原文'), 'launchFailed'),
                                     (voice.subprocess.TimeoutExpired('open', 10), 'unconfirmed')):
                    with current_language.set(language), patch.object(voice.sys, 'platform', 'darwin'), \
                         patch.object(voice, 'hex_binary_path', return_value=binary), \
                         patch.object(voice.subprocess, 'run', side_effect=failure), \
                         self.assertRaises(HexToolError) as caught:
                        voice.open_dictation()
                    self.assertEqual(caught.exception.copy, {'key': 'ui.voice.dictation.' + key})
                    self.assertIs(caught.exception.__cause__, failure)

    def test_routes_reject_remote_and_shared_requests_before_installation_read(self):
        ctx = SimpleNamespace(call=lambda coro, **kwargs: asyncio.run(coro))
        router = build_router(ctx)
        for method, path in (('GET', '/api/v1/voice/dictation'), ('POST', '/api/v1/voice/dictation/open')):
            handler, _ = router.resolve(method, path)
            for ip, host, subject in (('203.0.113.3', 'localhost', None),
                                      ('127.0.0.1', '203.0.113.3', None), ('127.0.0.1', 'localhost', 'guest')):
                with patch.object(voice, 'dictation_capabilities') as inspect, \
                     patch.object(voice, 'open_dictation') as launch, self.assertRaises(BadRequest):
                    handler(Request(method, path, {}, {'Host': host}, b'', client_ip=ip, subject=subject))
                inspect.assert_not_called()
                launch.assert_not_called()

    def test_routes_launch_only_on_explicit_post_and_report_acceptance(self):
        ctx = SimpleNamespace(call=lambda coro, **kwargs: asyncio.run(coro))
        router = build_router(ctx)
        with patch.object(voice, 'dictation_capabilities', return_value={'available': True}) as inspect, \
             patch.object(voice, 'open_dictation') as launch:
            for method, path in (('GET', '/api/v1/voice/dictation'), ('POST', '/api/v1/voice/dictation/open')):
                handler, _ = router.resolve(method, path)
                response = handler(Request(method, path, {}, {'Host': 'localhost'}, b'', client_ip='::1'))
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                if method == 'GET':
                    launch.assert_not_called()
                else:
                    self.assertEqual(json.loads(response.body), {'requested': True})
            self.assertEqual(inspect.call_count, 2)
            launch.assert_called_once_with()

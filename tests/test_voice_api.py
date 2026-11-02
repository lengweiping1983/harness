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
    def test_default_app_and_explicit_native_commands_are_forwarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'hex'
            binary.touch()
            for args in ([], ['settings', 'set', 'dictation.shortcut', 'option+space']):
                with patch.object(voice, 'hex_binary_path', return_value=binary), \
                     patch.object(voice.sys, 'argv', ['harness-voice', *args]), \
                     patch.object(voice.os, 'execv') as execute:
                    self.assertEqual(voice.main(), 0)
                    execute.assert_called_once_with(str(binary), [str(binary), *(args or ['app'])])

    def test_native_command_has_same_arguments_on_windows_and_macos(self):
        with tempfile.TemporaryDirectory() as tmp:
            for platform, name in (('win32', 'hex.exe'), ('darwin', 'hex')):
                binary = Path(tmp) / name
                binary.touch()
                with patch.object(voice.sys, 'platform', platform), \
                     patch.object(voice, 'hex_binary_path', return_value=binary), \
                     patch.object(voice.sys, 'argv', ['harness-voice']), \
                     patch.object(voice.os, 'execv') as execute:
                    self.assertEqual(voice.main(), 0)
                    execute.assert_called_once_with(str(binary), [str(binary), 'app'])

    def test_missing_binary_does_not_launch(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(voice, 'hex_binary_path', return_value=Path(tmp)/'missing'), \
             patch.object(voice.os, 'execv') as execute, patch.object(voice.sys, 'stderr', io.StringIO()):
            self.assertEqual(voice.main(), 2)
            execute.assert_not_called()

"""语音的 Windows AVX2 准入使用系统替身，不启动真实引擎。"""
from __future__ import annotations

import asyncio
import ctypes
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness import voice
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin import hex_tools
from harness.web.api.voice import build_router
from harness.web.errors import BadRequest, ServiceUnavailable
from harness.web.router import Request
from tests.test_voice_api import wav_bytes


class VoiceCpuTests(unittest.TestCase):
    def test_windows_queries_os_avx2_support_with_the_win32_abi(self):
        """只接受系统报告的 AVX2，不能按 CPU 名称或启动结果推断。"""
        for present in (0, 1):
            feature = Mock(return_value=present)
            load = Mock(return_value=SimpleNamespace(IsProcessorFeaturePresent=feature))
            api = SimpleNamespace(WinDLL=load, c_uint32=ctypes.c_uint32, c_int=ctypes.c_int)
            with self.subTest(present=present), patch.object(hex_tools, 'sys', SimpleNamespace(platform='win32')), \
                 patch.object(hex_tools, 'ctypes', api), patch.object(hex_tools.subprocess, 'Popen') as spawn:
                self.assertIs(hex_tools.hex_cpu_supported(), bool(present))
            load.assert_called_once_with('kernel32', use_last_error=True)
            feature.assert_called_once_with(40)
            self.assertEqual(feature.argtypes, [ctypes.c_uint32])
            self.assertIs(feature.restype, ctypes.c_int)
            spawn.assert_not_called()

    def test_other_platforms_do_not_query_windows_or_require_avx2(self):
        """其他平台保留原有入口，不要求本机具备 Windows API。"""
        for platform in ('darwin', 'linux'):
            with self.subTest(platform=platform), patch.object(hex_tools, 'sys', SimpleNamespace(platform=platform)), \
                 patch.object(hex_tools, 'ctypes', SimpleNamespace()):
                self.assertTrue(hex_tools.hex_cpu_supported())

    def test_probe_failure_preserves_cause_and_bilingual_resource(self):
        """探测异常与确认不支持分别报告，原系统异常保留。"""
        original = OSError('fixture kernel failure 原文')
        api = SimpleNamespace(WinDLL=Mock(side_effect=original))
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch.object(hex_tools, 'sys', SimpleNamespace(platform='win32')), \
                 patch.object(hex_tools, 'ctypes', api), self.assertRaises(hex_tools.HexToolError) as caught:
                hex_tools.hex_cpu_supported()
            self.assertIs(caught.exception.__cause__, original)
            self.assertEqual(caught.exception.copy, {'key': 'ui.voice.global.cpuCheckFailed'})
            with current_language.set(language):
                self.assertEqual(str(caught.exception), tr('ui.voice.global.cpuCheckFailed'))

    def test_capability_rejects_cpu_before_installation_reads_or_launches(self):
        """硬件准入与引擎缺失分开，读取接口不会执行语音副作用。"""
        with patch.object(voice, 'sys', SimpleNamespace(platform='win32')), \
             patch.object(voice, 'hex_cpu_supported', return_value=False), \
             patch.object(voice, 'hex_binary_path') as binary, patch.object(voice, 'open_dictation') as launch:
            result = voice.dictation_capabilities()
        self.assertFalse(result['available'])
        self.assertFalse(result['cpu_supported'])
        self.assertEqual(result['reason'], {'key': 'ui.voice.global.cpuUnsupported'})
        binary.assert_not_called()
        launch.assert_not_called()
        failure = hex_tools.HexToolError('fixture', copy={'key': 'ui.voice.global.cpuCheckFailed'})
        with patch.object(voice, 'hex_cpu_supported', side_effect=failure):
            result = voice.dictation_capabilities()
        self.assertFalse(result['cpu_supported'])
        self.assertEqual(result['reason'], failure.copy)

    def test_direct_service_native_open_and_cli_never_launch_without_avx2(self):
        """服务、独立打开和命令行共用拒绝边界，既有 discovery 保持原件。"""
        with tempfile.TemporaryDirectory() as temporary:
            discovery = Path(temporary) / 'local-api.json'
            discovery.write_bytes(b'existing user data')
            manager = hex_tools.HexServiceManager(binary=Path(temporary) / 'hex.exe', discovery=discovery)
            with patch.object(hex_tools, 'hex_cpu_supported', return_value=False), \
                 patch.object(manager, '_discover') as discover, \
                 patch.object(manager, '_start_service') as start, \
                 patch.object(voice, 'hex_binary_path') as binary, \
                 patch.object(voice.os, 'execv') as execute, \
                 patch.object(voice, 'sys', SimpleNamespace(platform='win32', argv=['harness-voice', 'models'], stderr=Mock())):
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        for operation in (manager.ensure_running, voice.open_dictation):
                            with self.assertRaises(hex_tools.HexToolError) as caught:
                                operation()
                            self.assertEqual(caught.exception.copy, {'key': 'ui.voice.global.cpuUnsupported'})
                            self.assertEqual(str(caught.exception), tr('ui.voice.global.cpuUnsupported'))
                self.assertEqual(voice.main(), 2)
                discover.assert_not_called()
                start.assert_not_called()
                binary.assert_not_called()
                execute.assert_not_called()
            manager.stop()
            self.assertEqual(discovery.read_bytes(), b'existing user data')

    def test_voice_routes_expose_capability_and_refuse_engine_work_without_avx2(self):
        """实际语音路由保留资源身份；准备、上传和显式打开均不启动引擎。"""
        with tempfile.TemporaryDirectory() as temporary:
            manager = hex_tools.HexServiceManager(binary=Path(temporary) / 'hex.exe',
                                                   discovery=Path(temporary) / 'local-api.json')
            ctx = SimpleNamespace(harness=SimpleNamespace(hex_service=manager),
                                  call=lambda coroutine, **kwargs: asyncio.run(coroutine))
            router = build_router(ctx)
            with patch.object(voice, 'hex_cpu_supported', return_value=False), \
                 patch.object(hex_tools, 'hex_cpu_supported', return_value=False), \
                 patch.object(manager, '_start_service') as start:
                handler, _ = router.resolve('GET', '/api/v1/voice/dictation')
                request = lambda method, path, body=b'': Request(method, path, {},
                    {'Host': 'localhost', 'Content-Type': 'audio/wav'}, body, client_ip='127.0.0.1')
                response = handler(request('GET', '/api/v1/voice/dictation'))
                self.assertFalse(json.loads(response.body)['cpu_supported'])
                for path, body, error in (
                        ('/api/v1/voice/preparations', b'', ServiceUnavailable),
                        ('/api/v1/voice/transcriptions', wav_bytes(), ServiceUnavailable),
                        ('/api/v1/voice/dictation/open', b'', BadRequest)):
                    handler, _ = router.resolve('POST', path)
                    with self.subTest(path=path), self.assertRaises(error) as caught:
                        handler(request('POST', path, body))
                    self.assertEqual(caught.exception.details['copy'], {'key': 'ui.voice.global.cpuUnsupported'})
                start.assert_not_called()
            manager.stop()

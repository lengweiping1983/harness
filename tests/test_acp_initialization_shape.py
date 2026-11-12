"""ACP 初始化拒绝布尔版本和非对象能力，不把损坏回执标成就绪。"""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.acp.client import AcpClient, AcpError, AcpRunnerConfig
from harness.core.language import current_language
from harness.i18n import tr


class AcpInitializationShapeTests(unittest.IsolatedAsyncioTestCase):
    async def start_result(self, result, expected_key=None):
        client = AcpClient(AcpRunnerConfig('fixture'))
        process = SimpleNamespace(stdin=SimpleNamespace(close=Mock()))
        with tempfile.TemporaryDirectory() as cwd, patch('harness.acp.client.asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=process), patch.object(client, '_read', new_callable=AsyncMock), patch.object(client, '_drain_stderr', new_callable=AsyncMock), patch.object(client, '_request', new_callable=AsyncMock, return_value=result), patch('harness.acp.client.stop_process', new_callable=AsyncMock) as stop:
            if expected_key:
                with self.assertRaises(AcpError) as caught:
                    await client.start(cwd)
                self.assertEqual(str(caught.exception), tr(expected_key))
                self.assertFalse(client._ready)
                stop.assert_awaited_once_with(process)
            else:
                await client.start(cwd)
                self.assertTrue(client._ready)
                self.assertEqual(client.capabilities, result.get('agentCapabilities', {}))
            await client.close()

    async def test_invalid_version_and_capabilities_close_original_process(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for version in (True, 1.0, '1', None, 2):
                    with self.subTest(language=language, version=version):
                        await self.start_result({'protocolVersion': version}, 'acp.client.f7cfb75d24d3')
                for capabilities in (None, False, [], 'original', 1):
                    with self.subTest(language=language, capabilities=capabilities):
                        await self.start_result({'protocolVersion': 1, 'agentCapabilities': capabilities}, 'acp.client.1bc0016e9bd8')

    async def test_current_version_and_optional_object_capabilities_keep_original_values(self):
        for result in ({'protocolVersion': 1}, {'protocolVersion': 1, 'agentCapabilities': {}}, {'protocolVersion': 1, 'agentCapabilities': {'originalVendor': {'text': 'Original 原文'}}}):
            await self.start_result(result)

"""验证不同 Harness 与独立文件工具的观察策略互不影响。"""
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.llm.types import ToolUseBlock
from harness.tools.builtin.fs_tools import build_fs_tools
from harness.tools.registry import ToolRegistry, ToolRunContext
from tests.web_fixture import FixtureTransport


class FilePolicyIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'file.txt'
        self.path.write_bytes(b'original')
        self.apps = 0

    def host(self, enabled):
        self.apps += 1
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags.update(enable_fs=True, enable_fs_observation_policy=enabled)
        app = Harness(HarnessConfig(api_key='fixture-only', home=self.root / str(self.apps),
            cwd=self.root, model='fixture-model', **flags), transport=FixtureTransport())
        self.addAsyncCleanup(app.close)
        return app, app.create_session()

    async def call(self, registry, session, name, **args):
        ctx = ToolRunContext(call_id='file-policy', session_id=session.id, cwd=str(self.root),
            turn=1, step=1, services={'session': session})
        return await registry.execute_call(ToolUseBlock('file-policy', name, {'path': str(self.path), **args}), ctx)

    async def test_disabled_host_does_not_disable_existing_host_protection(self):
        protected, session = self.host(True)
        self.host(False)
        result = await self.call(protected.tools, session, 'write', content='replacement')
        self.assertTrue(result.is_error)
        self.assertIn('FS_EXISTS', result.content[0].text)
        self.assertEqual(self.path.read_bytes(), b'original')

    async def test_enabled_host_does_not_enable_another_hosts_disabled_policy(self):
        unprotected, session = self.host(False)
        self.host(True)
        result = await self.call(unprotected.tools, session, 'write', content='replacement')
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(self.path.read_bytes(), b'replacement')

    async def test_second_host_does_not_erase_observed_version_protection(self):
        protected, session = self.host(True)
        read = await self.call(protected.tools, session, 'read')
        self.assertFalse(read.is_error, read.content)
        self.host(False)
        self.path.write_bytes(b'external')
        result = await self.call(protected.tools, session, 'write', content='replacement')
        self.assertTrue(result.is_error)
        self.assertIn('FS_STALE', result.content[0].text)
        self.assertEqual(self.path.read_bytes(), b'external')

    async def test_standalone_tools_keep_secure_default_after_disabled_host(self):
        _, session = self.host(False)
        registry = ToolRegistry()
        for tool in build_fs_tools(str(self.root)):
            registry.register(tool)
        result = await self.call(registry, session, 'write', content='replacement')
        self.assertTrue(result.is_error)
        self.assertIn('FS_EXISTS', result.content[0].text)
        self.assertEqual(self.path.read_bytes(), b'original')

    async def test_disabled_host_read_does_not_record_observation_after_other_host(self):
        unprotected, session = self.host(False)
        self.host(True)
        result = await self.call(unprotected.tools, session, 'read')
        self.assertFalse(result.is_error, result.content)
        self.assertIsNone(result.data.get('_fs_observation'))
        self.assertEqual(session.file_observations.records, {})

    async def test_deleting_host_session_does_not_clear_same_named_sdk_observations(self):
        app, session = self.host(True)
        registry = ToolRegistry()
        for tool in build_fs_tools(str(self.root)):
            registry.register(tool)
        ctx = ToolRunContext(call_id='sdk', session_id=session.id, cwd=str(self.root), turn=1, step=1)
        read = await registry.execute_call(ToolUseBlock('sdk', 'read', {'path': str(self.path)}), ctx)
        self.assertFalse(read.is_error, read.content)
        app.delete_session(session.id)
        result = await registry.execute_call(ToolUseBlock('sdk', 'edit', {'path': str(self.path),
            'old_string': 'original', 'new_string': 'replacement'}), ctx)
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(self.path.read_bytes(), b'replacement')

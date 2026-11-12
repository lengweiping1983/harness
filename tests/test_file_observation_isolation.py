"""通过工具流水线验证无 Session 调用的观察只属于当前工具组。"""
import tempfile
import unittest
from pathlib import Path

from harness.llm.types import ToolUseBlock
from harness.session.store import SessionStore
from harness.tools.builtin import fs_observation
from harness.tools.builtin.fs_tools import build_fs_tools
from harness.tools.registry import ToolRegistry, ToolRunContext


class FileObservationIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'file.txt'
        self.path.write_bytes(b'original')
        self.session = SessionStore(str(self.root / 'sessions')).create()
        self.addCleanup(fs_observation.forget_session, self.session.id)
        self.first = self.registry()
        self.second = self.registry()

    def registry(self):
        registry = ToolRegistry()
        for tool in build_fs_tools(str(self.root)):
            registry.register(tool)
        return registry

    async def call(self, registry, name, *, session=None, session_id=None, **args):
        ctx = ToolRunContext(call_id='isolation', session_id=session_id or self.session.id,
            cwd=str(self.root), turn=1, step=1, services={'session': session} if session else {})
        return await registry.execute_call(ToolUseBlock('isolation', name, {'path': str(self.path), **args}), ctx)

    async def success(self, registry, name, **args):
        result = await self.call(registry, name, **args)
        self.assertFalse(result.is_error, result.content)
        return result

    async def test_same_sdk_session_name_does_not_grant_other_tool_group_write_access(self):
        await self.success(self.first, 'read')
        for name, args, code in (
            ('edit', {'old_string': 'original', 'new_string': 'replacement'}, 'FS_NOT_OBSERVED'),
            ('write', {'content': 'replacement'}, 'FS_EXISTS'),
        ):
            with self.subTest(tool=name):
                result = await self.call(self.second, name, **args)
                self.assertTrue(result.is_error)
                self.assertIn(code, result.content[0].text)
                self.assertEqual(self.path.read_bytes(), b'original')

    async def test_another_tool_groups_read_does_not_refresh_stale_observation(self):
        await self.success(self.first, 'read')
        self.path.write_bytes(b'external')
        await self.success(self.second, 'read')
        result = await self.call(self.first, 'write', content='replacement')
        self.assertTrue(result.is_error)
        self.assertIn('FS_STALE', result.content[0].text)
        self.assertEqual(self.path.read_bytes(), b'external')

    async def test_another_tool_groups_failed_edit_does_not_revoke_observation(self):
        await self.success(self.first, 'read')
        await self.success(self.second, 'read')
        failed = await self.call(self.second, 'edit', old_string='missing', new_string='replacement')
        self.assertTrue(failed.is_error)
        await self.success(self.first, 'edit', old_string='original', new_string='replacement')
        self.assertEqual(self.path.read_bytes(), b'replacement')

    async def test_session_observation_is_not_exported_to_sdk_cache(self):
        await self.success(self.first, 'read', session=self.session)
        result = await self.call(self.first, 'edit', old_string='original', new_string='replacement')
        self.assertTrue(result.is_error)
        self.assertIn('FS_NOT_OBSERVED', result.content[0].text)
        self.assertEqual(self.path.read_bytes(), b'original')

    async def test_session_failed_edit_does_not_revoke_sdk_observation(self):
        await self.success(self.first, 'read')
        await self.success(self.first, 'read', session=self.session)
        result = await self.call(self.first, 'edit', session=self.session,
            old_string='missing', new_string='replacement')
        self.assertTrue(result.is_error)
        await self.success(self.first, 'edit', old_string='original', new_string='replacement')
        self.assertEqual(self.path.read_bytes(), b'replacement')

    async def test_sdk_session_ids_remain_isolated_within_one_group(self):
        await self.success(self.first, 'read')
        result = await self.call(self.first, 'write', session_id='other', content='replacement')
        self.assertTrue(result.is_error)
        self.assertIn('FS_EXISTS', result.content[0].text)
        await self.success(self.first, 'edit', old_string='original', new_string='replacement')
        await self.success(self.first, 'write', content='next')
        self.assertEqual(self.path.read_bytes(), b'next')

    async def test_restored_target_invalidates_every_group_and_session(self):
        await self.success(self.first, 'read')
        await self.success(self.second, 'read')
        await self.success(self.first, 'read', session=self.session)
        fs_observation.forget_target(self.path)
        for registry, session in ((self.first, None), (self.second, None), (self.first, self.session)):
            with self.subTest(session=bool(session), registry=id(registry)):
                result = await self.call(registry, 'edit', session=session,
                    old_string='original', new_string='replacement')
                self.assertTrue(result.is_error)
                self.assertIn('FS_NOT_OBSERVED', result.content[0].text)
        self.assertEqual(self.path.read_bytes(), b'original')

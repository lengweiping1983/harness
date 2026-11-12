"""提交归属筛选仍保留成功写入、图片交付与用户改动保护。"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.core.errors import HarnessError
from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.execution.runtime import ExecutionRuntime


class FinalizeOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        project = root / 'project'
        project.mkdir()
        self.project = ProjectConfig(project, writable=True, commits=True, verification={'check': ('fixture',)})
        self.runtime = ExecutionRuntime(ExecutionConfig(root / 'state', {'project': self.project}))
        self.addAsyncCleanup(self.runtime.close)
        session = self.runtime.store.open_session('local', 'project', 'fixture', str(project))
        self.sid = session['session_id']
        self.ctx = SimpleNamespace(session_id=self.sid)
        self.policy = Mock()
        self.runtime._policy = Mock(return_value=(session, self.project, self.policy))
        self.runtime._verification = Mock(return_value=self.project.verification)
        self.runtime._git = AsyncMock(return_value={'exit_code': 0, 'stdout': '', 'truncated': False})
        self.evidence = self.runtime.store.evidence(self.sid, 'verification', {'verification_id': 'check', 'passed': True,
            'after': 'tree', 'command_hash': hashlib.sha256(json.dumps(['fixture']).encode()).hexdigest()})

    async def finalize(self):
        with patch('harness.execution.runtime.snapshot', return_value='tree'):
            return await self.runtime._finalize({'evidence_ids': [self.evidence], 'commit_message': 'fixture'}, self.ctx)

    def baseline(self, stdout=''):
        self.runtime.store.event(self.sid, 'session/open', {'baseline_git': {'exit_code': 0, 'stdout': stdout, 'truncated': False}})

    async def test_only_successful_patch_and_image_deliveries_are_committed(self):
        self.baseline()
        for name, failed, data in (
            ('harness_read', False, {'changed_files': ['read-only.txt']}),
            ('harness_apply_patch', True, {'changed_files': ['failed.txt']}),
            ('harness_apply_patch', False, {'changed_files': ['patch.txt']}),
            ('harness_image_deliver', False, {'delivery': {'changed_files': ['image.png']}}),
        ):
            self.runtime.store.event(self.sid, 'tool/result', {'name': name, 'is_error': failed, 'data': data})
        result = await self.finalize()
        self.assertEqual(result['committed_files'], ['image.png', 'patch.txt'])
        self.assertIn((self.project.root, ['add', '--', 'image.png', 'patch.txt']), [call.args for call in self.runtime._git.await_args_list])

    async def test_existing_user_change_blocks_commit_before_git_add(self):
        self.baseline(' M patch.txt\0')
        self.runtime.store.event(self.sid, 'tool/result', {'name': 'harness_apply_patch', 'is_error': False, 'data': {'changed_files': ['patch.txt']}})
        with self.assertRaises(HarnessError) as error:
            await self.finalize()
        self.assertEqual(error.exception.code, 'execution/user-changes-present')
        self.runtime._git.assert_not_awaited()

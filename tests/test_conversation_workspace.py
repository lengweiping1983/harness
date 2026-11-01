"""用隔离 Git 仓库验证新对话的目录、分支、工作树与拒绝边界。"""
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harness.app.harness import Harness
from harness.app.worktrees import WorktreeService
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.session.store import SessionStore
from harness.web.api.sessions import build_router
from harness.web.api.workspaces import build_router as workspace_router
from harness.web.errors import BadRequest, ServiceUnavailable
from harness.web.registry import WorkspaceRegistry
from harness.web.router import Request


class ConversationWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='harness-conversation-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / 'project'
        self.repo.mkdir()
        self.git('init', '--initial-branch=main')
        (self.repo / 'file.txt').write_text('原始内容\n', encoding='utf-8')
        self.git('add', 'file.txt')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'initial')
        self.git('branch', 'feature')
        self.service = WorktreeService(self.root / 'worktrees')
        self.registry = WorkspaceRegistry()
        self.project = self.registry.register(str(self.repo))
        self.store = SessionStore(self.root / 'home')
        host = SimpleNamespace(sessions=self.store, config=SimpleNamespace(cwd=self.repo),
                               get_session=self.store.get, worktrees=self.service)
        host.create_session = lambda **kwargs: Harness.create_session(host, **kwargs)
        self.host = host
        self.context = SimpleNamespace(harness=host, workspaces=lambda: self.registry)
        self.endpoint, _ = build_router(self.context).resolve('POST', '/api/v1/sessions')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode('utf-8').strip()

    def selection(self, mode='local', ref='main'):
        options = self.service.conversation_options(str(self.repo))
        return {'workspace_id': self.project['id'], 'mode': mode, 'ref': ref,
                'expected_branch': options['branch'], 'expected_head': options['head']}

    def create(self, project, **extra):
        request = Request('POST', '/api/v1/sessions', {}, {'X-Request-ID': 'new-conversation'},
                          json.dumps({'project': project, **extra}).encode('utf-8'))
        return json.loads(self.endpoint(request).body)['session']

    def test_options_are_read_only_and_available_before_registration(self):
        self.registry.unregister(self.project['id'])
        endpoint, _ = workspace_router(self.context).resolve('GET', '/api/v1/workspace-options')
        before = self.git('status', '--porcelain')
        request = Request('GET', '/api/v1/workspace-options', {'path': [str(self.repo)]}, {}, b'')
        options = json.loads(endpoint(request).body)
        self.assertEqual(options['branch'], 'main')
        self.assertEqual(set(options['local_branches']), {'main', 'feature'})
        self.assertTrue(options['is_git'])
        self.assertEqual(self.git('status', '--porcelain'), before)
        self.assertEqual(self.service.list(), [])
        folder = self.root / 'ordinary'
        folder.mkdir()
        plain = self.service.conversation_options(str(folder))
        self.assertFalse(plain['is_git'])
        self.assertEqual(plain['refs'], [])
        self.assertFalse((folder / '.git').exists())

    def test_local_current_branch_preserves_uncommitted_and_staged_bytes(self):
        file = self.repo / 'file.txt'
        file.write_text('已暂存\n', encoding='utf-8')
        self.git('add', 'file.txt')
        file.write_text('尚未暂存\n', encoding='utf-8')
        before = self.git('diff', '--cached')
        record = self.create(self.selection())
        self.assertEqual(Path(record['cwd']), self.repo)
        self.assertEqual(file.read_text(encoding='utf-8'), '尚未暂存\n')
        self.assertEqual(self.git('diff', '--cached'), before)
        self.assertEqual(record['metadata']['checkout_mode'], 'local')
        self.assertTrue(record['metadata']['creation_completed'])

    def test_clean_branch_switch_and_stale_selection(self):
        selection = self.selection(ref='feature')
        record = self.create(selection)
        self.assertEqual(self.git('branch', '--show-current'), 'feature')
        self.assertEqual(Path(record['cwd']), self.repo)
        with self.assertRaises(BadRequest) as caught:
            self.create(selection)
        self.assertEqual(caught.exception.code, 'session/workspace-changed')
        self.assertEqual(len(self.store.list_sessions()), 1)

    def test_dirty_branch_switch_is_rejected_without_stash_or_commit(self):
        (self.repo / 'file.txt').write_text('用户修改\n', encoding='utf-8')
        before = self.git('rev-parse', 'HEAD')
        with self.assertRaises(BadRequest) as caught:
            self.create(self.selection(ref='feature'))
        self.assertEqual(caught.exception.code, 'session/workspace-dirty')
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual(self.git('branch', '--show-current'), 'main')
        self.assertEqual(self.git('stash', 'list'), '')
        self.assertEqual(self.store.list_sessions(), [])

    def test_worktree_uses_selected_commit_and_preserves_original_directory(self):
        (self.repo / 'file.txt').write_text('留在原目录\n', encoding='utf-8')
        record = self.create(self.selection('worktree', 'feature'))
        directory = Path(record['cwd'])
        self.assertNotEqual(directory, self.repo)
        self.assertEqual((directory / 'file.txt').read_text(encoding='utf-8'), '原始内容\n')
        self.assertEqual((self.repo / 'file.txt').read_text(encoding='utf-8'), '留在原目录\n')
        self.assertEqual(self.git('branch', '--show-current'), 'main')
        self.assertEqual(record['metadata']['project_cwd'], str(self.repo))
        self.assertEqual(record['metadata']['workspace_id'], self.project['id'])
        self.assertEqual(record['metadata']['worktree_id'], self.service.list()[0]['id'])

    def test_subdirectory_project_keeps_relative_directory_in_worktree(self):
        subdirectory = self.repo / 'nested'
        subdirectory.mkdir()
        (subdirectory / 'app.txt').write_text('原文', encoding='utf-8')
        self.git('add', 'nested')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'nested')
        project = self.registry.register(str(subdirectory))
        selection = self.selection('worktree')
        selection['workspace_id'] = project['id']
        record = self.create(selection)
        self.assertEqual(Path(record['cwd']), Path(self.service.list()[0]['path']) / 'nested')

    def test_plain_folder_and_unborn_repository_allow_local_only(self):
        folder = self.root / 'ordinary'
        folder.mkdir()
        project = self.registry.register(str(folder))
        record = self.create({'workspace_id': project['id'], 'mode': 'local'})
        self.assertEqual(Path(record['cwd']), folder)
        for initialized in (False, True):
            if initialized:
                subprocess.run(['git', '-C', str(folder), 'init', '--initial-branch=main'], check=True, capture_output=True)
            with self.assertRaises(BadRequest):
                self.create({'workspace_id': project['id'], 'mode': 'worktree'})
        self.assertEqual(len(self.store.list_sessions()), 1)
        self.assertEqual(self.service.list(), [])

    def test_invalid_selection_is_rejected_before_any_git_write(self):
        cases = [None, True, {}, {'workspace_id': self.project['id'], 'mode': True},
                 {'workspace_id': 'unknown', 'mode': 'local'},
                 {**self.selection(), 'ref': '-f'}, {**self.selection(), 'expected_head': True},
                 {**self.selection(), 'extra': True}]
        with patch.object(self.service, 'prepare_conversation', wraps=self.service.prepare_conversation) as prepare:
            for value in cases:
                with self.subTest(value=value), self.assertRaises(BadRequest):
                    self.create(value)
            prepare.assert_not_called()
        self.assertEqual(self.store.list_sessions(), [])

    def test_disabled_git_support_still_allows_explicit_local_project(self):
        self.host.worktrees = None
        endpoint, _ = workspace_router(self.context).resolve('GET', '/api/v1/workspace-options')
        request = Request('GET', '/api/v1/workspace-options', {'path': [str(self.repo)]}, {}, b'')
        options = json.loads(endpoint(request).body)
        self.assertIsNone(options['is_git'])
        self.assertIsNone(options['dirty'])
        record = self.create({'workspace_id': self.project['id'], 'mode': 'local'})
        self.assertEqual(Path(record['cwd']), self.repo)
        with self.assertRaises(BadRequest):
            self.create({'workspace_id': self.project['id'], 'mode': 'worktree'})
        self.assertEqual(self.service.list(), [])

    def test_worktree_failure_does_not_fall_back_to_original_cwd(self):
        with patch.object(self.service, 'create', side_effect=OSError('Original Git failure')) as write:
            with self.assertRaises(ServiceUnavailable) as caught:
                self.create(self.selection('worktree'))
            self.assertEqual(caught.exception.code, 'session/workspace-uncertain')
            self.assertEqual(caught.exception.details['cause'], 'Original Git failure')
            self.assertEqual(caught.exception.details['copy']['key'], 'ui.newConversation.preparationUnconfirmed')
            write.assert_called_once()
        self.assertEqual(self.store.list_sessions(), [])

    def test_session_persistence_failure_retains_created_worktree(self):
        with patch.object(self.host, 'create_session', side_effect=OSError('disk failure')):
            with self.assertRaises(ServiceUnavailable) as caught:
                self.create(self.selection('worktree'))
        rows = self.service.list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(caught.exception.details['worktree_id'], rows[0]['id'])
        self.assertTrue(Path(rows[0]['path']).is_dir())

    def test_corrupt_git_metadata_is_not_treated_as_plain_folder(self):
        folder = self.root / 'broken'
        folder.mkdir()
        (folder / '.git').write_text('gitdir: missing', encoding='utf-8')
        before = (folder / '.git').read_bytes()
        with self.assertRaises(HarnessError):
            self.service.conversation_options(str(folder))
        self.assertEqual((folder / '.git').read_bytes(), before)

    def test_error_resources_follow_call_language_without_modifying_user_data(self):
        (self.repo / 'file.txt').write_text('用户原文', encoding='utf-8')
        messages = []
        for language in ('en', 'zh-CN', 'en'):
            token = current_language.set(language)
            try:
                with self.assertRaises(BadRequest) as caught:
                    self.create(self.selection(ref='feature'))
                messages.append(caught.exception.message)
                self.assertEqual(caught.exception.details['copy']['key'], 'ui.newConversation.dirtyBranch')
            finally:
                current_language.reset(token)
        self.assertEqual(messages[0], messages[2])
        self.assertNotEqual(messages[0], messages[1])
        self.assertEqual((self.repo / 'file.txt').read_text(encoding='utf-8'), '用户原文')

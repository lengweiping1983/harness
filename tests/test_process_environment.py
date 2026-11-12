"""ACP/MCP 共用环境及未完成作业归属回归。"""
import unittest
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness.app.file_search import ProjectFileSearch
from harness.tools.process_env import execution_env, stdio_env
from harness.tools.builtin.job_tools import JobRecord, JobRegistry


class ProcessEnvironmentTests(unittest.TestCase):
    def test_stdio_environment_scrubs_host_flags_and_keeps_explicit_override(self):
        with patch.dict('os.environ', {'NODE_OPTIONS': 'host-only', 'VSCODE_PID': '123',
                                       'OPENAI_API_KEY': 'host-secret', 'PATH': '/fixture'}, clear=True):
            original = dict(os.environ)
            expected = {'PATH': '/fixture', 'EXTRA': 'yes', 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
            self.assertEqual(stdio_env({'EXTRA': 'yes'}), expected)
            self.assertEqual(stdio_env({'EXTRA': 'yes', 'PYTHONUTF8': '0', 'PYTHONIOENCODING': 'gbk'}), expected)
            self.assertEqual(dict(os.environ), original)
            self.assertEqual(stdio_env({'NODE_OPTIONS': 'explicit'})['NODE_OPTIONS'], 'explicit')
            self.assertNotIn('OPENAI_API_KEY', stdio_env({'EXTRA': 'yes'}))

    def test_execution_environment_drops_host_credentials_and_does_not_readd_them(self):
        with patch.dict('os.environ', {
            'PATH': '/fixture',
            'OPENAI_API_KEY': 'host-secret',
            'AWS_ACCESS_KEY_ID': 'host-access',
            'AWS_SECRET_ACCESS_KEY': 'host-secret',
            'GOOGLE_APPLICATION_CREDENTIALS': '/host/credentials.json',
            'SSH_AUTH_SOCK': '/tmp/agent.sock',
            'NODE_OPTIONS': '--require=/host/inject.js',
            'PYTHONPATH': '/host/inject',
            'AWS_SHARED_CREDENTIALS_FILE': '/host/aws',
            'AWS_PROFILE': 'private',
            'CLOUDSDK_CONFIG': '/host/gcloud',
            'PIP_INDEX_URL': 'https://user:secret@example.invalid/simple',
            'GIT_ASKPASS': '/host/askpass',
            'GIT_SSH_COMMAND': 'ssh -i /host/key',
            'PUBLIC_SETTING': 'kept',
        }, clear=True):
            env = execution_env({
                'HARNESS_SESSION': 'session',
                'GITHUB_TOKEN': 'attempted-secret',
                'PUBLIC_EXTRA': 'kept-extra',
            })
        self.assertEqual(env['PATH'], '/fixture')
        self.assertEqual(env['PUBLIC_SETTING'], 'kept')
        self.assertEqual(env['PUBLIC_EXTRA'], 'kept-extra')
        self.assertEqual(env['HARNESS_SESSION'], 'session')
        for name in ('OPENAI_API_KEY', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
                     'GOOGLE_APPLICATION_CREDENTIALS', 'SSH_AUTH_SOCK', 'GITHUB_TOKEN',
                     'NODE_OPTIONS', 'PYTHONPATH', 'AWS_SHARED_CREDENTIALS_FILE', 'AWS_PROFILE',
                     'CLOUDSDK_CONFIG', 'PIP_INDEX_URL', 'GIT_ASKPASS', 'GIT_SSH_COMMAND'):
            self.assertNotIn(name, env)

    def test_file_search_git_worker_uses_scrubbed_environment(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {
            'PATH': '/fixture', 'OPENAI_API_KEY': 'host-secret',
            'NODE_OPTIONS': '--require=/host/inject.js', 'GIT_CONFIG_GLOBAL': '/host/config',
        }, clear=True):
            root = Path(directory)
            (root / '.git').mkdir()
            with patch('harness.app.file_search.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
                ProjectFileSearch()._scan(root)
            env = run.call_args.kwargs['env']
            self.assertEqual(env['PATH'], '/fixture')
            for name in ('OPENAI_API_KEY', 'NODE_OPTIONS', 'GIT_CONFIG_GLOBAL'):
                self.assertNotIn(name, env)

    def test_active_owners_excludes_only_settled_jobs(self):
        jobs = JobRegistry()
        for index, status in enumerate(('running', 'stopping', 'cleanup_failed', 'completed', 'failed', 'killed')):
            record = JobRecord(str(index), 'exec', 'fixture', status, 0, status=status)
            jobs._jobs[record.id] = record
        self.assertEqual(jobs.active_owners(), {'running', 'stopping', 'cleanup_failed'})
        self.assertTrue(all('owner' not in row for row in jobs.list_all_jobs()))

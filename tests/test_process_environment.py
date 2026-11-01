"""ACP/MCP 共用环境及未完成作业归属回归。"""
import unittest
import os
from unittest.mock import patch

from harness.tools.process_env import stdio_env
from harness.tools.builtin.job_tools import JobRecord, JobRegistry


class ProcessEnvironmentTests(unittest.TestCase):
    def test_stdio_environment_scrubs_host_flags_and_keeps_explicit_override(self):
        with patch.dict('os.environ', {'NODE_OPTIONS': 'host-only', 'VSCODE_PID': '123', 'PATH': '/fixture'}, clear=True):
            original = dict(os.environ)
            expected = {'PATH': '/fixture', 'EXTRA': 'yes', 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
            self.assertEqual(stdio_env({'EXTRA': 'yes'}), expected)
            self.assertEqual(stdio_env({'EXTRA': 'yes', 'PYTHONUTF8': '0', 'PYTHONIOENCODING': 'gbk'}), expected)
            self.assertEqual(dict(os.environ), original)
            self.assertEqual(stdio_env({'NODE_OPTIONS': 'explicit'})['NODE_OPTIONS'], 'explicit')

    def test_active_owners_excludes_only_settled_jobs(self):
        jobs = JobRegistry()
        for index, status in enumerate(('running', 'stopping', 'cleanup_failed', 'completed', 'failed', 'killed')):
            record = JobRecord(str(index), 'exec', 'fixture', status, 0, status=status)
            jobs._jobs[record.id] = record
        self.assertEqual(jobs.active_owners(), {'running', 'stopping', 'cleanup_failed'})
        self.assertTrue(all('owner' not in row for row in jobs.list_all_jobs()))

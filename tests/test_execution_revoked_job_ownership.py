"""项目登记变化不能使运行作业丢失其原始目录归属。"""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.execution.runtime import ExecutionRuntime
from harness.i18n import tr


class RevokedJobOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_owner_uses_persisted_root_after_project_removed_or_renamed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / 'project'
            other = root / 'other'
            project.mkdir()
            other.mkdir()
            runtime = ExecutionRuntime(ExecutionConfig(root / 'state', {'original': ProjectConfig(project)}))
            try:
                owner = runtime.store.open_session('local', 'original', 'Original 原文', str(project))['session_id']
                runtime.config = replace(runtime.config, projects={'renamed': ProjectConfig(project), 'other': ProjectConfig(other)})
                with patch.object(runtime.jobs, 'active_owners', return_value=[owner]):
                    for language in ('en', 'zh-CN', 'en'):
                        with current_language.set(language):
                            with self.assertRaises(HarnessError) as error:
                                runtime._assert_idle(runtime.config.projects['renamed'])
                            self.assertEqual(error.exception.code, 'execution/project-busy')
                            self.assertEqual(error.exception.message, tr('execution.runtime.8f929f44f128'))
                            runtime._assert_idle(runtime.config.projects['other'])
            finally:
                await runtime.close()

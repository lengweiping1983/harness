"""工作区别名保存失败不得覆盖原件或共用固定临时文件。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.core.errors import HarnessError
from harness.execution.config import ExecutionConfig
from harness.execution.workspaces import sync_projects


class WorkspaceAliasPersistenceTests(unittest.TestCase):
    def test_failed_replace_preserves_original_and_removes_only_owned_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            registry = root / 'workspaces.json'
            registry.write_text(json.dumps({'version':1,'workspaces':[{'path':str(project),'name':'fixture'}]}))
            home = root / 'private' / 'state'
            home.mkdir(parents=True)
            aliases = home / 'workspace-project-ids.json'
            original = b'{}'
            aliases.write_bytes(original)
            other = aliases.with_suffix('.tmp')
            other.write_bytes(b'owned by another operation')
            with patch('harness.execution.workspaces.os.replace', side_effect=OSError('fixture replace failed')):
                with self.assertRaises(HarnessError):
                    sync_projects(ExecutionConfig(home, {}, workspace_registry=registry))
            self.assertEqual(aliases.read_bytes(), original)
            self.assertEqual(other.read_bytes(), b'owned by another operation')
            self.assertEqual(set(home.iterdir()), {aliases, other})
            result = sync_projects(ExecutionConfig(home, {}, workspace_registry=registry))
            self.assertEqual(list(result.projects), ['fixture'])
            self.assertEqual(other.read_bytes(), b'owned by another operation')

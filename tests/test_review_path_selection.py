"""补丁只能包含所选字面路径；嵌套工作区不能改变仓库路径的含义。"""
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness.app.review import ReviewService


class ReviewPathSelectionTests(unittest.TestCase):
    def test_literal_paths_in_repository_root_and_nested_workspace(self):
        for nested in (False, True):
            with self.subTest(nested=nested), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = root / 'project' if nested else root
                workspace.mkdir(exist_ok=True)
                def git(*args):
                    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)
                git('init', '-q')
                for name in ('odd[1].txt', 'odd1.txt'):
                    (workspace / name).write_text('before\n')
                git('add', '--all')
                git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
                for name in ('odd[1].txt', 'odd1.txt'):
                    (workspace / name).write_text('after ' + name + '\n')
                selected = ReviewService().patch(workspace, 'odd[1].txt')
                self.assertIn('+after odd[1].txt', selected['patch'])
                self.assertNotIn('odd1.txt', selected['patch'])
                self.assertEqual(selected['added'], 1)
                self.assertEqual(selected['deleted'], 1)

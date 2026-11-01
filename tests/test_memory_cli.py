"""验证本地备份命令不启动模型、三层隔离与失败时保留文件。"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from harness.cli import main
from harness.app.memory import ProjectMemory
from harness.app.layered_memory import LayeredMemory
from harness.app.memory_archive import MemoryArchive


class MemoryCliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.project = self.root / 'project'
        self.target = self.root / 'target'
        self.project.mkdir()
        self.target.mkdir()
        self.home = self.root / 'home'
        self.env = patch.dict(os.environ, {'HARNESS_HOME': str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def call(self, *args):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error), patch('harness.cli.make_harness', side_effect=AssertionError('不应创建模型宿主')):
            result = main(list(args))
        return result, output.getvalue(), error.getvalue()

    def test_archive_action_help_resolves_current_language_before_any_host(self):
        from harness.cli import build_parser
        from harness.core.language import current_language
        from harness.i18n import tr
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch('harness.cli.make_harness') as host:
                parser = build_parser()
                actions = {action.dest: action for action in parser._actions}
                for name in ('export', 'verify', 'import'):
                    self.assertEqual(actions['memory_' + name].help, tr('cli.memory.' + name))
                    args = parser.parse_args(['--memory-' + name, 'Original 归档.jsonl'])
                    self.assertEqual(getattr(args, 'memory_' + name), 'Original 归档.jsonl')
                host.assert_not_called()
        self.assertFalse(self.home.exists())

    def test_three_scopes_export_verify_import_without_model_host(self):
        source = LayeredMemory(self.home, ProjectMemory())
        for scope in ('project', 'user', 'session'):
            store = source.store(scope, session_id='one')
            row = store.save(self.project, '隔离归档 ' + scope)
            archive = MemoryArchive(store)
            archive.archive(self.project, [{'id': row['id'], 'revision': 1}])
            file = self.root / (scope + '.jsonl')
            options = ['--memory-scope', scope]
            if scope == 'session':
                options.extend(['--memory-session', 'one'])
            result, output, error = self.call('--memory-export', str(file), '--cwd', str(self.project), *options)
            self.assertEqual(result, 0, error)
            self.assertEqual(json.loads(output)['count'], 1)
            self.assertEqual(json.loads(output)['scope'], scope)
            if scope == 'project':
                process = subprocess.run([sys.executable, '-m', 'harness.cli', '--memory-verify', str(file)],
                    cwd=Path(__file__).resolve().parent.parent, env={**os.environ, 'PYTHONUTF8': '1'},
                    capture_output=True, text=True, encoding='utf-8', timeout=20)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(json.loads(process.stdout)['operation'], 'verify')
            result, output, error = self.call('--memory-verify', str(file), *options)
            self.assertEqual(result, 0, error)
            self.assertEqual(json.loads(output)['count'], 1)
            with patch.dict(os.environ, {'HARNESS_HOME': str(self.root / 'new-home')}):
                result, output, error = self.call('--memory-import', str(file), '--cwd', str(self.target), *options)
            self.assertEqual(result, 0, error)
            self.assertEqual(json.loads(output)['imported'], 1)
            imported = LayeredMemory(self.root / 'new-home', ProjectMemory()).store(scope, session_id='one')
            self.assertEqual(MemoryArchive(imported).list(self.target)['archives'][0]['memory'], row)
            self.assertEqual(imported.list(self.target), [])

    def test_existing_file_and_invalid_input_are_preserved(self):
        file = self.root / 'existing.jsonl'
        file.write_text('原有文件', encoding='utf-8')
        for action in ('export', 'verify', 'import'):
            result, _, _ = self.call('--memory-' + action, str(file), '--cwd', str(self.project))
            self.assertEqual(result, 2)
            self.assertEqual(file.read_text(encoding='utf-8'), '原有文件')
        self.assertFalse(MemoryArchive(ProjectMemory()).path(self.project).exists())

    def test_conflicting_options_fail_before_creating_backup(self):
        file = self.root / 'should-not-exist.jsonl'
        for options in (['--memory-scope', 'session'], ['--memory-session', 'one'],
                        ['--status'], ['--ui'], ['unexpected prompt'], ['--list-external-agents']):
            with self.assertRaises(SystemExit) as error:
                self.call('--memory-export', str(file), *options)
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(file.exists())
        with self.assertRaises(SystemExit):
            self.call('--memory-scope', 'user')

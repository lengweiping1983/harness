"""用 Git 应用及真实会话验证补丁原文、计数和末行状态。"""
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness.app.comparison import ComparisonService
from harness.app.review import ReviewService
from harness.app.worktrees import WorktreeService
from harness.app.worktree_review import review
from harness.app import plans
from harness.app.turn_review import turn_changes
from harness.app.turn_net_changes import turn_net_changes
from harness.core.diff import unified_patch
from harness.session.events import AppendOp
from harness.session.store import SessionStore
from harness.tools.builtin.file_diff import file_diff


def git(root, *args, input=None):
    return subprocess.run(['git', *args], cwd=root, input=input, capture_output=True, check=True).stdout


def repository(root):
    root.mkdir(exist_ok=True)
    git(root, 'init', '-q')
    # 字节边界夹具不继承宿主的换行转换策略，保留 LF、CRLF 和无末行换行。
    git(root, 'config', 'core.autocrlf', 'false')
    git(root, 'config', 'core.eol', 'lf')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--allow-empty', '-qm', 'fixture')


class DiffBoundaryTests(unittest.TestCase):
    def test_generated_patch_recreates_exact_bytes_with_git(self):
        pairs = [('', 'a\u2028+fake\n'), ('one', 'two'), ('one\n', 'one'),
                 ('a\rb\r', 'a\rc\r'), ('a\r\nb\r\n', 'a\r\nc\r\n'), ('', '\n'),
                 ('--- old\n', '++ new\n'), ('a\u0085b\n', 'a\u0085c\n')]
        for before, after in pairs:
            with self.subTest(before=before, after=after), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repository(root)
                (root / 'sample.txt').write_bytes(before.encode())
                patch = unified_patch(before, after, fromfile='a/sample.txt', tofile='b/sample.txt')
                git(root, 'apply', '--', '-', input=patch.encode())
                self.assertEqual((root / 'sample.txt').read_bytes(), after.encode())

    def test_git_views_agree_on_lf_lines_and_preserve_source(self):
        samples = ['', 'a\u2028+fake\n', 'a\u2029-fake', 'a\rb\r', 'a\r\nb\r\n', '++ body\n', '\n']
        for text in samples:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repository(root)
                (root / 'sample.txt').write_bytes(text.encode())
                native = subprocess.run(['git', 'diff', '--no-index', '--numstat', '--',
                                         '/dev/null', 'sample.txt'], cwd=root, capture_output=True)
                self.assertIn(native.returncode, (0, 1))
                added = int(native.stdout.split(b'\t')[0]) if native.stdout else 0
                simple = ReviewService().patch(root, 'sample.txt')
                self.assertEqual((simple['added'], simple['deleted']), (added, 0))
                service = ComparisonService()
                summary = service.compare(root)
                self.assertEqual(summary['added'], added)
                selected = service.compare(root, path='sample.txt', revision=summary['revision'])
                self.assertEqual(selected['selection']['patch'], simple['patch'])
                if text:
                    for line in text.removesuffix('\n').split('\n'):
                        self.assertIn('+' + line, simple['patch'])

    def test_tracked_text_is_not_binary_and_header_like_rows_are_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository(root)
            (root / 'sample.txt').write_text('-- old\n', encoding='utf-8', newline='')
            git(root, 'add', '--all')
            git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'text')
            (root / 'sample.txt').write_text('++ new\nprint("Binary files a and b differ")\nGIT binary patch\n', encoding='utf-8', newline='')
            selected = ReviewService().patch(root, 'sample.txt')
            self.assertFalse(selected['binary'])
            self.assertEqual((selected['added'], selected['deleted']), (3, 1))
            service = ComparisonService()
            summary = service.compare(root)
            patch = service.compare(root, path='sample.txt', revision=summary['revision'])['selection']
            self.assertFalse(patch['binary'])
            self.assertEqual(patch['patch'], selected['patch'])

    def test_worktree_review_selects_literal_path_and_keeps_untracked_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = home / 'checkout'
            repository(root)
            for name in ('odd[1].txt', 'odd1.txt'):
                (root / name).write_text('before\n', encoding='utf-8', newline='')
            git(root, 'add', '--all')
            git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'files')
            base = git(root, 'rev-parse', 'HEAD').decode().strip()
            for name in ('odd[1].txt', 'odd1.txt'):
                (root / name).write_text('after ' + name + '\n', encoding='utf-8', newline='')
            service = WorktreeService(home)
            row = {'id': 'fixture', 'path': str(root), 'base_commit': base}
            selected = review(service, row, path='odd[1].txt')['selection']
            self.assertIn('+after odd[1].txt', selected['diff'])
            self.assertNotIn('odd1.txt', selected['diff'])
            (root / 'new.txt').write_text('a\u2028+fake\n', encoding='utf-8', newline='')
            selected = review(service, row, path='new.txt')['selection']
            self.assertIn('@@ -0,0 +1 @@', selected['diff'])
            self.assertIn('+a\u2028+fake\n', selected['diff'])


class SessionDiffBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_diff_preserves_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SessionStore(directory).create()
            before, after = '# Plan\r\ntext\u2028old', '# Plan\r\ntext\u2028new'
            await plans.submit(session, before, 'one')
            plan_id = await plans.submit(session, after, 'two')
            detail = plans.detail(session, plan_id)
            self.assertEqual(detail['plan'], after)
            self.assertIn(' # Plan\r\n', detail['diff'])
            self.assertIn('-text\u2028old\n\\ No newline at end of file\n+text\u2028new', detail['diff'])

    async def test_turn_and_net_counts_do_not_split_content_separators(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SessionStore(directory).create()
            await session.start_turn()
            before, after = 'old\n', 'a\u2028+fake\n++ body\n'
            display = file_diff(before, after, Path('sample.txt'), changed=True)
            display['snapshot'] = session.file_evidence.capture(display.pop('_file_change'))
            await session.append('tool/call', {'call_id': 'write-one', 'name': 'write',
                'status': 'running', 'turn': 1, 'input': {'path': 'sample.txt'}})
            await session.append('tool/result', {'call_id': 'write-one', 'name': 'write', 'turn': 1,
                'display': display, 'results': [{'tool_use_id': 'write-one', 'is_error': False, 'content': 'ok'}]}, surface_op=AppendOp())
            operation = turn_changes(session, 1)['files'][0]['operations'][0]
            net = turn_net_changes(session, 1)['files'][0]
            self.assertEqual(operation['counts'], {'added': 2, 'removed': 1})
            self.assertEqual(net['counts'], operation['counts'])

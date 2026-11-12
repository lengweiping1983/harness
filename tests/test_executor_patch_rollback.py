"""批量文件失败的回滚不能覆盖并发写入，并保留原始备份。"""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.execution.files import _replace, apply_changes
from harness.execution.policy import PathPolicy, file_version


class ExecutorPatchRollbackTests(unittest.TestCase):
    def run_failure(self, kind, language, *, interrupt=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / 'project'
            project.mkdir()
            a, b, c = [project / name for name in ('a.txt', 'b.txt', 'c.txt')]
            if kind != 'create':
                a.write_bytes(b'original-a')
            b.write_bytes(b'original-b')
            c.write_bytes(b'original-c')
            original = {p.name: p.read_bytes() if p.exists() else None for p in (a, b, c)}
            changes = [{'path': p.name, 'expected_version': file_version(p),
                        'content': None if p == a and kind == 'delete' else 'patch-' + p.name}
                       for p in (a, b, c)]
            failure = KeyboardInterrupt('fixture interrupt') if interrupt else OSError('fixture failure')

            def replace(target, content):
                if target == c:
                    if kind != 'ordinary':
                        a.write_bytes(b'concurrent-edit')
                    raise failure
                _replace(target, content)

            expected = KeyboardInterrupt if interrupt else OSError if kind == 'ordinary' else HarnessError
            with current_language.set(language), patch('harness.execution.files._replace', side_effect=replace):
                with self.assertRaises(expected) as caught:
                    apply_changes(PathPolicy(project, root / 'state'), changes, root / 'state', 1000)
            self.assertEqual(a.read_bytes(), b'original-a' if kind == 'ordinary' else b'concurrent-edit')
            self.assertEqual(b.read_bytes(), b'original-b')
            self.assertEqual(c.read_bytes(), b'original-c')
            manifests = list((root / 'state' / 'backups').glob('*/manifest.json'))
            self.assertEqual(len(manifests), 1)
            for row in json.loads(manifests[0].read_text()):
                saved = base64.b64decode(row['before']) if row['before'] is not None else None
                self.assertEqual(saved, original[row['path']])
            if kind == 'ordinary':
                self.assertIs(caught.exception, failure)
            elif interrupt:
                self.assertIs(caught.exception, failure)
                self.assertIn(manifests[0].parent.name, caught.exception.__notes__[0])
            else:
                self.assertEqual(caught.exception.code, 'execution/rollback-incomplete')
                self.assertIs(caught.exception.__cause__, failure)
                self.assertIn(manifests[0].parent.name, caught.exception.message)
                self.assertIn('a.txt', caught.exception.message)
                self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in caught.exception.message), language == 'zh-CN')

    def test_concurrent_create_modify_and_recreate_are_preserved(self):
        for language in ('en', 'zh-CN', 'en'):
            for kind in ('create', 'modify', 'delete'):
                with self.subTest(language=language, kind=kind):
                    self.run_failure(kind, language)

    def test_ordinary_failure_still_rolls_back_and_propagates_original_error(self):
        self.run_failure('ordinary', 'en')

    def test_interrupt_keeps_its_identity_and_reports_incomplete_rollback(self):
        self.run_failure('modify', 'zh-CN', interrupt=True)

    def test_successful_batch_keeps_receipt_and_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / 'project'
            project.mkdir()
            old = project / 'old.txt'
            old.write_bytes(b'original')
            result = apply_changes(PathPolicy(project, root / 'state'), [
                {'path': old.name, 'expected_version': file_version(old), 'content': None},
                {'path': 'new.txt', 'expected_version': 'absent', 'content': 'New 原文'},
            ], root / 'state', 1000)
            self.assertEqual(result['changed_files'], ['old.txt', 'new.txt'])
            self.assertEqual(result['versions'], {'old.txt': 'absent', 'new.txt': file_version(project / 'new.txt')})
            self.assertFalse(old.exists())
            self.assertEqual((project / 'new.txt').read_text(), 'New 原文')
            self.assertTrue((root / 'state' / 'backups' / result['backup_id'] / 'manifest.json').is_file())

    def test_rollback_write_failure_does_not_skip_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / 'project'
            project.mkdir()
            targets = [project / name for name in ('a', 'b', 'c')]
            for target in targets:
                target.write_bytes(b'original')
            failure = OSError('fixture forward failure')

            def replace(target, content):
                if target == targets[2]:
                    raise failure
                if target == targets[1] and content == b'original':
                    raise OSError('fixture rollback failure')
                _replace(target, content)

            changes = [{'path': p.name, 'expected_version': file_version(p), 'content': 'patch'} for p in targets]
            with patch('harness.execution.files._replace', side_effect=replace), self.assertRaises(HarnessError) as caught:
                apply_changes(PathPolicy(project, root / 'state'), changes, root / 'state', 1000)
            self.assertEqual(caught.exception.code, 'execution/rollback-incomplete')
            self.assertIs(caught.exception.__cause__, failure)
            self.assertEqual([p.read_bytes() for p in targets], [b'original', b'patch', b'original'])

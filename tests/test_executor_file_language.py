"""执行器文件预检使用本次语言，错误前保留原件与备份边界。"""
import tempfile
import unittest
from pathlib import Path

from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.execution.files import (
    MAX_SNAPSHOT_FILE_BYTES,
    MAX_SNAPSHOT_TOTAL_BYTES,
    apply_changes,
    snapshot,
)
from harness.execution.policy import PathPolicy, file_version


class ExecutorFileLanguageTests(unittest.TestCase):
    def test_preflight_errors_are_bilingual_and_never_modify_files_or_create_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            target = project / 'original.txt'
            target.write_text('Original evidence 原文')
            policy = PathPolicy(project, root / 'state')
            version = file_version(target)
            change = {'path': 'original.txt', 'expected_version': version, 'content': 'replacement'}
            cases = [([change, change], 1000, 'execution/duplicate-path'),
                     ([{**change, 'expected_version': 'absent'}], 1000, 'execution/file-conflict'),
                     ([change], 1, 'execution/file-too-large'),
                     ([{'path': 'new.txt', 'expected_version': 'absent', 'content': 'long'}], 1, 'execution/file-too-large'),
                     ([{'path': 'missing.txt', 'expected_version': 'absent', 'content': None}], 1000, 'execution/file-not-found')]
            original = target.read_bytes()
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    for changes, limit, code in cases:
                        with self.subTest(language=language, code=code), self.assertRaises(HarnessError) as caught:
                            apply_changes(policy, changes, root / 'state', limit)
                        self.assertEqual(caught.exception.code, code)
                        self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in caught.exception.message), language == 'zh-CN')
                        self.assertEqual(target.read_bytes(), original)
                        self.assertFalse((root / 'state').exists())
                    with self.assertRaises(HarnessError) as caught:
                        snapshot(policy, 1)
                    self.assertEqual(caught.exception.code, 'execution/file-too-large')
                    self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in caught.exception.message), language == 'zh-CN')

    def test_snapshot_uses_an_independent_bounded_limit_for_large_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            target = project / 'large.bin'
            payload = b'\x00' * (8 * 1024 * 1024 + 1)
            target.write_bytes(payload)
            policy = PathPolicy(project, root / 'state')

            before = snapshot(
                policy,
                8 * 1024 * 1024,
                snapshot_max_bytes=MAX_SNAPSHOT_FILE_BYTES,
                snapshot_max_total_bytes=MAX_SNAPSHOT_TOTAL_BYTES,
            )
            target.write_bytes(b'\x01' * len(payload))
            after = snapshot(
                policy,
                8 * 1024 * 1024,
                snapshot_max_bytes=MAX_SNAPSHOT_FILE_BYTES,
                snapshot_max_total_bytes=MAX_SNAPSHOT_TOTAL_BYTES,
            )
            self.assertNotEqual(before, after)

            with self.assertRaises(HarnessError) as caught:
                snapshot(policy, 8 * 1024 * 1024, snapshot_max_bytes=8 * 1024 * 1024)
            self.assertEqual(caught.exception.code, 'execution/file-too-large')

    def test_snapshot_enforces_an_aggregate_size_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            (project / 'first.bin').write_bytes(b'123456')
            (project / 'second.bin').write_bytes(b'abcdef')
            policy = PathPolicy(project, root / 'state')

            with self.assertRaises(HarnessError) as caught:
                snapshot(
                    policy,
                    1024,
                    snapshot_max_bytes=1024,
                    snapshot_max_total_bytes=10,
                )
            self.assertEqual(caught.exception.code, 'execution/project-too-large')

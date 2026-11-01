"""缺失路径保留失败事实，并提供有界且不越界的目录定位信息。"""
from pathlib import Path
import tempfile
import unittest

from harness.core.language import current_language
from harness.tools.builtin.fs_tools import _not_found_error


class MissingFileGuidanceTests(unittest.TestCase):
    def test_missing_entry_lists_actual_parent_without_creating_or_substituting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'src').mkdir()
            actual = root / 'src' / 'main.ts'
            actual.write_text('original', encoding='utf-8')
            missing = root / 'src' / 'main.js'
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    error = _not_found_error(missing, str(root))
                    self.assertEqual(error.code, 'FS_NOT_FOUND')
                    self.assertEqual(error.details['parent'], str(root / 'src'))
                    self.assertEqual(error.details['entries'], ['main.ts'])
                    self.assertIn('main.ts', error.message)
                    self.assertNotIn('tools.read.', error.message)
                    self.assertFalse(missing.exists())
                    self.assertEqual(actual.read_text(encoding='utf-8'), 'original')

    def test_nested_missing_directory_uses_nearest_existing_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'index.html').touch()
            error = _not_found_error(root / 'missing' / 'nested' / 'entry.js', str(root))
            self.assertEqual(error.details['parent'], str(root))
            self.assertEqual(error.details['entries'], ['index.html'])

    def test_entries_are_bounded_and_hidden_or_dependency_entries_are_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / '.secret').touch()
            (root / 'node_modules').mkdir()
            for number in range(40):
                (root / f'file-{number}.ts').touch()
            error = _not_found_error(root / 'missing.js', str(root))
            self.assertEqual(len(error.details['entries']), 32)
            self.assertTrue(error.details['entries_truncated'])
            self.assertNotIn('.secret', error.message)
            self.assertNotIn('node_modules', error.message)

    def test_external_missing_path_does_not_list_workspace_or_external_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            root = base / 'workspace'
            root.mkdir()
            (base / 'private.txt').touch()
            error = _not_found_error(base / 'missing.js', str(root))
            self.assertNotIn('parent', error.details)
            self.assertNotIn('private.txt', error.message)

    def test_missing_workspace_does_not_list_its_external_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            (base / 'private.txt').touch()
            root = base / 'missing-workspace'
            for target in (root, root / 'src' / 'main.js'):
                error = _not_found_error(target, str(root))
                self.assertNotIn('parent', error.details)
                self.assertNotIn('private.txt', error.message)

    def test_same_name_candidates_remain_hints_without_automatic_substitution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'actual').mkdir()
            candidate = root / 'actual' / 'main.js'
            candidate.write_text('untouched', encoding='utf-8')
            error = _not_found_error(root / 'main.js', str(root))
            self.assertEqual(error.code, 'FS_NOT_FOUND')
            self.assertEqual(error.details['candidates'], [str(candidate.relative_to(root))])
            self.assertFalse((root / 'main.js').exists())
            self.assertEqual(candidate.read_text(encoding='utf-8'), 'untouched')

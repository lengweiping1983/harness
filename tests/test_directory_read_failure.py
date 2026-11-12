"""目录读取失败不能伪装为空目录或完整清单。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.fs_tools import build_fs_tools, view_directory
from harness.tools.registry import ToolRunContext


class DirectoryReadFailureTests(unittest.TestCase):
    def test_root_and_nested_read_failures_keep_directory_identity_and_cause(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            child = root / 'Original-{reason}-原文'
            child.mkdir()
            original = child / 'keep.txt'
            original.write_bytes(b'Original content')
            (root / 'visible.txt').write_bytes(b'visible')
            reader = next(tool for tool in build_fs_tools(str(root)) if tool.schema.name == 'read')
            ctx = ToolRunContext(call_id='directory', session_id='fixture', cwd=str(root), turn=1, step=1)
            iterdir = Path.iterdir
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    for target in (root, child):
                        reason = PermissionError('Original {path} permission diagnostic')

                        def failing_iterdir(path):
                            if path == target:
                                raise reason
                            return iterdir(path)

                        with patch.object(Path, 'iterdir', failing_iterdir):
                            with self.subTest(language=language, target=target), self.assertRaises(ToolExecutionError) as error:
                                reader.execute({'path': str(root)}, ctx)
                        self.assertEqual(error.exception.code, 'FS_DIRECTORY_READ')
                        self.assertEqual(error.exception.details['path'], str(target))
                        self.assertEqual(error.exception.message, tr('fs.directory.read_failed', path=str(target), reason=str(reason)))
                        self.assertIs(error.exception.__cause__, reason)
                        self.assertEqual(original.read_bytes(), b'Original content')
            listing = view_directory(root)
            self.assertIn('f\tvisible.txt', listing)
            self.assertIn('f\tOriginal-{reason}-原文/keep.txt', listing)
            self.assertEqual(set(root.iterdir()), {child, root / 'visible.txt'})

    def test_empty_directory_remains_a_successful_empty_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            reader = next(tool for tool in build_fs_tools(str(root)) if tool.schema.name == 'read')
            ctx = ToolRunContext(call_id='empty', session_id='fixture', cwd=str(root), turn=1, step=1)
            value = reader.execute({'path': str(root)}, ctx)
            self.assertEqual(value['content'], '')
            self.assertEqual(value['entries'], 0)
            self.assertFalse(value['truncated'])
            self.assertEqual(list(root.iterdir()), [])

"""文件工具的成功、参数错误与截断提示按执行时语言解析。"""
from pathlib import Path
import tempfile
import unittest

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.fs_tools import apply_insert, apply_str_replace, maybe_truncate, normalize_view_range


class FileToolLanguageTests(unittest.TestCase):
    def test_file_feedback_follows_runtime_language(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'fixture.txt'
                path.write_text('original\n')
                result = apply_str_replace(path, 'original', 'updated', False)
                self.assertTrue(result['message'].startswith(tr('tools.fs.edited', path=str(path))))
                self.assertEqual(path.read_text(), 'updated\n')
                self.assertEqual(maybe_truncate('a' * 20, 5), 'aaaaa\n\n' + tr('owned.fs_tools.46b829d8ad9f'))
                clipped = maybe_truncate('a' * 20, 5)
                self.assertNotIn('grep -n', clipped)
                self.assertIn('offset', clipped)
                self.assertIn('limit', clipped)
                operations = (
                    lambda: normalize_view_range([0, 2], 1, path),
                    lambda: normalize_view_range([2, 3], 1, path),
                    lambda: normalize_view_range([2, 3], 1, path, total_known=False),
                    lambda: apply_str_replace(path, 'updated', None, False),
                    lambda: apply_insert(path, True, 'new'),
                    lambda: apply_insert(path, 0, None),
                    lambda: apply_insert(path, 9, 'new'),
                )
                for operation in operations:
                    with self.assertRaises(ToolExecutionError) as caught:
                        operation()
                    self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in caught.exception.message), language == 'zh-CN')
                    self.assertEqual(path.read_text(), 'updated\n')

    def test_registered_tools_render_complete_summaries_without_changing_source(self):
        from harness.tools.builtin.fs_tools import build_fs_tools, _not_found_error
        from harness.tools.registry import ToolRunContext

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / 'Original-{count}-原文.txt'
            path.write_text('Original {content}😀\n', encoding='utf-8')
            for folder in ('one', 'two'):
                (root / folder).mkdir()
                (root / folder / 'missing.txt').write_text('candidate', encoding='utf-8')
            tools = {tool.schema.name: tool for tool in build_fs_tools(str(root), observation_policy=False)}
            ctx = ToolRunContext(call_id='file-language', session_id='fixture', cwd=str(root), turn=1, step=1)
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    for numbered in (True, False):
                        value = tools['read'].execute({'path': str(path), 'line_numbers': numbered}, ctx)
                        text = tools['read'].to_content({}, value)[0].text
                        heading = f'{path} (Total lines: 1)' if language == 'en' else f'{path}（共 1 行）'
                        self.assertTrue(text.startswith(heading + '\n'))
                        self.assertIn('Original {content}😀', text)
                        loaded = tools['read'].to_content({}, {**value, 'total_lines_known': False})[0].text
                        self.assertIn('total line count was not computed' if language == 'en' else '总行数未统计', loaded)
                    target = root / ('new-' + language + '.txt')
                    for content, created in [('first', True), ('changed', False)]:
                        if created and target.exists():
                            target.unlink()
                        result = tools['write'].execute({'path': str(target), 'content': content}, ctx)
                        self.assertEqual(result['created'], created)
                        summary = tools['write'].to_content({}, result)[0].text
                        self.assertEqual(summary, tr('fs.write.summary.created' if created else 'fs.write.summary.overwritten',
                                                     path=str(target), size=len(content)))
                        self.assertEqual(target.read_text(encoding='utf-8'), content)
                    error = _not_found_error(root / 'missing.txt', str(root))
                    shown = tr('ui.common.list_separator').join(error.details['candidates'])
                    self.assertIn(shown, error.message)
                    value = {'pattern': 'Original', 'matches': [], 'count': 0,
                             'skipped_file_count': 2, 'skipped_files': ['a-{files}.txt', '原文.txt'],
                             'partial_file_count': 1, 'partial_files': ['large.txt']}
                    summary = tools['grep'].to_content({}, value)[0].text
                    self.assertIn(tr('fs.grep.summary.skipped', count=2, files=tr('ui.common.list_separator').join(value['skipped_files'])), summary)
                    self.assertIn(tr('fs.grep.summary.partial', count=1, files='large.txt'), summary)
                    self.assertEqual(path.read_text(encoding='utf-8'), 'Original {content}😀\n')

    def test_observation_rejections_and_read_notices_use_call_language(self):
        from harness.tools.builtin.fs_tools import build_fs_tools
        from harness.tools.registry import ToolRunContext

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / 'Original-{name}-原文.txt'
            original = 'Original {text}\nsecond\nthird\n'
            path.write_text(original, encoding='utf-8')
            missing = root / 'missing-{name}.txt'
            tools = {tool.schema.name: tool for tool in build_fs_tools(str(root))}
            for index, language in enumerate(('en', 'zh-CN', 'en')):
                ctx = ToolRunContext('language', f'fixture-{index}', str(root), 1, 1)
                with current_language.set(language):
                    cases = [('write', {'path': str(path), 'content': 'replacement'}, 'FS_EXISTS', path),
                             ('edit', {'path': str(path), 'old_string': 'Original', 'new_string': 'replacement'}, 'FS_NOT_OBSERVED', path)]
                    with self.assertRaises(ToolExecutionError):
                        tools['read'].execute({'path': str(missing)}, ctx)
                    cases.append(('edit', {'path': str(missing), 'old_string': 'Original', 'new_string': 'replacement'}, 'FS_NOT_FOUND', missing))
                    for name, args, code, target in cases:
                        with self.subTest(language=language, code=code), self.assertRaises(ToolExecutionError) as caught:
                            tools[name].execute(args, ctx)
                        self.assertEqual(caught.exception.code, code)
                        self.assertIn(str(target), caught.exception.message)
                        message = caught.exception.message.replace(str(target), '').replace(path.name, '')
                        if language == 'zh-CN':
                            for phrase in ('already exists', 'requires reading', 'cannot edit', 'not found'):
                                self.assertNotIn(phrase, message)
                        else:
                            self.assertFalse(any('\u4e00' <= c <= '\u9fff' for c in message))
                        self.assertEqual(path.read_text(encoding='utf-8'), original)
                        self.assertFalse(missing.exists())
                    missing.write_text('External 原文', encoding='utf-8')
                    with self.assertRaises(ToolExecutionError) as caught:
                        tools['edit'].execute({'path': str(missing), 'old_string': 'External', 'new_string': 'changed'}, ctx)
                    self.assertEqual(caught.exception.code, 'FS_NOT_FOUND')
                    self.assertEqual(caught.exception.message,
                        tr('python.fs_observation.e636f9bc334f', p0=str(missing)))
                    self.assertIn('上次读取时' if language == 'zh-CN' else 'most recent observation', caught.exception.message)
                    self.assertEqual(missing.read_text(encoding='utf-8'), 'External 原文')
                    missing.unlink()
                    value = tools['read'].execute({'path': str(path), 'limit': 1, 'line_numbers': False}, ctx)
                    self.assertTrue(value['truncated'])
                    self.assertNotIn('grep -n', value['notice'])
                    self.assertIn('offset', value['notice'])
                    self.assertIn('view_range', value['notice'])

    def test_range_notices_cover_eof_and_loaded_prefix_in_each_language(self):
        from harness.tools.builtin.fs_tools import read_range_notice
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language):
                for known, key in ((True, 'tools.read.range_eof'), (False, 'tools.read.range_prefix')):
                    self.assertEqual(read_range_notice([2, 9], 3, total_known=known),
                                     tr(key, start=2, requested_end=9, actual_end=3))
                    for requested in (None, [2, -1], [2, 3]):
                        self.assertEqual(read_range_notice(requested, 3, total_known=known), '')

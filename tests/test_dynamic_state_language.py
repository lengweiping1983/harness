"""动态冲突码和搜索工作进程的资源身份按实际读取语言解析。"""
import ast
import json
import unittest
from pathlib import Path
from harness.core.language import current_language
from harness.core.errors import ToolExecutionError
from harness.tools.builtin.todo_state import TodoConflict, TodoSnapshot
from harness.tools.builtin.fs_grep import decode_response
from harness.tools.builtin.fs_grep_worker import search_files
from harness.tools.builtin.fs_errors import SearchError


class DynamicStateLanguageTests(unittest.TestCase):
    def test_all_produced_todo_conflicts_resolve_in_each_recovery_state(self):
        codes = set()
        for module in ('todo_state.py', 'todo_tools.py'):
            path = Path(__file__).parents[1] / 'harness/tools/builtin' / module
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'TodoConflict':
                    self.assertIsInstance(node.args[0], ast.Constant)
                    codes.add(node.args[0].value)
        self.assertTrue(codes)
        for code in codes:
            for sequence in (-1, 7):
                snapshot = TodoSnapshot('fixture', 'list-fixture', source_seq=sequence)
                conflict = TodoConflict(code, snapshot, invalid_ids=['Original'], plan_id='plan-fixture', plan_status='pending')
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language):
                        value = conflict.payload()
                    self.assertEqual(value['code'], code)
                    self.assertFalse(value['updated'])
                    self.assertEqual(value['invalid_ids'], ['Original'])
                    self.assertEqual(value['current']['snapshot_token'], snapshot.token)
                    self.assertEqual(value['recovery']['max_retries'], 0 if code == 'PLAN_NOT_APPROVED' else 1)
                    for text in (value['message'], value['recovery']['instruction'], value['current']['notice']):
                        self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in text), language == 'zh-CN')
                self.assertEqual(snapshot.source_seq, sequence)
                self.assertEqual(snapshot.todos, [])

    def test_worker_error_identity_crosses_json_before_language_resolution(self):
        request = {'root': '.', 'args': {'pattern': '['}}
        with self.assertRaises(SearchError) as caught:
            search_files(request)
        encoded = json.dumps({'error': caught.exception.to_dict()}).encode()
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), self.assertRaises(ToolExecutionError) as failure:
                decode_response(encoded)
            self.assertEqual(failure.exception.code, 'FS_INVALID_REGEX')
            self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in failure.exception.message), language == 'zh-CN')
        self.assertEqual(request, {'root': '.', 'args': {'pattern': '['}})

    def test_all_search_error_sources_preserve_keys_until_host_resolution(self):
        import subprocess
        import sys
        import tempfile
        from harness.i18n import tr
        from harness.tools.builtin.fs_tools import iter_files
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cases = [({'path': ''}, 'FS_INVALID_PATH'),
                     ({'path': str(root.parent / 'outside-{path}')}, 'FS_INVALID_PATH'),
                     ({'path': 'missing-{path}'}, 'FS_NOT_FOUND'),
                     ({'glob': '../*'}, 'FS_INVALID_GLOB'),
                     ({'pattern': '['}, 'FS_INVALID_REGEX')]
            encoded = []
            for args, code in cases:
                request = {'root': str(root), 'args': {'pattern': 'Original', **args}, 'ignores': []}
                with self.assertRaises(SearchError) as caught:
                    search_files(request)
                self.assertEqual(caught.exception.code, code)
                encoded.append((caught.exception.to_dict(), code))
            worker = Path(__file__).parents[1] / 'harness/tools/builtin/fs_grep_worker.py'
            request_file = root / 'invalid-request.json'
            request_file.write_text('{}', encoding='utf-8')
            process = subprocess.run([sys.executable, '-X', 'utf8', '-I', str(worker), str(request_file)],
                                     capture_output=True, timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr)
            encoded.append((json.loads(process.stdout)['error'], 'FS_SEARCH_FAILED'))
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    for payload, code in encoded:
                        with self.subTest(language=language, code=code), self.assertRaises(ToolExecutionError) as caught:
                            decode_response(json.dumps({'error': payload}).encode())
                        self.assertEqual(caught.exception.code, code)
                        self.assertEqual(caught.exception.message, tr(payload['message_key'], **payload['message_values']))
                    with self.assertRaises(ToolExecutionError) as caught:
                        list(iter_files(root, '../*', set()))
                    self.assertEqual(caught.exception.message, tr('python.fs_walk.51f5eb2fa72f'))
            self.assertEqual(request_file.read_bytes(), b'{}')
            self.assertEqual(list(root.iterdir()), [request_file])

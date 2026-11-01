"""验证来源版本绑定、变化提示和有界只读核验。"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.app.layered_memory import LayeredMemory
from harness.app.memory import ProjectMemory
from harness.app.memory_sources import PREFIX, MAX_FILE_BYTES, SourceChecker, capture_sources
from harness.core.errors import HarnessError
from harness.session.store import SessionStore
from harness.tools.builtin.fs_tools import build_fs_tools
from harness.tools.builtin.memory_tools import build_layered_memory_tools
from harness.tools.registry import ToolRunContext


class MemorySourcesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.path = self.root / 'source.py'
        self.path.write_bytes(b'original source')
        self.session = SessionStore(self.root / 'state').create()
        self.ctx = ToolRunContext(call_id='source', session_id=self.session.id, cwd=str(self.root),
                                  turn=1, step=1, services={'session': self.session})
        self.read = next(tool for tool in build_fs_tools(str(self.root)) if tool.schema.name == 'read')
        self.memory = LayeredMemory(self.root / 'home', ProjectMemory())
        self.tools = {tool.schema.name: tool for tool in build_layered_memory_tools(self.memory)}

    def capture(self):
        self.read.execute({'path': 'source.py'}, self.ctx)
        return capture_sources(self.root, ['source.py'], '决策来源', self.session, self.session.id)

    def test_capture_requires_this_sessions_observation(self):
        with self.assertRaises(HarnessError):
            capture_sources(self.root, ['source.py'], '', self.session, self.session.id)
        evidence = self.capture()
        self.assertIn(hashlib.sha256(self.path.read_bytes()).hexdigest()[:16], evidence)
        other = SessionStore(self.root / 'other-state').create()
        with self.assertRaises(HarnessError):
            capture_sources(self.root, ['source.py'], '', other, other.id)
        with self.assertRaises(HarnessError):
            capture_sources(self.root, ['source.py'], '', self.session, other.id)

    def test_capture_preserves_old_observation_after_external_change(self):
        self.read.execute({'path': 'source.py'}, self.ctx)
        self.path.write_bytes(b'changed externally')
        evidence = capture_sources(self.root, ['source.py'], '', self.session, self.session.id)
        self.assertEqual(SourceChecker(self.root).check(evidence)['status'], 'changed')

    def test_changed_missing_and_unchanged_do_not_rewrite_memory_or_observation(self):
        evidence = self.capture()
        row = self.memory.project.save(self.root, '基于源码的决策', evidence=evidence, pinned=True)
        observations = self.session.file_observations.records.copy()
        self.assertEqual(SourceChecker(self.root).check(evidence)['status'], 'unchanged')
        self.path.write_bytes(b'new source')
        report = self.memory.project.preview(self.root)
        self.assertEqual(report.selected[0].source_status, 'changed')
        self.assertIn('"status": "changed"', report.text)
        self.path.unlink()
        self.assertEqual(SourceChecker(self.root).check(evidence)['status'], 'missing')
        self.assertEqual(self.memory.project.list(self.root)[0], row)
        self.assertEqual(self.session.file_observations.records, observations)

    def test_tool_captures_sources_and_recall_reports_change(self):
        self.read.execute({'path': 'source.py'}, self.ctx)
        saved = self.tools['memory_save'].execute({'content': '源码决策', 'source_files': ['source.py'], 'scope': 'project'}, self.ctx)
        self.assertTrue(saved['evidence'].startswith(PREFIX))
        self.path.write_bytes(b'changed source')
        records = self.tools['memory_recall'].execute({'scopes': ['project']}, self.ctx)['memories']
        self.assertEqual(records[0]['source_check']['status'], 'changed')
        self.assertEqual(records[0]['revision'], 1)
        schema = self.tools['user_memory_save'].schema.parameters
        self.assertNotIn('source_files', schema['properties'])

    def test_malformed_and_escaping_sources_never_read_files(self):
        checker = SourceChecker(self.root)
        with patch.object(Path, 'open', side_effect=AssertionError('不应读取文件')):
            self.assertEqual(checker.check(PREFIX + 'bad json')['status'], 'invalid')
            for path in ('../outside', '/absolute', 'C:/outside', '..\\outside', 'source.py/../source.py'):
                evidence = PREFIX + json.dumps({'note': '', 'files': [{'path': path, 'version': '0' * 16}]})
                self.assertEqual(checker.check(evidence)['status'], 'unsafe')

    def test_large_files_and_read_count_are_bounded(self):
        evidence = self.capture()
        self.path.write_bytes(b'x' * (MAX_FILE_BYTES + 1))
        with patch.object(Path, 'open', side_effect=AssertionError('超大文件不应打开')):
            self.assertEqual(SourceChecker(self.root).check(evidence)['status'], 'unchecked')
        self.path.write_bytes(b'original source')
        checker = SourceChecker(self.root)
        with patch('harness.app.memory_sources.MAX_CHECK_FILES', 1):
            self.assertEqual(checker.check(evidence)['status'], 'unchanged')
            another = PREFIX + json.dumps({'note': '', 'files': [{'path': 'another.py', 'version': '0' * 16}]})
            self.assertEqual(checker.check(another)['status'], 'unchecked')

    def test_worktree_source_is_resolved_against_current_tree(self):
        first, second = self.root / 'tree-one', self.root / 'tree-two'
        for tree in (first, second):
            tree.mkdir()
            (tree / '.git').write_text('gitdir: fixture', encoding='utf-8')
        (first / 'source.py').write_bytes(b'one')
        (second / 'source.py').write_bytes(b'two')
        evidence = PREFIX + json.dumps({'note': '', 'files': [
            {'path': 'source.py', 'version': hashlib.sha256(b'one').hexdigest()[:16]}]})
        self.assertEqual(SourceChecker(first).check(evidence)['status'], 'unchanged')
        self.assertEqual(SourceChecker(second).check(evidence)['status'], 'changed')

    def test_source_check_is_included_in_final_character_budget(self):
        evidence = self.capture()
        self.memory.project.save(self.root, '来源决策', evidence=evidence, pinned=True)
        complete = self.memory.project.context(self.root)
        self.assertIn('source_check', complete)
        self.assertEqual(self.memory.project.context(self.root, budget=len(complete)), complete)
        report = self.memory.project.preview(self.root, budget=len(complete) - 1)
        self.assertEqual(report.text, '')
        self.assertEqual(report.skipped_budget[0].source_status, 'unchanged')

    def test_check_byte_budget_is_shared_and_does_not_open_over_budget_source(self):
        self.path.write_bytes(b'ab')
        other = self.root / 'other.py'
        other.write_bytes(b'cd')
        def payload(name, content):
            return PREFIX + json.dumps({'note': '', 'files': [
                {'path': name, 'version': hashlib.sha256(content).hexdigest()[:16]}]})
        with patch('harness.app.memory_sources.MAX_CHECK_BYTES', 3):
            checker = SourceChecker(self.root)
            self.assertEqual(checker.check(payload('source.py', b'ab'))['status'], 'unchanged')
            self.assertEqual(checker.remaining, 1)
            with patch.object(Path, 'open', side_effect=AssertionError('预算不足不应打开文件')):
                self.assertEqual(checker.check(payload('other.py', b'cd'))['status'], 'unchecked')

    def test_duplicate_source_identity_fields_are_invalid_and_never_read_files(self):
        from harness.app.memory_sources import parse_sources
        version = 'a'*16
        records = [
            '{"note":"first","note":"second","files":[{"path":"file.txt","version":"'+version+'"}]}',
            '{"note":"original","files":[{"path":"first.txt","path":"second.txt","version":"'+version+'"}]}',
            '{"note":"original","files":[{"path":"file.txt","version":"'+version+'","version":"'+'b'*16+'"}]}',
        ]
        with tempfile.TemporaryDirectory() as directory:
            for raw in records:
                evidence = PREFIX+raw
                with self.assertRaisesRegex(ValueError, 'memory/invalid-file-sources'):
                    parse_sources(evidence)
                checker = SourceChecker(directory)
                with patch.object(checker, '_read', side_effect=AssertionError('ambiguous source must not read')):
                    self.assertEqual(checker.check(evidence), {'status':'invalid','files':[]})
                self.assertEqual(evidence, PREFIX+raw)
            self.assertIsNone(parse_sources('Original third-party evidence 原文'))

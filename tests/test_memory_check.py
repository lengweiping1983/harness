"""核对记忆逐字引用的身份、版本、来源和只读边界。"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.app.layered_memory import LayeredMemory
from harness.app.memory import ProjectMemory
from harness.app.memory_sources import PREFIX
from harness.core.errors import HarnessError
from harness.tools.builtin.memory_tools import build_layered_memory_tools
from harness.tools.registry import ToolRunContext


class MemoryCheckTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.memory = ProjectMemory()
        self.row = self.memory.save(self.root, '🙂 队列只说明排序规则，不包含备份保留期。')

    def ref(self, **changes):
        return {'id': self.row['id'], 'revision': self.row['revision'], 'excerpt': '队列只说明排序规则', **changes}

    def test_verbatim_match_uses_unicode_offsets_and_one_read_without_writes(self):
        before = self.memory.export(self.root)
        with patch.object(self.memory, 'list', wraps=self.memory.list) as read:
            result = self.memory.check(self.root, [self.ref()])[0]
            self.assertEqual(read.call_count, 1)
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(self.row['content'][result['start']:result['end']], result['excerpt'])
        self.assertEqual(result['start'], 2)
        self.assertNotIn('verified', result)
        self.assertEqual(self.memory.export(self.root), before)

    def test_missing_stale_and_invented_excerpt_do_not_return_substitute_text(self):
        for reference, status in [(self.ref(id='unknown'), 'missing'),
                                  (self.ref(revision=8), 'changed'),
                                  (self.ref(excerpt='备份保留30天'), 'excerpt_missing')]:
            with self.subTest(status=status):
                result = self.memory.check(self.root, [reference])[0]
                self.assertEqual(result['status'], status)
                self.assertNotIn('excerpt', result)
        self.memory.delete(self.root, self.row['id'], 1)
        self.assertEqual(self.memory.check(self.root, [self.ref()])[0]['status'], 'missing')

    def test_invalid_references_fail_before_read(self):
        for references in ([], [self.ref()] * 2, [self.ref(revision=True)], [self.ref(excerpt=' ')],
                           [self.ref(excerpt='x' * 1001)], [{**self.ref(), 'extra': 1}], [None]):
            with self.subTest(references=references), patch.object(self.memory, 'list') as read:
                with self.assertRaises(HarnessError):
                    self.memory.check(self.root, references)
                read.assert_not_called()

    def test_matching_excerpt_keeps_changed_source_warning(self):
        path = self.root / 'source.py'
        path.write_bytes(b'new')
        evidence = PREFIX + json.dumps({'note': '', 'files': [{'path': 'source.py',
                                      'version': hashlib.sha256(b'old').hexdigest()[:16]}]})
        self.row = self.memory.save(self.root, self.row['content'], evidence=evidence,
                                   memory_id=self.row['id'], expected_revision=1)
        result = self.memory.check(self.root, [self.ref()])[0]
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['source_check']['status'], 'changed')

    def test_session_check_cannot_resolve_other_session_record(self):
        memory = LayeredMemory(self.root / 'home', self.memory)
        self.row = memory.store('session', session_id='one').save(self.root, self.row['content'])
        tools = {tool.schema.name: tool for tool in build_layered_memory_tools(memory)}
        self.assertEqual(tools['memory_check'].effect, 'read')
        context = ToolRunContext(call_id='check', session_id='two', cwd=str(self.root), turn=1, step=1)
        result = tools['memory_check'].execute({'references': [self.ref()], 'scope': 'session'}, context)
        self.assertEqual(result['meaning'], 'record-excerpt-only')
        self.assertEqual(result['checks'][0]['status'], 'missing')

    def test_matching_summary_retains_stale_derivation_status(self):
        other = self.memory.save(self.root, '另一项事实')
        summary = self.memory.consolidate(self.root, [
            {'id': self.row['id'], 'revision': 1}, {'id': other['id'], 'revision': 1}], '派生摘要')['memory']
        self.memory.save(self.root, '事实已更新', memory_id=other['id'], expected_revision=1)
        result = self.memory.check(self.root, [{'id': summary['id'], 'revision': summary['revision'],
                                               'excerpt': '派生摘要'}])[0]
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['derivation']['status'], 'changed')
        self.assertTrue(result['derivation']['requires_reverification'])

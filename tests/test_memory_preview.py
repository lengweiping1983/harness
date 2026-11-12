"""验证召回预览解释与正式注入一致，且不复制敏感正文。"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.app.layered_memory import LayeredMemory
from harness.app.memory import ProjectMemory


class MemoryPreviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.memory = ProjectMemory()

    def test_reason_counts_cover_every_record_and_limit(self):
        pinned = self.memory.save(self.root, '固定偏好', pinned=True)
        for index in range(7):
            self.memory.save(self.root, f'队列资料 {index}')
        self.memory.save(self.root, '无关字体约定')
        report = self.memory.preview(self.root, query='队列')
        self.assertEqual(report.text, self.memory.context(self.root, query='队列'))
        self.assertEqual(report.selected[0].id, pinned['id'])
        self.assertEqual(report.selected[0].reason, 'pinned')
        self.assertEqual(sum(item.reason == 'query' for item in report.selected), 5)
        self.assertEqual(report.skipped_query_limit, 2)
        self.assertEqual(report.unmatched, 1)
        self.assertEqual(report.total, len(report.selected) + len(report.skipped_budget)
                         + report.skipped_query_limit + report.unmatched)

    def test_budget_skip_preserves_revision_and_explanation_hides_content(self):
        large = self.memory.save(self.root, '私有正文' * 900, pinned=True, evidence='私有证据')
        small = self.memory.save(self.root, '短资料', pinned=True)
        report = self.memory.preview(self.root, query='私有查询', budget=1000)
        self.assertEqual([item.id for item in report.selected], [small['id']])
        self.assertEqual([(item.id, item.revision) for item in report.skipped_budget], [(large['id'], 1)])
        explanation = json.dumps(report.explain(), ensure_ascii=False)
        for private in ('私有正文', '私有证据', '私有查询', '短资料'):
            self.assertNotIn(private, explanation)
        self.assertEqual(report.explain()['used_chars'], len(report.text))

    def test_reports_are_recomputed_after_record_update(self):
        row = self.memory.save(self.root, '第一版', pinned=True)
        before = self.memory.preview(self.root)
        self.memory.save(self.root, '第二版', pinned=True, memory_id=row['id'], expected_revision=1)
        after = self.memory.preview(self.root)
        self.assertEqual(before.selected[0].revision, 1)
        self.assertEqual(after.selected[0].revision, 2)
        self.assertIn('第二版', after.text)
        self.assertNotIn('第二版', before.text)

    def test_layer_errors_are_explicit_without_leaking_exception_text(self):
        memory = LayeredMemory(self.root / 'home', self.memory)
        path = memory.user.path(self.root)
        path.parent.mkdir(parents=True)
        path.write_bytes(b'private-database-fixture')
        self.memory.save(self.root, '健康项目资料', pinned=True)
        report = memory.preview(self.root, session_id='one')
        self.assertEqual(report.text, memory.context(self.root, session_id='one'))
        layers = {layer['scope']: layer for layer in report.explain()['layers']}
        self.assertEqual(layers['user']['status'], 'unavailable')
        self.assertEqual(layers['user']['error_code'], 'memory/storage-unavailable')
        self.assertEqual(layers['project']['status'], 'ready')
        self.assertNotIn('private-database-fixture', json.dumps(report.explain()))
        self.assertEqual(path.read_bytes(), b'private-database-fixture')

    def test_unbound_layers_are_distinct_from_empty_stores(self):
        memory = LayeredMemory(self.root / 'home', self.memory)
        layers = {layer.scope: layer.status for layer in memory.preview('', session_id='').layers}
        self.assertEqual(layers, {'user': 'ready', 'project': 'unbound', 'session': 'unbound'})
        self.assertFalse(memory.home.exists())

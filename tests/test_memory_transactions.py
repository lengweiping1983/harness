"""验证记忆并发更新和批量导入的事务边界。"""
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harness.app.memory import FORMAT, MAX_MEMORIES, ProjectMemory
from harness.core.errors import HarnessError


class MemoryTransactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.memory = ProjectMemory()

    def test_two_writers_cannot_both_replace_the_same_revision(self):
        initial = self.memory.save(self.root, '原决策')
        ready = threading.Barrier(2)

        def update(content):
            store = ProjectMemory()
            ready.wait(timeout=5)
            try:
                return store.save(self.root, content, memory_id=initial['id'], expected_revision=1)
            except HarnessError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ('决策甲', '决策乙')))
        accepted = [result for result in results if isinstance(result, dict)]
        self.assertEqual(len(accepted), 1)
        self.assertIn('memory/changed', results)
        self.assertEqual(self.memory.list(self.root)[0]['revision'], 2)
        self.assertEqual(self.memory.list(self.root)[0]['content'], accepted[0]['content'])

    def test_import_over_capacity_rolls_back_earlier_rows_in_same_batch(self):
        original = [{'content': f'已有记录 {index}'} for index in range(MAX_MEMORIES - 1)]
        self.memory.import_data(self.root, {'format': FORMAT, 'memories': original})
        before = self.memory.list(self.root)
        with self.assertRaises(HarnessError) as failure:
            self.memory.import_data(self.root, {'format': FORMAT, 'memories': [
                {'content': '本批次第一条'}, {'content': '本批次溢出条目'}]})
        self.assertEqual(failure.exception.code, 'memory/full')
        self.assertEqual(self.memory.list(self.root), before)

    def test_duplicate_update_leaves_both_records_unchanged(self):
        first = self.memory.save(self.root, '已有事实')
        second = self.memory.save(self.root, '另一事实')
        with self.assertRaises(HarnessError) as failure:
            self.memory.save(self.root, first['content'], memory_id=second['id'], expected_revision=1)
        self.assertEqual(failure.exception.code, 'memory/duplicate')
        records = {row['id']: row for row in self.memory.list(self.root)}
        self.assertEqual(records[first['id']], first)
        self.assertEqual(records[second['id']], second)

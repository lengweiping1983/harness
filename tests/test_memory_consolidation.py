"""验证可重试整理、原始资料保留和派生版本失效。"""
import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harness.app.memory import ProjectMemory
from harness.app.memory_consolidation import parse, lineage
from harness.app.memory_sources import PREFIX as FILE_PREFIX
from harness.core.errors import HarnessError


class MemoryConsolidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.memory = ProjectMemory()
        self.first = self.memory.save(self.root, '队列决策一', pinned=True, evidence='原始证据一')
        self.second = self.memory.save(self.root, '队列决策二', pinned=False, evidence='原始证据二')
        self.inputs = [{'id': row['id'], 'revision': row['revision']} for row in (self.first, self.second)]

    def consolidate(self, **kwargs):
        return self.memory.consolidate(self.root, self.inputs, '队列决策的整理摘要', source='chat:fixture', **kwargs)

    def test_selected_summary_suppresses_inputs_and_refills_query_quota(self):
        summary = self.consolidate()['memory']
        for index in range(6):
            self.memory.save(self.root, f'队列补充资料 {index}')
        report = self.memory.preview(self.root, query='队列', budget=20000)
        self.assertEqual(len(report.suppressed), 2)
        self.assertEqual({owner.id for _, owner in report.suppressed}, {summary['id']})
        self.assertEqual(sum(item.reason == 'query' for item in report.selected), 5)
        self.assertEqual(report.total, len(report.selected) + len(report.skipped_budget)
                         + report.skipped_query_limit + report.unmatched + len(report.suppressed))
        self.assertEqual(len(self.memory.list(self.root)), 9)

    def test_budget_rejected_summary_keeps_originals(self):
        self.memory.consolidate(self.root, self.inputs, '队列摘要' * 500)
        report = self.memory.preview(self.root, query='队列', budget=2000)
        self.assertEqual({item.id for item in report.selected}, {self.first['id'], self.second['id']})
        self.assertEqual(report.suppressed, ())

    def test_later_unpinned_summary_replaces_only_when_final_payload_fits(self):
        from unittest.mock import patch
        from harness.app.memory_context import select_context
        summary = self.consolidate(pinned=False)['memory']
        rows = {row['id']: row for row in self.memory.list(self.root)}
        ordered = [rows[self.first['id']], rows[self.second['id']], rows[summary['id']]]
        with patch('harness.app.memory_context.rank', return_value=ordered):
            report = select_context(ordered, context_key=self.memory.context_key, query='队列', budget=20000)
            self.assertEqual([item.id for item in report.selected], [summary['id']])
            self.assertEqual(len(report.suppressed), 2)
            ordered[-1] = {**ordered[-1], 'content': '队列摘要' * 500}
            limited = select_context(ordered, context_key=self.memory.context_key, query='队列', budget=2000)
            self.assertEqual({item.id for item in limited.selected}, {self.first['id'], self.second['id']})
            self.assertEqual(limited.suppressed, ())

    def test_changed_summary_keeps_originals_and_explicit_id_is_never_hidden(self):
        self.consolidate()
        exact = self.memory.preview(self.root, query=self.first['id'])
        self.assertIn(self.first['id'], {item.id for item in exact.selected})
        self.memory.save(self.root, '队列决策更新', memory_id=self.first['id'], expected_revision=2)
        report = self.memory.preview(self.root, query='队列')
        self.assertEqual(report.suppressed, ())
        self.assertIn(self.first['id'], {item.id for item in report.selected})

    def test_nested_coverage_points_to_selected_ancestor(self):
        child = self.consolidate()['memory']
        extra = self.memory.save(self.root, '队列第三项')
        ancestor = self.memory.consolidate(self.root, [
            {'id': child['id'], 'revision': child['revision']},
            {'id': extra['id'], 'revision': extra['revision']}], '队列最终摘要')['memory']
        report = self.memory.preview(self.root, query='队列', budget=20000)
        self.assertEqual([item.id for item in report.selected], [ancestor['id']])
        self.assertEqual(len(report.suppressed), 4)
        self.assertEqual({owner.id for _, owner in report.suppressed}, {ancestor['id']})

    def test_preserves_inputs_and_unpins_atomically(self):
        result = self.consolidate()
        self.assertFalse(result['replayed'])
        rows = {row['id']: row for row in self.memory.list(self.root)}
        self.assertEqual(len(rows), 3)
        self.assertTrue(result['memory']['pinned'])
        for original in (self.first, self.second):
            current = rows[original['id']]
            self.assertFalse(current['pinned'])
            self.assertEqual(current['content'], original['content'])
            self.assertEqual(current['evidence'], original['evidence'])
            self.assertEqual(current['revision'], original['revision'] + int(original['pinned']))
        self.assertEqual(lineage(result['memory'], rows)[0], 'current')

    def test_identical_retry_is_idempotent_including_reordered_inputs(self):
        first = self.consolidate()
        before = self.memory.list(self.root)
        self.inputs.reverse()
        second = self.consolidate()
        self.assertTrue(second['replayed'])
        self.assertEqual(second['memory'], first['memory'])
        self.assertEqual(self.memory.list(self.root), before)

    def test_revision_conflict_and_duplicate_content_do_not_partially_unpin(self):
        for mode in ('conflict', 'duplicate'):
            with self.subTest(mode=mode):
                before = self.memory.list(self.root)
                with self.assertRaises(HarnessError):
                    if mode == 'conflict':
                        self.memory.consolidate(self.root, [self.inputs[0], {**self.inputs[1], 'revision': 7}], '失败摘要')
                    else:
                        self.memory.consolidate(self.root, self.inputs, self.first['content'])
                self.assertEqual(self.memory.list(self.root), before)

    def test_capacity_or_evidence_limit_does_not_change_inputs(self):
        from unittest.mock import patch
        before = self.memory.list(self.root)
        with patch('harness.app.memory.MAX_MEMORIES', 2), self.assertRaises(HarnessError):
            self.consolidate()
        self.assertEqual(self.memory.list(self.root), before)
        with self.assertRaises(HarnessError):
            self.consolidate(evidence='证据' * 490)
        self.assertEqual(self.memory.list(self.root), before)

    def test_modified_summary_is_not_overwritten_by_retry(self):
        result = self.consolidate()
        self.memory.save(self.root, '用户后续修改', memory_id=result['memory']['id'], expected_revision=1)
        before = self.memory.list(self.root)
        with self.assertRaises(HarnessError):
            self.consolidate()
        self.assertEqual(self.memory.list(self.root), before)

    def test_parent_revision_change_invalidates_summary_without_deleting_it(self):
        result = self.consolidate()
        self.memory.save(self.root, '新的队列决策', memory_id=self.first['id'], expected_revision=2)
        report = self.memory.preview(self.root)
        summary = next(item for item in report.selected if item.id == result['memory']['id'])
        self.assertEqual(summary.derivation_status, 'changed')
        self.assertIn('"requires_reverification": true', report.text)
        self.assertEqual(len(self.memory.list(self.root)), 3)
        replay = self.consolidate()
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['derivation'], {'status': 'changed', 'requires_reverification': True})

    def test_source_file_change_is_propagated_through_summary(self):
        path = self.root / 'source.py'
        path.write_bytes(b'original')
        evidence = FILE_PREFIX + json.dumps({'note': '', 'files': [
            {'path': 'source.py', 'version': hashlib.sha256(b'original').hexdigest()[:16]}]})
        self.first = self.memory.save(self.root, '队列决策一', pinned=True, evidence=evidence,
                                      memory_id=self.first['id'], expected_revision=1)
        self.inputs[0]['revision'] = 2
        result = self.consolidate()
        fresh = self.memory.preview(self.root, query='队列')
        self.assertEqual(len(fresh.suppressed), 2)
        path.write_bytes(b'changed')
        row = next(row for row in self.memory.recall(self.root) if row['id'] == result['memory']['id'])
        self.assertEqual(row['derivation']['status'], 'current')
        self.assertEqual(row['source_check']['status'], 'changed')
        self.assertTrue(row['derivation']['requires_reverification'])
        report = self.memory.preview(self.root, query='队列')
        self.assertEqual(report.suppressed, ())
        self.assertIn(self.first['id'], {item.id for item in report.selected})

    def test_simultaneous_identical_requests_create_one_summary(self):
        barrier = threading.Barrier(2)
        def run(_):
            barrier.wait(timeout=5)
            return self.consolidate()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, range(2)))
        self.assertEqual(sorted(row['replayed'] for row in results), [False, True])
        self.assertEqual(len(self.memory.list(self.root)), 3)

    def test_cycle_is_not_treated_as_valid_derivation(self):
        result = self.consolidate()
        summary = result['memory']
        value = parse(summary['evidence'])
        value['inputs'][0] = {'id': summary['id'], 'input_revision': 1, 'revision': 1, 'pinned': False}
        from harness.app.memory_consolidation import PREFIX
        summary['evidence'] = PREFIX + json.dumps(value, separators=(',', ':'))
        rows = {row['id']: row for row in self.memory.list(self.root)}
        rows[summary['id']] = summary
        self.assertEqual(lineage(summary, rows)[0], 'invalid')

    def test_export_import_preserves_ids_revisions_and_current_lineage(self):
        result = self.consolidate()
        target = self.root / 'import-target'
        target.mkdir()
        exported = self.memory.export(self.root)
        self.assertEqual(self.memory.import_data(target, exported)['imported'], 3)
        rows = {row['id']: row for row in self.memory.list(target)}
        self.assertEqual(lineage(rows[result['memory']['id']], rows)[0], 'current')
        for source in exported['memories']:
            self.assertEqual(rows[source['id']]['revision'], source['revision'])
            self.assertEqual(rows[source['id']]['content'], source['content'])
        self.assertEqual(self.memory.import_data(target, exported), {'imported': 0, 'duplicates': 3})

    def test_import_identity_collision_preserves_existing_records_and_rolls_back(self):
        self.consolidate()
        target = self.root / 'import-target'
        target.mkdir()
        existing = self.memory.save(target, self.first['content'], evidence='独立条目')
        before = self.memory.list(target)
        with self.assertRaises(HarnessError):
            self.memory.import_data(target, self.memory.export(self.root))
        self.assertEqual(self.memory.list(target), before)
        self.assertEqual(self.memory.list(target)[0]['id'], existing['id'])

    def test_summary_skipped_by_budget_is_unchecked_instead_of_not_derived(self):
        self.consolidate()
        report = self.memory.preview(self.root, budget=10)
        self.assertEqual(report.text, '')
        self.assertEqual(report.skipped_budget[0].derivation_status, 'unchecked')
        self.assertEqual(report.skipped_budget[0].source_status, 'unchecked')

    def test_import_rejects_noncanonical_ids_without_changing_existing_data(self):
        before = self.memory.list(self.root)
        for identifier in ('../memory', 'mem_invalid', 'x?scope=user', 'custom-id'):
            with self.subTest(identifier=identifier), self.assertRaises(HarnessError):
                self.memory.import_data(self.root, {'format': self.memory.format, 'memories': [
                    {'id': identifier, 'revision': 1, 'content': '输入资料'}]})
            self.assertEqual(self.memory.list(self.root), before)

    def test_source_id_lookup_does_not_return_summary_that_mentions_it(self):
        self.consolidate()
        found = self.memory.recall(self.root, self.first['id'])
        self.assertEqual([row['id'] for row in found], [self.first['id']])
        self.memory.delete(self.root, self.first['id'], 2)
        self.assertEqual(self.memory.recall(self.root, self.first['id']), [])

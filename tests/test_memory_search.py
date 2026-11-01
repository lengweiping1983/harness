"""验证关键词边界和路径身份，保留中文短语召回。"""
import json
import unittest
from pathlib import Path

from harness.app.memory_search import rank


class MemorySearchTests(unittest.TestCase):
    def test_boundary_fixture_preserves_exact_results_except_open_semantic_challenges(self):
        data = json.loads((Path(__file__).parent / 'fixtures/memory_retrieval_boundaries.json').read_text(encoding='utf-8'))
        for case in data['cases']:
            if '挑战' in case['name']:
                continue
            with self.subTest(case=case['name']):
                self.assertEqual([row['id'] for row in rank(data['rows'], case['query'])], case['expected'])

    def test_path_does_not_match_longer_filename_or_parent_prefix(self):
        rows = [{'id': str(index), 'topic': '', 'content': '', 'evidence': path}
                for index, path in enumerate(['src/main.py', 'src/main.py.bak', 'other/src/main.py', 'src/main.pytest'])]
        self.assertEqual([row['id'] for row in rank(rows, 'src/main.py')], ['0'])

    def test_stable_ties_and_casefold_preserve_original_order(self):
        rows = [{'id': str(index), 'topic': 'API', 'content': '', 'evidence': ''} for index in range(3)]
        self.assertEqual(rank(rows, 'api'), rows)
        self.assertEqual(rank(rows, ''), rows)

    def test_rank_fusion_prefers_consensus_without_duplicate_query_weight(self):
        from harness.app.memory_search import rank_queries
        rows = [{'id': key, 'topic': '', 'content': content, 'evidence': ''} for key, content in
                [('first', 'queue'), ('second', 'recovery'), ('both', 'queue recovery')]]
        selected, matches = rank_queries(rows, ['queue', 'recovery'])
        duplicate, duplicate_matches = rank_queries(rows, ['queue', 'recovery', ' QUEUE '])
        self.assertEqual(selected[0]['id'], 'both')
        self.assertEqual(matches['both'], [0, 1])
        self.assertEqual(selected, duplicate)
        self.assertEqual(matches, duplicate_matches)

    def test_machine_fields_do_not_match_but_notes_and_source_paths_do(self):
        from harness.app.memory_sources import PREFIX as FILE_PREFIX
        from harness.app.memory_consolidation import PREFIX as SUMMARY_PREFIX
        file = {'id': 'file', 'topic': '', 'content': '文件事实', 'evidence': FILE_PREFIX + json.dumps({
            'note': '观察说明', 'files': [{'path': 'src/queue.py', 'version': '0123456789abcdef'}]})}
        summary = {'id': 'summary', 'topic': '', 'content': '整理事实', 'evidence': SUMMARY_PREFIX + json.dumps({
            'note': '整理说明', 'request': 'a' * 64, 'inputs': [
                {'id': 'parent_a', 'input_revision': 1, 'revision': 2, 'pinned': True},
                {'id': 'parent_b', 'input_revision': 1, 'revision': 1, 'pinned': False}]})}
        rows = [file, summary]
        for query in ('harness', 'inputs', 'revision', 'request', '0123456789abcdef', 'parent_a', 'a' * 64):
            with self.subTest(query=query):
                self.assertEqual(rank(rows, query), [])
        for query, expected in [('观察说明', file), ('src/queue.py', file), ('整理说明', summary)]:
            self.assertEqual(rank(rows, query), [expected])

    def test_invalid_known_evidence_keeps_body_searchable_without_indexing_raw_fields(self):
        row = {'id': 'broken', 'topic': '', 'content': '保留事实',
               'evidence': 'harness:file-sources:v1\n{"note":"invalid-only","files":[]}'}
        self.assertEqual(rank([row], 'invalid-only'), [])
        self.assertEqual(rank([row], '保留事实'), [row])
        plain = {**row, 'evidence': 'ordinary source note'}
        self.assertEqual(rank([plain], 'ordinary'), [plain])

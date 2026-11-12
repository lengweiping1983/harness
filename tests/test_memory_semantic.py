"""本地候选入口默认关闭，精确身份及作用域不被向量相似度覆盖。"""
import tempfile
import unittest
import re
from harness.i18n import tr
from pathlib import Path

from harness.app.memory import ProjectMemory
from harness.app.layered_memory import LayeredMemory
from harness.app.memory_semantic import LocalMemoryCandidates, fuse_candidates, candidate_window
from harness.tools.builtin.memory_tools import build_layered_memory_tools
from harness.tools.registry import ToolRegistry, ToolRunContext
from harness.llm.types import ToolUseBlock


class LocalCandidateTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_invalid_input_fails_before_worker_or_database_creation(self):
        from unittest.mock import patch
        from harness.core.errors import HarnessError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ProjectMemory()
            provider = LocalMemoryCandidates(root / 'missing-model')
            cases = [(' ', {}), (None, {}), ('x' * 501, {}), ('natural question', {'limit': 11}),
                     ('natural question', {'offset': 200}), ('natural question', {'offset': True}),
                     ('natural question', {'snapshot': 'bad'}), ('API', {'offset': 200, 'snapshot': 'a' * 64})]
            with patch.object(store, 'list') as read, patch('harness.app.memory_semantic_worker.SemanticWorker') as worker:
                for query, options in cases:
                    with self.subTest(query=query, options=options):
                        with self.assertRaises((HarnessError, ValueError)):
                            await provider.search_async(store, root, query, **options)
                with self.assertRaises(TypeError):
                    await provider.search_async(store, root, 'natural question', path='untrusted')
                read.assert_not_called()
                worker.assert_not_called()
            self.assertIsNone(provider._worker)
            self.assertFalse((root / '.harness').exists())

    def test_scan_pages_cover_rows_and_reject_mutation_or_cross_session_snapshot(self):
        from harness.core.errors import HarnessError
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        memory = LayeredMemory(root / 'home', ProjectMemory())
        store = memory.store('session', session_id='one')
        rows = [{'id': str(index), 'revision': 1, 'topic': '', 'content': str(index)} for index in range(450)]
        first, page = candidate_window(store, root, rows, 0, None)
        self.assertEqual(len(first), 200)
        self.assertEqual(page['next_offset'], 200)
        second, middle = candidate_window(store, root, rows, 200, page['snapshot'])
        third, last = candidate_window(store, root, rows, 400, page['snapshot'])
        self.assertEqual(first + second + third, rows)
        self.assertEqual(middle['remaining'], 50)
        self.assertIsNone(last['next_offset'])
        self.assertEqual(last['remaining'], 0)
        self.assertEqual(page['omitted'], 250)
        variants = [list(reversed(rows)), [{**rows[0], 'revision': 2}, *rows[1:]],
                    [{**rows[0], 'content': 'changed-with-same-revision'}, *rows[1:]], rows[:-1]]
        for changed in variants:
            with self.assertRaises(HarnessError) as error:
                candidate_window(store, root, changed, 200, page['snapshot'])
            self.assertEqual(error.exception.code, 'memory/scan-changed')
        with self.assertRaises(HarnessError):
            candidate_window(memory.store('session', session_id='two'), root, rows, 200, page['snapshot'])

    def test_invalid_scan_parameters_fail_before_database_read(self):
        from unittest.mock import patch
        from harness.core.errors import HarnessError
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        store, provider = ProjectMemory(), LocalMemoryCandidates(root / 'missing')
        for values in ({'offset': True}, {'offset': -1}, {'offset': 1000}, {'offset': 200},
                       {'snapshot': 'bad'}, {'snapshot': 1}):
            with self.subTest(values=values), patch.object(store, 'list') as read:
                with self.assertRaises(HarnessError):
                    provider.search(store, root, 'natural question', **values)
                read.assert_not_called()
        with patch.object(store, 'list') as read:
            with self.assertRaises(HarnessError):
                provider.search(store, root, 'exact_symbol', offset=200, snapshot='a' * 64)
            read.assert_not_called()

    def test_fusion_explains_keyword_only_semantic_only_and_consensus(self):
        rows = [{'id': name, 'topic': '', 'content': content, 'evidence': ''} for name, content in
                [('keyword', 'queue'), ('semantic', 'unrelated'), ('both', 'queue'), ('absent', 'none')]]
        result = fuse_candidates(rows, 'queue', [0.1, 0.8, 0.9, 0.1], 10)
        by_id = {row['id']: explanation for row, explanation in result}
        self.assertEqual(result[0][0]['id'], 'both')
        self.assertEqual(by_id['keyword']['match'], 'keyword')
        self.assertIsNone(by_id['keyword']['semantic_rank'])
        self.assertEqual(by_id['semantic']['match'], 'semantic')
        self.assertIsNone(by_id['semantic']['keyword_rank'])
        self.assertEqual(by_id['both']['keyword_rank'], 2)
        self.assertEqual(by_id['both']['semantic_rank'], 1)
        self.assertAlmostEqual(by_id['both']['fusion_score'], 1 / 62 + 1 / 61)
        self.assertNotIn('absent', by_id)
        for scores in ([0.1], [float('nan')] * 4, [10 ** 4000] * 4):
            with self.assertRaises(ValueError) as caught:
                fuse_candidates(rows, 'queue', scores, 5)
            self.assertEqual(str(caught.exception), tr('python.memory.semantic.similarities'))

    def test_vector_cache_tracks_scope_revision_and_full_content_without_caching_text(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        provider = LocalMemoryCandidates(root / 'missing')
        memory = LayeredMemory(root / 'home', ProjectMemory())
        class Model:
            def __init__(self):
                self.encoded = 0
            def passage_embed(self, texts):
                self.encoded += len(texts)
                return [[1.0] + [0.0] * 383 for _ in texts]
        model = Model()
        row = {'id': 'fixed', 'revision': 1, 'topic': 'private-topic', 'content': 'private-body' * 40}
        stores = [memory.store('session', session_id=identity) for identity in ('one', 'two')]
        self.assertEqual(provider._embeddings(stores[0], root, [row], model)[1], 1)
        self.assertEqual(provider._embeddings(stores[0], root, [row], model)[1], 0)
        self.assertEqual(provider._embeddings(stores[1], root, [row], model)[1], 1)
        self.assertEqual(provider._embeddings(stores[0], root, [{**row, 'revision': 2}], model)[1], 1)
        self.assertEqual(provider._embeddings(stores[0], root, [{**row, 'content': row['content'] + 'suffix'}], model)[1], 1)
        self.assertNotIn('private-body', repr(provider._vectors))
        self.assertNotIn('private-topic', repr(provider._vectors))
        self.assertEqual(model.encoded, 4)

    def test_cache_eviction_is_bounded_and_invalid_batches_do_not_partially_populate(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        provider = LocalMemoryCandidates(root / 'missing')
        class Model:
            def passage_embed(self, texts):
                return [[1.0] + [0.0] * 383 for _ in texts]
        store, model = ProjectMemory(), Model()
        rows = [{'id': str(index), 'revision': 1, 'topic': '', 'content': str(index)} for index in range(600)]
        for offset in range(0, 600, 100):
            provider._embeddings(store, root, rows[offset:offset + 100], model)
        self.assertEqual(len(provider._vectors), 512)
        self.assertEqual(provider._embeddings(store, root, [rows[-1]], model)[1], 0)
        self.assertEqual(provider._embeddings(store, root, [rows[0]], model)[1], 1)
        before = list(provider._vectors)
        class InvalidModel:
            def passage_embed(self, texts):
                return [[1.0] + [0.0] * 383, [float('nan')] * 384]
        with self.assertRaises(ValueError):
            provider._embeddings(store, root, [{**rows[0], 'revision': 8}, {**rows[1], 'revision': 8}], InvalidModel())
        class OversizedModel:
            def passage_embed(self, texts):
                return [[10 ** 4000] + [0.0] * 383]
        with self.assertRaises(ValueError) as caught:
            provider._embeddings(store, root, [{**rows[0], 'revision': 9}], OversizedModel())
        self.assertEqual(str(caught.exception), tr('python.memory.semantic.document_vector'))
        self.assertEqual(list(provider._vectors), before)

    def test_environment_configuration_and_explicit_disable(self):
        import os
        from unittest.mock import patch
        from harness import HarnessConfig
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        with patch.dict(os.environ, {'HARNESS_MEMORY_MODEL_CACHE': str(root / 'models')}):
            config = HarnessConfig.from_env(home=root / 'state')
            self.assertEqual(config.memory_model_cache, root / 'models')
            self.assertIsNone(HarnessConfig.from_env(home=root / 'state', memory_model_cache=None).memory_model_cache)
            self.assertNotIn('memory_model_cache', config.to_dict())
        self.assertFalse((root / 'models').exists())
        for invalid in (' ', True, 12, []):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                HarnessConfig(memory_model_cache=invalid)

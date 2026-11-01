"""语义检索双语错误覆盖真实所属进程，不下载模型或使用用户记忆。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.app.memory import ProjectMemory
from harness.app.memory_semantic import LocalMemoryCandidates, fuse_candidates
from harness.app.memory_semantic_worker import SemanticWorker, frame, receive
from harness.core.errors import HarnessError
from harness.core.language import current_language
from harness.i18n import tr
from tests.test_memory_semantic_worker import FixtureWorker


class SemanticLanguageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = ProjectMemory()

    async def test_real_worker_uses_each_request_language_without_recreating_or_writing_memory(self):
        self.store.save(self.root, 'private source 原文')
        original = self.store.path(self.root).read_bytes()
        provider = LocalMemoryCandidates(self.root / 'missing-cache')
        self.addAsyncCleanup(provider.aclose)
        process = None
        for language in ('en', 'zh-CN', 'en'):
            token = current_language.set(language)
            try:
                with self.assertRaises(HarnessError) as failure:
                    await provider.search_async(self.store, self.root, 'natural language query')
                self.assertEqual(failure.exception.message, tr('python.memory.semantic.cache_missing'))
                self.assertEqual(failure.exception.code, 'memory/semantic-worker')
                if language == 'zh-CN':
                    self.assertIn('缓存缺失', failure.exception.message)
                else:
                    self.assertIn('cache is missing', failure.exception.message)
                if process is not None:
                    self.assertIs(provider._worker._process, process)
                process = provider._worker._process
            finally:
                current_language.reset(token)
        self.assertEqual(self.store.path(self.root).read_bytes(), original)
        self.assertFalse(provider.cache_dir.exists())
        await provider.aclose()
        self.assertTrue(process.cleanup_confirmed)

    async def test_local_validation_and_closed_sync_async_entries_follow_runtime_language(self):
        provider = LocalMemoryCandidates(self.root / 'missing')
        for language in ('en', 'zh-CN'):
            token = current_language.set(language)
            try:
                with self.assertRaises(ValueError) as failure:
                    provider.search(self.store, self.root, ' ')
                self.assertEqual(str(failure.exception), tr('python.memory.semantic.query'))
                with self.assertRaises(ValueError) as failure:
                    fuse_candidates([], 'query', [1], 1)
                self.assertEqual(str(failure.exception), tr('python.memory.semantic.similarities'))
            finally:
                current_language.reset(token)
        await provider.aclose()
        for language in ('en', 'zh-CN'):
            token = current_language.set(language)
            try:
                with self.assertRaises(RuntimeError) as failure:
                    provider.search(self.store, self.root, 'exact')
                self.assertEqual(str(failure.exception), tr('python.memory.semantic.closed'))
                with self.assertRaises(RuntimeError) as failure:
                    await provider.search_async(self.store, self.root, 'exact')
                self.assertEqual(str(failure.exception), tr('python.memory.semantic.closed'))
            finally:
                current_language.reset(token)
        self.assertIsNone(provider._worker)
        self.assertFalse(self.store.path(self.root).exists())

    async def test_protocol_errors_reap_process_in_both_languages(self):
        for language in ('en', 'zh-CN'):
            worker = FixtureWorker(self.root)
            self.addAsyncCleanup(worker.aclose)
            token = current_language.set(language)
            try:
                for query, key in [('wrong identity', 'identity'), ('oversized frame', 'frame_size')]:
                    with self.assertRaises(ValueError) as failure:
                        await worker.search(self.store, self.root, query)
                    self.assertEqual(str(failure.exception), tr('python.memory.semantic.' + key))
                    self.assertIsNone(worker._process)
                original = worker._argv
                def invalid_token(port):
                    argv = original(port)
                    argv[2] = argv[2].replace("os.environ['HARNESS_MEMORY_WORKER_KEY']", "'无效认证'")
                    return argv
                worker._argv = invalid_token
                with self.assertRaises(ValueError) as failure:
                    await worker.search(self.store, self.root, 'natural question')
                self.assertEqual(str(failure.exception), tr('python.memory.semantic.authentication'))
                self.assertIsNone(worker._process)
            finally:
                current_language.reset(token)

    async def test_frame_bounds_and_external_diagnostics_preserve_detail(self):
        for language in ('en', 'zh-CN'):
            token = current_language.set(language)
            try:
                with patch('harness.app.memory_semantic_worker.MAX_FRAME', 1):
                    with self.assertRaises(ValueError) as failure:
                        frame({'message': 'payload'})
                    self.assertEqual(str(failure.exception), tr('python.memory.semantic.frame_bound'))
                    reader = asyncio.StreamReader()
                    reader.feed_data(b'\x00\x00\x00\x02')
                    with self.assertRaises(ValueError) as failure:
                        await receive(reader)
                    self.assertEqual(str(failure.exception), tr('python.memory.semantic.frame_size'))
                worker = SemanticWorker(self.root / 'cache')
                worker._tail.extend('original native diagnostic 原始诊断'.encode())
                self.assertEqual(worker._failure().message, tr('python.memory.semantic.exited', detail='original native diagnostic 原始诊断'))
            finally:
                current_language.reset(token)

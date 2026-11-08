"""模型能力缓存严格读取及学习提交边界。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.core.language import current_language
from harness.llm.capabilities import CapabilityRegistry


class CapabilityCacheTests(unittest.TestCase):
    def test_roundtrip_and_failed_write_do_not_commit_learning(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry = CapabilityRegistry(root, discover=False)
            self.assertFalse((root / 'model-capabilities.json').exists())
            registry.learn('https://example.test/v1', 'model', context_window=200)
            before = (root / 'model-capabilities.json').read_bytes()
            with patch('harness.llm.capabilities.atomic_json', side_effect=OSError('fixture')):
                with self.assertRaises(OSError):
                    registry.learn('https://example.test/v1', 'model', context_window=100)
            self.assertEqual(registry.peek('https://example.test/v1', 'model')['context_window'], 200)
            self.assertEqual((root / 'model-capabilities.json').read_bytes(), before)
            registry.learn('https://example.test/v1', 'model', context_window=100)
            self.assertEqual(CapabilityRegistry(root).peek('https://example.test/v1', 'model')['context_window'], 100)

    def test_damage_is_rejected_without_rewrite_or_partial_learning(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry = CapabilityRegistry(root, discover=False)
            path = root / 'model-capabilities.json'
            frames = [[], {'version': True, 'models': {}, 'learned': {}}, {'version': 1},
                      *[{'version': 1, 'models': {'url|m': row}, 'learned': {}} for row in
                        [[], {'context_window': True}, {'context_window': -1}, {'fetched_at': float('nan')}, {'fetched_at': 10 ** 400},
                         {'supported_parameters': [1]}, {'reasoning_mandatory': 1}, {'obsolete': 1}]]]
            self.assertEqual(CapabilityRegistry._clean({'fetched_at': 10 ** 400})['fetched_at'], 0)
            for content in [b'\xff', b'{', *[json.dumps(row).encode() for row in frames]]:
                path.write_bytes(content)
                for operation in (lambda: CapabilityRegistry(root), lambda: registry.learn('url', 'm', context_window=100)):
                    with self.subTest(content=content), self.assertRaises(ValueError):
                        operation()
                    self.assertEqual(path.read_bytes(), content)
                    self.assertEqual(registry.learned, {})

    def test_current_metadata_and_language_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'model-capabilities.json'
            row = {'context_window': None, 'max_output_tokens': 100, 'fetched_at': 0,
                   'supported_parameters': ['tools'], 'supported_efforts': [], 'supports_reasoning_budget': False}
            path.write_text(json.dumps({'version': 1, 'models': {'url|m': row}, 'learned': {}}))
            self.assertEqual(CapabilityRegistry(root).cache['url|m'], row)
            path.write_text('{}')
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language), self.assertRaises(ValueError) as error:
                    CapabilityRegistry(root)
                self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in str(error.exception)), language == 'zh-CN')


class CapabilityRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_persistence_does_not_publish_or_throttle_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = CapabilityRegistry(Path(folder))
            with patch.object(registry, '_fetch', return_value={'context_window': 100}), patch('harness.llm.capabilities.atomic_json', side_effect=OSError('fixture')):
                with self.assertRaises(OSError):
                    await registry.resolve('url', 'm')
            self.assertEqual(registry.cache, {})
            self.assertEqual(registry.checked, {})
            self.assertEqual(registry.pending, {})
            with patch.object(registry, '_fetch', return_value={'context_window': 100}):
                self.assertEqual((await registry.resolve('url', 'm'))['context_window'], 100)
            self.assertEqual(CapabilityRegistry(Path(folder)).peek('url', 'm')['context_window'], 100)

    async def test_refresh_preserves_external_damage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry = CapabilityRegistry(root)
            path = root / 'model-capabilities.json'
            path.write_bytes(b'corrupt')
            with patch.object(registry, '_fetch', return_value={'context_window': 100}):
                with self.assertRaises(ValueError):
                    await registry.resolve('url', 'm')
            self.assertEqual(path.read_bytes(), b'corrupt')
            self.assertEqual(registry.cache, {})

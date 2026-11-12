"""学习缓存损坏、写入失败和双语错误的回归。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.core.language import current_language
from harness.llm.context_windows import load_context_windows, save_context_windows


class ContextWindowCacheTests(unittest.TestCase):
    def test_missing_empty_and_current_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cache.json'
            self.assertEqual(load_context_windows(path), {})
            self.assertFalse(path.exists())
            for windows in ({}, {'https://example.test|model': 1, 'url|large': 10_000_000}):
                save_context_windows(path, windows)
                self.assertEqual(load_context_windows(path), windows)

    def test_damage_is_rejected_and_preserved_on_read_and_save(self):
        invalid = [b'\xff', b'{', b'[]', b'{"version":true,"windows":{}}',
                   b'{"version":2,"windows":{}}', b'{"version":1}',
                   *[json.dumps({'version': 1, 'windows': value}).encode() for value in
                     [[], {'broken': 5}, {'|model': 5}, {'url|': 5}, {'url|m': True}, {'url|m': 0}, {'url|m': 10_000_001}]]]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cache.json'
            for content in invalid:
                path.write_bytes(content)
                for operation in (lambda: load_context_windows(path), lambda: save_context_windows(path, {'url|m': 100})):
                    with self.subTest(content=content), self.assertRaises(ValueError):
                        operation()
                    self.assertEqual(path.read_bytes(), content)

    def test_write_and_read_errors_propagate_without_replacing_original(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cache.json'
            save_context_windows(path, {'url|m': 100})
            before = path.read_bytes()
            with patch.object(Path, 'replace', side_effect=OSError('fixture write failure')):
                with self.assertRaises(OSError):
                    save_context_windows(path, {'url|m': 200})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob('*.tmp')), [])
            with patch.object(Path, 'read_text', side_effect=PermissionError('fixture denied')):
                with self.assertRaises(PermissionError):
                    load_context_windows(path)
            with self.assertRaises(ValueError):
                save_context_windows(path, {'url|m': False})
            self.assertEqual(path.read_bytes(), before)

    def test_error_uses_current_language(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cache.json'
            path.write_text('{}')
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language), self.assertRaises(ValueError) as error:
                    load_context_windows(path)
                self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in str(error.exception)), language == 'zh-CN')

    def test_host_save_failure_keeps_memory_and_allows_same_value_retry(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from harness.app.harness import Harness
        with tempfile.TemporaryDirectory() as folder:
            host = SimpleNamespace(config=SimpleNamespace(base_url='url', model='m', home=Path(folder)),
                                   adapter=SimpleNamespace(capabilities=SimpleNamespace(learn=Mock())),
                                   _learned_windows={'url|m': 100})
            with patch('harness.app.harness.save_context_windows', side_effect=OSError('fixture')):
                with self.assertRaises(OSError):
                    Harness._on_context_window_learned(host, 200)
            self.assertEqual(host._learned_windows, {'url|m': 100})
            host.adapter.capabilities.learn.assert_not_called()
            Harness._on_context_window_learned(host, 200)
            self.assertEqual(load_context_windows(Path(folder) / 'context-windows.json'), {'url|m': 200})
            self.assertEqual(host._learned_windows, {'url|m': 200})
            host.adapter.capabilities.learn.assert_called_once_with('url', 'm', context_window=200)

    def test_agent_notification_failure_keeps_window_and_retries_same_value(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from harness.core.agent import AgentLoop
        callback = Mock(side_effect=[OSError('fixture'), None])
        agent = SimpleNamespace(context_window=100, compact_threshold=50,
                                context_working_budget=80, output_reserve=10,
                                on_context_window_learned=callback)
        with self.assertRaises(OSError):
            AgentLoop._learn_context_window(agent, 200, 'url', 'm')
        self.assertEqual((agent.context_window, agent.compact_threshold), (100, 50))
        AgentLoop._learn_context_window(agent, 200, 'url', 'm')
        self.assertEqual(agent.context_window, 200)
        self.assertEqual(callback.call_count, 2)
        AgentLoop._learn_context_window(agent, 200, 'url', 'm')
        self.assertEqual(callback.call_count, 2)

"""独立 MCP 入口按本机配置绑定语言，调用线程原上下文不被覆盖。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness.core.language import current_language
from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.i18n import resources
from harness.mcp_server.server import create_server


class ExecutorLanguageBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_initialization_catalog_and_call_use_configured_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for language in ('en', 'zh-CN', 'en'):
                config = ExecutionConfig(root / 'state', {'fixture': ProjectConfig(root)}, language=language)
                captured = {}
                def server(*args, **kwargs):
                    captured.update(kwargs)
                    return SimpleNamespace()
                seen = []
                async def call(*args):
                    seen.append(current_language.get())
                    return {'is_error': False}
                runtime = SimpleNamespace(call=call, registry=SimpleNamespace(names=lambda: []))
                with patch('harness.mcp_server.server.Server', side_effect=server):
                    create_server(config, runtime_holder={'runtime': runtime}, manage_runtime=False)
                self.assertEqual(captured['instructions'], resources(language)['execution.mcp.instructions'])
                outer = 'zh-CN' if language == 'en' else 'en'
                with current_language.set(outer):
                    await captured['on_list_tools'](None, None)
                    await captured['on_call_tool'](None, SimpleNamespace(name='harness_context', arguments={}))
                    self.assertEqual(current_language.get(), outer)
                self.assertEqual(seen, [language])

    async def test_config_language_roundtrip_and_invalid_value_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'project').mkdir()
            path = root / 'config.json'
            for language in ('en', 'zh-CN', 'auto'):
                data = {'home': 'state', 'projects': {'fixture': {'root': 'project'}}, 'language': language}
                path.write_text(json.dumps(data))
                original = path.read_bytes()
                self.assertEqual(ExecutionConfig.load(path).language, language)
                self.assertEqual(path.read_bytes(), original)
            data['language'] = 'unsupported'
            path.write_text(json.dumps(data))
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                ExecutionConfig.load(path)
            self.assertEqual(path.read_bytes(), original)

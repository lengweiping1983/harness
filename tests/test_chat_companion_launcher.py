import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import start_chat_companion


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class ChatCompanionLauncherTests(unittest.TestCase):
    """启动入口只复用同配置的现有桥，不接管未知端口。"""

    def _config(self, folder: Path):
        home = folder / 'state'
        home.mkdir()
        return SimpleNamespace(home=home, language='zh-CN')

    def _key(self, config):
        (config.home / 'companion-key.json').write_text(json.dumps({'token': 'a' * 64}), encoding='utf-8')

    def test_missing_key_is_read_only_and_reports_not_started(self):
        with TemporaryDirectory() as folder:
            config = self._config(Path(folder))
            self.assertEqual(start_chat_companion.probe(config, 8877), ('not_started', None))
            self.assertEqual(list(config.home.iterdir()), [])

    def test_invalid_key_is_rejected_without_replacement(self):
        with TemporaryDirectory() as folder:
            config = self._config(Path(folder))
            key = config.home / 'companion-key.json'
            key.write_text('{"token":"bad"}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '配对码文件无效'):
                start_chat_companion.probe(config, 8877)
            self.assertEqual(key.read_text(encoding='utf-8'), '{"token":"bad"}')

    def test_existing_matching_bridge_is_reused(self):
        with TemporaryDirectory() as folder:
            config = self._config(Path(folder))
            self._key(config)
            response = _Response(b'{"protocol":"harness-chat-companion","protocol_version":2,"executor":"ready"}')
            with patch.object(start_chat_companion, 'urlopen', return_value=response), patch('harness.mcp_server.companion.serve') as serve:
                self.assertEqual(start_chat_companion.run(config, 8877, 'local', False, Path(folder) / 'executor.json'), 0)
                serve.assert_not_called()

    def test_foreign_listener_is_never_stopped(self):
        with TemporaryDirectory() as folder:
            config = self._config(Path(folder))
            self._key(config)
            response = _Response(b'{"protocol":"other","protocol_version":1}')
            with patch.object(start_chat_companion, 'urlopen', return_value=response), patch('harness.mcp_server.companion.serve') as serve:
                self.assertEqual(start_chat_companion.run(config, 8877, 'local', False, Path(folder) / 'executor.json'), 2)
                serve.assert_not_called()

    def test_old_protocol_is_not_reused_or_replaced(self):
        with TemporaryDirectory() as folder:
            config = self._config(Path(folder))
            self._key(config)
            response = _Response(b'{"protocol":"harness-chat-companion","protocol_version":1,"executor":"ready"}')
            with patch.object(start_chat_companion, 'urlopen', return_value=response), patch('harness.mcp_server.companion.serve') as serve:
                self.assertEqual(start_chat_companion.run(config, 8877, 'local', False, Path(folder) / 'executor.json'), 2)
                serve.assert_not_called()


if __name__ == '__main__':
    unittest.main()

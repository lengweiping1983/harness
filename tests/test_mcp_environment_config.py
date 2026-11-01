"""环境MCP声明只接受当前数组格式，损坏输入不转换或静默丢弃。"""
import copy
import json
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from harness.app.config import HarnessConfig, _load_mcp_servers
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.mcp_client import McpError, McpServerConfig


VALID = {'server_name': 'fixture', 'command': 'fixture-command'}
INVALID_FIELDS = [
    {'args': 'private-secret'}, {'args': [7]}, {'env': {'TOKEN': 7}}, {'headers': {'Token': False}},
    {'server_name': 7}, {'server_name': 'bad\n'}, {'command': 7}, {'cwd': False}, {'url': 7},
    {'tool_call_timeout_ms': True}, {'tool_call_timeout_ms': -1}, {'startup_timeout_ms': '10'},
    {'enabled_tools': 'read'}, {'disabled_tools': [7]}, {'fail_on_startup_error': 1},
    {'reconnect_enabled': 'false'}, {'reconnect_initial_delay_ms': -1},
    {'reconnect_max_delay_ms': None}, {'reconnect_max_attempts': True},
]


class McpEnvironmentConfigTests(unittest.TestCase):
    def test_empty_and_current_stdio_http_declarations_preserve_values(self):
        payload = [dict(VALID, args=['--token', 'Original 原文'], env={'TOKEN': 'private-secret'},
            cwd='/Original/原文', enabled_tools=[], disabled_tools=['write'], startup_timeout_ms=1,
            reconnect_max_attempts=0),
            {'server_name': 'http', 'transport': 'streamable-http', 'url': 'https://example.invalid/mcp',
             'headers': {'Authorization': 'Bearer private-secret'}}]
        original = copy.deepcopy(payload)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                self.assertEqual(_load_mcp_servers(None), [])
                self.assertEqual(_load_mcp_servers(''), [])
                self.assertEqual(_load_mcp_servers('[]'), [])
                servers = _load_mcp_servers(json.dumps(payload))
                self.assertEqual([server.server_name for server in servers], ['fixture', 'http'])
                for value, server in zip(payload, servers):
                    result = asdict(server)
                    self.assertEqual({key: result[key] for key in value}, value)
        self.assertEqual(payload, original)

    def test_legacy_wrappers_invalid_json_and_mixed_rows_fail_as_a_whole(self):
        cases = ['broken', ' ', '{}', 'null', 'true', json.dumps({'mcpServers': [VALID]}),
            json.dumps({'servers': [VALID]}), json.dumps([VALID, 7]), json.dumps([VALID, {}]),
            json.dumps([VALID, VALID]), json.dumps([{**VALID, 'unknown': 'private-secret'}])]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for raw in cases:
                    with self.subTest(language=language, raw=raw), self.assertRaises(ValueError) as caught:
                        _load_mcp_servers(raw)
                    self.assertNotIn('private-secret', str(caught.exception))
                    self.assertIn('HARNESS_MCP_SERVERS', str(caught.exception))
                    self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in str(caught.exception)), language == 'zh-CN')

    def test_invalid_field_types_do_not_coerce_or_drop_server(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for fields in INVALID_FIELDS:
                    payload = [dict(VALID, server_name='first'), {**VALID, **fields}]
                    raw = json.dumps(payload)
                    with self.subTest(language=language, fields=fields), self.assertRaises(ValueError) as caught:
                        _load_mcp_servers(raw)
                    self.assertEqual(str(caught.exception), tr('config.mcp.invalid_server', index=2))
                    self.assertNotIn('private-secret', str(caught.exception))
                    self.assertEqual(json.loads(raw), payload)

    def test_direct_config_uses_same_strict_field_validation(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for fields in INVALID_FIELDS:
                    with self.subTest(language=language, fields=fields), self.assertRaises(McpError):
                        McpServerConfig(**{**VALID, **fields})

    def test_file_errors_preserve_original_and_do_not_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mcp.json'
            for original in (b'broken private-secret', b'\xff', json.dumps([VALID, {'args': 'old'}]).encode()):
                path.write_bytes(original)
                for language in ('en', 'zh-CN', 'en'):
                    with current_language.set(language), self.assertRaises(ValueError):
                        _load_mcp_servers('@' + str(path))
                    self.assertEqual(path.read_bytes(), original)
            path.write_text(json.dumps([VALID]), encoding='utf-8')
            self.assertEqual(_load_mcp_servers('@' + str(path))[0].command, VALID['command'])
            with patch.object(Path, 'read_text', side_effect=PermissionError('private-secret')):
                with self.assertRaises(ValueError) as caught:
                    _load_mcp_servers('@' + str(path))
                self.assertNotIn('private-secret', str(caught.exception))
            path.unlink()
            with self.assertRaises(ValueError):
                _load_mcp_servers('@' + str(path))

    def test_from_env_rejects_invalid_declaration_before_creating_runtime_files(self):
        with tempfile.TemporaryDirectory() as directory:
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language), patch.dict(os.environ,
                    {'HARNESS_HOME': directory, 'HARNESS_MCP_SERVERS': json.dumps([VALID, {'args': 'private-secret'}])}, clear=True):
                    with self.assertRaises(ValueError):
                        HarnessConfig.from_env()
                    self.assertEqual(list(Path(directory).iterdir()), [])

    def test_semantic_errors_use_complete_current_language_templates(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for fields, key, params in (
                    ({'transport': 'obsolete'}, 'invalid_transport', {'value': repr('obsolete')}),
                    ({'server_name': 'bad\n'}, 'invalid_name', {'value': repr('bad\n')}),
                    ({'command': ' '}, 'command_required', {'server': 'fixture'}),
                    ({'transport': 'streamable-http', 'url': ' '}, 'url_required', {'server': 'fixture'}),
                ):
                    with self.subTest(language=language, fields=fields), self.assertRaises(McpError) as caught:
                        McpServerConfig(**{**VALID, **fields})
                    self.assertEqual(str(caught.exception), tr('mcp.config.' + key, **params))

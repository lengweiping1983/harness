"""托管MCP启动先校验当前配置快照，不转换损坏命令或覆盖原件。"""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from harness.app.mcp_errors import McpServiceError
from harness.app.mcp_service import McpService
from harness.core.errors import ConfigError
from harness.core.language import current_language
from harness.execution.config import ExecutionConfig
from harness.i18n import tr


class McpServiceConfigIntegrityTests(unittest.TestCase):
    def payload(self, root):
        return {'home': str(root / 'private'), 'language': 'en', 'projects': {'original': {
            'root': str(root / 'project'), 'principals': ['local', 'OriginalPrincipal'],
            'writable': True, 'commands': True, 'commits': True,
            'verification': {'verify': ['python', 'Original script 原文.py', 'argument with spaces']}}}}

    def test_invalid_snapshot_is_preserved_before_dependencies_or_processes(self):
        for all_projects in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project = root / 'project'
                project.mkdir()
                valid = self.payload(root)
                mutations = []
                for invalid in ('echo unsafe', {'command': 'echo'}, [7], [], True):
                    item = deepcopy(valid)
                    item['projects']['original']['verification']['verify'] = invalid
                    mutations.append(json.dumps(item).encode())
                for item in ([], {'version': 0, **valid}, {**valid, 'projects': []}, {**valid, 'language': 'invalid'}):
                    mutations.append(json.dumps(item).encode())
                mutations.extend([b'{', b'\xff'])
                for index, raw in enumerate(mutations):
                    with self.subTest(all_projects=all_projects, case=index):
                        service = McpService(root / str(index))
                        service.home.mkdir(parents=True)
                        config = service.home / 'config.json'
                        config.write_bytes(raw)
                        before = {p.name: p.read_bytes() for p in service.home.iterdir()}
                        try:
                            with patch.object(service, '_python') as python, patch.object(service, '_private_home') as private, patch.object(service, '_cloudflared') as tunnel, patch.object(service, '_spawn') as spawn, current_language.set('en'):
                                service.start(project, workspace_registry=str(root / 'registry.json') if all_projects else None)
                                service._thread.join(timeout=3)
                                self.assertFalse(service._thread.is_alive())
                                python.assert_not_called()
                                private.assert_not_called()
                                tunnel.assert_not_called()
                                spawn.assert_not_called()
                            self.assertIsInstance(service._state['error'], McpServiceError)
                            for language in ('en', 'zh-CN', 'en'):
                                with current_language.set(language):
                                    state = service.snapshot()
                                    self.assertEqual(state['status'], 'failed')
                                    self.assertEqual(state['error'], tr('ui.mcpService.error.invalidSavedConfig'))
                            self.assertEqual({p.name: p.read_bytes() for p in service.home.iterdir()}, before)
                        finally:
                            service.stop()

    def test_valid_commands_and_all_project_backup_preserve_original_parameters(self):
        for all_projects in (False, True):
            with self.subTest(all_projects=all_projects), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project = root / 'project'
                project.mkdir()
                service = McpService(root / 'state')
                service.home.mkdir(parents=True)
                config = service.home / 'config.json'
                previous = self.payload(root)
                raw = json.dumps(previous, indent=2, ensure_ascii=False).encode()
                config.write_bytes(raw)
                try:
                    with patch.object(service, '_python', return_value='fixture-python'), patch.object(service, '_private_home'), patch.object(service, '_cloudflared', return_value='fixture-tunnel'), patch.object(service, '_spawn', side_effect=RuntimeError('Stop before any real process')) as spawn:
                        service._launch(project.resolve(), 'selected', str(root / 'registry.json') if all_projects else None)
                    spawn.assert_called_once()
                    current = ExecutionConfig.load(config)
                    self.assertEqual(current.skill_home, service.home.parent.resolve())
                    key = 'original' if all_projects else 'selected'
                    self.assertEqual(current.projects[key].verification['verify'], ('python', 'Original script 原文.py', 'argument with spaces'))
                    if all_projects:
                        self.assertEqual(current.projects[key].principals, ('local', 'OriginalPrincipal'))
                        self.assertEqual((service.home / 'config.before-all-projects.json').read_bytes(), raw)
                        self.assertEqual(json.loads(config.read_text(encoding='utf-8'))['projects'], previous['projects'])
                    else:
                        self.assertFalse((service.home / 'config.before-all-projects.json').exists())
                finally:
                    service.stop()

    def test_file_loader_and_snapshot_parser_share_current_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'project').mkdir()
            data = self.payload(root)
            data['projects']['original']['root'] = 'project'
            original = deepcopy(data)
            path = root / 'config.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            self.assertEqual(ExecutionConfig.load(path), ExecutionConfig.from_dict(data, base=root))
            self.assertEqual(data, original)
            data['projects']['original']['verification']['verify'] = 'legacy string'
            with self.assertRaises(ConfigError):
                ExecutionConfig.from_dict(data, base=root)
            self.assertEqual(ExecutionConfig.load(path).projects['original'].verification['verify'], tuple(original['projects']['original']['verification']['verify']))

    def test_atomic_config_failure_and_external_change_preserve_original_file(self):
        import os
        for stage in ('sync', 'replace', 'external'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                service = McpService(root)
                path = root/'config.json'
                original = b'{"original":true}'
                path.write_bytes(original)
                failure = OSError('Original config write failure')
                fsync = os.fsync
                def change(descriptor):
                    fsync(descriptor)
                    path.write_bytes(b'{"external":true}')
                target = 'harness.app.mcp_service.os.'+('replace' if stage=='replace' else 'fsync')
                try:
                    with patch(target, side_effect=change if stage=='external' else failure):
                        with self.assertRaises(McpServiceError if stage=='external' else OSError) as caught:
                            service._save_config(path, {'updated':True}, original)
                    if stage=='external':
                        self.assertEqual(caught.exception.key, 'ui.mcpService.error.configChanged')
                        self.assertEqual(path.read_bytes(), b'{"external":true}')
                    else:
                        self.assertIs(caught.exception, failure)
                        self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(list(root.glob('.mcp-config-*')), [])
                finally:
                    service.stop()

    def test_atomic_config_creates_and_updates_exact_current_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = McpService(root)
            path = root/'config.json'
            try:
                service._save_config(path, {'original':'原文'}, None)
                original = path.read_bytes()
                service._save_config(path, {'updated':'原文'}, original)
                self.assertEqual(json.loads(path.read_bytes()), {'updated':'原文'})
                self.assertEqual(list(root.glob('.mcp-config-*')), [])
            finally:
                service.stop()

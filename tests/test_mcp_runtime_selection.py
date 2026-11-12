"""MCP 运行必需依赖属于当前宿主，不隐式改用历史虚拟环境。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.app.mcp_service import McpService
from harness.core.language import current_language
from harness.i18n import tr


class McpRuntimeSelectionTests(unittest.TestCase):
    def test_current_interpreter_is_used_only_when_both_required_dependencies_exist(self):
        service = McpService.__new__(McpService)
        with patch('harness.app.mcp_service.importlib.util.find_spec', return_value=object()) as spec:
            self.assertEqual(service._python(), sys.executable)
            self.assertEqual([call.args[0] for call in spec.call_args_list], ['mcp', 'uvicorn'])

    def test_missing_dependency_does_not_use_or_modify_old_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = McpService.__new__(McpService)
            service.source = root
            old = root / '.run' / 'mcp-venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            old.parent.mkdir(parents=True)
            old.write_bytes(b'old runtime retained')
            for language in ('en', 'zh-CN'):
                token = current_language.set(language)
                try:
                    for missing in ('mcp', 'uvicorn'):
                        with patch('harness.app.mcp_service.importlib.util.find_spec', side_effect=lambda name: None if name == missing else object()):
                            with self.assertRaises(RuntimeError) as failure:
                                service._python()
                            self.assertEqual(str(failure.exception), tr('ui.mcpService.error.dependencies'))
                            self.assertEqual(old.read_bytes(), b'old runtime retained')
                finally:
                    current_language.reset(token)

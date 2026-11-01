"""使用本机已有解释器核对真实输出、退出码及所属临时脚本回收。"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.core.language import current_language
from harness.tools.builtin.code_tools import build_code_tools
from harness.tools.registry import ToolRunContext


class CodeInterpreterExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_available_interpreters_preserve_literal_output_and_cleanup_success_and_failure(self):
        tool = build_code_tools(None)[0]
        scripts = {
            'python': 'import sys\nprint("Original 原文 {name} $(literal)")\nsys.exit({status})\n',
            'python3': 'import sys\nprint("Original 原文 {name} $(literal)")\nsys.exit({status})\n',
            'node': 'console.log("Original 原文 {name} $(literal)");process.exit({status});\n',
            'nodejs': 'console.log("Original 原文 {name} $(literal)");process.exit({status});\n',
            'bash': "printf '%s\\n' 'Original 原文 {name} $(literal)'\nexit {status}\n",
            'sh': "printf '%s\\n' 'Original 原文 {name} $(literal)'\nexit {status}\n",
        }
        mkstemp = tempfile.mkstemp
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for alias, script in scripts.items():
                interpreter = 'node' if alias == 'nodejs' else 'bash' if alias == 'sh' else alias
                if alias != 'python' and shutil.which(interpreter) is None:
                    with self.subTest(alias=alias):
                        self.skipTest('Interpreter is not installed: '+interpreter)
                    continue
                for language in ('en', 'zh-CN', 'en'):
                    for status in (0, 3):
                        with self.subTest(alias=alias, language=language, status=status):
                            paths = []
                            def temporary(**kwargs):
                                descriptor, path = mkstemp(**kwargs, dir=root)
                                paths.append(Path(path))
                                return descriptor, path
                            context = ToolRunContext(call_id='fixture', session_id='fixture', cwd=str(root), turn=1, step=1)
                            with current_language.set(language), patch('harness.tools.builtin.code_tools.tempfile.mkstemp', side_effect=temporary):
                                value = await tool.execute({'language':alias, 'code':script.replace('{status}', str(status))}, context)
                            self.assertEqual(value['stdout'], 'Original 原文 {name} $(literal)\n')
                            self.assertEqual(value['stderr'], '')
                            self.assertEqual(value['exit_code'], status)
                            self.assertEqual(value['error'], status != 0)
                            self.assertEqual(value['language'], alias)
                            self.assertEqual(value['execution_state'], 'exited')
                            self.assertEqual(len(paths), 1)
                            self.assertFalse(paths[0].exists())
            self.assertEqual(list(root.iterdir()), [])

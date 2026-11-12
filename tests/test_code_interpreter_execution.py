"""使用本机已有解释器核对真实输出、退出码及所属临时脚本回收。"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.core.language import current_language
from harness.tools.builtin.code_tools import _which, build_code_tools
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
                if alias not in ('python', 'python3') and _which(interpreter) is None:
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
                            newline = os.linesep if alias in ('python', 'python3') else '\n'
                            self.assertEqual(value['stdout'], 'Original 原文 {name} $(literal)' + newline)
                            self.assertEqual(value['stderr'], '')
                            self.assertEqual(value['exit_code'], status)
                            self.assertEqual(value['error'], status != 0)
                            self.assertEqual(value['language'], alias)
                            self.assertEqual(value['execution_state'], 'exited')
                            self.assertEqual(len(paths), 1)
                            self.assertFalse(paths[0].exists())
            self.assertEqual(list(root.iterdir()), [])

    async def test_explicit_mixed_line_endings_are_preserved_on_both_output_channels(self):
        """显式字节输出不能被平台断言或产品解码归一化。"""
        with tempfile.TemporaryDirectory() as directory:
            raw = 'Original 原文\r\nLF\nCR\rTail'
            script = 'import sys\nraw = ' + repr(raw.encode('utf-8')) + '\nsys.stdout.buffer.write(raw)\nsys.stderr.buffer.write(raw)\n'
            context = ToolRunContext(call_id='fixture', session_id='fixture', cwd=directory, turn=1, step=1)
            result = await build_code_tools(None)[0].execute({'language': 'python', 'code': script}, context)
            self.assertEqual(result['stdout'], raw)
            self.assertEqual(result['stderr'], raw)
            self.assertEqual(result['exit_code'], 0)

"""真实 bash/pwsh 出口共用退出信封，保留解码错误、原文及完整溢写日志。"""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from harness.core.language import current_language
from harness.tools import command_output
from harness.tools.builtin.pwsh_tools import build_pwsh_tools
from harness.tools.builtin.shell_tools import build_shell_tools
from harness.tools.registry import ToolRunContext


class ExitedCommandResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_tools_keep_exit_code_and_encoding_failures_independent(self):
        for language in ('en', 'zh-CN', 'en'):
            for code in (0, 7):
                for raw in (b'Original output', b'Original\xffinvalid'):
                    results = []
                    for kind, tool, collector in (
                        ('bash', build_shell_tools()[0], 'harness.tools.builtin.shell_tools.collect_process'),
                        ('pwsh', build_pwsh_tools()[0], 'harness.tools.builtin.pwsh_tools._collect'),
                    ):
                        with self.subTest(language=language, code=code, raw=raw, kind=kind), tempfile.TemporaryDirectory() as folder:
                            ctx = ToolRunContext('call', 'session', folder, 1, 1)
                            with current_language.set(language), ExitStack() as stack:
                                stack.enter_context(patch('asyncio.create_subprocess_exec', new=AsyncMock()))
                                stack.enter_context(patch('asyncio.create_subprocess_shell', new=AsyncMock()))
                                stack.enter_context(patch('harness.tools.builtin.pwsh_tools._resolve_powershell', return_value=('fixture', True)))
                                collect = stack.enter_context(patch(collector, new=AsyncMock(return_value=(raw, '原文 stderr'.encode(), code, False))))
                                result = await tool.execute({'command': 'Original command'}, ctx)
                            collect.assert_awaited_once()
                            self.assertEqual(result['exit_code'], code)
                            self.assertEqual(result['execution_state'], 'exited')
                            self.assertEqual(result['stdout'], 'Original output' if b'\xff' not in raw else 'Original')
                            self.assertEqual(result['stderr'], '原文 stderr')
                            self.assertEqual(result['error'], code != 0 or b'\xff' in raw)
                            self.assertEqual(bool(result.get('encoding_errors')), b'\xff' in raw)
                            results.append(result)
                    self.assertEqual(results[0], results[1])

    def test_shared_exit_result_keeps_full_logs_and_original_input(self):
        for prefix in ('harness-', 'harness-pwsh-'):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as folder:
                decoded = {'stdout': 'head\r\n' + '原文😀' * 3000 + '\r\ntail', 'stderr': 'stderr' * 3000,
                           'output_encoding': 'utf-8'}
                real_create = command_output.tempfile.mkstemp
                with patch.object(command_output.tempfile, 'mkstemp', side_effect=lambda **args: real_create(dir=folder, **args)):
                    result = command_output.exited_command_result(decoded, 0, prefix=prefix, stdout_limit=128, stderr_limit=80)
                self.assertTrue(result['truncated'])
                self.assertLessEqual(len(result['stdout']), 128)
                self.assertLessEqual(len(result['stderr']), 80)
                for key, channel in [('spilled_to', 'stdout'), ('stderr_spilled_to', 'stderr')]:
                    path = Path(result[key])
                    self.assertTrue(path.name.startswith(prefix + channel + '-'))
                    self.assertEqual(path.read_bytes(), decoded[channel].encode('utf-8'))
                self.assertNotIn('exit_code', decoded)
                self.assertFalse(result['error'])

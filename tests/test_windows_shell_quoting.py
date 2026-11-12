"""通过真实 Windows Job 进程验证 cmd 包装和参数边界。"""
import asyncio
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harness.tools.owned_process import spawn_owned
from harness.tools.windows_job import kernel_api
from harness.tools.windows_shell import windows_shell_command
from harness.tools.builtin.shell_tools import build_shell_tools
from harness.tools.registry import ToolRunContext
from harness.app.hooks import ProjectHooks


@unittest.skipUnless(os.name == 'nt', '需要 Windows Job Object 和 cmd.exe')
class WindowsShellQuotingTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, argv, cwd, *, shell=True):
        kernel = kernel_api()
        with patch.object(kernel, 'CreateProcessW', wraps=kernel.CreateProcessW) as create:
            with patch('harness.tools.windows_job.kernel_api', return_value=kernel):
                process = await spawn_owned(argv, cwd=str(cwd), env=dict(os.environ), shell=shell)
        try:
            async def read_all(stream):
                chunks = []
                while chunk := await stream.read(4096):
                    chunks.append(chunk)
                return b''.join(chunks)

            stdout, stderr, code = await asyncio.wait_for(asyncio.gather(
                read_all(process.stdout), read_all(process.stderr), process.wait()), 15)
            self.assertEqual(code, 0, stderr.decode(errors='replace'))
            if shell:
                command = create.call_args.args[1].value
                executable, native = windows_shell_command(argv[0], os.environ)
                self.assertEqual(create.call_args.args[0], sys.executable)
                self.assertTrue(command.endswith(subprocess.list2cmdline([executable, native])), command)
                self.assertTrue(native.endswith(f' /d /s /c "{argv[0]}"'), native)
            else:
                self.assertIsNone(create.call_args.args[0])
                self.assertEqual(create.call_args.args[1].value, subprocess.list2cmdline(argv))
            return stdout.decode('utf-8').strip()
        finally:
            await process.stop_owned()
            process.close()

    async def test_quoted_executable_and_literal_metacharacters(self):
        with tempfile.TemporaryDirectory(prefix='harness shell ') as folder:
            script = Path(folder) / 'argument probe.py'
            script.write_text('import json, sys; print(json.dumps(sys.argv[1:]))', encoding='utf-8')
            for value in ['two words', 'a&echo UNEXPECTED', 'a|echo UNEXPECTED', 'a>unexpected.txt', '(group)']:
                with self.subTest(value=value):
                    command = f'"{sys.executable}" "{script}" "{value}"'
                    output = await self.execute([command], folder)
                    self.assertEqual(json.loads(output), [value])
                    self.assertFalse((Path(folder) / 'unexpected.txt').exists())

    async def test_shell_operators_and_quoted_redirection(self):
        with tempfile.TemporaryDirectory(prefix='harness shell ') as folder:
            output = await self.execute(
                ['(echo first&echo second)>"result file.txt" && type "result file.txt" | findstr second'], folder)
            self.assertEqual(output, 'second')

    async def test_non_shell_argument_boundaries(self):
        with tempfile.TemporaryDirectory(prefix='harness shell ') as folder:
            values = ['two words', 'a&echo UNEXPECTED', 'embedded"quote', 'trailing\\']
            output = await self.execute(
                [sys.executable, '-c', 'import json, sys; print(json.dumps(sys.argv[1:]))', *values],
                folder, shell=False)
            self.assertEqual(json.loads(output), values)


@unittest.skipUnless(os.name == 'nt', '需要 Windows cmd.exe')
class WindowsShellRouteTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, route, owned, folder, command, timeout=10):
        ctx = ToolRunContext('shell-probe', 'shell-session', str(folder), 0, 0)
        if route == 'bash':
            tool = build_shell_tools(owned_processes=owned)[0]
            return await tool.execute({'command': command, 'timeout': timeout}, ctx)
        # 直接验证命令执行边界，不创建或改写用户的钩子信任存储。
        hooks = object.__new__(ProjectHooks)
        hooks.harness = SimpleNamespace(config=SimpleNamespace(enable_owned_processes=owned), jobs=None)
        return await hooks._command({'command': command, 'timeout': timeout, 'label': 'probe'},
                                    {'probe': 'stdin-ok'}, ctx, Path(folder))

    async def test_default_dir_and_chcp_dir_emit_utf8_without_replay(self):
        original_codepage = ctypes.windll.kernel32.GetConsoleOutputCP()
        with tempfile.TemporaryDirectory(prefix='harness utf8 ') as folder:
            root = Path(folder)
            (root / '原文目录').mkdir()
            (root / '原文文件.txt').write_bytes(b'fixture')
            script = root / 'probe.py'
            script.write_text("from pathlib import Path;import sys;p=Path('counter');p.write_bytes(p.read_bytes()+b'x' if p.exists() else b'x');print('原文😀');sys.exit(7)", encoding='utf-8')
            for route in ('bash', 'hook'):
                for owned in (False, True):
                    for command in ('dir /a', 'chcp 65001 >nul && dir /b /a'):
                        with self.subTest(route=route, owned=owned, command=command):
                            value = await self.invoke(route, owned, folder, command)
                            self.assertEqual(value['exit_code'], 0, value)
                            self.assertFalse(value.get('encoding_errors'), value)
                            self.assertIn('原文目录', value['stdout'])
                            self.assertIn('原文文件.txt', value['stdout'])
                    command = f'dir /b /a & "{sys.executable}" "{script}"'
                    value = await self.invoke(route, owned, folder, command)
                    self.assertEqual(value['exit_code'], 7, value)
                    self.assertFalse(value.get('encoding_errors'), value)
                    self.assertIn('原文😀', value['stdout'])
            self.assertEqual((root / 'counter').read_bytes(), b'xxxx')
            self.assertEqual(ctypes.windll.kernel32.GetConsoleOutputCP(), original_codepage)

    async def test_rule_one_window_is_closed_in_all_routes(self):
        with tempfile.TemporaryDirectory(prefix='harness rules ') as folder:
            root = Path(folder)
            marker = root / 'executed.txt'
            (root / 'a b.bat').write_text('@echo off\necho executed>executed.txt\necho QUOTED-OK\n', encoding='ascii')
            for route in ('bash', 'hook'):
                for owned in (False, True):
                    with self.subTest(route=route, owned=owned):
                        result = await self.invoke(route, owned, folder, 'a b.bat')
                        self.assertNotEqual(result['exit_code'], 0, result)
                        self.assertFalse(marker.exists())
                        result = await self.invoke(route, owned, folder, '"a b.bat"')
                        self.assertEqual(result['exit_code'], 0, result)
                        self.assertIn('QUOTED-OK', result['stdout'])
                        self.assertTrue(marker.exists())
                        marker.unlink()

    async def test_literal_arguments_and_hook_stdin(self):
        with tempfile.TemporaryDirectory(prefix='harness routes ') as folder:
            root = Path(folder)
            script = root / 'argument probe.py'
            script.write_text('import json,sys; print(json.dumps(sys.argv[1:])); '
                              'print(sys.stdin.read()); sys.exit(7)', encoding='utf-8')
            command = f'"{sys.executable}" "{script}" "literal&value>unexpected.txt"'
            for route in ('bash', 'hook'):
                for owned in (False, True):
                    with self.subTest(route=route, owned=owned):
                        result = await self.invoke(route, owned, folder, command)
                        self.assertEqual(result['exit_code'], 7, result)
                        self.assertEqual(json.loads(result['stdout'].splitlines()[0]), ['literal&value>unexpected.txt'])
                        if route == 'hook':
                            self.assertIn('stdin-ok', result['stdout'])
                        self.assertFalse((root / 'unexpected.txt').exists())

    async def test_fallback_timeout_cleans_command_tree(self):
        # 手动清理代替 with：命令树已确认终止后，被杀进程的工作目录句柄仍可能有
        # 毫秒级释放滞后；限时重试避免与操作系统收尾竞态误报失败。
        folder = tempfile.mkdtemp(prefix='harness timeout ')
        try:
            script = Path(folder) / 'wait.py'
            script.write_text('import time; time.sleep(30)', encoding='utf-8')
            for route in ('bash', 'hook'):
                with self.subTest(route=route):
                    result = await self.invoke(route, False, folder,
                                               f'"{sys.executable}" "{script}"', timeout=.5)
                    self.assertTrue(result['timed_out'], result)
        finally:
            import shutil
            for attempt in range(6):
                try:
                    shutil.rmtree(folder)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    await asyncio.sleep(.25 * (attempt + 1))

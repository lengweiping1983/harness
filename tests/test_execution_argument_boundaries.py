"""执行工具直接入口与当前schema一致，非法参数不进入解释器或进程。"""
import unittest
from unittest.mock import patch
from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.tools.registry import ToolRunContext
from harness.tools.builtin.code_tools import build_code_tools
from harness.tools.builtin.shell_tools import build_shell_tools
from harness.tools.builtin.pwsh_tools import build_pwsh_tools


class ExecutionArgumentBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for target in ('harness.tools.builtin.code_tools.start_process',
                       'harness.tools.builtin.shell_tools.start_process',
                       'harness.tools.processes.start_process'):
            guard = patch(target, side_effect=AssertionError('非法参数不得启动进程'))
            guard.start()
            self.addCleanup(guard.stop)

    async def test_invalid_timeouts_reject_before_platform_or_process_access(self):
        ctx = ToolRunContext('fixture', 'owner', '.', 1, 1)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for builder, args, key, upper, resolver in (
                    (lambda: build_code_tools(None), {'code': 'Original 原文'}, 'timeout', 900, 'harness.tools.builtin.code_tools._resolve_python'),
                    (build_shell_tools, {'command': 'Original 原文'}, 'timeout', 900, 'harness.tools.builtin.shell_tools._build_child_env'),
                    (build_pwsh_tools, {'command': 'Original 原文'}, 'timeoutMs', 900000, 'harness.tools.builtin.pwsh_tools._resolve_powershell'),
                ):
                    tool = builder()[0]
                    for value in (None, True, False, '1', [], {}, 0, -1, float('inf'), float('nan'), 10**400, upper+1):
                        with self.subTest(language=language, tool=tool.schema.name, value=value), patch(resolver) as platform:
                            with self.assertRaises(ToolExecutionError) as caught:
                                await tool.execute({**args, key: value}, ctx)
                            self.assertEqual(any('\u4e00' <= char <= '\u9fff' for char in caught.exception.message), language == 'zh-CN')
                            platform.assert_not_called()

    async def test_nonstring_code_and_commands_are_never_coerced(self):
        class Original:
            def __str__(self):
                raise AssertionError('不得隐式转换待执行内容')
        ctx = ToolRunContext('fixture', 'owner', '.', 1, 1)
        for builder, key, resolver in (
            (lambda: build_code_tools(None), 'code', 'harness.tools.builtin.code_tools._resolve_python'),
            (build_shell_tools, 'command', 'harness.tools.builtin.shell_tools._build_child_env'),
            (build_pwsh_tools, 'command', 'harness.tools.builtin.pwsh_tools._resolve_powershell'),
        ):
            tool = builder()[0]
            for value in (None, True, 42, [], {}, Original(), '', ' '):
                with self.subTest(tool=tool.schema.name, value=type(value).__name__), patch(resolver) as platform:
                    with self.assertRaises(ToolExecutionError):
                        await tool.execute({key: value}, ctx)
                    platform.assert_not_called()


class ExecutionValidArgumentsTests(unittest.IsolatedAsyncioTestCase):
    async def test_shell_keeps_command_and_exact_timeout_boundaries(self):
        from unittest.mock import AsyncMock
        original = "  printf 'Original 原文'  "
        ctx = ToolRunContext('fixture', 'owner', '.', 1, 1)
        for timeout in (0.001, 900):
            process = object()
            with patch('harness.tools.builtin.shell_tools.asyncio.create_subprocess_shell', AsyncMock(return_value=process)) as launch, \
                 patch('harness.tools.builtin.shell_tools.asyncio.create_subprocess_exec', AsyncMock(return_value=process)) as launch_windows, \
                 patch('harness.tools.builtin.shell_tools.windows_shell_command', return_value=('fixture-shell', original)), \
                 patch('harness.tools.builtin.shell_tools.collect_process', AsyncMock(return_value=(b'original', b'', 0, False))) as collect:
                result = await build_shell_tools()[0].execute({'command': original, 'timeout': timeout}, ctx)
                self.assertEqual(launch.call_args.args[0] if launch.called else launch_windows.call_args.args[-1], original)
                self.assertEqual(collect.call_args.args[:2], (process, timeout))
                self.assertEqual(result['exit_code'], 0)

    async def test_pwsh_millisecond_boundaries_do_not_clamp_to_one_hundred_ms(self):
        from unittest.mock import AsyncMock
        original = "  Write-Output 'Original 原文'  "
        ctx = ToolRunContext('fixture', 'owner', '.', 1, 1)
        for timeout in (1, 900000):
            process = object()
            with patch('harness.tools.builtin.pwsh_tools._resolve_powershell', return_value=('fixture-pwsh', True)), \
                 patch('harness.tools.builtin.pwsh_tools._launch', AsyncMock(return_value=process)) as launch, \
                 patch('harness.tools.builtin.pwsh_tools._collect', AsyncMock(return_value=(b'original', b'', 0, False))) as collect:
                result = await build_pwsh_tools()[0].execute({'command': original, 'timeoutMs': timeout}, ctx)
                self.assertEqual(collect.call_args.args[:2], (process, timeout / 1000))
                self.assertEqual(launch.await_count, 1)
                self.assertEqual(result['exit_code'], 0)

    async def test_code_keeps_script_bytes_and_exact_timeout_boundaries(self):
        from pathlib import Path
        from unittest.mock import AsyncMock
        original = "  # Original 原文\nprint('Original')\n"
        ctx = ToolRunContext('fixture', 'owner', '.', 1, 1)
        for timeout in (0.001, 900):
            paths = []
            process = object()
            async def create(*argv, **kwargs):
                path = Path(argv[-1])
                self.assertEqual(path.read_bytes(), original.encode('utf-8'))
                paths.append(path)
                return process
            with patch('harness.tools.builtin.code_tools.asyncio.create_subprocess_exec', side_effect=create), \
                 patch('harness.tools.builtin.code_tools.collect_process', AsyncMock(return_value=(b'original', b'', 0, False))) as collect:
                result = await build_code_tools(None)[0].execute({'code': original, 'timeout': timeout}, ctx)
                self.assertEqual(collect.call_args.args[:2], (process, timeout))
                self.assertEqual(result['exit_code'], 0)
            self.assertEqual(len(paths), 1)
            self.assertFalse(paths[0].exists())


class ExecutionBackgroundArgumentsTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_background_flags_do_not_reach_platform_or_jobs(self):
        from unittest.mock import Mock
        ctx = ToolRunContext('fixture', 'owner', '.', 1, 1)
        for builder, resolver in ((build_shell_tools, 'harness.tools.builtin.shell_tools._build_child_env'),
                                  (build_pwsh_tools, 'harness.tools.builtin.pwsh_tools._resolve_powershell')):
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    registry = Mock()
                    tool = builder(registry=registry)[0]
                    for value in (None, 'false', 0, 1, [], {}):
                        with self.subTest(value=value, language=language), patch(resolver) as platform:
                            with self.assertRaises(ToolExecutionError):
                                await tool.execute({'command': 'Original 原文', 'run_in_background': value}, ctx)
                            platform.assert_not_called()
                            registry.spawn.assert_not_called()
                    with patch(resolver) as platform, self.assertRaises(ToolExecutionError):
                        await builder()[0].execute({'command': 'Original 原文', 'run_in_background': True}, ctx)
                    platform.assert_not_called()

    async def test_explicit_background_with_registry_still_registers_once(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        ctx = ToolRunContext('fixture', 'owner', '.', 1, 1)
        for builder, kind in ((build_shell_tools, 'bash'), (build_pwsh_tools, 'pwsh')):
            registry = SimpleNamespace(assert_ready=Mock(), spawn=AsyncMock(return_value=SimpleNamespace(id='owned', public=lambda: {'id': 'owned'})))
            with patch('harness.tools.builtin.pwsh_tools._resolve_powershell', return_value=('fixture-pwsh', True)):
                result = await builder(registry=registry)[0].execute({'command': 'Original 原文', 'run_in_background': True}, ctx)
            self.assertTrue(result['background'])
            registry.spawn.assert_awaited_once()
            self.assertEqual(registry.spawn.call_args.kwargs['kind'], kind)
            self.assertEqual(registry.spawn.call_args.kwargs['owner'], ctx.session_id)

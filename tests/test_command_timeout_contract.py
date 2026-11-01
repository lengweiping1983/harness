"""注册表的执行期限必须覆盖工具声明的最长等待，内部仍使用本次请求的超时。"""
import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from harness.tools.builtin.code_tools import build_code_tools
from harness.tools.builtin.shell_tools import build_shell_tools
from harness.tools.builtin.pwsh_tools import build_pwsh_tools
from harness.tools.registry import ToolRegistry, ToolRunContext


class CommandTimeoutContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_outer_deadline_does_not_override_valid_long_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = ToolRunContext(call_id='fixture', session_id='fixture', cwd=directory, turn=1, step=1)
            cases = (
                (build_code_tools(directory)[0], {'code': 'pass', 'timeout': 900}, 'harness.tools.builtin.code_tools.collect_process'),
                (build_shell_tools()[0], {'command': 'fixture', 'timeout': 900}, 'harness.tools.builtin.shell_tools.collect_process'),
                (build_pwsh_tools()[0], {'command': 'fixture', 'timeoutMs': 900000}, 'harness.tools.builtin.pwsh_tools._collect'),
            )
            real_wait = asyncio.wait_for
            for tool, args, collector_path in cases:
                deadlines = []
                async def wait(coro, timeout):
                    deadlines.append(timeout)
                    return await real_wait(coro, timeout)
                collector = AsyncMock(return_value=(b'fixture', b'', 0, False))
                with self.subTest(tool=tool.schema.name), \
                     patch('harness.tools.registry.asyncio.wait_for', side_effect=wait), \
                     patch('asyncio.create_subprocess_exec', new=AsyncMock()), \
                     patch('asyncio.create_subprocess_shell', new=AsyncMock()), \
                     patch('harness.tools.builtin.pwsh_tools._resolve_powershell', return_value=('fixture', True)), \
                     patch(collector_path, new=collector):
                    result = await ToolRegistry()._run_with_around(tool, args, ctx, lambda: tool.execute(args, ctx))
                self.assertEqual(collector.await_args.args[1], 900)
                self.assertEqual(len(deadlines), 1)
                self.assertGreater(deadlines[0], 900)
                self.assertEqual(result['exit_code'], 0)

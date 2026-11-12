"""PowerShell前台创建取消后接管迟到句柄并等待清理。"""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from harness.tools.builtin.pwsh_tools import build_pwsh_tools
from harness.tools.processes import ProcessCleanupError
from harness.tools.registry import ToolRunContext


class PwshStartupOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancel_waits_for_late_process_cleanup(self):
        entered, release, stopping, stopped = (asyncio.Event() for _ in range(4))
        process = Mock()
        async def launch(*args):
            entered.set()
            await release.wait()
            return process
        async def cleanup(proc, *args, **kwargs):
            self.assertIs(proc, process)
            self.assertEqual(kwargs['kind'], 'pwsh')
            stopping.set()
            await stopped.wait()
        ctx = ToolRunContext('fixture', 'owner', '.', 1, 1)
        with patch('harness.tools.builtin.pwsh_tools._resolve_powershell', return_value=('fixture-pwsh', True)), \
             patch('harness.tools.builtin.pwsh_tools._launch', side_effect=launch), \
             patch('harness.tools.process_output.cleanup_process', side_effect=cleanup) as clean, \
             patch('harness.tools.builtin.pwsh_tools._collect', new_callable=AsyncMock) as collect:
            task = asyncio.create_task(build_pwsh_tools()[0].execute({'command': 'Original 原文'}, ctx))
            try:
                await entered.wait()
                task.cancel();await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set();await asyncio.wait_for(stopping.wait(), 2)
                task.cancel();await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set();stopped.set()
                result = (await asyncio.gather(task, return_exceptions=True))[0]
            self.assertIsInstance(result, asyncio.CancelledError)
            clean.assert_awaited_once();collect.assert_not_awaited()

    async def test_failed_cleanup_keeps_original_process_cleanup_error(self):
        entered, release = asyncio.Event(), asyncio.Event()
        failure = ProcessCleanupError('Original cleanup 原文')
        async def launch(*args):
            entered.set();await release.wait();return Mock()
        with patch('harness.tools.builtin.pwsh_tools._resolve_powershell', return_value=('fixture-pwsh', True)), \
             patch('harness.tools.builtin.pwsh_tools._launch', side_effect=launch), \
             patch('harness.tools.process_output.cleanup_process', new_callable=AsyncMock, side_effect=failure):
            task = asyncio.create_task(build_pwsh_tools()[0].execute({'command': 'fixture'}, ToolRunContext('fixture', 'owner', '.', 1, 1)))
            await entered.wait();task.cancel();release.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            self.assertIs(result, failure)

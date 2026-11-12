"""命令创建取消须接管迟到句柄，临时脚本保持到所属清理结束。"""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.code_tools import build_code_tools
from harness.tools.builtin.shell_tools import build_shell_tools
from harness.tools.processes import ProcessCleanupError, start_process, stop_process
from harness.llm.types import ToolUseBlock
from harness.tools.registry import ToolRegistry, ToolRunContext


class CommandStartupOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def check_startup(self, kind, owned):
        entered, release, stopping, stopped = [asyncio.Event() for _ in range(4)]
        scripts = []

        async def stop():
            stopping.set()
            await stopped.wait()

        process = SimpleNamespace(stop_owned=AsyncMock(side_effect=stop), close=Mock())
        async def create(*args, **kwargs):
            if kind == 'code':
                argv = args[0] if owned else args
                scripts.append(Path(argv[-1]))
            entered.set()
            await release.wait()
            return process

        tool = (build_code_tools(None, owned_processes=owned) if kind == 'code'
                else build_shell_tools(owned_processes=owned))[0]
        args = {'code': 'Original code 原文'} if kind == 'code' else {'command': 'Original command'}
        context = ToolRunContext(call_id='fixture', session_id='fixture', cwd=None, turn=1, step=1)
        with patch('harness.tools.builtin.code_tools.asyncio.create_subprocess_exec', side_effect=create), \
             patch('harness.tools.builtin.shell_tools.asyncio.create_subprocess_shell', side_effect=create), \
             patch('harness.tools.owned_process.spawn_owned', side_effect=create):
            task = asyncio.create_task(tool.execute(args, context))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                for script in scripts:
                    self.assertTrue(script.exists())
                release.set()
                await asyncio.wait_for(stopping.wait(), 2)
                for _ in range(2):
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    process.close.assert_not_called()
                    for script in scripts:
                        self.assertTrue(script.exists())
            finally:
                release.set()
                stopped.set()
                result = (await asyncio.gather(task, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        process.stop_owned.assert_awaited_once()
        process.close.assert_called_once()
        for script in scripts:
            self.assertFalse(script.exists())

    async def test_code_creation_and_cleanup_survive_repeated_cancellation(self):
        for owned in (False, True):
            with self.subTest(owned=owned):
                await self.check_startup('code', owned)

    async def test_foreground_shell_creation_and_cleanup_survive_repeated_cancellation(self):
        for owned in (False, True):
            with self.subTest(owned=owned):
                await self.check_startup('shell', owned)

    async def test_code_cleanup_failure_preserves_temporary_script_and_error(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language):
                await self.check_cleanup_failure()

    async def check_cleanup_failure(self):
        entered, release = asyncio.Event(), asyncio.Event()
        scripts = []
        failure = OSError('Original cleanup failure')
        process = SimpleNamespace(stop_owned=AsyncMock(side_effect=failure), close=Mock())
        async def create(*argv, **kwargs):
            scripts.append(Path(argv[-1]))
            entered.set()
            await release.wait()
            return process
        tool = build_code_tools(None)[0]
        context = ToolRunContext(call_id='fixture', session_id='fixture', cwd=None, turn=1, step=1)
        with patch('harness.tools.builtin.code_tools.asyncio.create_subprocess_exec', side_effect=create):
            task = asyncio.create_task(tool.execute({'code': 'Original 原文'}, context))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                release.set()
                result = (await asyncio.gather(task, return_exceptions=True))[0]
                self.assertIsInstance(result, ProcessCleanupError)
                self.assertEqual(result.details['temporary_script'], str(scripts[0]))
                self.assertIs(result.__cause__, failure)
                self.assertEqual(result.message, tr('process.startup.script_cleanup_failed', path=str(scripts[0])))
                self.assertTrue(scripts[0].exists())
                self.assertEqual(scripts[0].read_text(encoding='utf-8'), 'Original 原文')
                process.close.assert_not_called()
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                for script in scripts:
                    script.unlink(missing_ok=True)

    @unittest.skipIf(os.name == 'nt', '本场景核对POSIX基础执行器的真实进程组退出')
    async def test_basic_python_late_handle_is_reaped_before_script_removal(self):
        original = asyncio.create_subprocess_exec
        entered, release = asyncio.Event(), asyncio.Event()
        processes, scripts = [], []
        async def create(*argv, **kwargs):
            process = await original(*argv, **kwargs)
            processes.append(process)
            scripts.append(Path(argv[-1]))
            self.assertEqual(await process.stdout.readline(), b'READY\n')
            entered.set()
            await release.wait()
            return process
        tool = build_code_tools(None)[0]
        context = ToolRunContext(call_id='fixture', session_id='fixture', cwd=None, turn=1, step=1)
        with patch('harness.tools.builtin.code_tools.asyncio.create_subprocess_exec', side_effect=create):
            task = asyncio.create_task(tool.execute({'code': "import time\nprint('READY', flush=True)\ntime.sleep(30)"}, context))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertTrue(scripts[0].exists())
                release.set()
                result = (await asyncio.gather(task, return_exceptions=True))[0]
                self.assertIsInstance(result, asyncio.CancelledError)
                self.assertIsNotNone(processes[0].returncode)
                self.assertFalse(scripts[0].exists())
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                for process in processes:
                    if process.returncode is None:
                        await stop_process(process)
                for script in scripts:
                    script.unlink(missing_ok=True)

    async def test_cancelled_owner_does_not_start_a_not_yet_accepted_process(self):
        spawn, stop = AsyncMock(), AsyncMock()
        task = asyncio.create_task(start_process(spawn, stop=stop))
        asyncio.get_running_loop().call_soon(task.cancel)
        result = (await asyncio.gather(task, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        spawn.assert_not_awaited()
        stop.assert_not_awaited()

    async def test_normal_basic_python_still_returns_original_output(self):
        with tempfile.TemporaryDirectory() as directory:
            context = ToolRunContext(call_id='fixture', session_id='fixture', cwd=directory, turn=1, step=1)
            registry = ToolRegistry()
            registry.register(build_code_tools(None)[0])
            result = await registry.execute_call(ToolUseBlock(id='fixture', name='run_code',
                input={'code': "print('Original output 原文')"}), context)
            self.assertFalse(result.is_error)
            self.assertEqual(result.data['stdout'], 'Original output 原文' + os.linesep)
            self.assertEqual(result.data['exit_code'], 0)
            self.assertFalse(result.data['error'])
            self.assertEqual(list(Path(directory).iterdir()), [])

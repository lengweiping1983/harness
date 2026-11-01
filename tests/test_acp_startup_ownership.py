"""ACP 创建取消、迟到句柄与关闭共用同一资源归属。"""
import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.acp.client import AcpClient, AcpError, AcpRunnerConfig
from harness.core.language import current_language
from harness.i18n import tr


class AcpStartupOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_creation_cancel_finishes_and_close_is_repeatable(self):
        # 原缺陷不让事件循环让出执行，必须由独立解释器的外部超时限制。
        script = '''import asyncio,tempfile
from unittest.mock import patch
from harness.acp.client import AcpClient,AcpRunnerConfig
async def cancelled(*args,**kwargs): raise asyncio.CancelledError
async def main():
 with tempfile.TemporaryDirectory() as cwd, patch('harness.acp.client.asyncio.create_subprocess_exec',cancelled):
  client=AcpClient(AcpRunnerConfig('fixture'))
  try: await client.start(cwd)
  except asyncio.CancelledError: pass
  else: raise AssertionError('Cancellation must propagate')
  assert client._closed and not client._ready
  await client.close()
asyncio.run(main())
'''
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', script],
                                         capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_repeated_owner_cancel_waits_for_late_handle_and_cleanup(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as cwd, current_language.set(language):
                entered, created, cleaning, cleaned = (asyncio.Event() for _ in range(4))
                process = SimpleNamespace(stdin=SimpleNamespace(close=Mock()))

                async def spawn(*args, **kwargs):
                    entered.set()
                    await created.wait()
                    return process

                async def stop(proc):
                    self.assertIs(proc, process)
                    cleaning.set()
                    await cleaned.wait()

                client = AcpClient(AcpRunnerConfig('fixture'))
                with patch('harness.acp.client.asyncio.create_subprocess_exec', spawn), patch('harness.acp.client.stop_process', side_effect=stop) as stopped:
                    task = asyncio.create_task(client.start(cwd))
                    await asyncio.wait_for(entered.wait(), 2)
                    task.cancel()
                    await asyncio.sleep(0)
                    task.cancel()
                    self.assertFalse(task.done())
                    created.set()
                    await asyncio.wait_for(cleaning.wait(), 2)
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    cleaned.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 2)
                    await client.close()
                    stopped.assert_awaited_once_with(process)
                process.stdin.close.assert_called_once()
                self.assertFalse(client._ready)

    async def test_close_during_creation_owns_handle_before_reporting_closed(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as cwd, current_language.set(language):
                entered, created = asyncio.Event(), asyncio.Event()
                process = SimpleNamespace(stdin=SimpleNamespace(close=Mock()))

                async def spawn(*args, **kwargs):
                    entered.set()
                    await created.wait()
                    return process

                client = AcpClient(AcpRunnerConfig('fixture'))
                with patch('harness.acp.client.asyncio.create_subprocess_exec', spawn), patch('harness.acp.client.stop_process', new_callable=AsyncMock) as stopped:
                    starting = asyncio.create_task(client.start(cwd))
                    await asyncio.wait_for(entered.wait(), 2)
                    closing = asyncio.create_task(client.close())
                    await asyncio.sleep(0)
                    self.assertFalse(closing.done())
                    created.set()
                    await asyncio.wait_for(closing, 2)
                    with self.assertRaises(AcpError) as caught:
                        await asyncio.wait_for(starting, 2)
                    self.assertEqual(str(caught.exception), tr('acp.client.55374f4cc69b'))
                    stopped.assert_awaited_once_with(process)
                    await client.close()
                process.stdin.close.assert_called_once()

    async def test_spawn_failure_preserves_original_error(self):
        with tempfile.TemporaryDirectory() as cwd:
            client = AcpClient(AcpRunnerConfig('fixture'))
            error = OSError('Original spawn diagnostic 原文')
            with patch('harness.acp.client.asyncio.create_subprocess_exec', new_callable=AsyncMock, side_effect=error), patch('harness.acp.client.stop_process', new_callable=AsyncMock) as stopped:
                with self.assertRaises(OSError) as caught:
                    await client.start(cwd)
                self.assertIs(caught.exception, error)
                await client.close()
                stopped.assert_not_awaited()

    @unittest.skipIf(os.name == 'nt', '真实进程组验收属于POSIX；Windows分支不冒充实机验收')
    async def test_real_late_python_process_is_reaped_before_cancel_returns(self):
        created, release = asyncio.Event(), asyncio.Event()
        actual_spawn = asyncio.create_subprocess_exec
        process = None

        async def spawn(*args, **kwargs):
            nonlocal process
            process = await actual_spawn(*args, **kwargs)
            created.set()
            await release.wait()
            return process

        with tempfile.TemporaryDirectory() as cwd:
            client = AcpClient(AcpRunnerConfig(sys.executable, ('-c', 'import time; time.sleep(60)')))
            task = None
            try:
                with patch('harness.acp.client.asyncio.create_subprocess_exec', spawn):
                    task = asyncio.create_task(client.start(cwd))
                    await asyncio.wait_for(created.wait(), 5)
                    self.assertIsNone(process.returncode)
                    task.cancel()
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 5)
                self.assertIsNotNone(process.returncode)
                self.assertEqual(await process.wait(), process.returncode)
                await client.close()
            finally:
                release.set()
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()

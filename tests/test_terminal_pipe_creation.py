"""Windows管道创建取消后仍接收迟到句柄，回收完成才释放名称。"""
import asyncio
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.tools.builtin.terminal_tools import TerminalSessionService


class TerminalPipeCreationTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancel_waits_for_creation_and_owned_cleanup(self):
        entered, created, stopping, stopped = [asyncio.Event() for _ in range(4)]
        process = SimpleNamespace(pid=123, returncode=None, stdout=None, _transport=Mock())

        async def create(*args, **kwargs):
            entered.set()
            await created.wait()
            return process

        async def stop():
            stopping.set()
            await stopped.wait()
            process.returncode = 0

        process.stop_owned = AsyncMock(side_effect=stop)
        service = TerminalSessionService(backends={'fixture':['fixture']})
        service.handoff_grace = 0
        with tempfile.TemporaryDirectory() as directory, patch('harness.tools.builtin.terminal_tools.sys.platform', 'win32'), patch('harness.tools.windows_conpty.conpty_api', side_effect=ImportError()), patch('harness.tools.windows_shell.windows_console_argv', return_value=['fixture']), patch.object(subprocess, 'CREATE_NEW_PROCESS_GROUP', 1, create=True), patch.object(subprocess, 'CREATE_NO_WINDOW', 2, create=True), patch('harness.tools.builtin.terminal_tools.asyncio.create_subprocess_exec', side_effect=create) as spawn:
            task = asyncio.create_task(service.spawn(owner='owner',kind='fixture',name='same',cwd=directory))
            await entered.wait()
            try:
                for _ in range(2):
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    self.assertIn('same',service._reserved['owner'])
                created.set()
                await asyncio.wait_for(stopping.wait(),2)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                process._transport.close.assert_not_called()
            finally:
                created.set();stopped.set()
                result = (await asyncio.gather(task,return_exceptions=True))[0]
                await service.close_all()
            self.assertIsInstance(result,asyncio.CancelledError)
            process.stop_owned.assert_awaited_once()
            process._transport.close.assert_called_once()
            spawn.assert_awaited_once()
            self.assertEqual(service.list_all(),[])

"""前台终端工具重复取消不能遗落已接受的回收。"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.tools.builtin.terminal_tools import TerminalSessionService, _TerminalSession, build_terminal_tools
from harness.tools.processes import ProcessCleanupError
from harness.tools.registry import ToolRunContext


class TerminalForegroundCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_waits_for_single_close_and_preserves_cleanup_failure(self):
        for fails in (False, True):
            with self.subTest(fails=fails):
                sent, stopping, release = [asyncio.Event() for _ in range(3)]
                process = SimpleNamespace(pid=123, returncode=None, stdout=None, _transport=Mock())

                async def drain():
                    sent.set()

                async def stop():
                    stopping.set()
                    await release.wait()
                    if fails:
                        raise OSError('Original cleanup failure')
                    process.returncode = 0

                process.stdin = SimpleNamespace(write=Mock(), drain=AsyncMock(side_effect=drain))
                process.stop_owned = AsyncMock(side_effect=stop)
                service = TerminalSessionService()
                session = _TerminalSession(session_id='fixture',owner='owner',kind='fixture',name='same',cwd='.',proc=process,config=service.config)
                service._sessions[session.id] = session
                service._reserved['owner'] = {'same'}
                tool = next(tool for tool in build_terminal_tools(service) if tool.schema.name=='terminal_send')
                task = asyncio.create_task(tool.execute({'session_id':'fixture','text':'Original command'},ToolRunContext('call','owner','.',1,1)))
                await sent.wait()
                task.cancel()
                await stopping.wait()
                try:
                    for _ in range(2):
                        task.cancel()
                        await asyncio.sleep(0)
                        self.assertFalse(task.done())
                        process._transport.close.assert_not_called()
                        self.assertIn('same',service._reserved['owner'])
                finally:
                    release.set()
                    result = (await asyncio.gather(task,return_exceptions=True))[0]
                process.stdin.write.assert_called_once()
                process.stop_owned.assert_awaited_once()
                if fails:
                    self.assertIsInstance(result,ProcessCleanupError)
                    self.assertIn('Original cleanup failure',result.details['detail'])
                    self.assertFalse(session.is_closed)
                    self.assertIn('same',service._reserved['owner'])
                    process._transport.close.assert_not_called()
                    process.stop_owned.side_effect = None
                else:
                    self.assertIsInstance(result,asyncio.CancelledError)
                    self.assertTrue(session.is_closed)
                    self.assertNotIn('same',service._reserved['owner'])
                    process._transport.close.assert_called_once()
                await service.close_all()

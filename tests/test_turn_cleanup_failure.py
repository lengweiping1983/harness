"""轮次已开始后初始化失败也须提交结束，不能留下未结束轮次。"""
import asyncio
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import AsyncMock, patch

from harness import Harness, HarnessConfig
from tests.agent_configuration_fixture import RecordingTransport


class TurnCleanupFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_failure_does_not_leave_an_open_turn(self):
        for language in ('en','zh-CN'):
            for cancelled in (False,True):
                with self.subTest(language=language,cancelled=cancelled), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    flags = {item.name:False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
                    transport = RecordingTransport()
                    app = Harness(HarnessConfig(api_key='fixture',home=root/'state',cwd=root,language=language,**flags),transport=transport)
                    try:
                        session = app.create_session()
                        agent = app._agent_for(session)
                        failure = asyncio.CancelledError() if cancelled else RuntimeError('Original initialization {diagnostic}')
                        cleanup_failure = OSError('Original cleanup {diagnostic}')
                        agent.cancel_cleanup = AsyncMock(side_effect=cleanup_failure)
                        with patch('harness.core.agent.TodoProgressReminder.sync',side_effect=failure):
                            with self.assertRaises(BaseExceptionGroup) as caught:
                                await agent.prompt(session,'Original task')
                        self.assertEqual(caught.exception.exceptions, (failure, cleanup_failure))
                        agent.cancel_cleanup.assert_awaited_once()
                        agent.cancel_cleanup = None
                        self.assertIsNone(session.open_turn)
                        self.assertEqual(len([event for event in session.events if event.type=='turn/start']),1)
                        self.assertEqual(len([event for event in session.events if event.type=='turn/end']),1)
                        self.assertEqual(transport.requests,[])
                        next_result = await agent.prompt(session,'Next task')
                        self.assertEqual(next_result.reason,'completed')
                        self.assertEqual(next_result.turn,2)
                        self.assertIsNone(session.open_turn)
                    finally:
                        await app.close()

    async def test_step_limit_cleanup_failure_still_commits_end(self):
        """步数结束后的清理错误也不能留下打开轮次，不重复清理。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            app = Harness(HarnessConfig(api_key='fixture', home=root/'state', cwd=root, **flags), transport=RecordingTransport())
            try:
                session = app.create_session()
                agent = app._agent_for(session)
                agent.max_steps = 0
                failure = OSError('Original limit cleanup failure')
                agent.cancel_cleanup = AsyncMock(side_effect=failure)
                with self.assertRaises(OSError) as caught:
                    await agent.prompt(session, 'Original task')
                self.assertIs(caught.exception, failure)
                agent.cancel_cleanup.assert_awaited_once()
                self.assertIsNone(session.open_turn)
                self.assertEqual(len([e for e in session.events if e.type == 'turn/end']), 1)
            finally:
                await app.close()

    async def test_cleanup_and_end_commit_failures_preserve_every_original(self):
        """写入前后均不重试结束提交，主异常与清理异常不被存储失败遮蔽。"""
        for written in (False, True):
            with self.subTest(written=written), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
                app = Harness(HarnessConfig(api_key='fixture', home=root/'state', cwd=root, **flags), transport=RecordingTransport())
                try:
                    session = app.create_session()
                    agent = app._agent_for(session)
                    primary, cleanup, disk = RuntimeError('Original model'), OSError('Original cleanup'), OSError('Original disk')
                    agent.cancel_cleanup = AsyncMock(side_effect=cleanup)
                    commit = session.commit_event
                    attempts = []
                    def fail_end(event):
                        if event.type != 'turn/end':
                            return commit(event)
                        attempts.append(event)
                        if written:
                            commit(event)
                        raise disk
                    session.commit_event = fail_end
                    with patch('harness.core.agent.TodoProgressReminder.sync', side_effect=primary):
                        with self.assertRaises(BaseExceptionGroup) as caught:
                            await agent.prompt(session, 'Original task')
                    self.assertIs(caught.exception.exceptions[0], primary)
                    self.assertIs(caught.exception.exceptions[1], cleanup)
                    storage = caught.exception.exceptions[2]
                    self.assertIs(storage.__cause__, disk)
                    self.assertEqual(len(attempts), 1)
                    agent.cancel_cleanup.assert_awaited_once()
                finally:
                    from harness.core.errors import PersistenceError
                    with self.assertRaises(PersistenceError):
                        await app.close()

    async def test_unfinished_tool_is_paired_even_when_child_cleanup_fails(self):
        """真实日志中的未准入工具仍配对，但不伪造已执行副作用。"""
        from harness.session.events import AppendOp
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            app = Harness(HarnessConfig(api_key='fixture', home=root/'state', cwd=root, **flags), transport=RecordingTransport())
            try:
                session = app.create_session()
                agent = app._agent_for(session)
                primary, cleanup = RuntimeError('Original model'), OSError('Original cleanup')
                agent.cancel_cleanup = AsyncMock(side_effect=cleanup)
                async def fail_stream(*args, **kwargs):
                    await session.append('assistant/message', {'content': [{'type':'tool_use','id':'original-call','name':'fixture_write','input':{}}]}, surface_op=AppendOp())
                    raise primary
                with patch.object(agent, '_stream_once', side_effect=fail_stream):
                    with self.assertRaises(BaseExceptionGroup):
                        await agent.prompt(session, 'Original task')
                result = [e for e in session.events if e.type == 'tool/result']
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0].data['results'][0]['tool_use_id'], 'original-call')
                self.assertTrue(result[0].data['display']['not_executed'])
                self.assertIsNone(session.open_turn)
            finally:
                await app.close()

    async def test_repeated_cancellation_waits_for_owned_child_cleanup(self):
        """再次取消不能打断已开始的子任务回收，也不能重放该回收。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            app = Harness(HarnessConfig(api_key='fixture', home=root/'state', cwd=root, **flags), transport=RecordingTransport())
            try:
                session = app.create_session()
                agent = app._agent_for(session)
                entered, release = asyncio.Event(), asyncio.Event()
                interrupted = []
                async def cleanup():
                    entered.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        interrupted.append(True)
                        raise
                agent.cancel_cleanup = AsyncMock(side_effect=cleanup)
                with patch('harness.core.agent.TodoProgressReminder.sync', side_effect=asyncio.CancelledError()):
                    task = asyncio.create_task(agent.prompt(session, 'Original task'))
                    await entered.wait()
                    task.cancel()
                    await asyncio.sleep(0)
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    release.set()
                    with self.assertRaises(BaseExceptionGroup):
                        await task
                self.assertEqual(interrupted, [])
                agent.cancel_cleanup.assert_awaited_once()
                self.assertIsNone(session.open_turn)
                self.assertEqual(len([e for e in session.events if e.type == 'turn/end']), 1)
            finally:
                await app.close()

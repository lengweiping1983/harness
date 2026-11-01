"""并行读取取消后等全部已启动工具收尾，重复取消不遗落子任务。"""
import asyncio
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.core.language import current_language
from harness.tools.processes import ProcessCleanupError
from harness.core.todo_initialization import TodoInitialization
from harness.i18n import tr
from harness.llm.types import ToolSchema, ToolUseBlock
from harness.tools.registry import ToolDefinition
from tests.agent_configuration_fixture import RecordingTransport


class ParallelReadCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_tool_entry_resolves_language_for_each_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            app = Harness(HarnessConfig(api_key='fixture', home=root/'state', cwd=root, **flags), transport=RecordingTransport())
            languages = []
            async def read(args, ctx):
                languages.append(ctx.services['language'])
                return {'original': '用户内容 {value}'}
            key = 'python.fs_tools.58f503cb7fbd'
            app.add_tool(ToolDefinition(ToolSchema('fixture_read', '', {'type': 'object', 'properties': {}}, description_key=key), read, effect='read', concurrency_safe=True))
            try:
                session = app.create_session()
                event = await session.start_turn(1)
                agent = app._agent_for(session)
                agent._todo_initialization = TodoInitialization(event.seq)
                for step, language in enumerate(('en', 'zh-CN', 'en'), 1):
                    with current_language.set(language):
                        await agent._execute_tools(session, [ToolUseBlock('call-' + str(step), 'fixture_read', {})], turn=1, step=step)
                        call = next(event for event in session.events_of_type('tool/call')
                                    if event.data['call_id'] == 'call-' + str(step) and event.data['status'] == 'running')
                        self.assertEqual(call.data['description'], tr(key))
                        self.assertIn('用户内容 {value}', str(session.latest('tool/result').data))
                self.assertEqual(languages, ['en', 'zh-CN', 'en'])
                self.assertFalse(hasattr(agent, 'language'))
            finally:
                await app.close()

    async def test_repeated_cancel_waits_for_each_started_reader(self):
        loop = asyncio.get_running_loop()
        diagnostics = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, context: diagnostics.append(context))
        self.addCleanup(loop.set_exception_handler, previous)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {item.name:False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            app = Harness(HarnessConfig(api_key='fixture',home=root/'state',cwd=root,**flags),transport=RecordingTransport())
            started = {name:asyncio.Event() for name in ('first','second')}
            stopping = {name:asyncio.Event() for name in started}
            release = asyncio.Event()
            finished, executions = [], []

            async def read(args,ctx):
                name = args['name']
                executions.append(name)
                started[name].set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopping[name].set()
                    await release.wait()
                    finished.append(name)

            app.add_tool(ToolDefinition(ToolSchema('fixture_read','Fixture read',{'type':'object','properties':{'name':{'type':'string'}},'required':['name']}),read,effect='read',concurrency_safe=True))
            task = None
            try:
                session = app.create_session()
                event = await session.start_turn(1)
                agent = app._agent_for(session)
                agent._todo_initialization = TodoInitialization(event.seq)
                calls = [ToolUseBlock(name,'fixture_read',{'name':name}) for name in started]
                task = asyncio.create_task(agent._execute_tools(session,calls,turn=1,step=1))
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())),2)
                task.cancel()
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in stopping.values())),2)
                for _ in range(2):
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                self.assertEqual(finished,[])
            finally:
                release.set()
                result = (await asyncio.gather(task,return_exceptions=True))[0] if task else None
                await app.close()
            self.assertIsInstance(result,asyncio.CancelledError)
            self.assertCountEqual(finished,['first','second'])
            self.assertCountEqual(executions,['first','second'])
            await asyncio.sleep(0)
            self.assertEqual(diagnostics, [])

    async def test_original_failure_and_sibling_cleanup_failure_remain_visible(self):
        def leaves(error):
            if isinstance(error, BaseExceptionGroup):
                return [leaf for item in error.exceptions for leaf in leaves(item)]
            return [error]

        for language in ('en', 'zh-CN', 'en'):
            for cancel in (False, True):
                with self.subTest(language=language, cancel=cancel), current_language.set(language), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
                    app = Harness(HarnessConfig(api_key='fixture', home=root/'state', cwd=root, **flags), transport=RecordingTransport())
                    started = {name: asyncio.Event() for name in ('first', 'second')}
                    fail, stopping, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
                    original = ProcessCleanupError('Original reader failure')
                    cleanup = ProcessCleanupError('Original sibling cleanup failure')
                    executions = []
                    execution_languages = []

                    async def read(args, ctx):
                        name = args['name']
                        executions.append(name)
                        execution_languages.append(ctx.services['language'])
                        started[name].set()
                        if name == 'first':
                            await fail.wait()
                            raise original
                        try:
                            await asyncio.Event().wait()
                        finally:
                            stopping.set()
                            await release.wait()
                            raise cleanup

                    description_key = 'python.fs_tools.58f503cb7fbd'
                    app.add_tool(ToolDefinition(ToolSchema('fixture_read', 'Fixture read', {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name']}, description_key=description_key), read, effect='read', concurrency_safe=True))
                    task = None
                    try:
                        session = app.create_session()
                        event = await session.start_turn(1)
                        agent = app._agent_for(session)
                        self.assertFalse(hasattr(agent, 'language'), '直接工具入口须在尚未进入模型轮次时仍可用')
                        agent._todo_initialization = TodoInitialization(event.seq)
                        calls = [ToolUseBlock(name, 'fixture_read', {'name': name}) for name in started]
                        task = asyncio.create_task(agent._execute_tools(session, calls, turn=1, step=1))
                        await asyncio.wait_for(asyncio.gather(*(item.wait() for item in started.values())), 2)
                        if cancel:
                            task.cancel()
                        else:
                            fail.set()
                        await asyncio.wait_for(stopping.wait(), 2)
                        for _ in range(2):
                            task.cancel()
                            await asyncio.sleep(0)
                            self.assertFalse(task.done())
                    finally:
                        release.set()
                        result = (await asyncio.gather(task, return_exceptions=True))[0] if task else None
                        await app.close()
                    failures = leaves(result)
                    self.assertIn(cleanup, failures)
                    if cancel:
                        self.assertTrue(any(isinstance(item, asyncio.CancelledError) for item in failures))
                    else:
                        self.assertIn(original, failures)
                        self.assertEqual(sum(item is original for item in failures), 1)
                    self.assertCountEqual(executions, ['first', 'second'])
                    self.assertEqual(execution_languages, [language, language])
                    self.assertEqual(session.latest('tool/call').data['description'], tr(description_key))

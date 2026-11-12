"""目录取消等待原读取关闭，不遗留线程或重放请求。"""
import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness.tools.builtin.subagent_model_tools import build_list_subagent_models_tool
from harness.tools.processes import ProcessCleanupError


class CatalogCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfirmed_process_cleanup_is_not_a_normal_catalog_fallback(self):
        """回收未确认须阻止继续执行，不能返回本地模型降级结果。"""
        failure = ProcessCleanupError('fixture process still owned')
        tool = build_list_subagent_models_tool(base_url='https://fixture.invalid', model='fixture/model')
        with patch('harness.tools.builtin.subagent_model_tools.read_catalog', side_effect=failure) as read:
            with self.assertRaises(ProcessCleanupError) as caught:
                await tool.execute({'provider': 'openrouter'}, SimpleNamespace())
        self.assertIs(caught.exception, failure)
        read.assert_called_once()

    async def test_repeated_cancellation_waits_for_original_response_close(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                closed.set()

            def read(self, size):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('Fixture release missing')
                return b'{"data":[]}'
        tool = build_list_subagent_models_tool(base_url='https://fixture.invalid', model='fixture/model')
        def read(*args, **kwargs):
            with Response() as response:
                return response.read(kwargs['limit'] + 1)
        with patch('harness.tools.builtin.subagent_model_tools.read_catalog', side_effect=read) as open_response:
            task = asyncio.create_task(tool.execute({'provider':'openrouter'}, SimpleNamespace()))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertFalse(closed.is_set())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(closed.is_set())
                open_response.assert_called_once()
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

    async def test_cancel_and_read_failure_preserve_both_original_errors(self):
        entered, release = threading.Event(), threading.Event()
        failure = OSError('Original read failure')
        diagnostics = []
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: diagnostics.append(context))
        self.addCleanup(loop.set_exception_handler, previous)
        def read(*args):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('Fixture release missing')
            raise failure
        tool = build_list_subagent_models_tool(base_url='https://fixture.invalid', model='fixture/model')
        with patch('harness.tools.builtin.subagent_model_tools._fetch_catalog', side_effect=read) as fetch:
            task = asyncio.create_task(tool.execute({'provider':'openrouter'}, SimpleNamespace()))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel('Original cancellation')
                await asyncio.sleep(0)
                release.set()
                with self.assertRaises(BaseExceptionGroup) as caught:
                    await task
                self.assertIsInstance(caught.exception.exceptions[0], asyncio.CancelledError)
                self.assertEqual(caught.exception.exceptions[0].args, ('Original cancellation',))
                self.assertIs(caught.exception.exceptions[1], failure)
                fetch.assert_called_once()
                await asyncio.sleep(0)
                self.assertEqual(diagnostics, [])
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

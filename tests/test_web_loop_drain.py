"""事件循环排空等待原取消收尾，不重复取消或吞掉清理失败。"""
import asyncio
import unittest

from harness.web.hub import ConnectionHub
from harness.web.server import _drain_loop


class WebLoopDrainTests(unittest.TestCase):
    def test_hub_cancelled_writer_finishes_without_a_second_cancellation(self):
        loop = asyncio.new_event_loop()
        entered, finishing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        completed = []
        class Connection:
            closed = False
            async def send_json(self, event):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    finishing.set()
                    await release.wait()
                    completed.append('Original cleanup finished')
        hub = ConnectionHub()
        async def setup():
            subscriber = hub.subscribe('fixture', Connection())
            hub.publish('fixture', {'type': 'note'})
            await entered.wait()
            hub.close_all()
            await finishing.wait()
            # 重复关闭同一订阅也不得打断正在执行的finally。
            subscriber.close()
            loop.call_later(.01, release.set)
            return subscriber
        subscriber = loop.run_until_complete(setup())
        _drain_loop(loop)
        self.assertEqual(completed, ['Original cleanup finished'])
        self.assertTrue(subscriber.writer.done())
        self.assertTrue(loop.is_closed())

    def test_finalizer_error_is_propagated_after_other_tasks_are_drained(self):
        loop = asyncio.new_event_loop()
        entered = asyncio.Event()
        original = OSError('Original writer cleanup')
        completed = []
        async def fail():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                raise original
        async def other():
            try:
                await asyncio.Event().wait()
            finally:
                completed.append('Other cleanup finished')
        async def setup():
            tasks = [asyncio.create_task(fail()), asyncio.create_task(other())]
            await entered.wait()
            return tasks
        tasks = loop.run_until_complete(setup())
        with self.assertRaises(OSError) as caught:
            _drain_loop(loop)
        self.assertIs(caught.exception, original)
        self.assertEqual(completed, ['Other cleanup finished'])
        self.assertTrue(all(task.done() for task in tasks))
        self.assertTrue(loop.is_closed())

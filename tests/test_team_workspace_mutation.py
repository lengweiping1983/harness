"""团队工作树写入必须等待线程终态，失败不能被取消遮住。"""
import asyncio
import threading
import unittest

from harness.app.team_workspaces import _mutation


class TeamWorkspaceMutationTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancellation_waits_and_preserves_original_failure(self):
        for fails in (False, True):
            with self.subTest(fails=fails):
                entered, release = threading.Event(), threading.Event()
                original = OSError('write outcome requires inspection')
                effects = []

                def write():
                    effects.append('started')
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError('test barrier')
                    effects.append('finished')
                    if fails:
                        raise original
                    return 'saved'

                owner = asyncio.create_task(_mutation(write))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    for _ in range(3):
                        owner.cancel()
                        await asyncio.sleep(0)
                        self.assertFalse(owner.done())
                    release.set()
                    if fails:
                        with self.assertRaises(OSError) as error:
                            await asyncio.wait_for(owner, 3)
                        self.assertIs(error.exception, original)
                    else:
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(owner, 3)
                    self.assertEqual(effects, ['started', 'finished'])
                finally:
                    release.set()
                    await asyncio.gather(owner, return_exceptions=True)

    async def test_normal_result_and_failure_keep_their_identity(self):
        value = object()
        self.assertIs(await _mutation(lambda: value), value)
        original = RuntimeError('original')

        def fail():
            raise original

        with self.assertRaises(RuntimeError) as error:
            await _mutation(fail)
        self.assertIs(error.exception, original)

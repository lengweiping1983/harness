"""密钥轮换、持久恢复及异步保存回归，不使用真实凭据。"""
import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.llm.key_pool import ApiKeyPool


class KeyPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_rotation_failure_and_same_day_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'pool.json'
            pool = ApiKeyPool(['first', 'second'], state_path=path)
            await pool.amark_failed(0)
            self.assertEqual(await pool.acurrent(), (1, 'second'))
            restored = ApiKeyPool(['first', 'second'], state_path=path)
            self.assertEqual(restored.current(), (1, 'second'))
            self.assertNotIn('first', path.read_text())
            await pool.amark_failed(1)
            self.assertEqual(await pool.acurrent(), (0, 'first'))
            self.assertEqual(await pool.arotate(), (1, 'second'))
            self.assertEqual(pool.status()['failed'], [])

    async def test_save_does_not_block_loop_and_cancel_waits_for_owned_write(self):
        pool = ApiKeyPool(['first', 'second'])
        entered, release = threading.Event(), threading.Event()
        def save():
            entered.set()
            if not release.wait(3):
                raise TimeoutError('save not released')
        with patch.object(pool, '_save', side_effect=save):
            task = asyncio.create_task(pool.amark_failed(0))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(await pool.acurrent(), (1, 'second'))

    def test_save_failure_is_visible_and_success_clears_it(self):
        with tempfile.TemporaryDirectory() as folder:
            pool = ApiKeyPool(['first', 'second'], state_path=Path(folder) / 'pool.json')
            with patch('harness.llm.key_pool.tempfile.NamedTemporaryFile', side_effect=OSError('no space')):
                with self.assertLogs('harness.llm.key_pool', level='WARNING'):
                    pool.mark_failed(0)
            self.assertTrue(pool.status()['state_save_failed'])
            pool.rotate()
            self.assertFalse(pool.status()['state_save_failed'])

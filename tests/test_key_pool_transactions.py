"""多个Key池实例及同步异步调用不覆盖刚确认的状态。"""
import asyncio
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from harness.llm.key_pool import ApiKeyPool
from harness.llm.key_pool_lock import state_lock

KEYS=['fixture-first','fixture-second','fixture-third','fixture-fourth']


class KeyPoolTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_constructor_waits_for_pending_publication_before_loading(self):
        """首次读取也属于事务，不能与另一实例的原子发布争用文件句柄。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pool.json'
            pool = ApiKeyPool(KEYS, state_path=path)
            entered, release, loading = threading.Event(), threading.Event(), threading.Event()
            original = Path.replace
            def replace(source, target):
                entered.set()
                if not release.wait(10):
                    raise TimeoutError('publication not released')
                return original(source, target)
            @contextmanager
            def acquiring(target):
                if entered.is_set():
                    loading.set()
                with state_lock(target):
                    yield
            with patch.object(Path, 'replace', replace), \
                 patch('harness.llm.key_pool.state_lock', acquiring), ThreadPoolExecutor(max_workers=2) as executor:
                saving = executor.submit(pool.rotate)
                reading = None
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    reading = executor.submit(ApiKeyPool, KEYS, state_path=path)
                    self.assertTrue(await asyncio.to_thread(loading.wait, 5))
                    self.assertFalse(reading.done())
                finally:
                    release.set()
                    await asyncio.wrap_future(saving)
                loaded = await asyncio.wrap_future(reading)
                self.assertEqual(loaded.current(), (1, KEYS[1]))
                self.assertFalse(loaded.persistence_error)

    async def test_independent_instances_preserve_each_failure_and_latest_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'pool.json'
            first,second=ApiKeyPool(KEYS,state_path=path),ApiKeyPool(KEYS,state_path=path)
            first.mark_failed(0);second.mark_failed(1)
            self.assertEqual(first.status()['failed'],[0,1])
            self.assertEqual(second.current(),(2,KEYS[2]))
            self.assertEqual(first.rotate(),(3,KEYS[3]))
            self.assertEqual(second.current(),(3,KEYS[3]))

    async def test_cross_process_stale_instances_merge_under_file_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'pool.json'
            code="import pathlib,sys;from harness.llm.key_pool import ApiKeyPool;p=ApiKeyPool("+repr(KEYS)+",state_path=sys.argv[1]);print('ready',flush=True);sys.stdin.readline();p.mark_failed(int(sys.argv[2]))"
            processes=[subprocess.Popen([sys.executable,'-c',code,str(path),str(index)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for index in (0,1)]
            try:
                async with asyncio.timeout(5):
                    for process in processes:
                        self.assertEqual(await asyncio.to_thread(process.stdout.readline),'ready\n')
                for process in processes:process.stdin.write('go\n');process.stdin.flush()
                for process in processes:
                    stdout,stderr=await asyncio.to_thread(process.communicate,timeout=5)
                    self.assertEqual(process.returncode,0,stderr)
                self.assertEqual(ApiKeyPool(KEYS,state_path=path).status()['failed'],[0,1])
            finally:
                for process in processes:
                    if process.poll() is None:process.kill()
                    process.communicate()

    async def test_sync_call_waits_for_async_save_on_same_instance(self):
        pool=ApiKeyPool(KEYS)
        entered,release=threading.Event(),threading.Event()
        original=pool._save
        def save():
            entered.set();release.wait(3);original()
        with patch.object(pool,'_save',side_effect=save),ThreadPoolExecutor(max_workers=1) as executor:
            task=asyncio.create_task(pool.amark_failed(0))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                future=executor.submit(pool.mark_failed,1)
                self.assertFalse(future.done())
            finally:
                release.set();await task
            await asyncio.wrap_future(future)
        self.assertEqual(pool.status()['failed'],[0,1])

    async def test_failed_save_retry_preserves_both_local_and_other_instance_marks(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'pool.json'
            first,second=ApiKeyPool(KEYS,state_path=path),ApiKeyPool(KEYS,state_path=path)
            second.mark_failed(2)
            with patch.object(Path,'replace',side_effect=OSError('Original write failure')):
                first.mark_failed(0)
            self.assertTrue(first.persistence_error)
            second.mark_failed(1)
            first.rotate()
            self.assertEqual(json.loads(path.read_text())['failed'],[0,1,2])

    async def test_lock_failure_does_not_publish_unserialized_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'pool.json';pool=ApiKeyPool(KEYS,state_path=path);pool.rotate();original=path.read_bytes()
            with patch('harness.llm.key_pool.state_lock',side_effect=OSError('locked')):
                pool.mark_failed(0)
            self.assertTrue(pool.persistence_error)
            self.assertEqual(path.read_bytes(),original)

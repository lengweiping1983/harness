"""Hook 命令清理仅执行一次，重复取消必须等所属回收后传播。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from harness.app.hooks import ProjectHooks


class HookCommandOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_cleanup_joins_original_task_without_success_receipt(self):
        cleaning, release=asyncio.Event(),asyncio.Event()
        async def cleanup(*args,**kwargs):
            cleaning.set();await release.wait()
        stdout,stderr=asyncio.StreamReader(),asyncio.StreamReader()
        stdout.feed_eof();stderr.feed_eof()
        process=SimpleNamespace(stdout=stdout,stderr=stderr,returncode=None,pid=321)
        async def create(*args,**kwargs):
            Path(args[-2]).write_text('{"exit_code":0}',encoding='utf-8')
            return process
        hooks=ProjectHooks.__new__(ProjectHooks)
        hooks.harness=SimpleNamespace(config=SimpleNamespace(enable_owned_processes=False),jobs=object())
        context=SimpleNamespace(session_id='fixture')
        with tempfile.TemporaryDirectory() as directory, patch('harness.app.hooks.asyncio.create_subprocess_exec',side_effect=create), patch('harness.app.hooks.cleanup_process',new_callable=AsyncMock,side_effect=cleanup) as clean:
            task=asyncio.create_task(hooks._command({'command':'fixture','timeout':1,'label':'Original'}, {'original':'原文'},context,Path(directory)))
            try:
                await asyncio.wait_for(cleaning.wait(),1)
                task.cancel('Original cancellation')
                await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            clean.assert_awaited_once()
            self.assertIs(clean.call_args.args[0],process)

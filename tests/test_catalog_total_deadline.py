"""隔离HTTP服务验证目录总截止时间、读取上限与凭据传递。"""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.catalog_fetch import read_catalog
from harness.tools.builtin.subagent_model_tools import build_list_subagent_models_tool


TOTAL_TIMEOUT = 3.0


@contextmanager
def endpoint(mode):
    observed={'requests':0,'authorization':None,'started':threading.Event()}
    disconnected=threading.Event()
    stopping=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            observed['requests']+=1
            observed['authorization']=self.headers.get('Authorization')
            observed['started'].set()
            self.send_response(200);self.end_headers()
            try:
                if mode=='slow':
                    for _ in range(1000):
                        self.wfile.write(b' ');self.wfile.flush()
                        if stopping.wait(.03):break
                else:self.wfile.write(b'x'*1000 if mode=='large' else b'{"data":[]}')
            except (BrokenPipeError,ConnectionResetError):disconnected.set()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.daemon_threads=False
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02});thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}',observed,disconnected
    finally:stopping.set();server.shutdown();server.server_close();thread.join(2)


class CatalogTotalDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_total_deadline_also_reclaims_a_reader_still_starting(self):
        """启动尚未发出HTTP请求时也受总预算约束，返回前必须回收进程。"""
        children = []
        original = subprocess.Popen
        def spawn(*args, **kwargs):
            process = original(*args, **kwargs)
            children.append(process)
            return process
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), \
                 patch('harness.llm.catalog_fetch._READER', 'import time;time.sleep(30)'), \
                 patch('harness.llm.catalog_fetch.subprocess.Popen', side_effect=spawn):
                started = time.monotonic()
                with self.assertRaises(TimeoutError) as error:
                    await asyncio.to_thread(read_catalog, 'http://127.0.0.1:1', 'fixture-key', timeout=.05, limit=32)
                self.assertEqual(str(error.exception), tr('tools.models.catalogTimeout'))
                self.assertLess(time.monotonic() - started, 3)
                self.assertIsNotNone(children[-1].poll())
        self.assertEqual(len(children), 3)

    async def test_continuously_active_response_cannot_extend_total_deadline(self):
        for language in ('en','zh-CN','en'):
            with current_language.set(language),endpoint('slow') as (url,observed,closed):
                started=time.monotonic()
                with self.assertRaises(TimeoutError) as error:
                    await asyncio.to_thread(read_catalog,url,'fixture-key',timeout=TOTAL_TIMEOUT,limit=1024)
                self.assertEqual(str(error.exception),tr('tools.models.catalogTimeout'))
                self.assertLess(time.monotonic()-started,TOTAL_TIMEOUT+2)
                self.assertEqual(observed['requests'],1)
                self.assertTrue(await asyncio.to_thread(closed.wait,2))
                self.assertEqual(observed['requests'],1)

    async def test_byte_limit_precedes_json_and_key_is_not_in_process_arguments(self):
        original=subprocess.run
        for mode,expected in [('large',33),('valid',11)]:
            with endpoint(mode) as (url,observed,_),patch('harness.llm.catalog_fetch.subprocess.run',wraps=original) as run:
                value=await asyncio.to_thread(read_catalog,url,'fixture-private-key',timeout=2,limit=32)
                self.assertEqual(len(value),expected)
                self.assertEqual(observed['authorization'],'Bearer fixture-private-key')
                self.assertNotIn('fixture-private-key',repr(run.call_args.args))
                self.assertIn(b'fixture-private-key',run.call_args.kwargs['input'])
                self.assertEqual(observed['requests'],1)

    async def test_cancelled_tool_waits_for_its_process_deadline_without_replay(self):
        with endpoint('slow') as (url,observed,closed),patch('harness.tools.builtin.subagent_model_tools.CATALOG_TIMEOUT',TOTAL_TIMEOUT):
            tool=build_list_subagent_models_tool(base_url=url,model='fixture/model')
            task=asyncio.create_task(tool.execute({'provider':'openrouter'},None))
            try:
                self.assertTrue(await asyncio.to_thread(observed['started'].wait,TOTAL_TIMEOUT+2))
                self.assertFalse(task.done())
                task.cancel();await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
                self.assertFalse(task.done())
                with self.assertRaises(BaseExceptionGroup) as error:await task
                self.assertIsInstance(error.exception.exceptions[0],asyncio.CancelledError)
                self.assertIsInstance(error.exception.exceptions[1],TimeoutError)
                self.assertTrue(await asyncio.to_thread(closed.wait,2))
                self.assertEqual(observed['requests'],1)
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)

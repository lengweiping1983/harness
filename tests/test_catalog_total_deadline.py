"""隔离HTTP服务验证目录总截止时间、读取上限与凭据传递。"""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socketserver
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.catalog_fetch import read_catalog
import harness.llm.catalog_fetch as catalog_fetch
from harness.tools.owned_process import OwnedProcess
from harness.tools.processes import ProcessCleanupError
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
            try:
                self.send_response(200);self.end_headers()
                if mode=='slow':
                    for _ in range(1000):
                        self.wfile.write(b' ');self.wfile.flush()
                        if stopping.wait(.03):break
                else:self.wfile.write(b'x'*1000 if mode=='large' else b'{"data":[]}')
            except ConnectionError:disconnected.set()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.daemon_threads=False
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02});thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}',observed,disconnected
    finally:stopping.set();server.shutdown();server.server_close();thread.join(2)


class CatalogTotalDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_input_encoding_fails_before_process_creation(self):
        """不可编码参数在创建之前拒绝，避免留下尚未登记输入的进程。"""
        with patch.object(catalog_fetch, 'spawn_owned', AsyncMock()) as spawn:
            for key, error in ((object(), TypeError), ('\ud800', UnicodeEncodeError)):
                with self.subTest(error=error), self.assertRaises(error):
                    await catalog_fetch._read_catalog('https://fixture.invalid', key, timeout=1, limit=32)
            spawn.assert_not_awaited()

    async def test_cleanup_failure_keeps_native_cause_and_does_not_close_unknown_owner(self):
        """系统停止失败保留原件，明确拒绝把未知资源发布为已经关闭。"""
        original = OSError('fixture native stop failed')
        process = SimpleNamespace(pid=42, write_input=AsyncMock(),
            stdout=SimpleNamespace(read=AsyncMock(return_value=b'')),
            stderr=SimpleNamespace(read=AsyncMock(return_value=b'')),
            wait=AsyncMock(return_value=0), stop_owned=AsyncMock(side_effect=original), close=Mock())
        with patch.object(catalog_fetch, 'spawn_owned', AsyncMock(return_value=process)):
            with self.assertRaises(ProcessCleanupError) as caught:
                await catalog_fetch._read_catalog('http://fixture.invalid', 'fixture-key', timeout=1, limit=32)
        self.assertIs(caught.exception.__cause__, original)
        process.close.assert_not_called()
        process.stop_owned.assert_awaited_once()

    async def test_windows_aborted_connection_is_observed_as_a_disconnect(self):
        """Windows 的 10053 与重置、断管同样表示客户端连接已释放。"""
        from urllib.request import urlopen
        write = socketserver._SocketWriter.write
        def aborted(stream, data):
            if data == b' ':
                raise ConnectionAbortedError(10053, 'fixture connection aborted')
            return write(stream, data)
        with endpoint('slow') as (url, observed, closed), \
             patch.object(socketserver._SocketWriter, 'write', aborted):
            def read():
                with urlopen(url, timeout=2) as response:
                    return response.read()
            self.assertEqual(await asyncio.to_thread(read), b'')
            self.assertTrue(await asyncio.to_thread(closed.wait, 1))
            self.assertEqual(observed['requests'], 1)

    async def test_total_deadline_also_reclaims_a_reader_still_starting(self):
        """启动尚未发出HTTP请求时也受总预算约束，返回前必须回收进程。"""
        children = []
        def own(native):
            process = OwnedProcess(native)
            children.append(process)
            return process
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), \
                 patch('harness.llm.catalog_fetch._READER', 'import time;time.sleep(30)'), \
                 patch('harness.tools.owned_process.OwnedProcess', side_effect=own):
                started = time.monotonic()
                with self.assertRaises(TimeoutError) as error:
                    await asyncio.to_thread(read_catalog, 'http://127.0.0.1:1', 'fixture-key', timeout=.05, limit=32)
                self.assertEqual(str(error.exception), tr('tools.models.catalogTimeout'))
                self.assertLess(time.monotonic() - started, 3)
                self.assertTrue(children[-1].cleanup_confirmed)
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
        commands, children = [], []
        original = catalog_fetch.spawn_owned
        async def spawn(argv, **kwargs):
            commands.append(argv)
            process = await original(argv, **kwargs)
            process.write_input = AsyncMock(wraps=process.write_input)
            children.append(process)
            return process
        for mode,expected in [('large',33),('valid',11)]:
            with endpoint(mode) as (url,observed,_),patch.object(catalog_fetch, 'spawn_owned', side_effect=spawn):
                value=await asyncio.to_thread(read_catalog,url,'fixture-private-key',timeout=2,limit=32)
                self.assertEqual(len(value),expected)
                self.assertEqual(observed['authorization'],'Bearer fixture-private-key')
                self.assertNotIn('fixture-private-key',repr(commands[-1]))
                self.assertIn(b'fixture-private-key',children[-1].write_input.call_args.args[0])
                self.assertTrue(children[-1].cleanup_confirmed)
                self.assertEqual(observed['requests'],1)

    async def test_deadline_reclaims_a_descendant_that_inherits_the_http_connection(self):
        """启动器派生的真实后代不能绕过总预算或继续持有网络连接。"""
        reader = catalog_fetch._READER
        wrapper = ('import subprocess,sys;subprocess.run([sys.executable,"-I","-X","utf8","-c",'
                   + repr(reader) + '],check=True)')
        children = []
        def own(native):
            process = OwnedProcess(native)
            children.append(process)
            return process
        with endpoint('slow') as (url, observed, closed), \
             patch.object(catalog_fetch, '_READER', wrapper), \
             patch('harness.tools.owned_process.OwnedProcess', side_effect=own):
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                await asyncio.to_thread(read_catalog, url, 'fixture-key', timeout=TOTAL_TIMEOUT, limit=1024)
            self.assertLess(time.monotonic() - started, TOTAL_TIMEOUT + 2)
            self.assertEqual(observed['requests'], 1)
            self.assertTrue(children[0].cleanup_confirmed)
            self.assertTrue(await asyncio.to_thread(closed.wait, 2))

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

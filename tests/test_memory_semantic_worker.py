"""以真实所属进程验证取消、队列隔离、重建与宿主关闭。"""
import asyncio
import os
import sys
import tempfile
import unittest
import re
from harness.i18n import tr
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from harness import Harness, HarnessConfig
from harness.app.memory import ProjectMemory
from harness.app.layered_memory import LayeredMemory
from harness.tools.builtin.memory_tools import build_layered_memory_tools
from harness.tools.registry import ToolRegistry, ToolRunContext
from harness.llm.types import ToolUseBlock
from harness.app.memory_semantic import LocalMemoryCandidates
from harness.app.memory_semantic_worker import SemanticWorker
from harness.core.errors import HarnessError
from harness.core.language import current_language
from tests.web_fixture import FixtureTransport


SCRIPT = '''
import os, socket, struct, sys, time
from pathlib import Path
from harness.app.memory_semantic_worker import frame, _receive_sync
marker = Path(sys.argv[2])
if sys.argv[3] == 'slow':
    marker.touch()
    time.sleep(60)
with socket.create_connection(('127.0.0.1', int(sys.argv[1]))) as channel:
    channel.sendall(frame({'token': os.environ['HARNESS_MEMORY_WORKER_KEY']}))
    calls = 0
    while True:
        try: request = _receive_sync(channel)
        except EOFError: break
        calls += 1
        if request['query'] == 'blocked query':
            marker.touch()
            time.sleep(60)
        if request['query'] == 'exit query': os._exit(17)
        if request['query'] == 'changed snapshot':
            channel.sendall(frame({'id': request['id'], 'error': {
                'code': 'memory/scan-changed', 'message': 'Snapshot changed; restart scan.'}}))
            continue
        if request['query'] == 'oversized frame':
            channel.sendall(struct.pack('!I', 1048577))
            time.sleep(60)
        if request['query'] == 'flood query': print('x' * 20000, flush=True)
        result = {'pid': os.getpid(), 'calls': calls, 'query': request['query'], 'path': request['path'], 'cwd': request['cwd']}
        identity = 'wrong' if request['query'] == 'wrong identity' else request['id']
        channel.sendall(frame({'id': identity, 'result': result}))
'''


def minimal_environment():
    """清空宿主环境以验证工作进程的最小环境构造。

    宿主审计沙箱在进程创建时注入 ``LSBOX_*`` / ``SANDBOX_*`` 变量，其进程创建钩子
    要求这些变量存在于**父进程的真实环境**中；若连它们一并清空，任何子进程都会在
    执行任何代码之前被钩子终止（退出码 1、无 stderr），工作进程自身无法防御。
    因此只保留沙箱注入的审计变量，PATH/PYTHONPATH/USERPROFILE 等仍被清空；
    在无注入的平台上等价于 ``patch.dict(os.environ, {}, clear=True)``。
    """
    preserved = {key: value for key, value in os.environ.items()
                 if key.startswith(('LSBOX_', 'SANDBOX_'))}
    return patch.dict(os.environ, preserved, clear=True)


class FixtureWorker(SemanticWorker):
    def __init__(self, root, *, slow=False):
        super().__init__(root / 'models')
        self.marker = root / ('startup' if slow else 'blocked')
        self.slow = slow

    def _argv(self, port):
        return [sys.executable, '-c', SCRIPT, str(port), str(self.marker), 'slow' if self.slow else 'normal']


class SemanticWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = ProjectMemory()

    def test_worker_environment_scrubs_host_credentials_but_keeps_explicit_bootstrap(self):
        with patch.dict('os.environ', {
            'OPENAI_API_KEY': 'host-secret',
            'AWS_SECRET_ACCESS_KEY': 'host-secret',
            'NODE_OPTIONS': '--require=/host/inject.js',
            'PATH': '/fixture',
        }, clear=True):
            env = SemanticWorker._worker_environment('worker-key', str(self.root))
        self.assertEqual(env['HARNESS_MEMORY_WORKER_KEY'], 'worker-key')
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')
        self.assertEqual(env['PYTHONPATH'], str(self.root))
        self.assertEqual(env['PATH'], '/fixture')
        for name in ('OPENAI_API_KEY', 'AWS_SECRET_ACCESS_KEY', 'NODE_OPTIONS'):
            self.assertNotIn(name, env)

    async def started(self, worker):
        async def wait():
            while not worker.marker.exists():
                await asyncio.sleep(.01)
        await asyncio.wait_for(wait(), 10)

    async def test_persistent_process_reuse_bounded_logs_and_no_replay_on_crash(self):
        worker = FixtureWorker(self.root)
        self.addAsyncCleanup(worker.aclose)
        first = await worker.search(self.store, self.root, 'natural question')
        process = worker._process
        second = await worker.search(self.store, self.root, 'flood query')
        self.assertEqual(first['pid'], second['pid'])
        self.assertEqual(second['calls'], 2)
        self.assertLessEqual(len(worker._tail), 8192)
        with self.assertRaises(HarnessError):
            await worker.search(self.store, self.root, 'exit query')
        self.assertTrue(process.cleanup_confirmed)
        self.assertIsNone(worker._process)
        rebuilt = await worker.search(self.store, self.root, 'rebuilt question')
        self.assertEqual(rebuilt['calls'], 1)
        self.assertIsNot(process, worker._process)
        await worker.aclose()
        self.assertIsNone(worker._process)

    async def test_cancel_waiter_preserves_owner_then_repeated_owner_cancel_reaps_process(self):
        worker = FixtureWorker(self.root)
        self.addAsyncCleanup(worker.aclose)
        owner = asyncio.create_task(worker.search(self.store, self.root, 'blocked query'))
        await self.started(worker)
        process = worker._process
        waiting = asyncio.create_task(worker.search(self.store, self.root, 'queued question'))
        await asyncio.sleep(.02)
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        self.assertIs(worker._process, process)
        self.assertIsNone(process.returncode)
        owner.cancel()
        await asyncio.sleep(0)
        owner.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(owner, 10)
        self.assertTrue(process.cleanup_confirmed)
        self.assertIsNone(worker._process)
        self.assertEqual((await worker.search(self.store, self.root, 'next question'))['calls'], 1)

    async def test_close_during_startup_reaps_process_and_rejects_later_requests(self):
        worker = FixtureWorker(self.root, slow=True)
        self.addAsyncCleanup(worker.aclose)
        task = asyncio.create_task(worker.search(self.store, self.root, 'natural question'))
        await self.started(worker)
        process = worker._process
        await asyncio.wait_for(worker.aclose(), 10)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(process.cleanup_confirmed)
        with self.assertRaises(RuntimeError):
            await worker.search(self.store, self.root, 'after close')

    async def test_request_timeout_reaps_process_before_reporting_timeout(self):
        worker = FixtureWorker(self.root)
        self.addAsyncCleanup(worker.aclose)
        task = asyncio.create_task(worker.search(self.store, self.root, 'blocked query'))
        await self.started(worker)
        process = worker._process
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(task, .01)
        self.assertTrue(process.cleanup_confirmed)
        self.assertIsNone(worker._process)

    async def test_registry_timeout_reaps_owner_then_other_session_rebuilds(self):
        provider = LocalMemoryCandidates(self.root / 'models')
        worker = FixtureWorker(self.root)
        provider._worker = worker
        self.addAsyncCleanup(provider.aclose)
        registry = ToolRegistry()
        layers = LayeredMemory(self.root / 'home', self.store)
        for tool in build_layered_memory_tools(layers, candidates=provider):
            registry.register(tool)
        def context(identity):
            return ToolRunContext(call_id=identity, session_id=identity, cwd=str(self.root),
                                  turn=1, step=1, services={'task_read_only': True})
        warm = await registry.execute_call(ToolUseBlock('warm', 'memory_candidates',
            {'query': 'warm query', 'scope': 'session'}), context('one'))
        self.assertFalse(warm.is_error, warm.content)
        process = worker._process
        tool = registry.get('memory_candidates')
        tool.timeout = .1
        timed = await registry.execute_call(ToolUseBlock('timeout', 'memory_candidates',
            {'query': 'blocked query', 'scope': 'session'}), context('one'))
        self.assertTrue(timed.is_error)
        self.assertTrue(process.cleanup_confirmed)
        self.assertIsNone(worker._process)
        tool.timeout = 10
        next_session = await registry.execute_call(ToolUseBlock('next', 'memory_candidates',
            {'query': 'next query', 'scope': 'session'}), context('two'))
        self.assertFalse(next_session.is_error, next_session.content)
        self.assertEqual(Path(next_session.data['path']), layers.store('session', session_id='two').path(self.root))
        self.assertNotEqual(warm.data['path'], next_session.data['path'])

    async def test_wrong_response_identity_discards_process(self):
        worker = FixtureWorker(self.root)
        self.addAsyncCleanup(worker.aclose)
        with self.assertRaisesRegex(ValueError, re.escape(tr('python.memory.semantic.identity'))):
            await worker.search(self.store, self.root, 'wrong identity')
        self.assertIsNone(worker._process)

    async def test_crash_diagnostic_waits_for_owned_log_drain_before_reporting(self):
        """启动或请求崩溃先于日志读取时，错误仍须包含原始诊断。"""
        for startup in (True, False):
            with self.subTest(startup=startup):
                worker = FixtureWorker(self.root)
                self.addAsyncCleanup(worker.aclose)
                diagnostic = 'native diagnostic 原始诊断'
                script = (f'import sys;sys.stderr.write({diagnostic!r});sys.stderr.flush()'
                          if startup else SCRIPT.replace("os._exit(17)",
                              f"(sys.stderr.write({diagnostic!r}), sys.stderr.flush(), os._exit(17))"))
                argv = worker._argv
                worker._argv = lambda port: [*argv(port)[:2], script, *argv(port)[3:]]
                release = asyncio.Event()
                drain, dispose = worker._drain, worker._dispose

                async def delayed_drain(stream):
                    await release.wait()
                    await drain(stream)

                async def dispose_and_release():
                    release.set()
                    await dispose()

                with patch.object(worker, '_drain', side_effect=delayed_drain), \
                     patch.object(worker, '_dispose', side_effect=dispose_and_release):
                    for language in ('en', 'zh-CN', 'en'):
                        with self.subTest(language=language), current_language.set(language):
                            release.clear()
                            with self.assertRaises(HarnessError) as error:
                                await asyncio.wait_for(worker.search(self.store, self.root, 'exit query'), 10)
                            self.assertEqual(error.exception.code, 'memory/semantic-worker')
                            self.assertEqual(error.exception.message,
                                             tr('python.memory.semantic.exited', detail=diagnostic))
                            self.assertIsNone(worker._process)

    async def test_valid_snapshot_error_preserves_warm_process_and_next_session(self):
        worker = FixtureWorker(self.root)
        self.addAsyncCleanup(worker.aclose)
        first = await worker.search(self.store, self.root, 'natural question')
        process = worker._process
        with self.assertRaises(HarnessError) as error:
            await worker.search(self.store, self.root, 'changed snapshot')
        self.assertEqual(error.exception.code, 'memory/scan-changed')
        self.assertIs(worker._process, process)
        second = await worker.search(self.store, self.root, 'other session question')
        self.assertEqual(second['pid'], first['pid'])
        self.assertEqual(second['calls'], 3)

    async def test_relative_workspace_and_minimal_environment_are_preserved(self):
        # Windows 相对路径要求同盘，系统临时目录可能在另一个盘符。
        directory = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        worker = FixtureWorker(root)
        self.addAsyncCleanup(worker.aclose)
        relative = os.path.relpath(root, Path.cwd())
        with minimal_environment():
            result = await worker.search(self.store, relative, 'natural question')
        self.assertEqual(Path(result['cwd']), root.resolve())
        self.assertEqual(Path(result['path']), self.store.path(root))

    async def test_exit_without_stderr_keeps_native_status_and_interpreter_identity(self):
        """原生启动失败没有 stderr 时，仍保留停止前的真实退出码和解释器。"""
        worker = FixtureWorker(self.root)
        self.addAsyncCleanup(worker.aclose)
        worker._argv = lambda port: [sys.executable, '-c', 'import os;os._exit(23)']
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language), minimal_environment():
                with self.assertRaises(HarnessError) as error:
                    await asyncio.wait_for(worker.search(self.store, self.root, 'natural question'), 10)
                self.assertEqual(error.exception.code, 'memory/semantic-worker')
                self.assertEqual(error.exception.details['exit_code'], 23)
                self.assertEqual(error.exception.details['exit_code_hex'], '0x00000017')
                self.assertEqual(error.exception.details['executable'], sys.executable)
                self.assertGreater(error.exception.details['pid'], 0)
                self.assertIsNone(worker._process)

    async def test_invalid_authentication_and_oversized_response_are_reaped(self):
        worker = FixtureWorker(self.root)
        self.addAsyncCleanup(worker.aclose)
        original = worker._argv
        def wrong_auth(port):
            argv = original(port)
            argv[2] = argv[2].replace("os.environ['HARNESS_MEMORY_WORKER_KEY']", "'wrong'")
            return argv
        worker._argv = wrong_auth
        with self.assertRaisesRegex(ValueError, re.escape(tr('python.memory.semantic.authentication'))):
            await worker.search(self.store, self.root, 'natural question')
        self.assertIsNone(worker._process)
        worker._argv = original
        with self.assertRaisesRegex(ValueError, re.escape(tr('python.memory.semantic.frame_size'))):
            await worker.search(self.store, self.root, 'oversized frame')
        self.assertIsNone(worker._process)

    async def test_harness_closes_automatically_owned_provider_during_query(self):
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags['enable_project_memory'] = True
        app = Harness(HarnessConfig(api_key='fixture-only', home=self.root / 'home', cwd=self.root,
                      memory_model_cache=self.root / 'models', **flags), transport=FixtureTransport())
        self.addAsyncCleanup(app.close)
        provider = app._owned_memory_candidates
        self.assertIsInstance(provider, LocalMemoryCandidates)
        worker = FixtureWorker(self.root)
        provider._worker = worker
        task = asyncio.create_task(provider.search_async(self.store, self.root, 'blocked query'))
        await self.started(worker)
        process = worker._process
        await asyncio.wait_for(app.close(), 15)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(process.cleanup_confirmed)
        self.assertTrue(provider._closed)

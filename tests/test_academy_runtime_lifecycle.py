"""课程进程从创建至读取收尾的异常归属，不依赖宿主沙箱权限。"""
import contextlib
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from harness.academy.runtime import AcademyRuntime


@unittest.skipUnless(os.name == 'posix', '课程执行当前只支持 POSIX 沙箱')
class AcademyRuntimeLifecycleTests(unittest.TestCase):
    @contextlib.contextmanager
    def owned_runtime(self, source='import time; time.sleep(30)'):
        """用真实受控进程验证监督器，收尾兜底不依赖被测实现。"""
        popen, killpg = subprocess.Popen, os.killpg
        owned = []
        with tempfile.TemporaryDirectory() as directory:
            runtime = AcademyRuntime(Path(directory), timeout=2)
            def spawn(*args, **kwargs):
                process = popen(*args, **kwargs)
                owned.append(process)
                return process
            try:
                with (patch.object(runtime, 'command', return_value=[sys.executable, '-u', '-c', source]),
                      patch('harness.academy.runtime.subprocess.Popen', side_effect=spawn)):
                    yield runtime, owned
            finally:
                for process in owned:
                    try:
                        killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
                    for stream in (process.stdout, process.stderr):
                        stream.close()

    def assert_released(self, process):
        self.assertIsNotNone(process.poll())
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_started_callback_failure_reaps_process_and_closes_unstarted_pipes(self):
        for original in (RuntimeError('启动登记失败'), KeyboardInterrupt('调用方中断')):
            with self.subTest(original=original), self.owned_runtime() as (runtime, owned):
                def started():
                    raise original
                with self.assertRaises(BaseException) as caught:
                    runtime.run(['python', 'main.py'], on_started=started)
                self.assertIs(caught.exception, original)
                self.assertEqual(len(owned), 1)
                self.assert_released(owned[0])

    def test_second_reader_start_failure_reaps_process_and_first_reader(self):
        original = RuntimeError('第二个读取线程启动失败')
        thread_type = threading.Thread
        readers = []
        def reader(*args, **kwargs):
            thread = thread_type(*args, **kwargs)
            readers.append(thread)
            if len(readers) == 2:
                def fail():
                    raise original
                thread.start = fail
            return thread
        with self.owned_runtime() as (runtime, owned):
            with patch('harness.academy.runtime.threading.Thread', side_effect=reader):
                with self.assertRaises(RuntimeError) as caught:
                    runtime.run(['python', 'main.py'])
            self.assertIs(caught.exception, original)
            self.assert_released(owned[0])
            self.assertFalse(readers[0].is_alive())

    def test_output_reader_failure_is_not_a_successful_empty_result(self):
        original = OSError('stdout 原始读取失败')
        with self.owned_runtime('print("output must not disappear")') as (runtime, owned):
            def started():
                def fail(size):
                    raise original
                owned[0].stdout.read = fail
            with self.assertRaises(OSError) as caught:
                runtime.run(['python', 'main.py'], on_started=started)
            self.assertIs(caught.exception, original)
            self.assert_released(owned[0])

    def test_cleanup_signal_failure_preserves_primary_and_still_waits_and_closes_pipes(self):
        primary = RuntimeError('启动登记失败')
        cleanup = PermissionError('进程组信号被拒绝')
        with self.owned_runtime('pass') as (runtime, owned):
            def started():
                raise primary
            with patch('harness.academy.runtime.os.killpg', side_effect=cleanup):
                with self.assertRaises(BaseExceptionGroup) as caught:
                    runtime.run(['python', 'main.py'], on_started=started)
            self.assertEqual(caught.exception.exceptions, (primary, cleanup))
            self.assert_released(owned[0])

    def test_both_output_readers_preserve_their_original_errors(self):
        originals = (OSError('stdout 读取失败'), OSError('stderr 读取失败'))
        with self.owned_runtime('pass') as (runtime, owned):
            def started():
                for stream, original in zip((owned[0].stdout, owned[0].stderr), originals):
                    def fail(size, original=original):
                        raise original
                    stream.read = fail
            with self.assertRaises(BaseExceptionGroup) as caught:
                runtime.run(['python', 'main.py'], on_started=started)
            self.assertEqual(caught.exception.exceptions, originals)
            self.assert_released(owned[0])

    def test_read_and_close_failures_are_both_collected_as_original_objects(self):
        """同一管道的读取及关闭连续失败时，两者均作为独立失败传播。"""
        for primary in (OSError('stdout 原始读取失败'), KeyboardInterrupt('读取被中断')):
            cleanup = OSError('stdout 原始关闭失败')
            with self.subTest(primary=primary), self.owned_runtime('pass') as (runtime, owned), contextlib.ExitStack() as patches:
                closes = []
                def started():
                    stream = owned[0].stdout
                    close = stream.close
                    def fail_close():
                        closes.append(stream)
                        close()
                        raise cleanup
                    patches.enter_context(patch.object(stream, 'read', side_effect=primary))
                    patches.enter_context(patch.object(stream, 'close', side_effect=fail_close))
                with self.assertRaises(BaseExceptionGroup) as caught:
                    runtime.run(['python', 'main.py'], on_started=started)
                self.assertEqual(caught.exception.exceptions, (primary, cleanup))
                self.assertEqual(closes, [owned[0].stdout])
                self.assert_released(owned[0])

    def test_close_failure_after_successful_read_is_not_a_successful_result(self):
        """读取完成并不代表管道收尾成功，关闭失败保留原对象且不重试。"""
        original = OSError('stdout 原始关闭失败')
        with self.owned_runtime('print("done")') as (runtime, owned), contextlib.ExitStack() as patches:
            closes = []
            def started():
                stream = owned[0].stdout
                close = stream.close
                def fail_close():
                    closes.append(stream)
                    close()
                    raise original
                patches.enter_context(patch.object(stream, 'close', side_effect=fail_close))
            with self.assertRaises(OSError) as caught:
                runtime.run(['python', 'main.py'], on_started=started)
            self.assertIs(caught.exception, original)
            self.assertEqual(closes, [owned[0].stdout])
            self.assert_released(owned[0])

    def test_wait_timeout_is_bounded_and_does_not_skip_unowned_pipe_cleanup(self):
        primary = RuntimeError('启动登记失败')
        timeout = subprocess.TimeoutExpired('owned course process', 5)
        process = Mock(pid=12345, stdout=io.BytesIO(), stderr=io.BytesIO())
        process.wait.side_effect = timeout
        with tempfile.TemporaryDirectory() as directory:
            runtime = AcademyRuntime(Path(directory))
            def started():
                raise primary
            with (patch.object(runtime, 'command', return_value=['controlled']),
                  patch('harness.academy.runtime.subprocess.Popen', return_value=process),
                  patch('harness.academy.runtime.os.killpg') as kill):
                with self.assertRaises(BaseExceptionGroup) as caught:
                    runtime.run(['python', 'main.py'], on_started=started)
            self.assertEqual(caught.exception.exceptions, (primary, timeout))
            process.wait.assert_called_once_with(timeout=5)
            kill.assert_called_once_with(process.pid, signal.SIGKILL)
            self.assertTrue(process.stdout.closed)
            self.assertTrue(process.stderr.closed)

    def test_one_reader_join_failure_does_not_skip_the_other_reader(self):
        original = RuntimeError('第一个读取器 join 失败')
        thread_type = threading.Thread
        joined = []
        def reader(*args, **kwargs):
            thread = thread_type(*args, **kwargs)
            join = thread.join
            def finish(timeout):
                join(timeout)
                joined.append(thread)
                if len(joined) == 1:
                    raise original
            thread.join = finish
            return thread
        with self.owned_runtime('pass') as (runtime, owned):
            with patch('harness.academy.runtime.threading.Thread', side_effect=reader):
                with self.assertRaises(RuntimeError) as caught:
                    runtime.run(['python', 'main.py'])
            self.assertIs(caught.exception, original)
            self.assertEqual(len(joined), 2)
            self.assertTrue(all(not thread.is_alive() for thread in joined))
            self.assert_released(owned[0])

    def test_actual_sandbox_keeps_exact_output_truncation_and_timeout(self):
        """真实隔离执行仍保持原文、输出上限和超时，不用受控进程替身代替沙箱。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'main.py'
            script.write_text('import sys\nsys.stdout.write("原文 🧪\\n")\nsys.stderr.write("诊断\\n")\n', encoding='utf-8')
            runtime = AcademyRuntime(root, output_limit=1024)
            result = runtime.run(['python', 'main.py'])
            self.assertEqual(result['status'], 'passed', result)
            self.assertEqual(result['stdout'], '原文 🧪\n')
            self.assertEqual(result['stderr'], '诊断\n')
            script.write_text('print("x" * 10000)\n', encoding='utf-8')
            result = runtime.run(['python', 'main.py'])
            self.assertEqual(result['status'], 'passed', result)
            self.assertTrue(result['truncated'], result)
            self.assertEqual(result['stdout'], 'x' * 1024)
            script.write_text('while True: pass\n', encoding='utf-8')
            runtime.timeout = .2
            result = runtime.run(['python', 'main.py'])
            self.assertEqual(result['status'], 'timeout', result)
            self.assertNotEqual(result['exit_code'], 0)

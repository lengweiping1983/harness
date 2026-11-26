"""响应回调与关闭的原始失败保持，重复取消等待同一次收尾。"""
import asyncio
import errno
import ssl
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from harness.core.errors import LlmRequestError
from harness.llm.transport import UrllibTransport


class TransportResponseCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_body_read_classifies_bad_descriptor_as_recoverable(self):
        transport = UrllibTransport()
        failure = OSError(errno.EBADF, 'Bad file descriptor')
        for error, phase, transient in ((failure, 'read', True), (failure, 'connect', False),
                                        (ssl.SSLError(9, 'TLS error'), 'read', False)):
            with self.subTest(phase=phase, error=error):
                response = SimpleNamespace(status=200, read=Mock(side_effect=error), close=Mock())
                opener = Mock(return_value=response, side_effect=error if phase == 'connect' else None)
                with self.assertRaises(LlmRequestError) as caught:
                    _ = [chunk async for chunk in transport._stream_response(opener, timeout=1)]
                self.assertEqual(caught.exception.transient, transient)
                self.assertEqual(caught.exception.details['phase'], phase)
                self.assertIs(caught.exception.__cause__, error)
        response = SimpleNamespace(status=200, close=Mock())
        with self.assertRaises(OSError) as caught:
            _ = [chunk async for chunk in transport._stream_response(lambda: response, timeout=1,
                                                                     on_response=Mock(side_effect=failure))]
        self.assertIs(caught.exception, failure)
        response.close.assert_called_once()

    async def test_callback_failure_waits_for_close_despite_repeated_cancel(self):
        callback_failure = RuntimeError('Original callback 原文')
        closing, release = threading.Event(), threading.Event()
        def close():
            closing.set()
            if not release.wait(3):
                raise TimeoutError('fixture release')
        response = SimpleNamespace(status=200, close=Mock(side_effect=close))
        def callback(value):
            self.assertIs(value, response)
            raise callback_failure
        async def consume():
            return [chunk async for chunk in UrllibTransport()._stream_response(lambda: response, timeout=1, on_response=callback)]
        task = asyncio.create_task(consume())
        try:
            self.assertTrue(await asyncio.to_thread(closing.wait, 1))
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
        finally:
            release.set()
            try:
                await task
            except BaseException as exc:
                result = exc
            else:
                self.fail('Cancellation must propagate after close')
        self.assertIsInstance(result, asyncio.CancelledError)
        self.assertIs(result.__context__, callback_failure)
        response.close.assert_called_once()

    async def test_callback_and_close_failures_are_both_retained(self):
        callback_failure = RuntimeError('Original callback 原文')
        close_failure = OSError('Original close 原文')
        response = SimpleNamespace(status=200, close=Mock(side_effect=close_failure))
        def callback(value):
            raise callback_failure
        with self.assertRaises(ExceptionGroup) as caught:
            _ = [chunk async for chunk in UrllibTransport()._stream_response(lambda: response, timeout=1, on_response=callback)]
        self.assertEqual(caught.exception.exceptions, (callback_failure, close_failure))
        response.close.assert_called_once()

    async def test_successful_body_cannot_hide_a_close_failure(self):
        close_failure = OSError('Original close 原文')
        response = SimpleNamespace(status=200, read=Mock(return_value=b''), close=Mock(side_effect=close_failure))
        with self.assertRaises(OSError) as caught:
            _ = [chunk async for chunk in UrllibTransport()._stream_response(lambda: response, timeout=1)]
        self.assertIs(caught.exception, close_failure)
        response.close.assert_called_once()

    async def test_cancelled_read_and_close_failure_keep_both_originals(self):
        reading, release, finished = threading.Event(), threading.Event(), threading.Event()
        failure = OSError('Original cancelled close 原文')
        def read(size):
            reading.set()
            try:
                if not release.wait(3):
                    raise TimeoutError('fixture release')
                return b''
            finally:
                finished.set()
        def close():
            release.set()
            raise failure
        response = SimpleNamespace(status=200, read=Mock(side_effect=read), close=Mock(side_effect=close))
        async def consume():
            return [chunk async for chunk in UrllibTransport()._stream_response(lambda: response, timeout=1)]
        task = asyncio.create_task(consume())
        try:
            self.assertTrue(await asyncio.to_thread(reading.wait, 1))
            task.cancel()
            with self.assertRaises(BaseExceptionGroup) as caught:
                await task
            self.assertIn(failure, caught.exception.exceptions)
            self.assertTrue(any(isinstance(error, asyncio.CancelledError) for error in caught.exception.exceptions))
            self.assertTrue(await asyncio.to_thread(finished.wait, 1))
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        response.close.assert_called_once()

    async def test_callback_failure_with_successful_close_keeps_same_object(self):
        failure = RuntimeError('Original callback 原文')
        response = SimpleNamespace(status=200, close=Mock())
        def callback(value):
            raise failure
        with self.assertRaises(RuntimeError) as caught:
            _ = [chunk async for chunk in UrllibTransport()._stream_response(lambda: response, timeout=1, on_response=callback)]
        self.assertIs(caught.exception, failure)
        response.close.assert_called_once()

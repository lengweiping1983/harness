"""权限等待的重复取消不能再次打断已开始的工作清理。"""
import asyncio
import unittest
from unittest.mock import Mock

from harness.app.permissions import PermissionPolicy


class PermissionWaitCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancel_waits_for_single_work_cleanup(self):
        policy = object.__new__(PermissionPolicy)
        policy._changes = {}
        policy.snapshot = Mock(return_value={'version':'original'})
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        interrupted = []
        async def operation():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    interrupted.append(True)
                    raise
        task = asyncio.create_task(policy._await_current(operation(), 'fixture', 'original'))
        try:
            await entered.wait()
            task.cancel()
            await cleaning.wait()
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertEqual(interrupted, [])
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cancel_keeps_original_cleanup_failure(self):
        policy = object.__new__(PermissionPolicy)
        policy._changes = {}
        policy.snapshot = Mock(return_value={'version': 'original'})
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        failure = OSError('Original cleanup 原文')
        async def operation():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await release.wait()
                raise failure
        task = asyncio.create_task(policy._await_current(operation(), 'fixture', 'original'))
        try:
            await entered.wait()
            task.cancel()
            await cleaning.wait()
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
        finally:
            release.set()
        with self.assertRaises(BaseExceptionGroup) as caught:
            await task
        self.assertIn(failure, caught.exception.exceptions)
        self.assertTrue(any(isinstance(error, asyncio.CancelledError) for error in caught.exception.exceptions))

    async def test_policy_invalidation_keeps_cleanup_failure_and_does_not_retry(self):
        policy = object.__new__(PermissionPolicy)
        policy._changes = {}
        policy.snapshot = Mock(return_value={'version': 'original'})
        entered = asyncio.Event()
        failure = OSError('Original invalidation cleanup 原文')
        calls = []
        async def operation():
            calls.append('started')
            entered.set()
            try:
                await asyncio.Future()
            finally:
                raise failure
        task = asyncio.create_task(policy._await_current(operation(), 'fixture', 'original'))
        await entered.wait()
        policy._changes['fixture'].set()
        with self.assertRaises(ExceptionGroup) as caught:
            await task
        self.assertIn(failure, caught.exception.exceptions)
        self.assertEqual(calls, ['started'])

    async def test_primary_work_failure_is_not_duplicated_by_cleanup_collection(self):
        policy = object.__new__(PermissionPolicy)
        policy._changes = {}
        policy.snapshot = Mock(return_value={'version': 'original'})
        failure = OSError('Original work 原文')
        async def operation():
            raise failure
        with self.assertRaises(OSError) as caught:
            await policy._await_current(operation(), 'fixture', 'original')
        self.assertIs(caught.exception, failure)

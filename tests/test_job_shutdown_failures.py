"""后台作业批量清理不能忽略失败或跳过其他所属资源。"""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.job_tools import JobRecord, JobRegistry
from harness.tools.processes import ProcessCleanupError


class JobShutdownFailureTests(unittest.IsolatedAsyncioTestCase):
    def registry(self):
        registry = JobRegistry()
        for name in ('first', 'second'):
            registry._jobs[name] = JobRecord(name, 'exec', 'Original command', 'owner', 0,
                                            settled=asyncio.Event(), process=object())
        return registry

    async def test_failed_kill_is_reported_after_other_job_is_cleaned_and_can_be_retried(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                registry = self.registry()
                first = registry._jobs['first']
                handle = first.process
                failure = RuntimeError('Original cleanup {job} diagnostic')
                registry._reap = AsyncMock(side_effect=[failure, None])
                with self.assertRaises(ProcessCleanupError) as error:
                    await registry.cancel_all()
                self.assertEqual(error.exception.message, tr('jobs.cleanup.incomplete', job='first'))
                self.assertEqual(error.exception.details['job_id'], 'first')
                self.assertIn(str(failure), error.exception.details['detail'])
                self.assertEqual(registry._reap.await_count, 2)
                self.assertEqual(first.status, 'cleanup-failed')
                self.assertIs(first.process, handle)
                self.assertFalse(first.settled.is_set())
                self.assertEqual(registry._jobs['second'].status, 'killed')
                registry._reap = AsyncMock()
                await registry.cancel_all()
                registry._reap.assert_awaited_once_with(first)
                self.assertEqual(first.status, 'killed')

    async def test_exception_and_cancellation_are_both_preserved_after_all_attempts(self):
        registry = self.registry()
        failure, cancelled = RuntimeError('first failure'), asyncio.CancelledError('second cancellation')
        registry.kill = AsyncMock(side_effect=[failure, cancelled])
        with self.assertRaises(BaseExceptionGroup) as error:
            await registry.cancel_all()
        self.assertEqual(error.exception.exceptions, (failure, cancelled))
        self.assertEqual(registry.kill.await_count, 2)

    async def test_single_exception_keeps_identity_and_settled_jobs_are_skipped(self):
        registry = self.registry()
        registry._jobs['first'].status = 'completed'
        failure = RuntimeError('original failure')
        registry.kill = AsyncMock(side_effect=failure)
        with self.assertRaises(RuntimeError) as error:
            await registry.cancel_all()
        self.assertIs(error.exception, failure)
        registry.kill.assert_awaited_once_with('second', 'owner')

    async def test_executor_keeps_store_open_when_real_registry_reports_unconfirmed_cleanup(self):
        from harness.execution.runtime import ExecutionRuntime

        registry = self.registry()
        registry._reap = AsyncMock(side_effect=[RuntimeError('unconfirmed process'), None])
        runtime = object.__new__(ExecutionRuntime)
        runtime.closed = False
        runtime._close_task = None
        runtime._tasks, runtime._monitors = set(), set()
        runtime.jobs = registry
        runtime.store = Mock()
        for _ in range(2):
            with self.assertRaises(ProcessCleanupError):
                await runtime.close()
        runtime.store.close.assert_not_called()
        self.assertEqual(registry._reap.await_count, 2)
        self.assertEqual(registry._jobs['second'].status, 'killed')

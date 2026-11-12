"""项目执行保留回调关闭错误，并逐项回收剩余仓库租约。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from harness.project.execution import ProjectExecution
from harness.project.lease import RepositoryLease


class ProjectExecutionCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_callback_failure_is_preserved_and_remaining_leases_are_released(self):
        service = ProjectExecution(SimpleNamespace())
        failure = OSError('Original lease close failure')
        first, second = SimpleNamespace(close=Mock(side_effect=failure)), SimpleNamespace(close=Mock())
        service.leases = {'first':('run-one',first), 'second':('run-two',second)}
        with self.assertRaises(OSError) as error:service._release_lease('first','run-one',first)
        self.assertIs(error.exception, failure)
        self.assertIn('first', service.leases)
        with self.assertRaises(OSError) as error:await service.close()
        self.assertIs(error.exception, failure)
        second.close.assert_called_once();self.assertNotIn('second',service.leases)
        first.close.side_effect = None
        await service.close()
        self.assertEqual(service.leases, {})

    async def test_repeated_cancel_waits_for_review_and_then_releases_actual_repository_lock(self):
        service = ProjectExecution(SimpleNamespace())
        entered, release = asyncio.Event(), asyncio.Event()
        async def review():entered.set();await release.wait()
        with tempfile.TemporaryDirectory() as directory:
            lease = RepositoryLease(Path(directory))
            service.leases['fixture'] = ('run',lease)
            task = asyncio.create_task(review());service.reviews['run'] = task
            task.add_done_callback(lambda _: service.reviews.pop('run',None))
            closing = asyncio.create_task(service.close())
            try:
                await asyncio.wait_for(entered.wait(),1)
                closing.cancel();await asyncio.sleep(0);closing.cancel();await asyncio.sleep(0)
                self.assertFalse(closing.done());self.assertFalse(lease.stream.closed)
            finally:release.set()
            with self.assertRaises(asyncio.CancelledError):await closing
            self.assertTrue(lease.stream.closed)
            self.assertFalse(service.leases)
            with RepositoryLease(Path(directory)):pass

    async def test_review_failure_does_not_skip_remaining_lease(self):
        service = ProjectExecution(SimpleNamespace())
        failure = OSError('Original review failure')
        async def review():raise failure
        task = asyncio.create_task(review());service.reviews['run'] = task
        task.add_done_callback(lambda _: service.reviews.pop('run',None))
        lease = SimpleNamespace(close=Mock());service.leases['fixture'] = ('run',lease)
        with self.assertRaises(OSError) as error:await service.close()
        self.assertIs(error.exception, failure)
        lease.close.assert_called_once();self.assertFalse(service.leases)

    def test_preflight_preserves_original_and_lease_cleanup_failure(self):
        from unittest.mock import patch
        from harness.project.board import BoardError
        primary, cleanup = OSError('Original Git failure'), OSError('Original lease failure')
        service = ProjectExecution(SimpleNamespace())
        lease = SimpleNamespace(close=Mock(side_effect=cleanup))
        evidence = SimpleNamespace(snapshot=Mock(return_value={'available':True,'head':'a'*40,'branch':'main','root':'/fixture'}),
                                   _run=Mock(side_effect=[SimpleNamespace(returncode=0,stdout=b'/fixture/.git'), primary]))
        with patch('harness.project.execution.GitEvidence',return_value=evidence), patch(
                'harness.project.execution.RepositoryLease',return_value=lease):
            with self.assertRaises(BaseExceptionGroup) as error:service._preflight(Path('/fixture'),{})
        self.assertEqual(error.exception.exceptions,(primary,cleanup))
        self.assertNotIsInstance(error.exception,BoardError)

    async def test_launch_failure_releases_busy_scope_even_when_both_cleanups_fail(self):
        from unittest.mock import patch
        primary, preparation_error, lease_error = (OSError(message) for message in ('Original revision failure','Original preparation failure','Original lease failure'))
        root = Path('/fixture').resolve()
        task = {'id':'TASK','status':'READY','verification_policy':'git'}
        board = SimpleNamespace(read=Mock(side_effect=[{'revision':'r','tasks':[task]}, primary]))
        preparation = object()
        harness = SimpleNamespace(config=SimpleNamespace(enable_fs=True,enable_shell=True,api_key='fixture',api_keys=[],model_routes=[]),
            artifact_review=SimpleNamespace(verify=Mock()),runtime=SimpleNamespace(records={},live_records=Mock(return_value=[])),
            workspace_preparations=SimpleNamespace(claim=Mock(return_value=preparation),release=Mock(side_effect=preparation_error)))
        service = ProjectExecution(harness)
        lease = SimpleNamespace(close=Mock(side_effect=lease_error))
        with patch('harness.project.execution.TaskBoard',return_value=board), patch(
                'harness.project.execution.project_agent_selection',return_value=None), patch.object(
                service,'_preflight',return_value=(object(),{},root,lease)):
            with self.assertRaises(BaseExceptionGroup) as error:await service.launch(root,'TASK','r','request')
        self.assertEqual(error.exception.exceptions,(primary,preparation_error,lease_error))
        self.assertFalse(service.preparing)
        harness.workspace_preparations.release.assert_called_once_with(preparation)
        lease.close.assert_called_once()

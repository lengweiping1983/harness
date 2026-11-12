"""执行环境保留所属进程语义，不把能力探测当作平台验收。"""
import unittest
from unittest.mock import AsyncMock, patch

from harness.execution.environment import LocalExecutionEnvironment


class ExecutionEnvironmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_delegates_original_process_arguments(self):
        provider = LocalExecutionEnvironment()
        argv, env = ['python', '-V'], {'LANG': 'C.UTF-8'}
        process = object()
        with patch('harness.execution.environment.spawn_owned', AsyncMock(return_value=process)) as spawn:
            self.assertIs(await provider.spawn(argv, cwd='/work', env=env, shell=False), process)
        spawn.assert_awaited_once_with(argv, cwd='/work', env=env, shell=False)

    async def test_cleanup_error_is_not_swallowed(self):
        error = RuntimeError('cleanup failed')
        with patch('harness.execution.environment.spawn_owned', AsyncMock(side_effect=error)):
            with self.assertRaises(RuntimeError) as caught:
                await LocalExecutionEnvironment().spawn(['x'], cwd='/work', env={})
        self.assertIs(caught.exception, error)

    def test_capability_read_is_independent_and_does_not_launch(self):
        provider = LocalExecutionEnvironment()
        with patch('harness.execution.environment.spawn_owned') as spawn:
            snapshot = provider.capabilities().to_dict()
            snapshot['filesystem_isolation'] = 'sandbox'
            self.assertEqual(provider.capabilities().filesystem_isolation, 'host')
            self.assertEqual(provider.capabilities().evidence, 'implementation')
        spawn.assert_not_called()

    def test_unsupported_platform_is_reported_as_unavailable(self):
        with patch('harness.execution.environment.sys.platform', 'other'):
            self.assertEqual(LocalExecutionEnvironment().capabilities().process_cleanup, 'unavailable')

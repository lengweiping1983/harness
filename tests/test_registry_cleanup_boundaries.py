"""工具流水线不能把未确认的进程清理转成普通错误结果。"""
import unittest

from harness.llm.types import ToolSchema, ToolUseBlock
from harness.tools.processes import ProcessCleanupError
from harness.tools.registry import ToolDefinition, ToolRegistry, ToolRunContext


class RegistryCleanupBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_failure_propagates_from_each_hook_boundary(self):
        for stage in ('restriction', 'restriction_after_guard', 'restriction_before_execute', 'pre', 'guard', 'post'):
            for asynchronous in (False, True):
                with self.subTest(stage=stage, asynchronous=asynchronous):
                    registry = ToolRegistry()
                    executed = []
                    observed = []
                    failure = ProcessCleanupError('owned process still running', pid=123)

                    def execute(args, ctx):
                        executed.append(ctx.call_id)
                        return {'ok': True}

                    calls = 0
                    target = {'restriction_after_guard': 2, 'restriction_before_execute': 3}.get(stage, 1)

                    def fail(*args):
                        nonlocal calls
                        calls += 1
                        if calls == target:
                            raise failure

                    async def fail_async(*args):
                        return fail(*args)

                    registry.register(ToolDefinition(ToolSchema(name='probe', description='',
                        parameters={'type': 'object', 'properties': {}}), execute))
                    registration = {'restriction': registry.add_restriction,
                        'pre': registry.add_pre_hook, 'guard': registry.add_guard,
                        'post': registry.add_post_hook}.get(stage, registry.add_restriction)
                    registration(fail_async if asynchronous else fail)
                    registry.add_result_observer(lambda *args: observed.append(args))
                    with self.assertRaises(ProcessCleanupError) as caught:
                        await registry.execute_call(ToolUseBlock(id='call-1', name='probe', input={}),
                            ToolRunContext(call_id='', session_id='session-1', cwd=None, turn=1, step=1))
                    self.assertIs(caught.exception, failure)
                    self.assertEqual(executed, ['call-1'] if stage == 'post' else [])
                    self.assertEqual(observed, [])

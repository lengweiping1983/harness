"""收尾纠正工具执行后不应继续向模型发送已过期的状态判断。"""
import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.llm.transport import Transport
from harness.tools.registry import ToolDefinition


class CorrectionTransport(Transport):
    def __init__(self):
        self.requests = []

    async def post_stream(self, url, *, headers, body, timeout):
        self.requests.append(json.loads(body))
        if len(self.requests) == 2:
            delta = {'tool_calls': [{'index': 0, 'id': 'collect', 'function': {
                'name': 'collect_result', 'arguments': '{}'}}]}
            finish = 'tool_calls'
        else:
            delta, finish = {'content': '已汇总真实结果'}, 'stop'
        for chunk in ({'choices': [{'delta': delta, 'finish_reason': None}]},
                      {'choices': [{'delta': {}, 'finish_reason': finish}]}):
            yield ('data: '+json.dumps(chunk)+'\n\n').encode()
        yield b'data: [DONE]\n\n'


class CompletionFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_corrective_tool_drops_stale_feedback_and_rechecks_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            flags = {f.name: False for f in fields(HarnessConfig) if f.name.startswith('enable_')}
            transport = CorrectionTransport()
            app = Harness(HarnessConfig(api_key='fixture-only', home=Path(directory)/'state', cwd=directory,
                model='fixture', max_steps=4, max_retries=0, **flags), transport=transport)
            self.addAsyncCleanup(app.close)
            collected = False
            checked = []
            def collect(args, context):
                nonlocal collected
                collected = True
                return '真实结果已取回'
            app.tools.register(ToolDefinition({'name': 'collect_result', 'description': '读取结果',
                'parameters': {'type': 'object', 'properties': {}}}, collect, effect='read'))
            session = app.create_session()
            agent = app._agent_for(session)
            async def guard(*args):
                checked.append(collected)
                return None if collected else '尚未取回成员结果：请调用 collect_result'
            agent.stop_guard = guard
            result = await agent.prompt(session, '汇总结果')
            self.assertEqual(result.reason, 'completed', result.error)
            self.assertEqual(checked, [False, True])
            self.assertEqual(len(transport.requests), 3)
            def has_feedback(request):
                return any(m.get('role') in ('system', 'developer') and
                    '尚未取回成员结果' in str(m.get('content')) for m in request['messages'])
            self.assertTrue(has_feedback(transport.requests[1]))
            self.assertFalse(has_feedback(transport.requests[2]))

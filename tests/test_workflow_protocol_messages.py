"""工作流损坏协议明确拒绝，不转换身份或补造参数后执行。"""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.workflow_host import WorkflowGuest, WorkflowLimits


class WorkflowProtocolMessageTests(unittest.IsolatedAsyncioTestCase):
    def guest(self, messages):
        guest = WorkflowGuest(SimpleNamespace(), script='', meta={'name': 'Original'}, args=None, limits=WorkflowLimits())
        reader = asyncio.StreamReader()
        reader.feed_data(b'\n'.join(messages) + b'\n')
        reader.feed_eof()
        guest._proc = SimpleNamespace(stdout=reader)
        guest._serve = AsyncMock()
        return guest

    async def test_invalid_frames_are_rejected_before_dispatch_in_each_language(self):
        request = {'id': 1, 'method': 'begin', 'params': {}}
        final = {'final': True, 'ok': True, 'result': {'value': 'Original 原文'}}
        values = [None, [], 1, {}, {**request, 'id': True}, {**request, 'id': 1.0},
                  {**request, 'id': '1'}, {**request, 'id': 0}, {**request, 'id': -1},
                  {'id': 1, 'method': 'begin'}, {**request, 'params': None},
                  {**request, 'params': []}, {**request, 'method': False},
                  {**request, 'extra': 1}, {**final, 'final': 1}, {**final, 'ok': 1},
                  {'final': True, 'ok': True}, {**final, 'error': 'extra'},
                  {'final': True, 'ok': False, 'error': 1},
                  {**request, 'params': {'value': float('nan')}}]
        frames = [json.dumps(value).encode() for value in values] + [b'{broken', b'{"id":1,"id":2,"method":"begin","params":{}}', b'{"id":1,"method":"begin","params":{"x":"\xff"}}']
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for frame in frames:
                    guest = self.guest([frame, json.dumps(final).encode()])
                    with self.subTest(language=language, frame=frame), self.assertRaises(ToolExecutionError) as error:
                        await guest._read_stdout()
                    self.assertEqual(error.exception.code, 'workflow/invalid-message')
                    self.assertEqual(error.exception.message, tr('workflow.protocol.invalid_message'))
                    guest._serve.assert_not_awaited()
                    self.assertEqual(guest._rpc_tasks, set())

    async def test_reused_request_identity_cannot_dispatch_twice(self):
        request = b'{"id":1,"method":"startChild","params":{"prompt":"Original"}}'
        guest = self.guest([request, request])
        with self.assertRaises(ToolExecutionError):
            await guest._read_stdout()
        await asyncio.gather(*tuple(guest._rpc_tasks))
        guest._serve.assert_awaited_once_with(1, 'startChild', {'prompt': 'Original'})

    async def test_valid_parallel_requests_and_both_final_states_keep_values(self):
        for final in ({'final': True, 'ok': True, 'result': {'value': 'Original 原文😀'}},
                      {'final': True, 'ok': False, 'error': 'Original failure {params}'}):
            guest = self.guest([
                b'  ', b'{"id":1,"method":"begin","params":{}}',
                b'{"id":2,"method":"progress","params":{"events":[]}}',
                json.dumps(final, ensure_ascii=False).encode(),
            ])
            self.assertEqual(await guest._read_stdout(), final)
            await asyncio.gather(*tuple(guest._rpc_tasks))
            self.assertEqual(guest._serve.await_count, 2)

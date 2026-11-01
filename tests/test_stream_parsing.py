"""验证模型流分片、非法事件和不完整调用的解析边界。"""
import json
import unittest
from unittest.mock import Mock

from harness.core.errors import LlmResponseError
from harness.llm.adapter import OpenRouterAdapter
from harness.llm.transport import parse_sse_stream
from harness.tools.registry import ToolDefinition, ToolRegistry, ToolRunContext


async def stream(parts):
    for part in parts:
        yield part


def event(payload):
    return ('data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n').encode()


class StreamParsingTests(unittest.IsolatedAsyncioTestCase):
    async def test_utf8_and_crlf_survive_every_byte_boundary(self):
        payload = {'choices': [{'delta': {'content': '中文🙂'}}]}
        wire = event(payload).replace(b'\n', b'\r\n')
        for split in range(len(wire) + 1):
            with self.subTest(split=split):
                self.assertEqual([value async for value in parse_sse_stream(stream([wire[:split], wire[split:]]))], [payload])

    async def test_multiline_comments_empty_events_and_done(self):
        wire = b': heartbeat\r\ndata:\r\n\r\ndata: {\r\ndata: "value": 1}\r\n\r\ndata: [DONE]\r\n\r\ndata: invalid\r\n\r\n'
        self.assertEqual([value async for value in parse_sse_stream(stream([wire]))], [{'value': 1}])

    async def test_invalid_json_is_an_explicit_model_response_error(self):
        with self.assertRaises(LlmResponseError):
            _ = [value async for value in parse_sse_stream(stream([b'data: {broken}\n\n']))]

    async def test_non_object_json_is_rejected_before_consumers_access_fields(self):
        for payload in ([], [1], None, True, 1, 'text'):
            with self.subTest(payload=payload):
                with self.assertRaises(LlmResponseError) as raised:
                    _ = [value async for value in parse_sse_stream(stream([event(payload)]))]
                self.assertEqual(raised.exception.code, 'llm/invalid-sse-event')

    async def test_truncated_utf8_is_rejected(self):
        with self.assertRaises(LlmResponseError) as raised:
            _ = [value async for value in parse_sse_stream(stream([b'data: {"text":"\xe4']))]
        self.assertEqual(raised.exception.code, 'llm/invalid-utf8')

    async def test_unfinished_stream_never_emits_tool_use_or_done(self):
        adapter = OpenRouterAdapter(api_key='fixture-only')
        wire = event({'choices': [{'delta': {'tool_calls': [
            {'index': 0, 'id': 'call', 'function': {'name': 'write', 'arguments': '{"path":"test"}'}}]}}]})
        received = []
        with self.assertRaises(LlmResponseError) as raised:
            async for value in adapter._consume(stream([wire])):
                received.append(value.kind)
        self.assertEqual(raised.exception.code, 'llm/incomplete-response')
        self.assertNotIn('tool_use', received)
        self.assertNotIn('done', received)

    async def test_fragmented_tool_call_is_emitted_once_after_completion(self):
        adapter = OpenRouterAdapter(api_key='fixture-only')
        chunks = [
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call', 'function': {'name': 'write', 'arguments': '{"path":'}}]}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"中文.txt"}'}}]}, 'finish_reason': 'tool_calls'}]},
        ]
        received = [value async for value in adapter._consume(stream([*(event(chunk) for chunk in chunks), b'data: [DONE]\n\n']))]
        calls = [value.tool_use for value in received if value.kind == 'tool_use']
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0].id, calls[0].name, calls[0].input), ('call', 'write', {'path': '中文.txt'}))
        self.assertEqual(received[-1].kind, 'done')

    async def test_invalid_json_never_reaches_a_permissive_tool(self):
        execute = Mock(return_value='不应执行')
        registry = ToolRegistry()
        registry.register(ToolDefinition({'name': 'action', 'parameters': {'type': 'object'}}, execute))
        adapter = OpenRouterAdapter(api_key='fixture-only')
        wire = event({'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call',
            'function': {'name': 'action', 'arguments': '{broken'}}]}, 'finish_reason': 'tool_calls'}]})
        failure = None
        try:
            async for value in adapter._consume(stream([wire])):
                if value.kind == 'tool_use':
                    await registry.execute_call(value.tool_use, ToolRunContext(call_id='call', session_id='session', cwd='', turn=1, step=1))
        except LlmResponseError as error:
            failure = error
        execute.assert_not_called()
        self.assertIsNotNone(failure)
        self.assertEqual(failure.code, 'llm/invalid-tool-response')

    async def test_non_object_or_error_marker_arguments_are_rejected(self):
        for arguments in ('[]', 'null', 'true', '"text"', '{"__parse_error__":"invalid"}'):
            with self.subTest(arguments=arguments):
                adapter = OpenRouterAdapter(api_key='fixture-only')
                wire = event({'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call',
                    'function': {'name': 'action', 'arguments': arguments}}]}, 'finish_reason': 'tool_calls'}]})
                with self.assertRaises(LlmResponseError) as raised:
                    _ = [value async for value in adapter._consume(stream([wire]))]
                self.assertEqual(raised.exception.code, 'llm/invalid-tool-response')

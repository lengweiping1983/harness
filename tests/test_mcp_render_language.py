"""MCP完整摘要按请求语言生成，第三方文本与协议身份保持。"""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.mcp_tools import build_mcp_resource_tools, _render_tool_result, MAX_TEXT_CHARS


class McpRenderLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_resource_tool_preserves_server_content_and_language(self):
        payload = {'contents': [
            {'uri': 'fixture://Original/{user}', 'mimeType': 'text/Original', 'text': 'Original 原文 {user}😀'},
            {'uri': 'fixture://binary', 'mimeType': 'application/Original', 'blob': 'QUJD'},
            {'uri': 'fixture://long', 'text': '😀' * (MAX_TEXT_CHARS + 1)},
        ]}
        original = copy.deepcopy(payload)
        request = AsyncMock(return_value=payload)
        registry = SimpleNamespace(get=lambda name: SimpleNamespace(request_resource=request))
        tool = next(item for item in build_mcp_resource_tools(registry) if item.schema.name == 'read_mcp_resource')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                result = await tool.execute({'server': 'Original-{server}', 'uri': 'fixture://Original/{user}'}, None)
                text = result.content[0].text
                self.assertIn('Original 原文 {user}😀', text)
                self.assertIn('fixture://Original/{user}', text)
                self.assertIn('(text/Original)' if language == 'en' else '（text/Original）', text)
                self.assertIn(tr('mcp.render.resourceHeading', server='Original-{server}'), text)
                self.assertIn(tr('mcp.render.binaryLength', count=4), text)
                self.assertIn(tr('mcp.render.truncatedText', text='😀'*MAX_TEXT_CHARS), text)
                self.assertNotIn('😀'*(MAX_TEXT_CHARS+1), text)
                self.assertEqual(result.data, original)
        self.assertEqual(payload, original)
        self.assertEqual(request.await_count, 3)

    async def test_media_summary_is_complete_and_external_content_is_literal(self):
        value = {'content': [
            {'type': 'text', 'text': 'Original 原文 {mime}'},
            {'type': 'image', 'mimeType': 'image/Original', 'data': 'QUJD'},
            {'type': 'audio', 'mimeType': 'audio/Original', 'data': 'QUJD'},
            {'type': 'resource_link', 'name': 'Original {name}', 'uri': 'fixture://原文'},
            {'type': 'resource', 'resource': {'uri': 'fixture://inline', 'text': 'Inline 原文'}},
        ]}
        original = copy.deepcopy(value)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                result = _render_tool_result(value)
                self.assertIn('Original 原文 {mime}', result)
                self.assertIn('Inline 原文', result)
                expected = '[Image image/Original, base64 data omitted; characters: 4]' if language == 'en' else '[图片 image/Original，base64 数据已省略；字符数：4]'
                self.assertIn(expected, result)
                self.assertIn(tr('mcp.render.audio', mime='audio/Original'), result)
                self.assertIn(tr('mcp.render.resourceLink', name='Original {name}', uri='fixture://原文'), result)
        self.assertEqual(value, original)

    async def test_all_resource_tools_reject_bad_arguments_before_lookup(self):
        from unittest.mock import Mock
        registry = SimpleNamespace(get=Mock())
        tools = build_mcp_resource_tools(registry)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for tool in tools:
                    for args, key in (([], 'argumentsObject'), ({}, 'serverRequired'),
                                      ({'server': 'Original', 'cursor': 3}, 'cursorString')):
                        with self.subTest(language=language, tool=tool.schema.name, args=args), self.assertRaises(ToolExecutionError) as error:
                            await tool.execute(args, None)
                        self.assertEqual(error.exception.message, tr('mcp.render.'+key, tool=tool.schema.name))
        registry.get.assert_not_called()


class McpTransportLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_failures_keep_cause_process_identity_and_raw_detail(self):
        import asyncio
        from unittest.mock import Mock, patch
        from harness.tools.builtin.mcp_client import McpConnectionLost, McpError, McpServerConfig, StdioTransport

        config = McpServerConfig('fixture', command='fixture-command')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                transport = StdioTransport(config)
                failure = OSError('Original 原文 {detail}')
                with patch('harness.tools.builtin.mcp_client.asyncio.create_subprocess_exec', new=AsyncMock(side_effect=failure)) as spawn:
                    with self.assertRaises(McpError) as caught:
                        await transport.start()
                    self.assertIs(caught.exception.__cause__, failure)
                    self.assertEqual(str(caught.exception), tr('python.mcp_client.03f460c04284',
                        server=repr(config.server_name), command=repr(config.command), detail=str(failure)))
                    spawn.assert_awaited_once()
                self.assertIsNone(transport._proc)
                pipe_error = BrokenPipeError('Original 管道 {detail}')
                proc = SimpleNamespace(stdin=SimpleNamespace(write=Mock(), drain=AsyncMock(side_effect=pipe_error), close=Mock()),
                    stdout=SimpleNamespace(readline=AsyncMock(return_value=b'')), returncode=7, pid=313)
                transport._proc = proc
                with self.assertRaises(McpConnectionLost) as caught:
                    await transport.send({'jsonrpc': '2.0', 'method': 'original/{method}'})
                self.assertEqual(str(caught.exception), tr('python.mcp_client.5e804f0369f7', detail=str(pipe_error)))
                self.assertIs(caught.exception.__cause__, pipe_error)
                proc.stdin.write.assert_called_once()
                with self.assertRaises(McpConnectionLost) as caught:
                    await transport.receive()
                self.assertEqual(str(caught.exception), tr('python.mcp_client.701a06716c37', code=7))
                proc.returncode = None
                proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
                proc.terminate, proc.kill = Mock(), Mock()
                with self.assertRaises(McpError) as caught:
                    await transport.close()
                self.assertEqual(str(caught.exception), tr('python.mcp_client.f28817e37533', pid=313))
                self.assertIs(transport._proc, proc)
                self.assertEqual(proc.communicate.await_count, 2)
                proc.terminate.assert_called_once()
                proc.kill.assert_called_once()

    async def test_http_and_connection_failures_do_not_replay_requests(self):
        import asyncio
        from unittest.mock import patch
        from harness.core.errors import LlmError
        from harness.tools.builtin.mcp_client import McpConnection, McpConnectionLost, McpError, McpServerConfig, McpServerSupervisor, StreamableHttpTransport

        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                config = McpServerConfig('fixture', transport='streamable-http', url='https://fixture.invalid/mcp')
                transport = StreamableHttpTransport(config)
                await transport.start()
                for status in (None, 401):
                    original = LlmError('Original 原文 {detail}', **({'status': status} if status else {}))
                    calls = []
                    async def stream(*args, **kwargs):
                        calls.append((args, kwargs))
                        raise original
                        yield b''
                    with patch('harness.tools.builtin.mcp_client._McpHttpStream.post_stream', side_effect=stream):
                        kind = McpConnectionLost if status is None else McpError
                        with self.assertRaises(kind) as caught:
                            await transport.send({'jsonrpc': '2.0', 'id': 1, 'method': 'original/{method}'})
                    expected = tr('python.mcp_client.00cb7479f314', detail=str(original)) if status is None else str(original)
                    self.assertEqual(str(caught.exception), expected)
                    self.assertIs(caught.exception.__cause__, original)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(transport._active_sends, set())
                io = SimpleNamespace(start=AsyncMock(side_effect=asyncio.TimeoutError), close=AsyncMock())
                connection = McpConnection(config, transport=io)
                with self.assertRaises(McpError) as caught:
                    await connection.open()
                self.assertEqual(str(caught.exception), tr('python.mcp_client.015bcb4fab79', server='fixture'))
                io.start.assert_awaited_once()
                io.close.assert_awaited_once()
                io = SimpleNamespace(send=AsyncMock(side_effect=asyncio.TimeoutError))
                connection = McpConnection(config, transport=io)
                with self.assertRaises(McpError) as caught:
                    await connection.notify('original/{method}')
                self.assertEqual(str(caught.exception), tr('python.mcp_client.227fbddfad99', method='original/{method}'))
                io.send.assert_awaited_once()
                read_error = RuntimeError('Original 读取 {detail}')
                io.receive = AsyncMock(side_effect=read_error)
                future = asyncio.get_running_loop().create_future()
                connection._pending[1] = future
                await connection._read_loop()
                self.assertTrue(connection.closed)
                with self.assertRaises(McpConnectionLost) as caught:
                    await future
                self.assertEqual(str(caught.exception), tr('python.mcp_client.301e317de0da', detail=str(read_error)))
                service = McpServerSupervisor(config)
                service._connection = SimpleNamespace()
                with self.assertRaises(McpError) as caught:
                    await service.request_resource('original/{method}')
                self.assertEqual(str(caught.exception), tr('python.mcp_client.311642c47e8f', method='original/{method}'))

    def test_remote_error_fields_and_tool_text_remain_literal_and_bounded(self):
        import json
        from harness.tools.builtin.mcp_client import _describe_error, _describe_tool_error

        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for code in (None, 0, -32601):
                    for message in (None, '', 'Original 原文 {message}'):
                        for data in (None, {}, {'original': '{data}' * 100}):
                            error = {'code': code, 'message': message, 'data': data}
                            text = _describe_error(error)
                            if code is not None:
                                self.assertIn(str(code), text)
                            if message:
                                self.assertIn(message, text)
                            if data is not None:
                                raw = json.dumps(data, ensure_ascii=False, default=str)
                                self.assertIn(raw[:400], text)
                                if len(raw) > 400:
                                    self.assertNotIn(raw, text)
                raw = 'Original 原文 {detail}\nsecond'
                self.assertEqual(_describe_tool_error({'content': [{'type': 'text', 'text': raw}]}),
                    tr('python.mcp_client.f3bfc8c8b8d6', detail=raw))
                self.assertEqual(_describe_tool_error({'content': []}), tr('python.mcp_client.0c27fb1f0fe5'))

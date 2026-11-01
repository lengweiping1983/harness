"""损坏的远端工具分页不能被当作成功目录发布。"""
import copy
import unittest
from unittest.mock import AsyncMock

from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.mcp_client import (
    McpConnection, McpError, McpRemoteTool, McpServerConfig, McpServerSupervisor, public_tool_name,
)


TOOL = {'name': 'new', 'description': 'Original 描述 {name}', 'inputSchema': {'type': 'object'}}


class McpCatalogIntegrityTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, pages):
        config = McpServerConfig('fixture', command='fixture', reconnect_enabled=False)
        connection = McpConnection(config)
        connection.request = AsyncMock(side_effect=copy.deepcopy(pages))
        supervisor = McpServerSupervisor(config)
        supervisor._connection = connection
        supervisor._install_generation([McpRemoteTool('old', 'Original old', {'type': 'object'})])
        return supervisor, connection

    async def test_invalid_second_page_preserves_entire_previous_generation(self):
        bad_tools = [7, {}, {**TOOL, 'name': ''}, {**TOOL, 'name': 7},
            {**TOOL, 'description': None}, {**TOOL, 'description': 7},
            {'name': 'missing-schema'}, {**TOOL, 'inputSchema': None},
            {**TOOL, 'inputSchema': {'type': 'array'}}, {**TOOL, 'annotations': []}]
        bad_pages = [{}, {'tools': None}, {'tools': {}}, {'tools': 'private-secret'}]
        bad_pages += [{'tools': [tool]} for tool in bad_tools]
        for language in ('en', 'zh-CN', 'en'):
            for invalid in bad_pages:
                with self.subTest(language=language, invalid=invalid), current_language.set(language):
                    service, connection = self.fixture([{'tools': [TOOL], 'nextCursor': 'next'}, invalid])
                    self.addAsyncCleanup(service.aclose)
                    with self.assertRaises(McpError) as caught:
                        await service.refresh()
                    self.assertIn(str(caught.exception), [tr('mcp.catalog.invalid_page'), tr('mcp.catalog.invalid_tool', index=2)])
                    self.assertNotIn('private-secret', str(caught.exception))
                    self.assertEqual(service.public_names(), [public_tool_name('fixture', 'old')])
                    self.assertIsNone(service.remote_tool(public_tool_name('fixture', 'new')))
                    self.assertEqual(connection.request.await_count, 2)

    async def test_repeated_cursor_fails_instead_of_publishing_partial_tools(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language), current_language.set(language):
                service, connection = self.fixture([{'tools': [TOOL], 'nextCursor': 'loop'}, {'tools': [], 'nextCursor': 'loop'}])
                self.addAsyncCleanup(service.aclose)
                with self.assertRaises(McpError) as caught:
                    await service.refresh()
                self.assertEqual(str(caught.exception), tr('mcp.catalog.repeated_cursor'))
                self.assertEqual(service.public_names(), [public_tool_name('fixture', 'old')])
                self.assertEqual(connection.request.await_count, 2)

    async def test_non_string_cursor_is_not_a_successful_end_of_catalog(self):
        for language in ('en', 'zh-CN', 'en'):
            for cursor in (None, 7, False, []):
                with self.subTest(language=language, cursor=cursor), current_language.set(language):
                    service, connection = self.fixture([{'tools': [TOOL], 'nextCursor': cursor}])
                    self.addAsyncCleanup(service.aclose)
                    with self.assertRaises(McpError) as caught:
                        await service.refresh()
                    self.assertEqual(str(caught.exception), tr('mcp.catalog.invalid_cursor'))
                    self.assertEqual(service.public_names(), [public_tool_name('fixture', 'old')])
                    connection.request.assert_awaited_once_with('tools/list', None)

    async def test_current_pagination_keeps_opaque_empty_cursor_and_original_schema(self):
        schema = {'type': 'object', 'properties': {'text': {'type': 'string', 'description': 'Original 原文'}},
            '$defs': {'extra': {'type': 'string'}}, 'additionalProperties': False}
        pages = [{'tools': [{**TOOL, 'inputSchema': schema, 'annotations': {'readOnlyHint': True}}], 'nextCursor': ''},
            {'tools': [{'name': 'second', 'inputSchema': {'type': 'object'}}], '_meta': {'Original': '原文'}}]
        original = copy.deepcopy(pages)
        service, connection = self.fixture(pages)
        self.addAsyncCleanup(service.aclose)
        tools = await service.refresh()
        self.assertEqual([tool.name for tool in tools], ['new', 'second'])
        self.assertEqual(tools[0].description, TOOL['description'])
        self.assertEqual(tools[0].input_schema, schema)
        self.assertEqual(tools[0].annotations, {'readOnlyHint': True})
        self.assertEqual(tools[1].description, '')
        self.assertEqual(tools[1].annotations, {})
        self.assertEqual(connection.request.await_args_list[1].args, ('tools/list', {'cursor': ''}))
        self.assertEqual(pages, original)
        self.assertEqual(service.public_names(), [public_tool_name('fixture', name) for name in ('new', 'second')])

    async def test_legitimate_empty_catalog_replaces_previous_generation(self):
        service, connection = self.fixture([{'tools': []}])
        self.addAsyncCleanup(service.aclose)
        self.assertEqual(await service.refresh(), [])
        self.assertEqual(service.public_names(), [])
        connection.request.assert_awaited_once_with('tools/list', None)

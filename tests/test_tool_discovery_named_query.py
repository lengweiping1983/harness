"""查询包含真实工具名时，附加描述词不能把已提供能力过滤为空。"""
import unittest

from harness.core.language import current_language
from harness.session.store import SessionStore
from harness.tools.builtin.present_tools import build_present_tools
from harness.tools.builtin.todo_tools import TodoStore, build_todo_tools
from harness.tools.discovery import ToolDiscovery, build_tool_search
from harness.i18n import tr
from harness.tools.registry import ToolDefinition, ToolRegistry


class NamedToolQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_loaded_todos_are_searchable_without_activation_or_scope_expansion(self):
        registry = ToolRegistry()
        tools = build_todo_tools(TodoStore())
        search = build_tool_search()
        for tool in [*tools, search]:
            registry.register(tool)
        discovery = ToolDiscovery()
        session = SessionStore().create()
        discovery.bind(session)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                schemas = [schema.resolve_description(language) for schema in registry.schemas()]
                result = await discovery.search({'query': 'todo_write todo_read'}, schemas, registry)
                self.assertEqual({row['name'] for row in result['tools']}, {'todo_write', 'todo_read'})
                self.assertEqual(result['total_matches'], 2)
                self.assertEqual(result['tools'][0]['description'], next(
                    schema.description[:240] for schema in schemas if schema.name == result['tools'][0]['name']))
                self.assertEqual((await discovery.search({'query': 'todo_read'}, schemas, registry))['total_matches'], 1)
                scoped = [schema for schema in schemas if schema.name == 'todo_read']
                restricted = await discovery.search({'query': 'todo_write todo_read'}, scoped, registry)
                self.assertEqual([row['name'] for row in restricted['tools']], ['todo_read'])
                self.assertEqual((await discovery.search({'query': ''}, scoped, registry))['total_matches'], 1)
                hidden = await discovery.search({'query': 'todo_write'}, scoped, registry)
                self.assertFalse(any(row['name'] == 'todo_write' for row in hidden['tools']))
                self.assertEqual((await discovery.search({'query': 'todo_write todo_read'}, [], registry))['tools'], [])
                self.assertEqual(next(schema.description for schema in schemas if schema.name == 'search_tools'),
                                 tr('tool.discovery.description'))
        self.assertIsNone(session.latest('note'))
        self.assertFalse(discovery.loaded)

    async def test_present_with_extra_english_words_is_found_in_both_catalog_languages(self):
        registry = ToolRegistry()
        tool = build_present_tools()[0]
        tool.defer_loading = True
        tool.discovery_namespace = 'builtin:present'
        registry.register(tool)
        async def unused(args, ctx):
            self.fail('发现工具不能执行目标操作')
        hyphenated = ToolDefinition({'name': 'asset-preview', 'description': '原文预览',
            'parameters': {'type': 'object'}}, unused, defer_loading=True)
        registry.register(hyphenated)
        related = ToolDefinition({'name': 'asset_info', 'description': 'present file preview asset-preview export',
            'parameters': {'type': 'object'}}, unused)
        registry.register(related)
        discovery = ToolDiscovery()
        for language in ('en', 'zh-CN', 'en'):
            session = SessionStore().create()
            discovery.bind(session)
            with current_language.set(language):
                schemas = [item.schema.resolve_description(language) for item in (tool, hyphenated, related)]
                result = await discovery.search({'query': 'present file preview'}, schemas, registry)
                self.assertEqual([row['name'] for row in result['tools']], ['present'])
                self.assertEqual(result['total_matches'], 1)
                self.assertIn('present', discovery.loaded)
                self.assertEqual(session.latest('note').data['tools'], ('present',))
                hidden = await discovery.search({'query': 'present file preview'}, [], registry)
                self.assertEqual(hidden['tools'], [])
                unrelated = await discovery.search({'query': 'unavailable renderer'}, schemas, registry)
                self.assertEqual(unrelated['tools'], [])
                prefixed = await discovery.search({'query': 'presenter file preview'}, schemas, registry)
                self.assertEqual(prefixed['tools'], [])
                punctuated = await discovery.search({'query': 'asset-preview file export'}, schemas, registry)
                self.assertEqual([row['name'] for row in punctuated['tools']], ['asset-preview'])
                extended = await discovery.search({'query': 'asset-preview-extra file export'}, schemas, registry)
                self.assertEqual(extended['tools'], [])
                mixed = await discovery.search({'query': 'asset_info present'}, schemas, registry)
                self.assertEqual({row['name'] for row in mixed['tools']}, {'asset_info', 'present'})
                self.assertNotIn('asset_info', discovery.loaded)

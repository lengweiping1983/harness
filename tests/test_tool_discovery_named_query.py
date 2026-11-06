"""查询包含真实工具名时，附加描述词不能把已提供能力过滤为空。"""
import unittest

from harness.core.language import current_language
from harness.session.store import SessionStore
from harness.tools.builtin.present_tools import build_present_tools
from harness.tools.discovery import ToolDiscovery
from harness.tools.registry import ToolDefinition, ToolRegistry


class NamedToolQueryTests(unittest.IsolatedAsyncioTestCase):
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
        discovery = ToolDiscovery()
        for language in ('en', 'zh-CN', 'en'):
            session = SessionStore().create()
            discovery.bind(session)
            with current_language.set(language):
                schemas = [item.schema.resolve_description(language) for item in (tool, hyphenated)]
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

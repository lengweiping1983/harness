"""分类按显式键解析；工具标题不是按原文猜测翻译的产品文案。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness.core.language import current_language
from harness.i18n import resources, tr
from harness.llm.types import ToolSchema
from harness.tools.categories import CATEGORIES, resolve
from harness.tools.registry import ToolDefinition, ToolRegistry
from harness.web.views import tool_catalog


class ToolCatalogResourceIdentityTests(unittest.TestCase):
    def test_same_category_instances_resolve_labels_at_read_time(self):
        instances = list(CATEGORIES)
        ids = [category.id for category in instances]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch('harness.i18n.localize_text', side_effect=AssertionError('reverse lookup'), create=True):
                for category in instances:
                    self.assertEqual(category.label, tr(category.label_key))
                    self.assertIs(resolve(category.id, 'fixture'), category)
                self.assertEqual(resolve('mcp', 'fixture').label, 'MCP')
        self.assertEqual([category.id for category in CATEGORIES], ids)

    def test_background_tool_collection_uses_same_plural_label_as_panel(self):
        async def execute(args, ctx):
            return 'Original result 原文'
        registry = ToolRegistry()
        for name in ('job_list', 'job_output', 'job_kill'):
            registry.register(ToolDefinition(ToolSchema(name=name, description='Original description 原文',
                parameters={'type': 'object', 'properties': {}}), execute))
        host = SimpleNamespace(tools=registry)
        category = resolve('job', 'job_list')
        for language, expected in (('en', 'Background tasks'), ('zh-CN', '后台任务'), ('en', 'Background tasks')):
            with current_language.set(language):
                catalog = tool_catalog(host)
                self.assertEqual([item['name'] for item in catalog], ['job_kill', 'job_list', 'job_output'])
                self.assertTrue(all(item['category'] == 'job' and item['category_label'] == expected for item in catalog))
                self.assertIs(resolve('job', 'job_list'), category)
                self.assertEqual(category.label, expected)
                self.assertEqual(tr('ui.components.inspector.Inspector.75b269496f69'), expected)

    def test_catalog_preserves_custom_title_even_with_internal_executor_and_matching_product_text(self):
        original = resources('zh-CN')['python.presets.ea932f93ed9d']
        async def execute(args, ctx):
            return 'Original result 原文'
        execute.__module__ = 'harness.tools.fixture'
        registry = ToolRegistry()
        registry.register(ToolDefinition(ToolSchema(name='FixtureTool', description='Original description 原文',
            parameters={'type': 'object', 'properties': {}}), execute, title=original, category='file'))
        host = SimpleNamespace(tools=registry)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch('harness.i18n.localize_text', side_effect=AssertionError('reverse lookup'), create=True):
                item = tool_catalog(host)[0]
                self.assertEqual(item['name'], 'FixtureTool')
                self.assertEqual(item['title'], original)
                self.assertEqual(item['category'], 'file')
                self.assertEqual(item['category_label'], resolve('file', 'FixtureTool').label)

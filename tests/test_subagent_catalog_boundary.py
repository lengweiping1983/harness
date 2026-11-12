"""模型目录读取有界且严格，工作线程保留调用语言和原始模型数据。"""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.subagent_model_tools import (
    MAX_CATALOG_BYTES, MAX_MODELS, _fetch_catalog, build_list_subagent_models_tool,
)


class SubagentCatalogBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_catalog_read_limit_is_enforced_before_json_parsing(self):
        for language in ('en', 'zh-CN', 'en'):
            raw = b'x' * (MAX_CATALOG_BYTES + 1)
            with current_language.set(language), patch('harness.tools.builtin.subagent_model_tools.read_catalog', return_value=raw):
                with self.assertRaises(ValueError) as error:
                    _fetch_catalog('https://fixture.invalid', '')
                self.assertEqual(str(error.exception), tr('tools.models.catalogTooLarge', limit=MAX_CATALOG_BYTES))


    def test_invalid_utf8_json_and_structure_are_not_empty_catalogs(self):
        for raw in (b'{', b'null', b'[]', b'{}', b'{"data":{}}', b'{"data":[null]}', b'{"data":[{"name":"\xff"}]}'):
            for language in ('en', 'zh-CN', 'en'):
                with self.subTest(raw=raw, language=language), current_language.set(language):
                    with patch('harness.tools.builtin.subagent_model_tools.read_catalog', return_value=raw):
                        with self.assertRaises(ValueError) as error:
                            _fetch_catalog('https://fixture.invalid', '')
                    self.assertEqual(str(error.exception), tr('tools.models.catalogInvalid'))

    def test_valid_empty_and_unicode_catalogs_preserve_original_fields(self):
        for data in ([], [{'id': 'fixture/model', 'name': 'Original 模型', 'extra': {'original': True}}]):
            with patch('harness.tools.builtin.subagent_model_tools.read_catalog', return_value=json.dumps({'data': data}, ensure_ascii=False).encode()):
                self.assertEqual(_fetch_catalog('https://fixture.invalid', ''), data)

    async def test_worker_failure_uses_each_call_language_on_same_tool(self):
        tool = build_list_subagent_models_tool(base_url='https://fixture.invalid', model='original/model')
        context = SimpleNamespace()
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch('harness.tools.builtin.subagent_model_tools.read_catalog', return_value=b'{"data":null}'):
                result = await tool.execute({'provider': 'openrouter'}, context)
                self.assertIn(tr('tools.models.catalogInvalid'), result.content[0].text)
                self.assertIn('original/model', result.content[0].text)
                self.assertEqual(result.data['models'], [])

    async def test_complete_model_count_and_truncation_copy_keep_original_names(self):
        tool = build_list_subagent_models_tool(base_url='https://fixture.invalid', model='original/model')
        for size in (2, MAX_MODELS + 1):
            rows = [{'id': f'fixture/{index:03}', 'name': f'Original 模型 {index}'} for index in range(size)]
            for language in ('en', 'zh-CN', 'en'):
                with self.subTest(size=size, language=language), current_language.set(language):
                    with patch('harness.tools.builtin.subagent_model_tools._fetch_catalog', return_value=rows):
                        result = await tool.execute({'provider': 'openrouter'}, SimpleNamespace())
                    key = 'tools.models.catalogTruncated' if size > MAX_MODELS else 'tools.models.catalogCount'
                    self.assertEqual(result.content[0].text.splitlines()[0], tr(key, provider='openrouter', count=size, limit=MAX_MODELS))
                    self.assertEqual(result.data['models'], rows[:MAX_MODELS])
                    self.assertEqual(result.data['model_count'], size)
                    self.assertEqual(result.data['truncated'], size > MAX_MODELS)
                    self.assertIn('Original 模型 0', result.content[0].text)

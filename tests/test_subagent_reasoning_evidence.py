"""模型目录未知能力不补造档位，空目录不伪造模型条目。"""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.subagent_model_tools import build_list_subagent_models_tool


class SubagentReasoningEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tool = build_list_subagent_models_tool(base_url='https://fixture.invalid', model='fixture/model')
        self.args = {'provider': 'openrouter', 'model': 'fixture/model'}

    async def test_missing_or_invalid_capability_does_not_advertise_default_efforts(self):
        entries = [{'id': 'fixture/model'}] + [{'id': 'fixture/model', 'supported_parameters': value}
                    for value in (None, 'reasoning', {}, [1])]
        for entry in entries:
            for language in ('en', 'zh-CN', 'en'):
                with self.subTest(entry=entry, language=language), current_language.set(language):
                    with patch('harness.tools.builtin.subagent_model_tools._fetch_catalog', return_value=[entry]):
                        result = await self.tool.execute(self.args, SimpleNamespace())
                    self.assertEqual(result.data['reasoning_efforts'], [])
                    self.assertEqual(result.data['reasoning_support'], 'unreported')
                    self.assertIsNone(result.data['reasoning_efforts_source'])
                    self.assertIn(tr('tools.models.reasoningUnreported'), result.content[0].text)

    async def test_successful_empty_or_other_model_catalog_does_not_fabricate_entry(self):
        for rows in ([], [{'id': 'other/model'}]):
            with self.subTest(rows=rows), patch('harness.tools.builtin.subagent_model_tools._fetch_catalog', return_value=rows):
                with self.assertRaises(ToolExecutionError):
                    await self.tool.execute(self.args, SimpleNamespace())

    async def test_unavailable_catalog_preserves_diagnostic_without_claiming_levels(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch('harness.tools.builtin.subagent_model_tools._fetch_catalog', side_effect=OSError('External original 原文')):
                result = await self.tool.execute(self.args, SimpleNamespace())
                self.assertFalse(result.data['catalog_available'])
                self.assertEqual(result.data['reasoning_efforts'], [])
                self.assertEqual(result.data['reasoning_support'], 'unreported')
                self.assertIn('External original 原文', result.content[0].text)
                self.assertIn(tr('tools.models.reasoningUnreported'), result.content[0].text)

    async def test_declared_support_and_explicit_absence_keep_distinct_evidence(self):
        for supported in (['reasoning'], ['temperature'], []):
            for language in ('en', 'zh-CN', 'en'):
                with self.subTest(supported=supported, language=language), current_language.set(language):
                    entry = {'id': 'fixture/model', 'name': 'Original 名称', 'supported_parameters': supported}
                    with patch('harness.tools.builtin.subagent_model_tools._fetch_catalog', return_value=[entry]):
                        result = await self.tool.execute(self.args, SimpleNamespace())
                    self.assertTrue(result.data['catalog_available'])
                    self.assertEqual(result.data['model'], entry)
                    if supported == ['reasoning']:
                        self.assertEqual(result.data['reasoning_support'], 'supported')
                        self.assertEqual(result.data['reasoning_efforts_source'], 'local_presets')
                        self.assertEqual([row['id'] for row in result.data['reasoning_efforts']], ['low', 'medium', 'high'])
                        self.assertEqual([row['label'] for row in result.data['reasoning_efforts']],
                                         ['Low', 'Medium', 'High'] if language == 'en' else ['低', '中', '高'])
                        self.assertIn(tr('tools.models.localReasoningNote'), result.content[0].text)
                    else:
                        self.assertEqual(result.data['reasoning_support'], 'unsupported')
                        self.assertEqual(result.data['reasoning_efforts'], [])
                        self.assertIsNone(result.data['reasoning_efforts_source'])

"""目录条目必须有唯一文本身份，直接SDK参数不隐式转型。"""
import json
import unittest
from unittest.mock import patch

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.subagent_model_tools import _fetch_catalog, build_list_subagent_models_tool


class SubagentCatalogIdentityTests(unittest.IsolatedAsyncioTestCase):
    def test_invalid_or_duplicate_model_identity_is_not_a_valid_catalog(self):
        cases=[{}, {'id':True}, {'id':1}, {'id':''}, {'id':'  '}, {'id':'fixture','name':{}}, {'id':'fixture','name':None}]
        for language in ('en','zh-CN','en'):
            for item in cases:
                with self.subTest(language=language,item=item), current_language.set(language), patch('harness.tools.builtin.subagent_model_tools.read_catalog',return_value=json.dumps({'data':[item]}).encode()):
                    with self.assertRaises(ValueError) as error:
                        _fetch_catalog('https://fixture.invalid','')
                    self.assertEqual(str(error.exception),tr('tools.models.catalogInvalid'))
            with current_language.set(language), patch('harness.tools.builtin.subagent_model_tools.read_catalog',return_value=b'{"data":[{"id":"same"},{"id":"same"}]}'):
                with self.assertRaises(ValueError):
                    _fetch_catalog('https://fixture.invalid','')

    def test_duplicate_json_fields_and_invalid_unicode_fail_as_localized_catalog_errors(self):
        for raw in (b'{"data":[],"data":[{"id":"fixture"}]}',b'{"data":[{"id":"wrong","id":"fixture"}]}',b'{"data":[{"id":"fixture","extra":"\\ud800"}]}',b'['*2000+b'0'+b']'*2000):
            with self.subTest(raw=raw[:80]), patch('harness.tools.builtin.subagent_model_tools.read_catalog',return_value=raw):
                with self.assertRaises(ValueError) as error:
                    _fetch_catalog('https://fixture.invalid','')
                self.assertEqual(str(error.exception),tr('tools.models.catalogInvalid'))

    async def test_invalid_direct_arguments_never_fetch_or_start_worker(self):
        tool=build_list_subagent_models_tool(base_url='https://fixture.invalid',model='original/model')
        cases=[None,[],True,1]
        for field in ('provider','model'):
            cases.extend({field:value} for value in (None,True,1,[],{}))
        with patch('harness.tools.builtin.subagent_model_tools._fetch_catalog') as fetch:
            for language in ('en','zh-CN','en'):
                for value in cases:
                    with self.subTest(language=language,value=value),current_language.set(language):
                        with self.assertRaises(ToolExecutionError):
                            await tool.execute(value,None)
            fetch.assert_not_called()

    def test_current_optional_and_unknown_fields_keep_original_values(self):
        rows=[{'id':'fixture/model','name':'Original 模型','supported_parameters':None,'vendor':{'original':['原文']}}, {'id':'other'}]
        with patch('harness.tools.builtin.subagent_model_tools.read_catalog',return_value=json.dumps({'data':rows},ensure_ascii=False).encode()):
            self.assertEqual(_fetch_catalog('https://fixture.invalid',''),rows)

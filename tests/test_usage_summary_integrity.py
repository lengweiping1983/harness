"""用量汇总不把布尔、浮点、非有限或文本数据转换成有效token数。"""
import copy
from types import MappingProxyType
import unittest

from harness.core.errors import SessionInvariantError
from harness.core.language import current_language
from harness.i18n import tr
from harness.web.views import usage_summary


class UsageSummaryIntegrityTests(unittest.TestCase):
    def test_invalid_usage_fails_explicitly_without_mutating_original_records(self):
        for language in ('en','zh-CN','en'):
            with current_language.set(language):
                for field in ('input_tokens','output_tokens','cache_read_tokens','cache_reported_input_tokens','reasoning_tokens'):
                    for value in (True,False,-1,1.5,float('nan'),float('inf'),'12',[],{}):
                        record={'role':'assistant','usage':{field:value},'content':'Original 原文'}
                        with self.subTest(field=field,value=value),self.assertRaises(SessionInvariantError) as error:
                            usage_summary([record])
                        self.assertEqual(error.exception.message,tr('web.usage.invalid'))
                        self.assertIs(record['usage'][field],value)
                for usage in (True,1,[], 'private-invalid-usage'):
                    with self.assertRaises(SessionInvariantError) as error:usage_summary([{'usage':usage}])
                    self.assertNotIn('private-invalid-usage',error.exception.message)

    def test_current_integer_counts_and_frozen_records_preserve_exact_values(self):
        records=[{'usage':MappingProxyType({'input_tokens':10,'output_tokens':3,'cache_read_tokens':2,'cache_reported_input_tokens':5})},
                 {'usage':{'input_tokens':4,'output_tokens':1,'reasoning_tokens':1}}, {'role':'user','content':'Original 原文'}, {'usage':None}]
        mutable=copy.deepcopy(records[1:])
        value=usage_summary(records)
        self.assertEqual(value,{'input_tokens':14,'output_tokens':4,'cache_read_tokens':2,'cache_reported_input_tokens':5,'reasoning_tokens':1,'total_tokens':18,'requests':2})
        self.assertEqual(records[1:],mutable)
        self.assertEqual(usage_summary([])['total_tokens'],0)

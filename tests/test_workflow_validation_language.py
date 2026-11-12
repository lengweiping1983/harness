"""workflow schema、结构化结果和最终协议拒绝保留路径及原文。"""
import copy
import unittest
from harness.core.language import current_language
from harness.core.errors import ToolExecutionError
from harness.tools.builtin.workflow_host import collect_schema_violations, validate_against_schema, SchemaError, WorkflowGuest


class WorkflowValidationLanguageTests(unittest.TestCase):
    def assert_language(self, text, language):
        self.assertEqual(any('\u4e00' <= c <= '\u9fff' for c in text), language == 'zh-CN', text)

    def test_schema_violations_keep_paths_and_do_not_mutate_schema(self):
        cases = [({'type': 'object', 'required': True}, 'schema.required'),
            ({'type': 'object', 'required': ['missing']}, 'missing'),
            ({'type': 'object', 'additionalProperties': 7}, 'additionalProperties'),
            ({'type': 'object', 'unknown': True}, 'schema.unknown'),
            ({'type': 'object', 'properties': {'item': {'type': ['string']}}}, 'schema.properties.item.type'),
            ({'type': 'object', 'properties': {'item': {'type': 'string', 'enum': []}}}, 'schema.properties.item.enum'),
            ({'type': 'object', 'properties': {'item': {'oneOf': []}}}, 'schema.properties.item.oneOf'),
            ({'type': 'object', 'properties': {'item': {'type': 'string', 'const': 7}}}, 'schema.properties.item.const'),
            ({'type': 'object', 'properties': {'item': {'type': 'string', 'enum': ['a'], 'const': 'b'}}}, 'schema.properties.item.const')]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for schema, path in cases:
                    original = copy.deepcopy(schema)
                    violations = collect_schema_violations(schema)
                    self.assertTrue(violations)
                    message = str(SchemaError(violations))
                    self.assertIn(path, message)
                    self.assert_language(message, language)
                    self.assertEqual(schema, original)
                cycle = {'type': 'object'}
                cycle['properties'] = {'self': cycle}
                self.assert_language(str(SchemaError(collect_schema_violations(cycle))), language)
                self.assertIs(cycle['properties']['self'], cycle)

    def test_structured_output_rejection_preserves_values_and_json_types(self):
        schema = {'type': 'object', 'properties': {'count': {'type': 'integer'}, 'mode': {'type': 'string', 'enum': ['ok']}}, 'required': ['count'], 'additionalProperties': False}
        invalid = [{'mode': 'ok'}, {'count': True}, {'count': 1, 'extra': 7}, {'count': 1, 'mode': 'other'}, []]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for value in invalid:
                    original = copy.deepcopy(value)
                    with self.assertRaises(ToolExecutionError) as caught:
                        validate_against_schema(value, schema)
                    self.assert_language(caught.exception.message, language)
                    self.assertIn('value', caught.exception.message)
                    self.assertEqual(value, original)
                validate_against_schema({'count': 1, 'mode': 'ok'}, schema)

    def test_final_protocol_errors_preserve_status_and_external_error(self):
        guest = WorkflowGuest.__new__(WorkflowGuest)
        guest._started = 2
        valid = {'value': 'Original 原文', 'stopReason': 'completed', 'agentsStarted': 2, 'error': None}
        cases = [None, {}, {**valid, 'stopReason': 'unknown'}, {**valid, 'agentsStarted': True}, {**valid, 'error': 7}]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for value in cases:
                    result = guest._interpret_final(value)
                    self.assertEqual(result['stopReason'], 'error')
                    self.assertEqual(result['agentsStarted'], 2)
                    self.assert_language(result['error'], language)
                self.assertEqual(guest._interpret_final(valid), valid)
                failed = {**valid, 'stopReason': 'error', 'error': 'External 原文'}
                self.assertEqual(guest._interpret_final(failed), failed)

"""声明式图表的工具入参、双语错误与原始配置身份回归。"""
import json
import unittest
from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.widget_tools import CHART_KINDS, TABLE_KINDS, build_widget_tools, validate_widget
from harness.tools.registry import ToolRunContext

SPECS = {
    'echarts': {'series': [{'type': 'bar', 'data': [3, 8]}]},
    'chartjs': {'type': 'bar', 'data': {'labels': ['A', 'B'], 'datasets': [{'data': [3, 8]}]}},
    'plotly': {'data': [{'type': 'bar', 'x': ['A', 'B'], 'y': [3, 8]}]},
    'vega': {'marks': []},
    'vega-lite': {'mark': 'bar', 'data': {'values': [{'x': 'A', 'y': 3}]}},
}


class ChartWidgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_formats_preserve_original_config_and_reach_display(self):
        show, _ = build_widget_tools()
        ctx = ToolRunContext(call_id='chart', session_id='fixture', cwd=None, turn=1, step=1)
        self.assertEqual(set(SPECS), set(CHART_KINDS))
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                wire = show.schema.resolve_description(language)
                self.assertEqual(wire.description, tr('python.widget_tools.show.description'))
                self.assertEqual(set(wire.parameters['properties']['kind']['enum']), {'auto', 'svg', 'html', *CHART_KINDS, *TABLE_KINDS})
                for kind, spec in SPECS.items():
                    source = '  \n' + json.dumps(spec, ensure_ascii=False, indent=2) + '\n'
                    with self.subTest(language=language, kind=kind):
                        value = await show.execute({'title': 'Original 图表', 'widget_code': source, 'kind': kind}, ctx)
                        self.assertEqual(value['code'], source)
                        self.assertEqual(value['kind'], kind)
                        self.assertEqual(show.describe({}, value)['widget']['code'], source)
                        self.assertNotIn(source, show.to_content({}, value)[0].text)

    def test_invalid_json_resources_and_reserved_keys_are_rejected_without_rewriting_input(self):
        cases = [
            ('null', 'ui.chart.invalidSpec'),
            ('{"series":[],"series":[]}', 'ui.chart.invalidJson'),
            ('{"series":[],"x":NaN}', 'ui.chart.invalidJson'),
            ('{"series":[],"x":"\\ud800"}', 'ui.chart.invalidJson'),
            ('{"series":[],"x":1e999}', 'ui.chart.invalidJson'),
            ('{"series":[],"x":' + '9' * 400 + '}', 'ui.chart.invalidJson'),
            ('{"series":[],"x":{"constructor":{}}}', 'ui.chart.unsafeKey'),
            ('{"series":[],"data":{"url":"https://example.com/data.json"}}', 'ui.chart.inlineData'),
            ('{"series":[],"x":' + '[' * 50 + '0' + ']' * 50 + '}', 'ui.chart.tooDeep'),
        ]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for source, key in cases:
                    with self.subTest(source=source, language=language), self.assertRaises(ToolExecutionError) as error:
                        validate_widget('Original', source, 'echarts', None)
                    self.assertEqual(error.exception.message, tr(key))
                with self.assertRaises(ToolExecutionError) as error:
                    validate_widget('Original', '{"data":[{"type":"scatter3d"}]}', 'plotly', None)
                self.assertEqual(error.exception.message, tr('ui.chart.plotlyTypes'))


class TableWidgetTests(unittest.TestCase):
    def test_original_whitespace_quotes_and_line_endings_are_preserved(self):
        for kind, source in [('csv', 'name,value\r\n"A,B","a""b"\r\n x ,001\r\n'), ('tsv', 'name\tvalue\n"A\tB"\t"line\nnext"\n')]:
            value = validate_widget('Original', source, kind, None)
            self.assertEqual(value[1:3], (source, kind))

    def test_rejects_malformed_quoting_ragged_records_and_limits(self):
        cases = [('a,b\nx', 'ui.table.columns'), ('a,b\n"open', 'ui.table.invalid'), ('a,b\nx"y,z', 'ui.table.invalid'), ('a,b\n"x" q,z', 'ui.table.invalid'), ('x\n' * 10001, 'ui.table.tooLarge'), (','.join(['x'] * 101), 'ui.table.tooLarge')]
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for source, key in cases:
                    with self.subTest(language=language, key=key), self.assertRaises(ToolExecutionError) as error:
                        validate_widget('Original', source, 'csv', None)
                    self.assertEqual(error.exception.message, tr(key))

    def test_table_size_uses_the_same_unicode_units_as_the_browser(self):
        for source, key in [('😀' * 100001, 'ui.table.tooLarge'), ('a\n\ud800', 'ui.table.invalid')]:
            with self.subTest(key=key), self.assertRaises(ToolExecutionError) as error:
                validate_widget('Original', source, 'csv', None)
            self.assertEqual(error.exception.message, tr(key))

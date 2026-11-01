"""可视化必须经过真实工具声明、供应商转录、纠错和对话展示事件。"""
import unittest
from tests.widget_visual_fixture import visual_fixture, SVG, HTML


class WidgetFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_call_is_rejected_then_svg_and_html_are_persisted(self):
        data = await visual_fixture()
        self.assertEqual(data['reason'], 'completed', data['error'])
        self.assertIsNone(data['open_turn'])
        self.assertEqual(len(data['requests']), 4)
        schema = next(t['function'] for t in data['requests'][0]['tools'] if t['function']['name'] == 'show_widget')
        self.assertEqual(schema['parameters']['required'], ['title', 'widget_code'])
        self.assertIn('widget_code', schema['parameters']['properties'])
        self.assertNotIn('max_completion_tokens', data['requests'][0])
        malformed = data['assistant'][0]['content'][0]['input']
        self.assertIsInstance(malformed, str)
        self.assertIn('Received arguments:', malformed)
        self.assertIn('<div>unfinished', malformed)
        rejected, svg, html = data['tools']
        self.assertTrue(rejected['results'][0]['is_error'])
        self.assertTrue(rejected['display']['details']['not_executed'])
        self.assertNotIn('widget', rejected['display'])
        for row, code, kind in ((svg, SVG, 'svg'), (html, HTML, 'html')):
            self.assertFalse(row['results'][0]['is_error'])
            self.assertEqual(row['display']['widget']['code'], code)
            self.assertEqual(row['display']['widget']['kind'], kind)
            self.assertNotIn(code, row['results'][0]['content'])

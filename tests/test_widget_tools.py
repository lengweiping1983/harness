"""对话内小组件工具的参数契约、校验与双语行为。"""
import unittest

from harness.core.errors import ToolExecutionError
from harness.core.language import current_language
from harness.i18n import tr
from harness.tools.builtin.widget_tools import (
    MAX_WIDGET_CHARS,
    build_widget_tools,
    detect_widget_kind,
    validate_widget,
)
from harness.tools.registry import ToolRunContext

SVG = '<svg viewBox="0 0 680 120" width="100%"><text x="10" y="20">hi</text></svg>'
HTML = '<div class="card"><button id="go">Go</button><script>document.getElementById("go").onclick=()=>{}</script></div>'


def ctx() -> ToolRunContext:
    return ToolRunContext(call_id='widget', session_id='fixture', cwd=None, turn=1, step=1)


class WidgetValidationTests(unittest.TestCase):
    def test_detect_kind(self):
        self.assertEqual(detect_widget_kind(SVG), 'svg')
        self.assertEqual(detect_widget_kind(f'  \n{SVG}'), 'svg')
        self.assertEqual(detect_widget_kind(HTML), 'html')
        self.assertEqual(detect_widget_kind('<span>x</span>'), 'html')

    def test_title_and_code_required(self):
        for title in (None, '', '   ', 1, True):
            with self.subTest(title=title):
                with self.assertRaises(ToolExecutionError) as error:
                    validate_widget(title, SVG, None, None)
                self.assertEqual(error.exception.message, tr('python.widget_tools.error.title'))
        for code in (None, '', '  ', 1, []):
            with self.subTest(code=code):
                with self.assertRaises(ToolExecutionError) as error:
                    validate_widget('t', code, None, None)
                self.assertEqual(error.exception.message, tr('python.widget_tools.error.code'))
        with self.assertRaises(ToolExecutionError) as error:
            validate_widget('x' * 121, SVG, None, None)
        self.assertEqual(error.exception.message, tr('python.widget_tools.error.title_long', p0='120'))
        with self.assertRaises(ToolExecutionError) as error:
            validate_widget('t', 'a' * (MAX_WIDGET_CHARS + 1), None, None)
        self.assertEqual(error.exception.message, tr('python.widget_tools.error.too_large', p0=f'{MAX_WIDGET_CHARS}'))

    def test_kind_rules(self):
        with self.assertRaises(ToolExecutionError):
            validate_widget('t', SVG, 'png', None)
        with self.assertRaises(ToolExecutionError) as error:
            validate_widget('t', HTML, 'svg', None)
        self.assertEqual(error.exception.message, tr('python.widget_tools.error.kind_mismatch', p0='svg', p1='html'))
        self.assertEqual(validate_widget('t', SVG, 'svg', None)[2], 'svg')
        self.assertEqual(validate_widget('t', HTML, 'html', None)[2], 'html')
        self.assertEqual(validate_widget('t', SVG, 'auto', None)[2], 'svg')

    def test_svg_rules(self):
        with self.assertRaises(ToolExecutionError) as error:
            validate_widget('t', SVG + SVG, None, None)
        self.assertEqual(error.exception.message, tr('python.widget_tools.error.svg_invalid'))
        with self.assertRaises(ToolExecutionError) as error:
            validate_widget('t', '<svg width="100"></svg>', None, None)
        self.assertEqual(error.exception.message, tr('python.widget_tools.error.svg_viewbox'))
        with self.assertRaises(ToolExecutionError) as error:
            validate_widget('t', SVG.replace('</svg>', '<script>alert(1)</script></svg>'), None, None)
        self.assertEqual(error.exception.message, tr('python.widget_tools.error.svg_script'))

    def test_full_documents_and_interactive_fragments_are_supported(self):
        cases = ['<!DOCTYPE html><html><head><title>Demo</title></head><body><p>x</p></body></html>',
                 '<form onsubmit="event.preventDefault()"><input></form>',
                 '<div style="position: fixed; top: 0">x</div>', HTML]
        for code in cases:
            with self.subTest(code=code):
                self.assertEqual(validate_widget('t', code, None, None)[1:3], (code, 'html'))

    def test_svg_structure_and_nested_svg(self):
        for code in ('<svg viewBox="0 0 10 10"><g></svg>',
                     '<!DOCTYPE svg [<!ENTITY x "test">]><svg viewBox="0 0 10 10">&x;</svg>'):
            with self.subTest(code=code), self.assertRaises(ToolExecutionError):
                validate_widget('t', code, 'auto', None)
        nested = '<!-- header --><svg width="120" height="80"><svg x="1" y="1"><rect width="10" height="10" /></svg></svg>'
        self.assertEqual(validate_widget('t', nested, 'svg', None)[2], 'svg')
        self.assertEqual(detect_widget_kind('<?xml version="1.0"?>' + SVG), 'svg')

    def test_loading_messages_rules(self):
        for bad in ('x', [1], [], ['a', 'b', 'c', 'd', 'e'], ['ok', ' ']):
            with self.subTest(bad=bad):
                with self.assertRaises(ToolExecutionError):
                    validate_widget('t', SVG, None, bad)
        self.assertEqual(validate_widget('t', SVG, None, [' a ', 'b'])[3], ['a', 'b'])


class WidgetToolContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_widget_contract_and_display(self):
        show, _guide = build_widget_tools()
        self.assertEqual(show.schema.name, 'show_widget')
        self.assertEqual(show.effect, 'read')
        self.assertTrue(show.concurrency_safe)
        self.assertEqual(show.schema.description_key, 'python.widget_tools.show.description')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                from harness.llm.translate import tool_schema_to_wire
                wire = tool_schema_to_wire(show.schema.resolve_description(language))['function']
                self.assertEqual(wire['description'], tr('python.widget_tools.show.description'))
                self.assertEqual(wire['parameters']['properties']['widget_code']['description'], tr('python.widget_tools.show.param.code'))
                value = await show.execute(
                    {'title': ' Agent 架构 ', 'widget_code': SVG, 'loading_messages': [' preparing ']}, ctx())
                self.assertEqual(value['title'], 'Agent 架构')
                self.assertEqual(value['kind'], 'svg')
                self.assertEqual(value['chars'], len(SVG))
                self.assertEqual(value['loading_messages'], ['preparing'])
                text = show.to_content({}, value)[0].text
                self.assertEqual(text, tr('python.widget_tools.confirm', p0='Agent 架构', p1='svg', p2=f'{len(SVG)}'))
                display = show.describe({}, value)
                self.assertEqual(display['widget']['code'], SVG)
                self.assertEqual(display['widget']['kind'], 'svg')
                # 模型内容块不重复携带代码本体，代码只进界面展示数据。
                self.assertNotIn(SVG, text)
        with self.assertRaises(ToolExecutionError) as error:
            await show.execute(None, ctx())
        self.assertEqual(error.exception.message, tr('python.widget_tools.error.title'))
        with self.assertRaises(ToolExecutionError):
            await show.execute({'title': 't'}, ctx())

    async def test_guidelines_contract_and_language(self):
        _show, guide = build_widget_tools()
        self.assertEqual(guide.schema.name, 'widget_guidelines')
        self.assertEqual(guide.effect, 'read')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                text = await guide.execute({'modules': ['chart', 'diagram', 'chart']}, ctx())
                base = tr('python.widget_tools.guide.base')
                self.assertTrue(text.startswith(base))
                # 去重且保持请求顺序：base 之后依次是 chart、diagram。
                rest = text[len(base):].lstrip()
                self.assertTrue(rest.startswith(tr('python.widget_tools.guide.chart')))
                self.assertEqual(text.count(tr('python.widget_tools.guide.chart')), 1)
                self.assertTrue(text.endswith(tr('python.widget_tools.guide.diagram')))
        for bad in (None, [], ['unknown'], 'chart'):
            with self.subTest(bad=bad):
                with self.assertRaises(ToolExecutionError):
                    await guide.execute({'modules': bad}, ctx())
        with self.assertRaises(ToolExecutionError) as error:
            await guide.execute({'modules': ['unknown']}, ctx())
        self.assertEqual(error.exception.message, tr('python.widget_tools.error.module_item', p0="'unknown'"))

    def test_bilingual_resources_switch(self):
        samples = ('python.widget_tools.error.svg_viewbox', 'python.widget_tools.confirm', 'python.widget_tools.guide.base')
        first_en = {}
        for round_index, language in enumerate(('en', 'zh-CN', 'en')):
            with current_language.set(language):
                resolved = {key: tr(key) for key in samples}
            if round_index == 0:
                first_en = resolved
                continue
            if language == 'zh-CN':
                self.assertNotEqual(first_en, resolved)
            else:
                # 同一实例英文→中文→英文往返后资源保持一致。
                self.assertEqual(first_en, resolved)


if __name__ == '__main__':
    unittest.main()

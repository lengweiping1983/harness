"""工具反馈的组合提示遵循当前语言，不改动参数正文和校验拒绝。"""
import unittest

from harness.core.language import current_language
from harness.core.errors import ToolArgsError
from harness.i18n import tr
from harness.tools.repeat_reminder import preview_arguments, RepeatToolReminder
from harness.tools.schema import validate_or_raise


class ToolFeedbackLanguageTests(unittest.TestCase):
    def test_truncated_arguments_and_error_separators(self):
        for language in ('en', 'zh-CN'):
            with current_language.set(language):
                self.assertEqual(preview_arguments('original', 100), 'original')
                self.assertEqual(preview_arguments('original', 3), tr('prompt.repeat.arguments_truncated', text='ori', count=5))
                with self.assertRaises(ToolArgsError) as caught:
                    validate_or_raise({}, {'type': 'object', 'required': ['first', 'second']})
                self.assertIn(tr('common.error_separator').join(caught.exception.details['errors']), caught.exception.message)
                self.assertTrue(caught.exception.details['not_executed'])

    def test_detailed_repeat_feedback_uses_runtime_language(self):
        reminder = RepeatToolReminder(thresholds=[2, 3], arguments_preview_chars=2)
        with current_language.set('en'):
            self.assertIsNone(reminder.observe('session', 'read', {'path': 'original'}))
            reminder.observe('session', 'read', {'path': 'original'})
        with current_language.set('zh-CN'):
            text = reminder.observe('session', 'read', {'path': 'original'})
            self.assertIn('字符', text)
            self.assertNotIn('more chars', text)


class QuestionAnswerLanguageTests(unittest.TestCase):
    def test_answer_rendering_uses_runtime_punctuation_and_preserves_content(self):
        from harness.tools.builtin.interaction_tools import _render_answers
        questions = [{'id': 'q', 'header': '用户 header'}]
        answers = [{'id': 'q', 'selected': ['English', '中文'], 'custom': '保留 original'}]
        for language, row in (
            ('en', '- 用户 header: English, 中文; additional: 保留 original'),
            ('zh-CN', '- 用户 header：English、中文；补充：保留 original'),
            ('en', '- 用户 header: English, 中文; additional: 保留 original'),
        ):
            with self.subTest(language=language), current_language.set(language):
                self.assertIn(row, _render_answers(questions, answers))


class PowerShellFeedbackLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_collection_label_is_resolved_per_call(self):
        from unittest.mock import AsyncMock, patch
        from harness.tools.builtin.pwsh_tools import _collect
        collect = AsyncMock(return_value=(b'', b'', 0, False))
        with patch('harness.tools.process_output.collect_process', collect):
            for language in ('en', 'zh-CN', 'en'):
                with current_language.set(language):
                    await _collect(object(), 1)
                    self.assertEqual(collect.await_args.kwargs['label'], tr('python.pwsh_tools.785521b4c16d'))
            await _collect(object(), 1, label='用户 label')
            self.assertEqual(collect.await_args.kwargs['label'], '用户 label')

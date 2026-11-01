"""问题规范化后的答案身份必须与最终工具反馈一致。"""
import copy
import unittest

from harness.core.events import EventBus
from harness.core.language import current_language
from harness.i18n import tr
from harness.interaction.questions import QuestionBroker, QuestionError, MAX_OPTION_CHARS
from harness.llm.types import ToolUseBlock
from harness.tools.builtin.interaction_tools import build_ask_user_question_tools
from harness.tools.registry import ToolRegistry, ToolRunContext


class QuestionRenderIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def run_question(self, language, *, text_input=False, long_label=False):
        bus = EventBus()
        broker = QuestionBroker(bus)
        registry = ToolRegistry()
        registry.register(build_ask_user_question_tools(broker)[0])
        label = 'Original 选择' + ('x' * 240 if long_label else '')
        question = {'id': '  original-question \t', 'header': 'Original 标题', 'question': 'Original 问题'}
        if text_input:
            question.update(input_type='text', text_input_reason='Original factual reason')
        else:
            question['options'] = [{'label': '  ' + label + '  '}, {'label': 'Other 原文'}]
        original = copy.deepcopy(question)
        received = []

        async def answer(payload):
            received.append(payload)
            answer = {'id': payload['questions'][0]['id'], 'selected': [] if text_input else [payload['questions'][0]['options'][0]['label']]}
            if text_input:
                answer['custom'] = 'Original 自定义 {id}'
            await broker.answer_and_notify(payload['question_id'], [answer], session_id='parent')

        bus.on('user_question/ask', answer)
        with current_language.set(language):
            result = await registry.execute_call(ToolUseBlock(id='original-call', name='ask_user_question', input={'questions': [question]}),
                ToolRunContext(call_id='', session_id='child', cwd=None, turn=1, step=1, services={'root_session_id': 'parent'}))
            self.assertFalse(result.is_error)
            normalized = result.data['answers'][0]
            self.assertEqual(normalized['id'], 'original-question')
            chosen = tr('python.interaction_tools.3c5045c728a5') if text_input else label[:MAX_OPTION_CHARS]
            if text_input:
                chosen = tr('python.interaction_tools.5536cab40d03', p0=chosen, p1='Original 自定义 {id}')
            expected = tr('python.interaction_tools.answer_row', header='Original 标题', answer=chosen)
            self.assertIn(expected, result.content[0].text)
            if not text_input:
                self.assertNotIn(tr('python.interaction_tools.3c5045c728a5'), result.content[0].text)
                self.assertEqual(normalized['selected'], [label[:MAX_OPTION_CHARS]])
        self.assertEqual(question, original)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]['session_id'], 'parent')
        self.assertEqual(received[0]['asker_session_id'], 'child')
        self.assertEqual(broker.pending(), [])
        self.assertEqual(broker._answer_tasks, set())

    async def test_invalid_or_duplicate_option_ids_never_publish_a_question(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for option_id in (None, True, 42, [], {}, '', '   ', 'x' * (MAX_OPTION_CHARS + 1)):
                    bus = EventBus()
                    published = []
                    bus.on('user_question/ask', lambda payload: published.append(payload))
                    broker = QuestionBroker(bus)
                    with self.assertRaises(QuestionError):
                        await broker.ask(session_id='owner', questions=[{'id': 'q', 'question': 'Original',
                            'options': [{'id': option_id, 'label': 'Original'}]}])
                    self.assertEqual(published, [])
                    self.assertEqual(broker.pending(), [])
                broker = QuestionBroker()
                with self.assertRaises(QuestionError):
                    await broker.ask(session_id='owner', questions=[{'id': 'q', 'question': 'Original',
                        'options': [{'id': 'approve', 'label': 'First'}, {'id': 'approve', 'label': 'Other'}]}])
                self.assertEqual(broker.pending(), [])

    async def test_padded_question_id_renders_chosen_normalized_option(self):
        for language in ('en', 'zh-CN', 'en'):
            for long_label in (False, True):
                with self.subTest(language=language, long_label=long_label):
                    await self.run_question(language, long_label=long_label)

    async def test_padded_question_id_keeps_custom_answer_text(self):
        for language in ('en', 'zh-CN', 'en'):
            with self.subTest(language=language):
                await self.run_question(language, text_input=True)

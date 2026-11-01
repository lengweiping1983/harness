"""问答持久提交、权限路由及取消的独立业务回归。"""
import asyncio
import unittest
from unittest.mock import AsyncMock

from harness.interaction.questions import QuestionBroker, QuestionError, QuestionUnavailableError


class QuestionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.broker = QuestionBroker()
        self.task = asyncio.create_task(self.broker.ask(questions=[{'id': 'scope', 'question': '范围', 'options': [{'label': 'yes'}]}], session_id='child', parent_session_id='parent'))
        await asyncio.sleep(0)
        self.question = self.broker.pending('parent')[0]

    async def asyncTearDown(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def test_failed_persist_keeps_question_open_then_resolves_after_commit(self):
        async def failed(*args):
            raise OSError('disk full')
        with self.assertRaises(OSError):
            await self.broker.answer_and_notify(self.question['question_id'], [{'id': 'scope', 'selected': ['yes']}], session_id='parent', before_resolve=failed)
        self.assertFalse(self.task.done())
        committed = []
        async def commit(record, answers):
            self.assertFalse(record.future.done())
            committed.append(answers)
        result = await self.broker.answer_and_notify(self.question['question_id'], [{'id': 'scope', 'selected': ['yes']}], session_id='parent', before_resolve=commit)
        self.assertTrue(result['resolved'])
        self.assertEqual(await self.task, committed[0])
        self.assertEqual(self.broker.pending(), [])

    async def test_wrong_route_and_invalid_answer_never_commit_or_resolve(self):
        commit = AsyncMock()
        with self.assertRaises(QuestionUnavailableError):
            await self.broker.answer_and_notify(self.question['question_id'], [], session_id='child', before_resolve=commit)
        with self.assertRaises(QuestionError):
            await self.broker.answer_and_notify(self.question['question_id'], [{'id': 'scope', 'selected': ['unknown']}], session_id='parent', before_resolve=commit)
        commit.assert_not_awaited()
        self.assertFalse(self.task.done())

    async def test_cancel_closes_question_without_fabricating_answer(self):
        self.assertEqual(self.broker.cancel_session('child'), 1)
        with self.assertRaises(QuestionError):
            await self.task
        self.assertEqual(self.broker.pending(), [])

    async def test_old_string_and_missing_selected_are_rejected_without_committing(self):
        commit = AsyncMock()
        for answer in ({'id': 'scope', 'selected': 'yes'}, {'id': 'scope', 'custom': 'original'}):
            with self.subTest(answer=answer), self.assertRaises(QuestionError):
                await self.broker.answer_and_notify(self.question['question_id'], [answer], session_id='parent', before_resolve=commit)
            self.assertFalse(self.task.done())
            commit.assert_not_awaited()
        await self.broker.answer_and_notify(self.question['question_id'], [{'id': 'scope', 'selected': [], 'custom': 'original'}], session_id='parent', before_resolve=commit)
        self.assertEqual(await self.task, [{'id': 'scope', 'selected': [], 'custom': 'original'}])
        commit.assert_awaited_once()

    async def test_omitted_questions_still_have_explicit_empty_selections(self):
        await self.broker.answer_and_notify(self.question['question_id'], [], session_id='parent')
        self.assertEqual(await self.task, [{'id': 'scope', 'selected': []}])


class QuestionTimeoutLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_is_not_an_answer_or_authorization(self):
        from harness.core.language import current_language
        from harness.interaction.questions import QuestionBroker, QuestionError
        from harness.tools.builtin.interaction_tools import build_ask_user_question_tools

        broker = QuestionBroker(default_timeout=1)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                with self.assertRaises(QuestionError) as caught:
                    await broker.ask(questions=[{'id': 'consent', 'question': 'Original 授权?',
                        'options': [{'id': 'yes', 'label': 'Allow'}, {'id': 'no', 'label': 'Deny'}]}], session_id='fixture')
                self.assertEqual(broker.pending('fixture'), [])
                text = str(caught.exception)
                guidance = build_ask_user_question_tools(broker)[0].schema.description
                phrase = '超时不代表批准' if language == 'zh-CN' else 'Timeout is not approval'
                self.assertIn(phrase, text)
                self.assertIn(phrase, guidance)

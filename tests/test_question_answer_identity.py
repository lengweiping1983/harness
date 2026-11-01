"""问题回答的两种传输均在通知前校验原始身份类型。"""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.core.language import current_language
from harness.interaction.questions import QuestionError
from harness.i18n import tr
from harness.web.api.interaction import build_router
from harness.web.errors import BadRequest
from harness.web.router import Request
from harness.web.server import Handler


class QuestionAnswerIdentityTests(unittest.TestCase):
    def test_invalid_identity_cannot_reach_question_broker(self):
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                for payload in ({}, *({'question_id': value} for value in (None, True, False, 7, 0, [], {}, ['id'], '   '))):
                    with self.subTest(language=language, payload=payload):
                        broker = SimpleNamespace(answer_and_notify=AsyncMock(return_value={}))
                        host = SimpleNamespace(questions=broker)
                        ctx = SimpleNamespace(harness=host, call=lambda task, **kwargs: asyncio.run(task))
                        path = '/api/v1/questions/answer'
                        endpoint, params = build_router(ctx).resolve('POST', path)
                        with self.assertRaises(BadRequest) as caught:
                            endpoint(Request('POST', path, {}, {}, json.dumps(payload).encode('utf-8')), **params)
                        self.assertEqual(caught.exception.code, 'question/missing-id')
                        self.assertEqual(caught.exception.message, tr('python.interaction.e25920e0b3d5'))
                        with self.assertRaises(QuestionError) as caught:
                            asyncio.run(Handler._answer_question(SimpleNamespace(harness=host), None, payload))
                        self.assertEqual(str(caught.exception), tr('python.server.e25920e0b3d5'))
                        broker.answer_and_notify.assert_not_awaited()

    def test_valid_identity_and_answers_are_forwarded_once(self):
        answers = [{'question':'Original 问题 {name}', 'answer':'Original 答案'}]
        for transport in ('http', 'ws'):
            broker = SimpleNamespace(answer_and_notify=AsyncMock(return_value={'status':'answered'}))
            host = SimpleNamespace(questions=broker)
            payload = {'question_id':'  question-original  ', 'answers':answers}
            if transport == 'http':
                ctx = SimpleNamespace(harness=host, call=lambda task, **kwargs: asyncio.run(task))
                path = '/api/v1/questions/answer'
                endpoint, params = build_router(ctx).resolve('POST', path)
                response = endpoint(Request('POST', path, {}, {}, json.dumps(payload).encode('utf-8')), **params)
                self.assertEqual(json.loads(response.body), {'status':'answered'})
            else:
                conn = SimpleNamespace(send_json=AsyncMock())
                result = asyncio.run(Handler._answer_question(SimpleNamespace(harness=host), conn, payload))
                self.assertEqual(result, {'status':'answered'})
                conn.send_json.assert_awaited_once_with({'type':'question_answered', 'status':'answered'})
            broker.answer_and_notify.assert_awaited_once_with('question-original', answers)

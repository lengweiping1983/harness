"""主题制课的真实发布、只读模型调用、学习完成和未知结果回归。"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.academy.authoring import CONTENT_SCHEMA, PLAN_SCHEMA, parse_response, validate_plan
from harness.academy.errors import AcademyError
from harness.academy.runtime import AcademyRuntime
from harness.academy.service import AcademyService
from harness.core.language import current_language
from harness.llm.types import AssistantMessage, LlmCallConfig, TextBlock


def localized(text):
    return {'en': text, 'zh-CN': '中文课程：' + text}


def plan():
    return {'title': localized('Learn a topic'), 'description': localized('Small lessons with practice.'),
        'assumptions': localized('Assume a beginner; adjust after practicing.'),
        'stages': [{'id': identity, 'title': localized(identity), 'goal': localized('Explain and apply one example.')}
                   for identity in ('begin', 'apply')],
        'lessons': [{'id': identity, 'stage_id': 'begin' if index < 2 else 'apply', 'mode': 'study',
                     'title': localized(identity), 'goal': localized('Explain this concept using your own example.'),
                     'prerequisites': [] if index == 0 else [['notice', 'practice'][index - 1]], 'duration_minutes': 10}
                    for index, identity in enumerate(('notice', 'practice', 'transfer'))]}


def content():
    return {'lessons': [{'id': identity, 'reading': localized('Observe a worked example, predict the result, and explain why it follows. ' * 3),
        'exercise': localized('Try a new example and explain each step in your own words.'),
        'rubric': localized('Observe the example, a reason, and an independent variation.'), 'starter': '', 'checker': '',
        'knowledge_checks': [{'id': 'concept', 'prompt': localized('Which statement follows from the example?'),
            'answer_id': 'evidence', 'hints': [localized('Compare the claim with the observation.')],
            'options': [{'id': option, 'label': localized(option), 'feedback': localized('Compare the observed result with this claim.')}
                        for option in ('guess', 'evidence')]}]}
        for identity in ('notice', 'practice', 'transfer')]}


class DesignAdapter:
    async def complete(self, messages, *, config, tools):
        payload = json.loads(messages[-1].text)
        self.calls.append((payload['task'], tools, current_language.get()))
        value = {'academy-planning': plan(), 'academy-writing': content(),
                 'academy-reviewing': self.review}[payload['task']]
        return AssistantMessage(content=(TextBlock(text=json.dumps(value)),), model='course-fixture')

    def __init__(self, review=None):
        self.calls = []
        self.review = review or {'approved': True, 'notes': []}


class AcademyAuthoringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.service = AcademyService(self.home)
        self.config = LlmCallConfig(model='requested-model')
        self.futures = []

    def schedule(self, identity):
        future = asyncio.get_running_loop().create_future()
        self.futures.append(future)
        return future

    async def asyncTearDown(self):
        for future in self.futures:
            future.cancel()
        await asyncio.sleep(0)

    async def test_one_topic_publishes_bilingual_course_without_running_code_or_creating_conversation(self):
        with current_language.set('zh-CN'):
            receipt = self.service.designer.create('topic-request', '清楚地解释设计', model=self.config.model, schedule=self.schedule)
        self.assertEqual(receipt, self.service.designer.create('topic-request', '清楚地解释设计', schedule=self.schedule))
        design = receipt['result']['design']
        adapter = DesignAdapter()
        with patch.object(AcademyRuntime, 'run', side_effect=AssertionError('制课不得运行代码')):
            await self.service.designer.generate(design['id'], adapter, self.config)
        self.assertEqual([call[0] for call in adapter.calls], ['academy-planning', 'academy-writing', 'academy-reviewing'])
        self.assertTrue(all(call[1] == [] and call[2] == 'zh-CN' for call in adapter.calls))
        saved = self.service.designer.design(design['id'])
        self.assertEqual(saved['status'], 'ready')
        self.assertEqual(saved['model'], 'course-fixture')
        views = []
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language):
                views.append(self.service.course(saved['course_id']))
        self.assertEqual(views[0], views[2])
        self.assertNotEqual(views[0]['title'], views[1]['title'])
        self.assertEqual(views[0]['origin'], 'personal')
        self.assertEqual(len(views[0]['lessons']), 3)
        self.assertFalse((self.home / 'sessions').exists())
        reopened = AcademyService(self.home)
        self.assertEqual(reopened.designer.design(saved['id'])['status'], 'ready')

    async def test_study_completion_requires_answers_and_reflection_and_never_awards_code_pass(self):
        receipt = self.service.designer.create('topic', 'Writing', schedule=self.schedule)
        await self.service.designer.generate(receipt['result']['design']['id'], DesignAdapter(), self.config)
        project = self.service.create_project('create', receipt['result']['design']['course_id'])['result']['project']
        self.assertEqual(self.service.files(project['id']), [])
        args = (project['id'], 'notice', project['fingerprint'])
        rejected = self.service.complete_study(args[0], 'bad', args[1], args[2], 'My explanation', [])
        self.assertEqual(rejected['result']['error_code'], 'study-incomplete')
        answers = [{'check_id': 'concept', 'option_id': 'evidence'}]
        complete = self.service.complete_study(args[0], 'complete', args[1], args[2], 'My own example and reason.', answers)
        self.assertEqual(complete['result']['project']['progress'][0]['state'], 'completed')
        self.assertEqual(self.service.runs(args[0]), [])
        self.assertEqual(self.service.complete_study(args[0], 'complete', args[1], args[2], 'My own example and reason.', answers), complete)
        rejected_run = self.service.execute(args[0], 'run-study', args[1], args[2])
        self.assertEqual(rejected_run['result']['error_code'], 'lesson-not-runnable')

    async def test_rejected_review_does_not_publish_and_reopen_does_not_replay_pending_generation(self):
        receipt = self.service.designer.create('review', 'A topic', schedule=self.schedule)
        identity = receipt['result']['design']['id']
        await self.service.designer.generate(identity, DesignAdapter({'approved': False, 'notes': ['Add a transfer task.']}), self.config)
        self.assertEqual(self.service.designer.design(identity)['error_code'], 'authoring-review-failed')
        self.assertFalse((self.home / 'academy/courses' / receipt['result']['design']['course_id']).exists())
        pending = self.service.designer.create('pending', 'Another topic', schedule=self.schedule)['result']['design']
        reopened = AcademyService(self.home)
        self.assertEqual(reopened.designer.design(pending['id'])['status'], 'unknown')
        self.assertEqual(reopened.designer.create('pending', 'Another topic')['result']['design']['id'], pending['id'])

    def test_invalid_model_contracts_and_forward_prerequisites_are_rejected(self):
        self.assertEqual(parse_response(json.dumps(plan()), PLAN_SCHEMA), plan())
        self.assertEqual(parse_response(json.dumps(content()), CONTENT_SCHEMA), content())
        with self.assertRaisesRegex(AcademyError, 'authoring-invalid'):
            parse_response('{"lessons": [], "lessons": []}', CONTENT_SCHEMA)
        invalid = deepcopy(plan())
        invalid['lessons'][0]['prerequisites'] = ['transfer']
        with self.assertRaisesRegex(AcademyError, 'authoring-invalid'):
            validate_plan(invalid)

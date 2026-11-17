"""教 AI 模式：误解冻结、无执行边界、学生独立测验与教学证据回归。"""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness.academy.errors import AcademyError
from harness.academy.service import AcademyService
from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.adapter import OpenRouterAdapter
from harness.llm.transport import Transport
from harness.llm.types import LlmCallConfig
from tests.test_academy import write_engine_fixture
from tests.test_academy_mastery import write_memory_fixture


class TeachTransport(Transport):
    """确定性学生回复：暴露误解并反问，不发起工具调用。"""

    def __init__(self):
        self.requests = []

    async def post_stream(self, url, *, headers, body, timeout):
        request = json.loads(body)
        self.requests.append(request)
        reply = 'I think one third is bigger because 3 > 2. Why is that wrong? / 我觉得三分之一更大，因为 3 大于 2。为什么错了？'
        yield ('data: ' + json.dumps({'model': 'teach-fixture', 'choices': [{'delta': {'content': reply}, 'finish_reason': None}]}) + '\n\n').encode('utf-8')
        yield ('data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}) + '\n\n').encode('utf-8')
        yield b'data: [DONE]\n\n'


class QuizAdapter:
    """学生测验的单次模型调用夹具；捕获实际送入的系统指令与载荷。"""

    def __init__(self, payload, *, tool_uses=()):
        self.payload = payload
        self.tool_uses = list(tool_uses)
        self.requests = []

    async def complete(self, messages, *, config, tools):
        self.requests.append({'messages': messages, 'config': config, 'tools': tools})
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload, ensure_ascii=False)
        return SimpleNamespace(text=text, model='quiz-model', tool_uses=self.tool_uses)


class AcademyTeachTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='academy-teach-')
        self.root = Path(self.temporary.name)
        write_memory_fixture(self.root / 'library')
        self.service = AcademyService(self.root / 'home', self.root / 'library')
        self.project = self.service.create_project('create', 'memory-fixture')['result']['project']
        self.pid = self.project['id']
        self.futures = []
        self.config = LlmCallConfig(model='configured-model')
        self.now = time.time()
        patcher = patch('time.time', side_effect=lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        for future in self.futures:
            if not future.done():
                future.cancel()
        await asyncio.gather(*self.futures, return_exceptions=True)
        self.temporary.cleanup()

    def schedule(self, identity):
        future = asyncio.get_running_loop().create_future()
        self.futures.append(future)
        return future

    def complete(self, lesson_id):
        project = self.service.project(self.pid)
        if project['active_lesson_id'] != lesson_id:
            receipt = self.service.start_lesson(self.pid, 'start-' + lesson_id, lesson_id, project['revision'], project['fingerprint'])
            self.assertEqual(receipt['status'], 'ready')
            project = receipt['result']['project']
        receipt = self.service.complete_study(self.pid, 'complete-' + lesson_id, lesson_id, project['fingerprint'],
                                              'my explanation', [{'check_id': 'q1', 'option_id': 'right'}])
        self.assertEqual(receipt['status'], 'ready')

    def teach(self, request='teach-1', lesson_id='fractions', **kwargs):
        allow = kwargs.pop('allow_execution', False)
        require = kwargs.pop('require_execution', False)
        return self.service.tutor.start(self.pid, request, lesson_id, self.service.project(self.pid)['fingerprint'],
                                        'Let me teach you fractions. / 我来教你分数。', allow, require_execution=require, mode='teach',
                                        model=self.config.model, schedule=self.schedule, **kwargs)

    async def evaluate(self, receipt, transport=None):
        transport = transport or TeachTransport()
        adapter = OpenRouterAdapter('fixture-only', model=self.config.model, transport=transport, max_retries=0)
        await self.service.tutor.evaluate(receipt['result']['tutor_turn']['id'], adapter, self.config)
        return self.service.tutor.turns(self.pid)[0], transport

    def skill(self, lesson_id='fractions'):
        return next(item for item in self.service.mastery.view(self.pid)['skills'] if item['lesson_id'] == lesson_id)

    async def test_teach_requires_prior_independent_evidence(self):
        receipt = self.teach()
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['result']['error_code'], 'teach-not-ready')

    async def test_teach_freezes_course_misconception_without_execution_tools(self):
        self.complete('fractions')
        token = current_language.set('en')
        try:
            receipt = self.teach()
        finally:
            current_language.reset(token)
        self.assertEqual(receipt['status'], 'ready')
        turn = receipt['result']['tutor_turn']
        self.assertEqual(turn['mode'], 'teach')
        self.assertEqual(turn['misconception'], {'check_id': 'q1', 'option_id': 'wrong', 'prompt': 'Pick the right answer for fractions',
                                                 'label': 'Wrong', 'feedback': 'No'})
        self.assertFalse(turn['allow_execution'])
        turn, transport = await self.evaluate(receipt)
        self.assertEqual(turn['status'], 'completed')
        self.assertEqual(turn['actions'], [])
        request = transport.requests[0]
        self.assertEqual(request.get('tools', []), [])
        context = next(json.loads(item['content']) for item in request['messages'] if item.get('role') == 'user')
        self.assertEqual(context['misconception']['option_id'], 'wrong')
        system = next(item['content'] for item in request['messages'] if item.get('role') == 'system')
        self.assertIn('student', system)
        self.assertNotIn('answer_id', json.dumps(context))
        self.assertEqual(self.service.runs(self.pid), [])

    async def test_teach_rejects_execution_and_foreign_material_scope(self):
        self.complete('fractions')
        for kwargs in ({'allow_execution': True}, {'require_execution': True},
                       {'knowledge_check': {'check_id': 'q1', 'option_id': 'wrong'}},
                       {'evidence_run_id': 'run-x'}, {'proposal_path': 'main.py'}, {'model_run_id': 'model-x'}):
            with self.assertRaises(AcademyError) as caught:
                self.teach('teach-scope', **kwargs)
            self.assertEqual(caught.exception.code, 'invalid-request')

    async def test_teach_unavailable_without_course_misconception(self):
        library = self.root / 'engine-library'
        write_engine_fixture(library)
        service = AcademyService(self.root / 'engine-home', library)
        project = service.create_project('create', 'harness-foundations')['result']['project']
        service.store.put('run', {'id': 'run_passed', 'project_id': project['id'], 'lesson_id': 'agent-loop', 'kind': 'check',
                                  'input_changed': False, 'result': {'status': 'passed'}, 'created_at': time.time()})
        receipt = service.tutor.start(project['id'], 'teach-1', 'agent-loop', service.project(project['id'])['fingerprint'],
                                      'Teach me. / 教我。', False, mode='teach', schedule=self.schedule)
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['result']['error_code'], 'teach-unavailable')

    async def test_quiz_grades_student_and_adds_teaching_evidence(self):
        self.complete('fractions')
        self.now += 3 * 86400
        turn, _ = await self.evaluate(self.teach())
        adapter = QuizAdapter({'answers': [{'check_id': 'q1', 'option_id': 'right'}]})
        receipt = self.service.tutor.quiz(self.pid, turn['id'], 'quiz-1', model=self.config.model, schedule=self.schedule)
        self.assertEqual(receipt['status'], 'ready')
        quiz = receipt['result']['quiz']
        self.assertEqual(quiz['status'], 'running')
        await self.service.tutor.evaluate_quiz(quiz['id'], adapter, self.config)
        stored = self.service.tutor.quizzes(self.pid)[0]
        self.assertEqual(stored['status'], 'completed')
        self.assertTrue(stored['correct'])
        self.assertEqual(stored['results'], [{'check_id': 'q1', 'correct': True}])
        self.assertEqual(stored['model'], 'quiz-model')
        self.assertEqual(adapter.requests[0]['tools'], [])
        skill = self.skill()
        self.assertEqual(skill['evidence_count'], 2)
        self.assertGreater(skill['stability_days'], 2.0)

    async def test_quiz_wrong_answers_record_failure_without_lapsing_memory(self):
        self.complete('fractions')
        turn, _ = await self.evaluate(self.teach())
        adapter = QuizAdapter({'answers': [{'check_id': 'q1', 'option_id': 'wrong'}]})
        receipt = self.service.tutor.quiz(self.pid, turn['id'], 'quiz-1', schedule=self.schedule)
        await self.service.tutor.evaluate_quiz(receipt['result']['quiz']['id'], adapter, self.config)
        stored = self.service.tutor.quizzes(self.pid)[0]
        self.assertEqual(stored['status'], 'completed')
        self.assertFalse(stored['correct'])
        skill = self.skill()
        self.assertEqual(skill['evidence_count'], 1)
        self.assertEqual(skill['stability_days'], 2.0)

    async def test_quiz_requires_completed_teach_turn_and_runs_once(self):
        self.complete('fractions')
        receipt = self.service.tutor.quiz(self.pid, 'tutor_missing', 'quiz-0a', schedule=self.schedule)
        self.assertEqual(receipt['result']['error_code'], 'run-not-found')
        tutor_turn = self.service.tutor.start(self.pid, 'ask-1', 'fractions', self.service.project(self.pid)['fingerprint'],
                                              'Help. / 帮助。', False, schedule=self.schedule)
        receipt = self.service.tutor.quiz(self.pid, tutor_turn['result']['tutor_turn']['id'], 'quiz-0b', schedule=self.schedule)
        self.assertEqual(receipt['result']['error_code'], 'quiz-not-ready')
        await self.service.tutor.evaluate(tutor_turn['result']['tutor_turn']['id'],
                                          OpenRouterAdapter('fixture-only', model=self.config.model, transport=TeachTransport(), max_retries=0), self.config)
        turn, _ = await self.evaluate(self.teach())
        first = self.service.tutor.quiz(self.pid, turn['id'], 'quiz-1', schedule=self.schedule)
        replay = self.service.tutor.quiz(self.pid, turn['id'], 'quiz-1', schedule=self.schedule)
        self.assertEqual(first['result']['quiz']['id'], replay['result']['quiz']['id'])
        second = self.service.tutor.quiz(self.pid, turn['id'], 'quiz-2', schedule=self.schedule)
        self.assertEqual(second['result']['error_code'], 'quiz-exists')
        self.assertEqual(len(self.service.store.list('teach-quiz')), 1)

    async def test_quiz_malformed_student_output_fails_explicitly(self):
        self.complete('fractions')
        turn, _ = await self.evaluate(self.teach())
        payloads = ['not json', {'answers': []}, {'answers': [{'check_id': 'q1', 'option_id': 'nope'}]},
                    {'answers': [{'check_id': 'unknown', 'option_id': 'right'}]},
                    {'answers': [{'check_id': 'q1', 'option_id': 'right'}, {'check_id': 'q1', 'option_id': 'right'}]}]
        for index, payload in enumerate(payloads):
            receipt = self.service.tutor.quiz(self.pid, turn['id'], 'quiz-bad-' + str(index), schedule=self.schedule)
            await self.service.tutor.evaluate_quiz(receipt['result']['quiz']['id'], QuizAdapter(payload), self.config)
            stored = next(item for item in self.service.tutor.quizzes(self.pid) if item['request_id'] == 'quiz-bad-' + str(index))
            self.assertEqual(stored['status'], 'failed')
            self.assertEqual(stored['error_code'], 'quiz-invalid')
        receipt = self.service.tutor.quiz(self.pid, turn['id'], 'quiz-tools', schedule=self.schedule)
        await self.service.tutor.evaluate_quiz(receipt['result']['quiz']['id'],
                                               QuizAdapter({'answers': [{'check_id': 'q1', 'option_id': 'right'}]}, tool_uses=[{'id': 'x'}]), self.config)
        stored = next(item for item in self.service.tutor.quizzes(self.pid) if item['request_id'] == 'quiz-tools')
        self.assertEqual(stored['error_code'], 'quiz-invalid')
        self.assertEqual(self.skill()['evidence_count'], 1)

    async def test_quiz_restart_projection_is_unknown_without_replay(self):
        self.complete('fractions')
        turn, _ = await self.evaluate(self.teach())
        receipt = self.service.tutor.quiz(self.pid, turn['id'], 'quiz-1', schedule=self.schedule)
        restarted = AcademyService(self.root / 'home', self.root / 'library')
        quizzes = restarted.tutor.quizzes(self.pid)
        self.assertEqual(quizzes[0]['status'], 'unknown')
        self.assertEqual(quizzes[0]['error_code'], 'quiz-interrupted')
        self.assertEqual(receipt['result']['quiz']['id'], quizzes[0]['id'])

    async def test_quiz_language_and_transcript_are_frozen_from_teaching(self):
        self.complete('fractions')
        token = current_language.set('zh-CN')
        try:
            first, _ = await self.evaluate(self.teach('teach-zh-1'))
            second, _ = await self.evaluate(self.teach('teach-zh-2'))
        finally:
            current_language.reset(token)
        adapter = QuizAdapter({'answers': [{'check_id': 'q1', 'option_id': 'right'}]})
        receipt = self.service.tutor.quiz(self.pid, second['id'], 'quiz-zh', schedule=self.schedule)
        await self.service.tutor.evaluate_quiz(receipt['result']['quiz']['id'], adapter, self.config)
        messages = adapter.requests[0]['messages']
        self.assertIn('学生', messages[0].text)
        payload = json.loads(messages[1].text)
        self.assertEqual(len(payload['transcript']), 4)
        self.assertEqual(payload['transcript'][0]['role'], 'learner')
        self.assertEqual(payload['transcript'][1]['role'], 'student')
        self.assertNotIn('key', payload)
        self.assertNotIn('answer_id', json.dumps(payload))
        stored = self.service.tutor.quizzes(self.pid)[0]
        self.assertEqual(stored['language'], 'zh-CN')

    async def test_teach_history_stays_available_for_followup_questions(self):
        self.complete('fractions')
        first, _ = await self.evaluate(self.teach('teach-1'))
        second, transport = await self.evaluate(self.teach('teach-2'))
        context = next(json.loads(item['content']) for item in transport.requests[0]['messages'] if item.get('role') == 'user')
        self.assertEqual(context['history'][-1]['id'], first['id'])
        self.assertEqual(context['misconception']['option_id'], 'wrong')
        self.assertEqual(second['status'], 'completed')



class TutorPersonaTests(unittest.TestCase):
    def test_warm_persona_guidance_is_consumed_bilingually(self):
        for language, tutor_mark, teach_mark in (('en', 'growth-mindset', 'endearingly'), ('zh-CN', '成长型思维', '好奇而真诚')):
            token = current_language.set(language)
            try:
                self.assertIn(tutor_mark, tr('academy.tutor.instructions'))
                self.assertIn(teach_mark, tr('academy.teach.instructions'))
                self.assertIn('mastery', tr('academy.tutor.instructions'))
            finally:
                current_language.reset(token)

if __name__ == '__main__':
    unittest.main()

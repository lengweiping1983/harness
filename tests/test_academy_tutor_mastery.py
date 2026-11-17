"""带练掌握度画像：冻结注入、先修 surfaced、近期失败与历史隔离。"""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.academy.errors import AcademyError
from harness.academy.service import AcademyService
from harness.core.language import current_language
from harness.llm.adapter import OpenRouterAdapter
from harness.llm.transport import Transport
from harness.llm.types import LlmCallConfig

COURSE = 'primary-math-foundations'


class ConciseTransport(Transport):
    def __init__(self):
        self.requests = []

    async def post_stream(self, url, *, headers, body, timeout):
        request = json.loads(body)
        self.requests.append(request)
        yield ('data: ' + json.dumps({'model': 'fixture', 'choices': [{'delta': {'content': 'One small step. / 一小步。'}, 'finish_reason': None}]}) + '\n\n').encode('utf-8')
        yield ('data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}) + '\n\n').encode('utf-8')
        yield b'data: [DONE]\n\n'


class TutorMasteryProfileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='academy-profile-')
        self.root = Path(self.temporary.name)
        self.service = AcademyService(self.root / 'home')
        self.project = self.service.create_project('create', COURSE)['result']['project']
        self.pid = self.project['id']
        self.now = time.time()
        patcher = patch('time.time', side_effect=lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temporary.cleanup)
        self.config = LlmCallConfig(model='configured-model')
        token = current_language.set('en')
        self.addCleanup(current_language.reset, token)

    def open_lesson(self, lesson_id):
        project = self.service.project(self.pid)
        receipt = self.service.start_lesson(self.pid, 'start-' + lesson_id, lesson_id, project['revision'], project['fingerprint'])
        self.assertEqual(receipt['status'], 'ready')
        return receipt['result']['project']

    def schedule(self, identity):
        return asyncio.get_running_loop().create_future()

    async def test_profile_freezes_prerequisite_gap_and_recent_failures(self):
        project = self.open_lesson('place-value')
        self.service.complete_study(self.pid, 'complete-pv', 'place-value', project['fingerprint'],
                                    'The 5 is in the tens place.', [{'check_id': 'q1', 'option_id': 'b'}])
        self.now += 3 * 86400
        failed = self.service.mastery.review(self.pid, 'review-fail', 'place-value', [{'check_id': 'q1', 'option_id': 'a'}])
        self.assertFalse(failed['result']['review']['correct'])
        self.now += 86400
        self.open_lesson('add-meaning')
        receipt = self.service.tutor.start(self.pid, 'ask-1', 'add-meaning', self.service.project(self.pid)['fingerprint'],
                                           'Help me with addition. / 帮我学加法。', False, model=self.config.model, schedule=self.schedule)
        self.assertEqual(receipt['status'], 'ready')
        turn = receipt['result']['tutor_turn']
        stored = self.service.store.get('tutor-turn:' + self.pid, turn['id'], max_bytes=1024 * 1024)
        profile = stored['snapshot']['mastery']
        self.assertEqual(profile['skill']['lesson_id'], 'add-meaning')
        self.assertEqual(profile['skill']['state'], 'new')
        self.assertEqual(profile['prerequisites'], [{'lesson_id': 'place-value', 'state': 'learning', 'retrievability': profile['prerequisites'][0]['retrievability']}])
        self.assertLess(profile['prerequisites'][0]['retrievability'], 0.9)
        self.assertIn('place-value', profile['review_due'])
        self.assertEqual(profile['recent_failures'][0]['lesson_id'], 'place-value')
        transport = ConciseTransport()
        adapter = OpenRouterAdapter('fixture-only', model=self.config.model, transport=transport, max_retries=0)
        await self.service.tutor.evaluate(turn['id'], adapter, self.config)
        request = transport.requests[0]
        context = next(json.loads(item['content']) for item in request['messages'] if item.get('role') == 'user')
        self.assertEqual(context['mastery']['prerequisites'][0]['lesson_id'], 'place-value')
        system = ' '.join(item['content'] for item in request['messages'] if item.get('role') == 'system')
        self.assertIn('mastery', system)
        self.assertIn('prerequisite', system)

    async def test_followup_history_does_not_carry_stale_profile(self):
        self.open_lesson('add-meaning')
        first = self.service.tutor.start(self.pid, 'ask-1', 'add-meaning', self.service.project(self.pid)['fingerprint'],
                                         'First question. / 第一个问题。', False, model=self.config.model, schedule=self.schedule)
        transport = ConciseTransport()
        adapter = OpenRouterAdapter('fixture-only', model=self.config.model, transport=transport, max_retries=0)
        await self.service.tutor.evaluate(first['result']['tutor_turn']['id'], adapter, self.config)
        second = self.service.tutor.start(self.pid, 'ask-2', 'add-meaning', self.service.project(self.pid)['fingerprint'],
                                          'Follow up. / 追问。', False, model=self.config.model, schedule=self.schedule)
        transport2 = ConciseTransport()
        adapter2 = OpenRouterAdapter('fixture-only', model=self.config.model, transport=transport2, max_retries=0)
        await self.service.tutor.evaluate(second['result']['tutor_turn']['id'], adapter2, self.config)
        context = next(json.loads(item['content']) for item in transport2.requests[0]['messages'] if item.get('role') == 'user')
        self.assertIn('mastery', context)
        for entry in context['history']:
            self.assertNotIn('mastery', entry)
            self.assertNotIn('mastery', entry.get('context', {}))

    async def test_corrupt_profile_is_rejected_before_model(self):
        self.open_lesson('add-meaning')
        receipt = self.service.tutor.start(self.pid, 'ask-1', 'add-meaning', self.service.project(self.pid)['fingerprint'],
                                           'Question. / 问题。', False, model=self.config.model, schedule=self.schedule)
        turn = receipt['result']['tutor_turn']
        stored = self.service.store.get('tutor-turn:' + self.pid, turn['id'], max_bytes=1024 * 1024)
        stored['snapshot']['mastery'] = {'skill': 'broken'}
        self.service.store.put('tutor-turn:' + self.pid, stored)
        transport = ConciseTransport()
        adapter = OpenRouterAdapter('fixture-only', model=self.config.model, transport=transport, max_retries=0)
        with self.assertRaises(AcademyError) as caught:
            await self.service.tutor.evaluate(turn['id'], adapter, self.config)
        self.assertEqual(caught.exception.code, 'store-invalid')
        self.assertEqual(transport.requests, [])


if __name__ == '__main__':
    unittest.main()

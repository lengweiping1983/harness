"""挣扎优先协议：窗口恒温器、尝试冻结、揭示边界与带练防救场守卫。"""
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
from harness.web.api.academy import build_router
from harness.web.errors import ApiError
from harness.web.router import Request

COURSE = 'primary-math-foundations'
LESSON = 'multiply-meaning'


class ConciseTransport(Transport):
    def __init__(self):
        self.requests = []

    async def post_stream(self, url, *, headers, body, timeout):
        request = json.loads(body)
        self.requests.append(request)
        yield ('data: ' + json.dumps({'model': 'fixture', 'choices': [{'delta': {'content': 'What have you tried so far? / 你目前尝试了什么？'}, 'finish_reason': None}]}) + '\n\n').encode('utf-8')
        yield ('data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}) + '\n\n').encode('utf-8')
        yield b'data: [DONE]\n\n'


class AcademyStruggleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='academy-struggle-')
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

    def open_lesson(self, lesson_id=LESSON):
        project = self.service.project(self.pid)
        receipt = self.service.start_lesson(self.pid, 'start-' + lesson_id, lesson_id, project['revision'], project['fingerprint'])
        self.assertEqual(receipt['status'], 'ready')
        return receipt['result']['project']

    def start(self, request='struggle-1', lesson_id=LESSON):
        return self.service.struggle.start(self.pid, request, lesson_id)

    async def test_start_requires_active_lesson_and_struggle_declaration(self):
        receipt = self.start()
        self.assertEqual(receipt['result']['error_code'], 'lesson-not-active')
        self.open_lesson('place-value')
        receipt = self.start('struggle-place', lesson_id='place-value')
        self.assertEqual(receipt['result']['error_code'], 'struggle-unavailable')

    async def test_attempt_freezes_and_reveal_requires_attempt_or_window(self):
        self.open_lesson()
        receipt = self.start()
        self.assertEqual(receipt['status'], 'ready')
        session = receipt['result']['struggle']
        self.assertEqual(session['status'], 'struggling')
        self.assertIn('12', session['challenge'])
        self.assertEqual(session['window_minutes'], 5)
        view = self.service.struggle.view(self.pid, LESSON)
        self.assertEqual(view['remaining_seconds'], 300)
        early = self.service.struggle.reveal(self.pid, 'reveal-early')
        self.assertEqual(early['result']['error_code'], 'struggle-too-early')
        attempt = self.service.struggle.attempt(self.pid, 'attempt-1', 'I made 3 groups of 4, not 4 groups of 3. / 我摆成了 3 个 4。')
        self.assertEqual(attempt['status'], 'ready')
        self.assertEqual(attempt['result']['struggle']['status'], 'attempted')
        revealed = self.service.struggle.reveal(self.pid, 'reveal-1')
        self.assertEqual(revealed['status'], 'ready')
        self.assertEqual(revealed['result']['struggle']['status'], 'revealed')
        self.assertIsNotNone(revealed['result']['struggle']['revealed_at'])
        frozen = self.service.struggle.attempt(self.pid, 'attempt-2', 'changed mind')
        self.assertEqual(frozen['result']['error_code'], 'struggle-revealed')
        again = self.start('struggle-2')
        self.assertEqual(again['result']['error_code'], 'struggle-exists')
        replay = self.start()
        self.assertEqual(replay['result']['struggle']['id'], session['id'])
        self.assertEqual(self.service.struggle.view(self.pid, LESSON)['remaining_seconds'], 0)

    async def test_window_expiry_allows_reveal_without_attempt(self):
        self.open_lesson()
        self.start()
        self.now += 6 * 60
        revealed = self.service.struggle.reveal(self.pid, 'reveal-1')
        self.assertEqual(revealed['status'], 'ready')
        self.assertEqual(revealed['result']['struggle']['status'], 'revealed')
        self.assertEqual(revealed['result']['struggle']['attempt'], '')

    async def test_completed_lesson_blocks_struggle_and_struggle_gives_no_mastery(self):
        self.open_lesson()
        self.start()
        self.service.struggle.attempt(self.pid, 'attempt-1', 'my first try / 我的第一次尝试')
        self.service.struggle.reveal(self.pid, 'reveal-1')
        skill = next(row for row in self.service.mastery.view(self.pid)['skills'] if row['lesson_id'] == LESSON)
        self.assertEqual(skill['evidence_count'], 0)
        self.assertEqual(skill['state'], 'new')
        project = self.service.project(self.pid)
        receipt = self.service.complete_study(self.pid, 'complete-1', LESSON, project['fingerprint'],
                                              '4 groups of 3 makes 12.', [{'check_id': 'q1', 'option_id': 'a'}])
        self.assertEqual(receipt['status'], 'ready')
        blocked = self.start('struggle-after')
        self.assertEqual(blocked['result']['error_code'], 'struggle-done')

    async def test_tutor_guard_applies_only_before_reveal(self):
        self.open_lesson()
        self.start()
        fingerprint = self.service.project(self.pid)['fingerprint']
        token = current_language.set('en')
        self.addCleanup(current_language.reset, token)
        first = self.service.tutor.start(self.pid, 'ask-1', LESSON, fingerprint, 'I am stuck. / 我卡住了。', False,
                                         model=self.config.model, schedule=lambda identity: asyncio.get_running_loop().create_future())
        transport = ConciseTransport()
        adapter = OpenRouterAdapter('fixture-only', model=self.config.model, transport=transport, max_retries=0)
        await self.service.tutor.evaluate(first['result']['tutor_turn']['id'], adapter, self.config)
        system = ' '.join(item['content'] for item in transport.requests[0]['messages'] if item.get('role') == 'system')
        self.assertIn('productive struggle', system)
        self.service.struggle.attempt(self.pid, 'attempt-1', 'my try / 我的尝试')
        self.service.struggle.reveal(self.pid, 'reveal-1')
        second = self.service.tutor.start(self.pid, 'ask-2', LESSON, fingerprint, 'Now explain. / 现在讲解吧。', False,
                                          model=self.config.model, schedule=lambda identity: asyncio.get_running_loop().create_future())
        transport2 = ConciseTransport()
        adapter2 = OpenRouterAdapter('fixture-only', model=self.config.model, transport=transport2, max_retries=0)
        await self.service.tutor.evaluate(second['result']['tutor_turn']['id'], adapter2, self.config)
        system2 = ' '.join(item['content'] for item in transport2.requests[0]['messages'] if item.get('role') == 'system')
        self.assertNotIn('productive struggle', system2)

    async def test_api_validation_precedes_writes(self):
        class Config:
            home = str(self.root / 'api-home')
        class Harness:
            config = Config()
        class Context:
            harness = Harness()
        router = build_router(Context())
        handler, params = router.resolve('POST', '/api/v1/academy/projects/{project_id}/struggle/attempt')
        request = Request('POST', '/api/v1/academy/projects/' + self.pid + '/struggle/attempt', {}, {},
                          b'{"request_id":"r1","text":""}', client_ip='127.0.0.1')
        with self.assertRaises(ApiError) as caught:
            handler(request, **params)
        self.assertEqual(caught.exception.code, 'academy/invalid-request')
        handler, params = router.resolve('GET', '/api/v1/academy/projects/{project_id}/struggle')
        request = Request('GET', '/api/v1/academy/projects/' + self.pid + '/struggle', {}, {}, b'', client_ip='127.0.0.1')
        with self.assertRaises(ApiError) as caught:
            handler(request, **params)
        self.assertEqual(caught.exception.code, 'academy/invalid-request')


if __name__ == '__main__':
    unittest.main()

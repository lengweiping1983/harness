"""初中数学团队课程：合同、双语、天平微世界、挣扎与教 AI 全链路。"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.academy.catalog import AcademyCatalog
from harness.academy.service import AcademyService
from harness.core.language import current_language

COURSE = 'middle-school-math'


class MiddleSchoolMathTests(unittest.TestCase):
    def setUp(self):
        self.catalog = AcademyCatalog()

    def test_contract_progression_and_bilingual_roundtrip(self):
        self.assertIn(COURSE, {course['id'] for course in self.catalog.courses()})
        views = []
        for language in ('en', 'zh-CN', 'en'):
            token = current_language.set(language)
            try:
                views.append(self.catalog.course(COURSE))
            finally:
                current_language.reset(token)
        english, chinese, repeated = views
        self.assertEqual(english, repeated)
        self.assertEqual(english['origin'], 'provided')
        self.assertNotEqual(english['title'], chinese['title'])
        self.assertEqual(len(english['lessons']), 6)
        self.assertEqual(len({lesson['stage'] for lesson in english['lessons']}), 2)
        self.assertEqual([lesson['order'] for lesson in english['lessons'] if lesson['milestone']], [3, 6])
        positions = set()
        for index, (en, zh) in enumerate(zip(english['lessons'], chinese['lessons'])):
            with self.subTest(lesson=en['id']):
                self.assertEqual(en['mode'], 'study')
                self.assertEqual(en['prerequisites'], [] if index == 0 else [english['lessons'][index - 1]['id']])
                for field in ('title', 'goal', 'reading', 'exercise', 'rubric', 'stage', 'stage_goal'):
                    self.assertTrue(en[field].strip())
                    self.assertTrue(zh[field].strip())
                    self.assertNotEqual(en[field], zh[field])
                for check_en, check_zh in zip(en['knowledge_checks'], zh['knowledge_checks']):
                    self.assertIn(check_en['answer_id'], {option['id'] for option in check_en['options']})
                    positions.add(next(i for i, option in enumerate(check_en['options']) if option['id'] == check_en['answer_id']))
                    self.assertTrue(all(option['feedback'].strip() for option in check_en['options']))
                    self.assertTrue(all(option['feedback'].strip() for option in check_zh['options']))
        self.assertEqual(positions, {0, 1})
        self.assertGreaterEqual(sum(1 for lesson in english['lessons'] if lesson.get('steps')), 6)
        self.assertGreaterEqual(sum(1 for lesson in english['lessons'] if any(step.get('on_wrong') for step in lesson.get('steps', []))), 2)
        balance = next(lesson for lesson in english['lessons'] if lesson['id'] == 'equation-balance')
        self.assertEqual(balance['world'], {'kind': 'balance-scale'})
        struggle = next(lesson for lesson in english['lessons'] if lesson['id'] == 'solve-two-step')
        self.assertEqual(struggle['struggle']['window_minutes'], 6)
        self.assertTrue(struggle['struggle']['challenge'].strip())

    def test_balance_world_struggle_and_teach_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AcademyService(Path(directory) / 'home')
            now = time.time()
            with patch('time.time', side_effect=lambda: now):
                project = service.create_project('create', COURSE)['result']['project']
                pid = project['id']
                receipt = service.start_lesson(pid, 'open-balance', 'equation-balance', project['revision'], project['fingerprint'])
                self.assertEqual(receipt['status'], 'ready')
                project = receipt['result']['project']
                world = service.worlds.start(pid, 'world-1', 'equation-balance')
                self.assertEqual(world['status'], 'ready')
                self.assertEqual(world['result']['world']['kind'], 'balance-scale')
                predict = service.worlds.predict(pid, 'predict-1', 'option-0')
                self.assertTrue(predict['result']['world']['prediction']['correct'])
                observe = service.worlds.observe(pid, 'observe-1', {'left': 7, 'right': 7})
                self.assertTrue(observe['result']['world']['observations'][-1]['state']['balanced'])
                complete = service.worlds.complete(pid, 'complete-world', 'Both sides must change together to stay balanced.')
                self.assertEqual(complete['status'], 'ready')
                skill = next(row for row in service.mastery.view(pid)['skills'] if row['lesson_id'] == 'equation-balance')
                self.assertEqual(skill['evidence_count'], 1)
                receipt = service.start_lesson(pid, 'open-two-step', 'solve-two-step', service.project(pid)['revision'], service.project(pid)['fingerprint'])
                project = receipt['result']['project']
                struggle = service.struggle.start(pid, 'struggle-1', 'solve-two-step')
                self.assertEqual(struggle['status'], 'ready')
                self.assertIn('3x', struggle['result']['struggle']['challenge'])
                early = service.struggle.reveal(pid, 'reveal-early')
                self.assertEqual(early['result']['error_code'], 'struggle-too-early')
                service.struggle.attempt(pid, 'attempt-1', 'I divided 11 by 3 first and got stuck. / 我先除以 3，卡住了。')
                revealed = service.struggle.reveal(pid, 'reveal-1')
                self.assertEqual(revealed['result']['struggle']['status'], 'revealed')
                receipt = service.complete_study(pid, 'complete-two-step', 'solve-two-step', service.project(pid)['fingerprint'],
                                                 'Undo minus 4 first, then divide by 3: x = 5.', [{'check_id': 'q1', 'option_id': 'a'}])
                self.assertEqual(receipt['status'], 'ready')
                teach = service.tutor.start(pid, 'teach-1', 'solve-two-step', service.project(pid)['fingerprint'],
                                            'Let me teach you two-step equations. / 我来教你两步方程。', False, mode='teach')
                self.assertEqual(teach['status'], 'ready')
                self.assertEqual(teach['result']['tutor_turn']['misconception']['option_id'], 'b')


if __name__ == '__main__':
    unittest.main()

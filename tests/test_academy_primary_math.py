"""小学数学团队课程：合同、双语消费、学习活动、记忆重测与教 AI 可用性。"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.academy.catalog import AcademyCatalog
from harness.academy.service import AcademyService
from harness.core.language import current_language

COURSE = 'primary-math-foundations'


class PrimaryMathCourseTests(unittest.TestCase):
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
        self.assertNotEqual(english['assumptions'], chinese['assumptions'])
        self.assertEqual(len(english['lessons']), 6)
        self.assertEqual(len({lesson['stage'] for lesson in english['lessons']}), 2)
        self.assertEqual([lesson['order'] for lesson in english['lessons'] if lesson['milestone']], [3, 6])
        positions = set()
        for index, (en, zh) in enumerate(zip(english['lessons'], chinese['lessons'])):
            with self.subTest(lesson=en['id']):
                self.assertEqual(en['mode'], 'study')
                self.assertEqual(en['prerequisites'], [] if index == 0 else [english['lessons'][index - 1]['id']])
                self.assertEqual(self.catalog.starter(COURSE, en['id']), {})
                for field in ('title', 'goal', 'reading', 'exercise', 'rubric', 'stage', 'stage_goal'):
                    self.assertTrue(en[field].strip())
                    self.assertTrue(zh[field].strip())
                    self.assertNotEqual(en[field], zh[field])
                self.assertTrue(en['knowledge_checks'])
                for check_en, check_zh in zip(en['knowledge_checks'], zh['knowledge_checks']):
                    self.assertGreaterEqual(len(check_en['options']), 2)
                    self.assertIn(check_en['answer_id'], {option['id'] for option in check_en['options']})
                    positions.add(next(index for index, option in enumerate(check_en['options']) if option['id'] == check_en['answer_id']))
                    for option_en, option_zh in zip(check_en['options'], check_zh['options']):
                        self.assertTrue(option_en['feedback'].strip())
                        self.assertTrue(option_zh['feedback'].strip())
                    self.assertTrue(all(hint.strip() for hint in check_en['hints']))
        self.assertEqual(positions, {0, 1})

    def test_study_activity_memory_review_and_teach_work_without_sandbox(self):
        with tempfile.TemporaryDirectory() as directory:
            service = AcademyService(Path(directory) / 'home')
            now = time.time()
            with patch('time.time', side_effect=lambda: now):
                project = service.create_project('create', COURSE)['result']['project']
                pid = project['id']
                receipt = service.complete_study(pid, 'complete-1', 'place-value', project['fingerprint'],
                                                 'The 5 stands in the tens place, so it is worth 50.',
                                                 [{'check_id': 'q1', 'option_id': 'b'}])
                self.assertEqual(receipt['status'], 'ready')
                progress = next(row for row in receipt['result']['project']['progress'] if row['lesson_id'] == 'place-value')
                self.assertEqual(progress['state'], 'completed')
                skill = next(row for row in service.mastery.view(pid)['skills'] if row['lesson_id'] == 'place-value')
                self.assertEqual(skill['state'], 'learning')
                self.assertEqual(service.mastery.queue(pid), [])
                now += 3 * 86400
                queue = service.mastery.queue(pid)
                self.assertEqual(queue[0]['lesson_id'], 'place-value')
                for check in queue[0]['checks']:
                    self.assertEqual(set(check), {'id', 'prompt', 'options'})
                review = service.mastery.review(pid, 'review-1', 'place-value', [{'check_id': 'q1', 'option_id': 'b'}])
                self.assertEqual(review['status'], 'ready')
                self.assertTrue(review['result']['review']['correct'])
                self.assertGreater(review['result']['skill']['stability_days'], 2.0)
                teach = service.tutor.start(pid, 'teach-1', 'place-value', service.project(pid)['fingerprint'],
                                            'Let me teach you place value. / 我来教你数位。', False, mode='teach')
                self.assertEqual(teach['status'], 'ready')
                turn = teach['result']['tutor_turn']
                self.assertEqual(turn['mode'], 'teach')
                self.assertEqual(turn['misconception']['check_id'], 'q1')
                self.assertEqual(turn['misconception']['option_id'], 'a')
                self.assertFalse(turn['allow_execution'])


if __name__ == '__main__':
    unittest.main()

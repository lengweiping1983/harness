"""小学数学应用题（波利亚解题）课程包：合同、双语消费、四步排序与迁移挑战。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.academy.catalog import AcademyCatalog
from harness.academy.service import AcademyService
from harness.core.language import current_language

COURSE = 'primary-math-word-problems'


class PrimaryWordProblemsTests(unittest.TestCase):
    def setUp(self):
        self.catalog = AcademyCatalog()

    def test_contract_bilingual_polya_order_transfer(self):
        views = []
        for language in ('en', 'zh-CN', 'en'):
            token = current_language.set(language)
            try:
                views.append(self.catalog.course(COURSE))
            finally:
                current_language.reset(token)
        english, chinese, repeated = views
        self.assertEqual(english, repeated)
        self.assertEqual(english['band'], 'primary')
        self.assertEqual(len(english['lessons']), 4)
        self.assertNotEqual(english['title'], chinese['title'])
        transfer_steps = [step for lesson in english['lessons'] for step in lesson.get('steps', []) if step.get('transfer')]
        self.assertGreaterEqual(len(transfer_steps), 3)
        self.assertTrue(any(lesson.get('struggle') for lesson in english['lessons']))
        order_step = next(step for lesson in english['lessons'] for step in lesson.get('steps', []) if step['kind'] == 'ordering')
        self.assertEqual(order_step['answer'], ['u', 'p', 'c', 'l'])

    def test_first_lesson_understand_award_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AcademyService(Path(temp) / 'home')
            project = service.create_project('create', COURSE)['result']['project']
            pid = project['id']
            receipt = service.start_lesson(pid, 'start', 'understand', project['revision'], project['fingerprint'])
            self.assertEqual(receipt['status'], 'ready')
            self.assertTrue(service.steps.answer(pid, 'a1', 'understand', 'worked', 'The total after buying is asked.')['result']['step']['correct'])
            self.assertTrue(service.steps.answer(pid, 'a2', 'understand', 'known', 'a')['result']['step']['correct'])
            last = service.steps.answer(pid, 'a3', 'understand', 'independent', 'b')
            self.assertTrue(last['result']['step']['completed'])
            skill = next(row for row in service.mastery.view(pid)['skills'] if row['lesson_id'] == 'understand')
            self.assertEqual(skill['evidence_count'], 1)


"""小学科学沉与浮课程包：合同、双语消费、微世界与迁移挑战。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.academy.catalog import AcademyCatalog
from harness.academy.service import AcademyService
from harness.core.language import current_language

COURSE = 'primary-science-foundations'


class PrimaryScienceCourseTests(unittest.TestCase):
    def setUp(self):
        self.catalog = AcademyCatalog()

    def test_contract_bilingual_and_world_transfer(self):
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
        world_lesson = next(lesson for lesson in english['lessons'] if lesson.get('world'))
        self.assertEqual(world_lesson['world']['kind'], 'float-sink')
        transfer_steps = [step for lesson in english['lessons'] for step in lesson.get('steps', []) if step.get('transfer')]
        self.assertGreaterEqual(len(transfer_steps), 2)
        for lesson in english['lessons']:
            self.assertEqual(lesson['mode'], 'study')
            self.assertTrue(lesson['knowledge_checks'])
            self.assertTrue(lesson['steps'])

    def test_first_lesson_steps_grade_and_award_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AcademyService(Path(temp) / 'home')
            project = service.create_project('create', COURSE)['result']['project']
            pid = project['id']
            receipt = service.start_lesson(pid, 'start', 'sink-or-float', project['revision'], project['fingerprint'])
            self.assertEqual(receipt['status'], 'ready')
            view = service.steps.view(pid, 'sink-or-float')
            self.assertEqual([step['id'] for step in view['steps']], ['worked', 'predict', 'independent'])
            self.assertTrue(service.steps.answer(pid, 'a1', 'sink-or-float', 'worked', 'Comparing densities decides sink or float.')['result']['correct'])
            self.assertTrue(service.steps.answer(pid, 'a2', 'sink-or-float', 'predict', 'a')['result']['correct'])
            last = service.steps.answer(pid, 'a3', 'sink-or-float', 'independent', 10)
            self.assertTrue(last['result']['completed'])
            skill = next(row for row in service.mastery.view(pid)['skills'] if row['lesson_id'] == 'sink-or-float')
            self.assertEqual(skill['evidence_count'], 1)

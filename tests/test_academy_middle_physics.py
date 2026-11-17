"""初中物理力与平衡课程包：合同、双语消费、天平微世界与迁移挑战。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.academy.catalog import AcademyCatalog
from harness.academy.service import AcademyService
from harness.core.language import current_language

COURSE = 'middle-school-physics'


class MiddleSchoolPhysicsTests(unittest.TestCase):
    def setUp(self):
        self.catalog = AcademyCatalog()

    def test_contract_bilingual_world_and_transfer(self):
        views = []
        for language in ('en', 'zh-CN', 'en'):
            token = current_language.set(language)
            try:
                views.append(self.catalog.course(COURSE))
            finally:
                current_language.reset(token)
        english, chinese, repeated = views
        self.assertEqual(english, repeated)
        self.assertEqual(english['band'], 'middle')
        self.assertEqual(len(english['lessons']), 4)
        self.assertNotEqual(english['title'], chinese['title'])
        world_lesson = next(lesson for lesson in english['lessons'] if lesson.get('world'))
        self.assertEqual(world_lesson['world']['kind'], 'balance-scale')
        transfer_steps = [step for lesson in english['lessons'] for step in lesson.get('steps', []) if step.get('transfer')]
        self.assertGreaterEqual(len(transfer_steps), 2)
        self.assertTrue(any(lesson.get('struggle') for lesson in english['lessons']))

    def test_first_lesson_steps_grade_and_award_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AcademyService(Path(temp) / 'home')
            project = service.create_project('create', COURSE)['result']['project']
            pid = project['id']
            receipt = service.start_lesson(pid, 'start', 'net-force', project['revision'], project['fingerprint'])
            self.assertEqual(receipt['status'], 'ready')
            self.assertTrue(service.steps.answer(pid, 'a1', 'net-force', 'worked', 'Zero net force means motion does not change.')['result']['step']['correct'])
            self.assertTrue(service.steps.answer(pid, 'a2', 'net-force', 'compute', 7)['result']['step']['correct'])
            last = service.steps.answer(pid, 'a3', 'net-force', 'independent', 'b')
            self.assertTrue(last['result']['step']['completed'])
            skill = next(row for row in service.mastery.view(pid)['skills'] if row['lesson_id'] == 'net-force')
            self.assertEqual(skill['evidence_count'], 1)

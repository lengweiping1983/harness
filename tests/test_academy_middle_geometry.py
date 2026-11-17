"""初中几何度量与推理课程包：合同、双语消费、推理步骤与迁移挑战。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.academy.catalog import AcademyCatalog
from harness.academy.service import AcademyService
from harness.core.language import current_language

COURSE = 'middle-school-geometry'


class MiddleSchoolGeometryTests(unittest.TestCase):
    def setUp(self):
        self.catalog = AcademyCatalog()

    def test_contract_bilingual_transfer_struggle(self):
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
        transfer_steps = [step for lesson in english['lessons'] for step in lesson.get('steps', []) if step.get('transfer')]
        self.assertGreaterEqual(len(transfer_steps), 2)
        self.assertTrue(any(lesson.get('struggle') for lesson in english['lessons']))

    def test_first_lesson_perimeter_area_award_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AcademyService(Path(temp) / 'home')
            project = service.create_project('create', COURSE)['result']['project']
            pid = project['id']
            receipt = service.start_lesson(pid, 'start', 'perimeter-area', project['revision'], project['fingerprint'])
            self.assertEqual(receipt['status'], 'ready')
            self.assertTrue(service.steps.answer(pid, 'a1', 'perimeter-area', 'worked', 'Area covers squares; perimeter walks sides.')['result']['step']['correct'])
            self.assertTrue(service.steps.answer(pid, 'a2', 'perimeter-area', 'area', 24)['result']['step']['correct'])
            last = service.steps.answer(pid, 'a3', 'perimeter-area', 'independent', 'b')
            self.assertTrue(last['result']['step']['completed'])
            skill = next(row for row in service.mastery.view(pid)['skills'] if row['lesson_id'] == 'perimeter-area')
            self.assertEqual(skill['evidence_count'], 1)

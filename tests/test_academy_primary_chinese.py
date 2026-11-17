"""小学语文阅读理解课程包：合同、双语消费、排序/推断步骤与迁移挑战（人文题材扩展）。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.academy.catalog import AcademyCatalog
from harness.academy.service import AcademyService
from harness.core.language import current_language

COURSE = 'primary-chinese-reading'


class PrimaryChineseReadingTests(unittest.TestCase):
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
        self.assertEqual(english['band'], 'primary')
        self.assertEqual(len(english['lessons']), 4)
        self.assertNotEqual(english['title'], chinese['title'])
        transfer_steps = [step for lesson in english['lessons'] for step in lesson.get('steps', []) if step.get('transfer')]
        self.assertGreaterEqual(len(transfer_steps), 2)
        self.assertTrue(any(lesson.get('struggle') for lesson in english['lessons']))
        kinds = {step['kind'] for lesson in english['lessons'] for step in lesson.get('steps', [])}
        self.assertIn('ordering', kinds)

    def test_first_lesson_context_clues_award_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AcademyService(Path(temp) / 'home')
            project = service.create_project('create', COURSE)['result']['project']
            pid = project['id']
            receipt = service.start_lesson(pid, 'start', 'word-meaning', project['revision'], project['fingerprint'])
            self.assertEqual(receipt['status'], 'ready')
            self.assertTrue(service.steps.answer(pid, 'a1', 'word-meaning', 'worked', 'Eating a lot implies hunger.')['result']['step']['correct'])
            self.assertTrue(service.steps.answer(pid, 'a2', 'word-meaning', 'clue', 'a')['result']['step']['correct'])
            last = service.steps.answer(pid, 'a3', 'word-meaning', 'independent', 'b')
            self.assertTrue(last['result']['step']['completed'])
            skill = next(row for row in service.mastery.view(pid)['skills'] if row['lesson_id'] == 'word-meaning')
            self.assertEqual(skill['evidence_count'], 1)


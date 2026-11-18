"""初中文言文入门课程包：合同、双语消费、实词匹配/断句排序与迁移挑战。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.academy.catalog import AcademyCatalog
from harness.academy.service import AcademyService
from harness.core.language import current_language

COURSE = 'middle-school-chinese-classical'


class MiddleSchoolClassicalTests(unittest.TestCase):
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
        self.assertGreaterEqual(len(transfer_steps), 3)
        self.assertTrue(any(lesson.get('struggle') for lesson in english['lessons']))
        kinds = {step['kind'] for lesson in english['lessons'] for step in lesson.get('steps', [])}
        self.assertTrue({'matching', 'ordering'} <= kinds)

    def test_first_lesson_content_words_award_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            service = AcademyService(Path(temp) / 'home')
            project = service.create_project('create', COURSE)['result']['project']
            pid = project['id']
            receipt = service.start_lesson(pid, 'start', 'content-words', project['revision'], project['fingerprint'])
            self.assertEqual(receipt['status'], 'ready')
            self.assertTrue(service.steps.answer(pid, 'a1', 'content-words', 'worked', 'A running rabbit hits the stump.')['result']['step']['correct'])
            self.assertTrue(service.steps.answer(pid, 'a2', 'content-words', 'match', {'z': 'r', 't': 'h', 'q': 'l'})['result']['step']['correct'])
            last = service.steps.answer(pid, 'a3', 'content-words', 'independent', 'a')
            self.assertTrue(last['result']['step']['completed'])
            skill = next(row for row in service.mastery.view(pid)['skills'] if row['lesson_id'] == 'content-words')
            self.assertEqual(skill['evidence_count'], 1)


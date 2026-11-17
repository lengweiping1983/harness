"""步骤式引擎：逐步宿主评分、即时反馈、例题→独立序列与记忆证据。"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.academy import steps
from harness.academy.errors import AcademyError
from harness.academy.service import AcademyService

COURSE = 'primary-math-foundations'
LESSON = 'fraction-intuition'


class StepGradeTests(unittest.TestCase):
    def test_explain_requires_nonempty_text(self):
        step = {'kind': 'explain'}
        self.assertTrue(steps.grade(step, 'Because equal parts share one name.')[0])
        for bad in ('', '   ', 'x' * 2001, 5):
            with self.assertRaises(AcademyError) as caught:
                steps.grade(step, bad)
            self.assertEqual(caught.exception.code, 'step-invalid')

    def test_numeric_tolerance_and_type(self):
        step = {'kind': 'numeric', 'answer': 4, 'tolerance': 0.5}
        self.assertTrue(steps.grade(step, 4.2)[0])
        self.assertFalse(steps.grade(step, 5)[0])
        with self.assertRaises(AcademyError):
            steps.grade(step, '4')

    def test_choice_and_ordering(self):
        choice = {'kind': 'choice', 'answer_id': 'b', 'options': [{'id': 'a'}, {'id': 'b'}]}
        self.assertTrue(steps.grade(choice, 'b')[0])
        self.assertFalse(steps.grade(choice, 'a')[0])
        with self.assertRaises(AcademyError):
            steps.grade(choice, 'c')
        ordering = {'kind': 'ordering', 'items': [{'id': 'x'}, {'id': 'y'}, {'id': 'z'}], 'answer': ['x', 'y', 'z']}
        self.assertTrue(steps.grade(ordering, ['x', 'y', 'z'])[0])
        self.assertFalse(steps.grade(ordering, ['y', 'x', 'z'])[0])
        with self.assertRaises(AcademyError):
            steps.grade(ordering, ['x', 'y'])

    def test_matching_grades_bijection(self):
        step = {'kind': 'matching', 'left': [{'id': 'a'}, {'id': 'b'}], 'right': [{'id': 'x'}, {'id': 'y'}], 'answer': {'a': 'x', 'b': 'y'}}
        self.assertTrue(steps.grade(step, {'a': 'x', 'b': 'y'})[0])
        self.assertFalse(steps.grade(step, {'a': 'y', 'b': 'x'})[0])
        for bad in ({'a': 'x'}, {'a': 'x', 'b': 'z'}, {'a': 'x', 'b': 'y', 'c': 'x'}, 'nope'):
            with self.assertRaises(AcademyError):
                steps.grade(step, bad)
    def test_public_step_hides_answers_and_reverses_ordering(self):
        ordering = {'id': 's', 'kind': 'ordering', 'prompt': 'p', 'items': [{'id': 'x', 'label': 'X'}, {'id': 'y', 'label': 'Y'}], 'answer': ['x', 'y']}
        public = steps.public_step(ordering)
        self.assertNotIn('answer', public)
        self.assertEqual([item['id'] for item in public['items']], ['y', 'x'])
        numeric = steps.public_step({'id': 'n', 'kind': 'numeric', 'prompt': 'p', 'answer': 4, 'tolerance': 0})
        self.assertNotIn('answer', numeric)
        self.assertEqual(numeric['tolerance'], 0)


class StepSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = AcademyService(self.root / 'home')
        self.project = self.service.create_project('create', COURSE)['result']['project']
        self.pid = self.project['id']
        self.now = time.time()
        patcher = patch('time.time', side_effect=lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)

    def open_lesson(self, lesson_id=LESSON):
        project = self.service.project(self.pid)
        receipt = self.service.start_lesson(self.pid, 'start-' + lesson_id, lesson_id, project['revision'], project['fingerprint'])
        self.assertEqual(receipt['status'], 'ready')

    def answer(self, step_id, response, request=None):
        return self.service.steps.answer(self.pid, request or ('ans-' + step_id), LESSON, step_id, response)

    def test_full_step_sequence_grades_each_step_and_awards_evidence(self):
        self.open_lesson()
        view = self.service.steps.view(self.pid, LESSON)
        self.assertEqual([step['id'] for step in view['steps']], ['worked', 'denominator', 'denominator-support', 'which-half', 'third-sequence'])
        for step in view['steps']:
            self.assertNotIn('answer', step)
            self.assertNotIn('answer_id', step)
        self.assertFalse(view['completed'])
        worked = self.answer('worked', 'Equal parts are needed so each part is the same size.')
        self.assertTrue(worked['result']['step']['correct'])
        self.assertIsNotNone(worked['result']['step']['feedback'])
        wrong = self.answer('denominator', 5)
        self.assertFalse(wrong['result']['step']['correct'])
        self.assertIsNotNone(wrong['result']['step']['feedback'])
        retry = self.answer('denominator', 4, request='ans-denominator-2')
        self.assertTrue(retry['result']['step']['correct'])
        self.assertEqual(retry['result']['step']['response']['attempts'], 2)
        self.answer('which-half', 'b')
        last = self.answer('third-sequence', ['cut', 'take', 'write'])
        self.assertTrue(last['result']['step']['completed'])
        self.assertTrue(self.service.steps.view(self.pid, LESSON)['completed'])
        skill = next(row for row in self.service.mastery.view(self.pid)['skills'] if row['lesson_id'] == LESSON)
        self.assertEqual(skill['evidence_count'], 1)
        self.assertEqual(skill['stability_days'], 2.5)
        again = self.answer('worked', 'another explanation', request='ans-after')
        self.assertEqual(again['result']['error_code'], 'steps-completed')

    def test_support_step_is_optional_and_routing_is_public(self):
        self.open_lesson()
        view = self.service.steps.view(self.pid, LESSON)
        denom = next(step for step in view['steps'] if step['id'] == 'denominator')
        self.assertEqual(denom['on_wrong'], 'denominator-support')
        self.answer('worked', 'Equal parts share one fraction name.')
        self.answer('denominator', 4)
        self.answer('which-half', 'b')
        last = self.answer('third-sequence', ['cut', 'take', 'write'])
        self.assertTrue(last['result']['step']['completed'])
    def test_numeric_answer_returns_number_line_mark(self):
        self.open_lesson()
        result = self.answer('denominator', 7)['result']['step']
        self.assertEqual(result['number_line'], {'min': 0, 'max': 12, 'value': 7})
        choice = self.answer('which-half', 'b')['result']['step']
        self.assertNotIn('number_line', choice)
    def test_scaffold_is_contingent_on_wrong_answer_and_hidden_in_public(self):
        self.open_lesson()
        view = self.service.steps.view(self.pid, LESSON)
        self.assertTrue(all('scaffold' not in step for step in view['steps']))
        wrong = self.answer('denominator', 5)
        self.assertFalse(wrong['result']['step']['correct'])
        self.assertIsNotNone(wrong['result']['step']['scaffold'])
        right = self.answer('denominator', 4, request='ans-denominator-ok')
        self.assertTrue(right['result']['step']['correct'])
        self.assertNotIn('scaffold', right['result']['step'])
    def test_unknown_step_and_lesson_without_steps(self):
        self.open_lesson()
        missing = self.answer('nope', 'x')
        self.assertEqual(missing['result']['error_code'], 'step-not-found')
        other = self.service.create_project('create-hf', 'harness-foundations')['result']['project']
        self.service.start_lesson(other['id'], 'open-hf', 'agent-loop', other['revision'], other['fingerprint'])
        unavailable = self.service.steps.answer(other['id'], 'ans-hf', 'agent-loop', 'q1', 'x')
        self.assertEqual(unavailable['result']['error_code'], 'steps-unavailable')

    def test_blindspot_repair_loop_flags_and_calibration(self):
        """高确信错误记盲点；之后答对记修复并给 repaired 校准；投影保留历史标记。"""
        self.open_lesson()
        wrong = self.service.steps.answer(self.pid, 'bs-wrong', LESSON, 'denominator', 5, confidence='sure')['result']['step']
        self.assertEqual(wrong['calibration'], 'blindspot')
        self.assertTrue(wrong['response']['blindspot'])
        self.assertNotIn('repaired', wrong['response'])
        fixed = self.service.steps.answer(self.pid, 'bs-fix', LESSON, 'denominator', 4, confidence='sure')['result']['step']
        self.assertTrue(fixed['correct'])
        self.assertEqual(fixed['calibration'], 'repaired')
        self.assertTrue(fixed['response']['blindspot'])
        self.assertTrue(fixed['response']['repaired'])
        projected = {row['step_id']: row for row in self.service.steps.view(self.pid, LESSON)['responses']}
        self.assertTrue(projected['denominator']['blindspot'])
        self.assertTrue(projected['denominator']['repaired'])
    def test_diagnosis_recorded_and_projected(self):
        """错误自诊断随作答记录持久化并投影；重试修复后仍保留诊断原文。"""
        self.open_lesson()
        wrong = self.service.steps.answer(self.pid, 'dg-wrong', LESSON, 'denominator', 5, confidence='sure',
                                          diagnosis='I counted parts instead of equal groups.')['result']['step']
        self.assertEqual(wrong['response']['diagnosis'], 'I counted parts instead of equal groups.')
        fixed = self.service.steps.answer(self.pid, 'dg-fix', LESSON, 'denominator', 4, confidence='sure')['result']['step']
        self.assertEqual(fixed['calibration'], 'repaired')
        self.assertEqual(fixed['response']['diagnosis'], 'I counted parts instead of equal groups.')
        projected = {row['step_id']: row for row in self.service.steps.view(self.pid, LESSON)['responses']}
        self.assertEqual(projected['denominator']['diagnosis'], 'I counted parts instead of equal groups.')
    def test_report_aggregates_repairs_transfers_blindspots(self):
        """学习报告聚合来自持久作答：修复数、迁移成功数与待修复盲点数。"""
        self.open_lesson('add-meaning')
        self.service.steps.answer(self.pid, 'rp-wrong', 'add-meaning', 'transfer-pages', 'b', confidence='sure')
        view = self.service.mastery.view(self.pid)
        self.assertEqual(view['repairs'], 0)
        self.assertEqual(view['transfers'], 0)
        self.assertEqual(view['blindspots_open'], 1)
        self.service.steps.answer(self.pid, 'rp-fix', 'add-meaning', 'transfer-pages', 'a', confidence='sure')
        view = self.service.mastery.view(self.pid)
        self.assertEqual(view['repairs'], 1)
        self.assertEqual(view['transfers'], 1)
        self.assertEqual(view['blindspots_open'], 0)
        self.assertEqual(view['streak_days'], 1)
        self.assertEqual(view['calibration']['sure_correct'], 1)
        self.assertEqual(view['calibration']['sure_wrong'], 0)
        self.assertEqual(len(view['activity']), 14)
        self.assertGreaterEqual(view['activity'][-1]['evidence'], 1)
    def test_transfer_challenge_public_flag_and_memory_weight(self):
        """迁移挑战公开标记不泄答案；新情境答对给予更高初始稳定性。"""
        self.open_lesson('add-meaning')
        view = self.service.steps.view(self.pid, 'add-meaning')
        worked = next(step for step in view['steps'] if step['id'] == 'worked')
        self.assertIn('reference', worked)
        transfer = next(step for step in view['steps'] if step['id'] == 'transfer-pages')
        self.assertTrue(transfer['transfer'])
        self.assertNotIn('answer_id', transfer)
        self.service.steps.answer(self.pid, 'tr-worked', 'add-meaning', 'worked', 'Adding combines two amounts into one total.')
        self.service.steps.answer(self.pid, 'tr-match', 'add-meaning', 'match-stories', {'l1': 'r1', 'l2': 'r2', 'l3': 'r3'})
        self.service.steps.answer(self.pid, 'tr-which', 'add-meaning', 'which-add', 'a')
        last = self.service.steps.answer(self.pid, 'tr-transfer', 'add-meaning', 'transfer-pages', 'a')
        self.assertTrue(last['result']['step']['completed'])
        skill = next(row for row in self.service.mastery.view(self.pid)['skills'] if row['lesson_id'] == 'add-meaning')
        self.assertEqual(skill['stability_days'], 3.5)
        self.assertEqual(skill['evidence_count'], 1)
    def test_confidence_calibration_and_projection(self):
        """置信度只产生校准反馈与记录，不改变宿主评分真值；重放保持幂等。"""
        self.open_lesson()
        blind = self.service.steps.answer(self.pid, 'ans-blind', LESSON, 'denominator', 5, confidence='sure')['result']['step']
        self.assertFalse(blind['correct'])
        self.assertEqual(blind['calibration'], 'blindspot')
        self.assertEqual(blind['response']['confidence'], 'sure')
        lucky = self.service.steps.answer(self.pid, 'ans-lucky', LESSON, 'which-half', 'b', confidence='guess')['result']['step']
        self.assertTrue(lucky['correct'])
        self.assertEqual(lucky['calibration'], 'lucky')
        calibrated = self.service.steps.answer(self.pid, 'ans-cal', LESSON, 'which-half', 'b', confidence='sure')['result']['step']
        self.assertEqual(calibrated['calibration'], 'calibrated')
        expected = self.service.steps.answer(self.pid, 'ans-exp', LESSON, 'third-sequence', ['take', 'cut', 'write'], confidence='unsure')['result']['step']
        self.assertFalse(expected['correct'])
        self.assertEqual(expected['calibration'], 'expected')
        plain = self.service.steps.answer(self.pid, 'ans-plain', LESSON, 'worked', 'Equal parts share one name.')['result']['step']
        self.assertNotIn('calibration', plain)
        self.assertNotIn('confidence', plain['response'])
        view = self.service.steps.view(self.pid, LESSON)
        projected = {row['step_id']: row for row in view['responses']}
        self.assertEqual(projected['denominator']['confidence'], 'sure')
        self.assertTrue(projected['denominator']['blindspot'])
        self.assertEqual(projected['which-half']['confidence'], 'sure')
        self.assertNotIn('confidence', projected['worked'])
        replay = self.service.steps.answer(self.pid, 'ans-blind', LESSON, 'denominator', 5, confidence='sure')
        self.assertEqual(replay['status'], 'ready')
        self.assertEqual(replay['result']['step']['calibration'], 'blindspot')
    def test_receipt_idempotency_and_invalid_response(self):
        self.open_lesson()
        first = self.answer('denominator', 4)
        replay = self.answer('denominator', 4)
        self.assertEqual(first['result']['step']['response']['attempts'], replay['result']['step']['response']['attempts'])
        bad = self.service.steps.answer(self.pid, 'ans-bad', LESSON, 'denominator', 'four')
        self.assertEqual(bad['result']['error_code'], 'step-invalid')



class MiddleSchoolStepTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = AcademyService(self.root / 'home')
        self.project = self.service.create_project('create', 'middle-school-math')['result']['project']
        self.pid = self.project['id']
        self.now = time.time()
        patcher = patch('time.time', side_effect=lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)

    def test_expressions_step_sequence_with_matching_awards_evidence(self):
        project = self.service.project(self.pid)
        receipt = self.service.start_lesson(self.pid, 'open-expr', 'expressions', project['revision'], project['fingerprint'])
        self.assertEqual(receipt['status'], 'ready')
        view = self.service.steps.view(self.pid, 'expressions')
        self.assertEqual([step['kind'] for step in view['steps']], ['explain', 'matching', 'numeric', 'choice'])
        match = view['steps'][1]
        self.assertNotIn('answer', match)
        self.service.steps.answer(self.pid, 'a1', 'expressions', 'worked', 'Multiply first because 2x means 2 times x.')
        wrong = self.service.steps.answer(self.pid, 'a2', 'expressions', 'match-values', {'e1': 'v9', 'e2': 'v11', 'e3': 'v10'})
        self.assertFalse(wrong['result']['step']['correct'])
        right = self.service.steps.answer(self.pid, 'a3', 'expressions', 'match-values', {'e1': 'v11', 'e2': 'v9', 'e3': 'v10'})
        self.assertTrue(right['result']['step']['correct'])
        self.service.steps.answer(self.pid, 'a4', 'expressions', 'evaluate', 13)
        last = self.service.steps.answer(self.pid, 'a5', 'expressions', 'independent', 'b')
        self.assertTrue(last['result']['step']['completed'])
        skill = next(row for row in self.service.mastery.view(self.pid)['skills'] if row['lesson_id'] == 'expressions')
        self.assertEqual(skill['evidence_count'], 1)
        self.assertEqual(skill['stability_days'], 2.5)

if __name__ == '__main__':
    unittest.main()

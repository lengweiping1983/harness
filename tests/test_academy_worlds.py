"""微世界：内核真值、预测冻结、操作边界、解释完成与记忆证据。"""
from __future__ import annotations

import tempfile
import uuid
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.academy import worlds
from harness.i18n import tr
from harness.academy.errors import AcademyError
from harness.academy.mastery import INITIAL_STABILITY
from harness.academy.service import AcademyService

COURSE = 'primary-math-foundations'


class WorldKernelTests(unittest.TestCase):
    def test_fraction_kernel_truth_and_bounds(self):
        self.assertEqual(worlds.fraction_state({'parts': 6, 'shaded': 2})['label'], '2/6')
        self.assertAlmostEqual(worlds.fraction_state({'parts': 4, 'shaded': 1})['value'], 0.25)
        for params in ({'parts': 1, 'shaded': 0}, {'parts': 13, 'shaded': 1}, {'parts': 4, 'shaded': 5},
                       {'parts': 4, 'shaded': -1}, {'parts': 4.0, 'shaded': 1}, {'parts': True, 'shaded': 1}, {}):
            with self.assertRaises(AcademyError) as caught:
                worlds.fraction_state(params)
            self.assertEqual(caught.exception.code, 'world-invalid')

    def test_float_kernel_truth_and_bounds(self):
        """沉浮与浸入比例由密度比决定；越界参数明确拒绝。"""
        state = worlds.float_state({'object': 6, 'liquid': 10})
        self.assertTrue(state['floats'])
        self.assertEqual(state['submerged'], 0.6)
        sunk = worlds.float_state({'object': 12, 'liquid': 10})
        self.assertFalse(sunk['floats'])
        self.assertEqual(sunk['submerged'], 1.0)
        equal = worlds.float_state({'object': 10, 'liquid': 10})
        self.assertFalse(equal['floats'])
        self.assertEqual(equal['submerged'], 1.0)
        with self.assertRaises(AcademyError):
            worlds.float_state({'object': 0, 'liquid': 10})
        with self.assertRaises(AcademyError):
            worlds.float_state({'object': 6, 'liquid': 21})
    def test_groups_and_balance_truth(self):
        state = worlds.groups_state({'groups': 4, 'per_group': 3})
        self.assertEqual(state['total'], 12)
        self.assertEqual(state['repeated'], '3 + 3 + 3 + 3')
        self.assertEqual(state['product'], '4 × 3')
        self.assertTrue(worlds.balance_state({'left': 7, 'right': 7})['balanced'])
        self.assertEqual(worlds.balance_state({'left': 7, 'right': 3})['difference'], 4)
        with self.assertRaises(AcademyError):
            worlds.groups_state({'groups': 10, 'per_group': 3})

    def test_challenges_are_graded_by_kernel_truth_with_misconception_distractors(self):
        for kind in worlds.KINDS:
            task = worlds.challenge(kind)
            labels = [option['label'] for option in task['options']]
            self.assertEqual(len(labels), 3)
            self.assertEqual(len(set(labels)), 3)
            self.assertEqual(task['answer_id'], 'option-0')
            if kind == 'fraction-bar':
                self.assertEqual(labels[0], worlds.fraction_state(task['target'])['label'])
                self.assertEqual(labels[1], '6/2')
            elif kind == 'group-array':
                self.assertEqual(labels[0], str(worlds.groups_state(task['target'])['total']))
                self.assertEqual(labels[1], '7')
            elif kind == 'balance-scale':
                self.assertEqual(labels[0], '7')
                self.assertTrue(worlds.balance_state({'left': 7, 'right': int(labels[0])})['balanced'])
            else:
                self.assertEqual(labels[0], tr('ui.academy.world.float.optionSink'))
                self.assertFalse(worlds.float_state(task['target'])['floats'])


class WorldSessionTests(unittest.TestCase):
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

    def open_lesson(self, lesson_id):
        project = self.service.project(self.pid)
        receipt = self.service.start_lesson(self.pid, 'start-' + lesson_id + '-' + uuid.uuid4().hex[:8], lesson_id, project['revision'], project['fingerprint'])
        self.assertEqual(receipt['status'], 'ready')

    def start(self, lesson_id='fraction-intuition', request='world-1'):
        return self.service.worlds.start(self.pid, request, lesson_id)

    def test_full_predict_observe_explain_flow_grades_by_kernel(self):
        self.open_lesson('fraction-intuition')
        receipt = self.start()
        self.assertEqual(receipt['status'], 'ready')
        session = receipt['result']['world']
        self.assertEqual(session['status'], 'predicting')
        self.assertEqual(session['kind'], 'fraction-bar')
        self.assertNotIn('answer_id', session['challenge'])
        self.assertEqual(session['challenge']['target'], {'parts': 6, 'shaded': 2})
        view = self.service.worlds.view(self.pid, 'fraction-intuition')['world']
        self.assertNotIn('answer_id', view['challenge'])
        wrong = self.service.worlds.predict(self.pid, 'predict-1', 'option-1')
        self.assertEqual(wrong['status'], 'ready')
        self.assertFalse(wrong['result']['world']['prediction']['correct'])
        self.assertEqual(wrong['result']['world']['status'], 'observing')
        early = self.service.worlds.complete(self.pid, 'complete-early', 'I think it is 6/2.')
        self.assertEqual(early['result']['error_code'], 'world-not-ready')
        observed = self.service.worlds.observe(self.pid, 'observe-1', {'parts': 6, 'shaded': 2})
        self.assertEqual(observed['status'], 'ready')
        state = observed['result']['world']['observations'][-1]['state']
        self.assertEqual(state['label'], '2/6')
        bad = self.service.worlds.observe(self.pid, 'observe-bad', {'parts': 6, 'shaded': 9})
        self.assertEqual(bad['result']['error_code'], 'world-invalid')
        completed = self.service.worlds.complete(self.pid, 'complete-1', 'I predicted 6/2, but the bar shows 2/6: the bottom counts equal parts.')
        self.assertEqual(completed['status'], 'ready')
        self.assertEqual(completed['result']['world']['status'], 'completed')
        skill = next(row for row in self.service.mastery.view(self.pid)['skills'] if row['lesson_id'] == 'fraction-intuition')
        self.assertEqual(skill['evidence_count'], 1)
        self.assertEqual(skill['stability_days'], INITIAL_STABILITY['world'])
        again = self.start(request='world-2')
        self.assertEqual(again['result']['error_code'], 'world-exists')
        replay = self.start()
        self.assertEqual(replay['result']['world']['id'], session['id'])

    def test_prediction_requires_valid_option_and_predicting_state(self):
        self.open_lesson('fraction-intuition')
        self.start()
        bad = self.service.worlds.predict(self.pid, 'predict-bad', 'option-99')
        self.assertEqual(bad['result']['error_code'], 'world-invalid')
        self.service.worlds.predict(self.pid, 'predict-1', 'option-0')
        again = self.service.worlds.predict(self.pid, 'predict-2', 'option-1')
        self.assertEqual(again['result']['error_code'], 'world-not-ready')
        self.assertTrue(self.service.worlds.view(self.pid, 'fraction-intuition')['world']['prediction']['correct'])

    def test_observation_limit_and_missing_session(self):
        self.open_lesson('fraction-intuition')
        missing = self.service.worlds.observe(self.pid, 'observe-missing', {'parts': 4, 'shaded': 1})
        self.assertEqual(missing['result']['error_code'], 'world-not-found')
        self.start()
        self.service.worlds.predict(self.pid, 'predict-1', 'option-0')
        for index in range(worlds.MAX_OBSERVATIONS):
            receipt = self.service.worlds.observe(self.pid, 'observe-' + str(index), {'parts': 4, 'shaded': 1})
            self.assertEqual(receipt['status'], 'ready')
        over = self.service.worlds.observe(self.pid, 'observe-over', {'parts': 4, 'shaded': 1})
        self.assertEqual(over['result']['error_code'], 'world-limit')

    def test_session_follows_active_lesson_across_switches(self):
        self.open_lesson('fraction-intuition')
        self.start()
        self.service.worlds.predict(self.pid, 'predict-f', 'option-0')
        self.open_lesson('multiply-meaning')
        self.start(lesson_id='multiply-meaning', request='world-mm')
        self.service.worlds.predict(self.pid, 'predict-mm', 'option-0')
        self.open_lesson('fraction-intuition')
        observe = self.service.worlds.observe(self.pid, 'observe-f', {'parts': 6, 'shaded': 2})
        self.assertEqual(observe['status'], 'ready')
        self.assertEqual(observe['result']['world']['lesson_id'], 'fraction-intuition')
        complete = self.service.worlds.complete(self.pid, 'complete-f', 'Equal parts make the fraction.')
        self.assertEqual(complete['result']['world']['lesson_id'], 'fraction-intuition')
        other = self.service.worlds.view(self.pid, 'multiply-meaning')['world']
        self.assertEqual(other['status'], 'observing')

    def test_world_unavailable_and_done_lessons(self):
        self.open_lesson('place-value')
        receipt = self.start(lesson_id='place-value')
        self.assertEqual(receipt['result']['error_code'], 'world-unavailable')
        self.open_lesson('multiply-meaning')
        project = self.service.project(self.pid)
        complete = self.service.complete_study(self.pid, 'complete-mm', 'multiply-meaning', project['fingerprint'],
                                               '4 groups of 3 is 12.', [{'check_id': 'q1', 'option_id': 'a'}])
        self.assertEqual(complete['status'], 'ready')
        receipt = self.start(lesson_id='multiply-meaning', request='world-mm')
        self.assertEqual(receipt['result']['error_code'], 'world-done')


if __name__ == '__main__':
    unittest.main()

"""记忆引擎与延迟独立重测的针对性回归：纯函数边界、证据投影、评分与回执。"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.academy import mastery
from harness.academy.errors import AcademyError
from harness.academy.protocol import SCHEMAS
from harness.academy.service import AcademyService
from harness.tools.schema import validate
from harness.web.api.academy import build_router
from harness.web.errors import ApiError
from harness.web.router import Request

DAY = mastery.DAY


def write_memory_fixture(root, with_steps=False):
    """三节 study 课、两个阶段、各一道自测；不依赖沙箱与模型。"""
    directory = root / 'memory-fixture'
    directory.mkdir(parents=True)
    lessons = []
    resources = {'title': 'Memory fixture', 'description': 'Memory fixture',
                 'stage.a': 'Stage A', 'stage.b': 'Stage B'}
    plan = [('fractions', 'stage.a', []), ('decimals', 'stage.a', []), ('ratios', 'stage.b', ['fractions'])]
    for order, (identity, stage, prerequisites) in enumerate(plan, 1):
        lesson = {'id': identity, 'order': order, 'mode': 'study', 'milestone': False,
                  'title_key': identity + '.title', 'goal_key': identity + '.goal', 'reading_key': identity + '.reading',
                  'stage_key': stage, 'references': [], 'prerequisites': prerequisites,
                  'knowledge_checks': [{'id': 'q1', 'prompt_key': identity + '.q',
                      'options': [{'id': 'right', 'label_key': identity + '.right', 'feedback_key': identity + '.right.fb'},
                                  {'id': 'wrong', 'label_key': identity + '.wrong', 'feedback_key': identity + '.wrong.fb'}],
                      'answer_id': 'right', 'hint_keys': [identity + '.hint']}]}
        if with_steps and identity == 'fractions':
            lesson['steps'] = [
                {'id': 's1', 'kind': 'numeric', 'prompt_key': 'fractions.s1', 'feedback_key': 'fractions.s1.fb', 'answer': 4, 'tolerance': 0},
                {'id': 's2', 'kind': 'choice', 'prompt_key': 'fractions.s2', 'feedback_key': 'fractions.s2.fb', 'transfer': True,
                 'answer_id': 'a', 'options': [{'id': 'a', 'label_key': 'fractions.s2.a', 'feedback_key': 'fractions.s2.a.fb'},
                                              {'id': 'b', 'label_key': 'fractions.s2.b', 'feedback_key': 'fractions.s2.b.fb'}]}]
        lessons.append(lesson)
        resources.update({identity + '.title': identity, identity + '.goal': identity, identity + '.reading': identity,
                          identity + '.q': 'Pick the right answer for ' + identity,
                          identity + '.right': 'Right', identity + '.right.fb': 'Yes',
                          identity + '.wrong': 'Wrong', identity + '.wrong.fb': 'No',
                          identity + '.hint': 'Think again'})
    if with_steps:
        resources.update({'fractions.s1': 'How many equal parts?', 'fractions.s1.fb': 'Think equal parts.',
                          'fractions.s2': 'Which transfer case is half?', 'fractions.s2.fb': 'Think new context.',
                          'fractions.s2.a': 'Two of four equal parts', 'fractions.s2.a.fb': 'Yes.',
                          'fractions.s2.b': 'Any two pieces', 'fractions.s2.b.fb': 'No.'})
    manifest = {'schema': 1, 'id': directory.name, 'version': '1.0.0', 'source_revision': 'fixture',
                'title_key': 'title', 'description_key': 'description', 'references': [], 'lessons': lessons}
    (directory / 'course.json').write_text(json.dumps(manifest), encoding='utf-8')
    (directory / 'locales').mkdir()
    for language in ('en', 'zh-CN'):
        (directory / 'locales' / (language + '.json')).write_text(json.dumps(resources, ensure_ascii=False), encoding='utf-8')


class MemoryMathTests(unittest.TestCase):
    def test_retrievability_at_stability_equals_target(self):
        for stability in (0.5, 2.0, 3.0, 30.0):
            self.assertAlmostEqual(mastery.retrievability(stability, stability), mastery.TARGET_RETENTION, places=6)

    def test_harder_recall_grows_stability_more(self):
        hard, _ = mastery.succeed(3.0, 5.0, 0.5)
        easy, _ = mastery.succeed(3.0, 5.0, 0.95)
        self.assertGreater(hard, easy)
        self.assertGreater(easy, 3.0)

    def test_massed_review_barely_grows_stability(self):
        grown, _ = mastery.succeed(3.0, 5.0, 0.999)
        self.assertLess(grown, 3.05)

    def test_lapse_shrinks_stability_and_raises_difficulty(self):
        stability, difficulty = mastery.lapse(3.0, 5.0)
        self.assertLess(stability, 3.0)
        self.assertGreater(difficulty, 5.0)
        self.assertGreaterEqual(mastery.lapse(0.6, 9.5)[0], mastery.STABILITY_FLOOR)
        self.assertLessEqual(mastery.lapse(0.6, 9.5)[1], 10.0)

    def test_stability_cap_and_difficulty_floor(self):
        self.assertLessEqual(mastery.succeed(700.0, 1.0, 0.1)[0], mastery.STABILITY_CAP)
        self.assertGreaterEqual(mastery.succeed(3.0, 1.0, 0.9)[1], 1.0)

    def test_durable_requires_stability_and_delayed_success(self):
        state = mastery._new_state()
        mastery._apply(state, {'at': 0.0, 'id': 'e0', 'lesson_id': 'x', 'kind': 'check', 'correct': True})
        moment = 0.0
        for index in range(40):
            moment += state['stability'] * DAY
            mastery._apply(state, {'at': moment, 'id': 'r' + str(index), 'lesson_id': 'x', 'kind': 'review', 'correct': True})
            if state['stability'] >= mastery.DURABLE_STABILITY:
                break
        view = mastery._skill_view({'id': 'x', 'title': 'X', 'stage': ''}, state, moment)
        self.assertEqual(view['state'], 'durable')
        self.assertGreaterEqual(view['reviews']['delayed_correct'], 1)

    def test_immediate_success_is_not_delayed_evidence(self):
        state = mastery._new_state()
        mastery._apply(state, {'at': 0.0, 'id': 'e0', 'lesson_id': 'x', 'kind': 'study', 'correct': True})
        mastery._apply(state, {'at': 60.0, 'id': 'r0', 'lesson_id': 'x', 'kind': 'review', 'correct': True})
        self.assertEqual(state['delayed_correct'], 0)
        self.assertEqual(state['attempts'], 1)


class MasteryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / 'library'
        write_memory_fixture(self.library)
        self.service = AcademyService(self.root / 'home', catalog_root=self.library)
        self.service.catalog.additional_root = self.root / 'additional'
        self.service.catalog.additional_root.mkdir()
        receipt = self.service.create_project('create', 'memory-fixture')
        self.pid = receipt['result']['project']['id']
        self.now = time.time()
        patcher = patch('time.time', side_effect=lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)

    def advance(self, days):
        self.now += days * DAY

    def answers(self, lesson_id, option='right'):
        return [{'check_id': 'q1', 'option_id': option}]

    def complete(self, lesson_id):
        project = self.service.project(self.pid)
        if project['active_lesson_id'] != lesson_id:
            receipt = self.service.start_lesson(self.pid, 'start-' + lesson_id, lesson_id,
                                                project['revision'], project['fingerprint'])
            self.assertEqual(receipt['status'], 'ready')
            project = receipt['result']['project']
        receipt = self.service.complete_study(self.pid, 'complete-' + lesson_id, lesson_id,
                                              project['fingerprint'], 'my own explanation', self.answers(lesson_id))
        self.assertEqual(receipt['status'], 'ready')

    def skill(self, lesson_id):
        return next(item for item in self.service.mastery.view(self.pid)['skills'] if item['lesson_id'] == lesson_id)

    def test_new_project_has_no_evidence_and_empty_queue(self):
        view = self.service.mastery.view(self.pid)
        self.assertEqual(view['due_count'], 0)
        self.assertTrue(all(item['state'] == 'new' for item in view['skills']))
        self.assertEqual(self.service.mastery.queue(self.pid), [])

    def test_study_completion_creates_learning_state(self):
        self.complete('fractions')
        skill = self.skill('fractions')
        self.assertEqual(skill['state'], 'learning')
        self.assertEqual(skill['stability_days'], mastery.INITIAL_STABILITY['study'])
        self.assertEqual(skill['evidence_count'], 1)
        self.assertAlmostEqual(skill['due_at'], self.now + 2 * DAY, places=3)
        self.assertEqual(self.service.mastery.queue(self.pid), [])

    def test_due_queue_hides_answers_feedback_and_hints(self):
        self.complete('fractions')
        self.advance(3)
        queue = self.service.mastery.queue(self.pid)
        self.assertEqual(len(queue), 1)
        item = queue[0]
        self.assertEqual(item['lesson_id'], 'fractions')
        self.assertLess(item['retrievability'], mastery.TARGET_RETENTION)
        self.assertAlmostEqual(item['overdue_days'], 1.0, places=2)
        self.assertEqual(set(item), {'lesson_id', 'title', 'goal', 'stage', 'retrievability', 'overdue_days', 'checks'})
        for check in item['checks']:
            self.assertEqual(set(check), {'id', 'prompt', 'options'})
            for option in check['options']:
                self.assertEqual(set(option), {'id', 'label'})
        self.assertFalse(validate(item, SCHEMAS['AcademyReviewItem']))

    def test_review_correct_grows_stability_and_records_evidence(self):
        self.complete('fractions')
        self.advance(3)
        receipt = self.service.mastery.review(self.pid, 'review-1', 'fractions', self.answers('fractions'))
        self.assertEqual(receipt['status'], 'ready')
        review = receipt['result']['review']
        self.assertTrue(review['correct'])
        self.assertEqual(review['results'], [{'check_id': 'q1', 'correct': True}])
        self.assertAlmostEqual(review['elapsed_days'], 3.0, places=3)
        skill = receipt['result']['skill']
        self.assertGreater(skill['stability_days'], mastery.INITIAL_STABILITY['study'])
        self.assertEqual(skill['reviews'], {'attempts': 1, 'correct': 1, 'delayed_correct': 1, 'delayed_attempts': 1})
        self.assertEqual(len(self.service.store.list('review')), 1)

    def test_delayed_attempts_counts_interval_reviews(self):
        """延迟重测尝试单独计数：即时重测不计入延迟分母，间隔≥1天计入。"""
        self.complete('fractions')
        self.advance(0.5)
        self.service.mastery.review(self.pid, 'r0', 'fractions', self.answers('fractions'))
        self.assertEqual(self.skill('fractions')['reviews']['delayed_attempts'], 0)
        self.advance(3)
        self.service.mastery.review(self.pid, 'r1', 'fractions', self.answers('fractions'))
        self.assertEqual(self.skill('fractions')['reviews'], {'attempts': 2, 'correct': 2, 'delayed_correct': 1, 'delayed_attempts': 1})

    def test_review_wrong_lapses_stability(self):
        self.complete('fractions')
        self.advance(3)
        receipt = self.service.mastery.review(self.pid, 'review-1', 'fractions', self.answers('fractions', 'wrong'))
        self.assertEqual(receipt['status'], 'ready')
        self.assertFalse(receipt['result']['review']['correct'])
        skill = receipt['result']['skill']
        self.assertLess(skill['stability_days'], mastery.INITIAL_STABILITY['study'])
        self.assertGreater(skill['difficulty'], mastery.INITIAL_DIFFICULTY)
        self.assertEqual(skill['reviews']['correct'], 0)

    def test_review_receipt_is_idempotent_and_conflicts_reject(self):
        self.complete('fractions')
        self.advance(3)
        first = self.service.mastery.review(self.pid, 'review-1', 'fractions', self.answers('fractions'))
        replay = self.service.mastery.review(self.pid, 'review-1', 'fractions', self.answers('fractions'))
        self.assertEqual(first['result']['review']['id'], replay['result']['review']['id'])
        self.assertEqual(len(self.service.store.list('review')), 1)
        with self.assertRaises(AcademyError) as caught:
            self.service.mastery.review(self.pid, 'review-1', 'fractions', self.answers('fractions', 'wrong'))
        self.assertEqual(caught.exception.code, 'request-conflict')

    def test_review_requires_prior_evidence(self):
        receipt = self.service.mastery.review(self.pid, 'review-1', 'decimals', self.answers('decimals'))
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['result']['error_code'], 'review-not-ready')

    def test_review_validates_answers_and_options(self):
        self.complete('fractions')
        for index, answers in enumerate(([], [{'check_id': 'q1', 'option_id': 'nope'}],
                        [{'check_id': 'unknown', 'option_id': 'right'}],
                        [{'check_id': 'q1', 'option_id': 'right'}, {'check_id': 'q1', 'option_id': 'right'}],
                        [{'check_id': 'q1', 'option_id': 'right', 'confidence': 'bogus'}],
                        [{'check_id': 'q1', 'option_id': 'right', 'extra': 1}])):
            receipt = self.service.mastery.review(self.pid, 'review-x' + str(index), 'fractions', answers)
            self.assertEqual(receipt['status'], 'failed')
            self.assertEqual(receipt['result']['error_code'], 'review-incomplete')
        receipt = self.service.mastery.review(self.pid, 'review-y', 'missing-lesson', self.answers('fractions'))
        self.assertEqual(receipt['result']['error_code'], 'lesson-not-found')

    def test_review_confidence_feeds_calibration(self):
        """延迟重测置信度计入校准六格；无置信度的答案不计入，不做隐式迁移。"""
        self.complete('fractions')
        self.complete('decimals')
        self.advance(3)
        receipt = self.service.mastery.review(self.pid, 'review-1', 'fractions',
                                              [{'check_id': 'q1', 'option_id': 'right', 'confidence': 'sure'}])
        self.assertEqual(receipt['status'], 'ready')
        self.assertEqual(receipt['result']['review']['answers'][0]['confidence'], 'sure')
        receipt = self.service.mastery.review(self.pid, 'review-2', 'decimals',
                                              [{'check_id': 'q1', 'option_id': 'wrong', 'confidence': 'guess'}])
        self.assertEqual(receipt['status'], 'ready')
        receipt = self.service.mastery.review(self.pid, 'review-3', 'decimals', self.answers('decimals'))
        self.assertEqual(receipt['status'], 'ready')
        calibration = self.service.mastery.view(self.pid)['calibration']
        self.assertEqual(calibration['sure_correct'], 1)
        self.assertEqual(calibration['guess_wrong'], 1)
        self.assertEqual(calibration['sure_wrong'], 0)
        self.assertEqual(calibration['guess_correct'], 0)
        self.assertEqual(calibration['unsure_correct'], 0)
        self.assertEqual(calibration['unsure_wrong'], 0)

    def test_complete_study_accepts_optional_confidence(self):
        """结业答案与公共协议一致：可选置信度被接受，非法值拒绝。"""
        project = self.service.project(self.pid)
        if project['active_lesson_id'] != 'fractions':
            receipt = self.service.start_lesson(self.pid, 'start-conf', 'fractions', project['revision'], project['fingerprint'])
            self.assertEqual(receipt['status'], 'ready')
            project = receipt['result']['project']
        rejected = self.service.complete_study(self.pid, 'complete-bad', 'fractions', project['fingerprint'],
                                               'my own explanation', [{'check_id': 'q1', 'option_id': 'right', 'confidence': 'bogus'}])
        self.assertEqual(rejected['status'], 'failed')
        self.assertEqual(rejected['result']['error_code'], 'invalid-request')
        receipt = self.service.complete_study(self.pid, 'complete-conf', 'fractions', project['fingerprint'],
                                              'my own explanation', [{'check_id': 'q1', 'option_id': 'right', 'confidence': 'unsure'}])
        self.assertEqual(receipt['status'], 'ready')

    def test_review_does_not_require_active_lesson(self):
        self.complete('fractions')
        self.complete('decimals')
        self.advance(3)
        receipt = self.service.mastery.review(self.pid, 'review-1', 'fractions', self.answers('fractions'))
        self.assertEqual(receipt['status'], 'ready')
        self.assertEqual(self.service.project(self.pid)['active_lesson_id'], 'decimals')

    def test_queue_interleaves_stages(self):
        for lesson_id in ('fractions', 'decimals', 'ratios'):
            self.complete(lesson_id)
        self.advance(3)
        queue = self.service.mastery.queue(self.pid)
        self.assertEqual(len(queue), 3)
        self.assertNotEqual(queue[0]['stage'], queue[1]['stage'])
        self.assertEqual({item['lesson_id'] for item in queue}, {'fractions', 'decimals', 'ratios'})

    def test_immediate_review_is_not_delayed_mastery(self):
        self.complete('fractions')
        receipt = self.service.mastery.review(self.pid, 'review-1', 'fractions', self.answers('fractions'))
        skill = receipt['result']['skill']
        self.assertEqual(skill['reviews']['delayed_correct'], 0)
        self.assertLess(skill['stability_days'], 2.05)
        self.assertEqual(skill['state'], 'learning')

    def test_check_evidence_projection_ignores_failed_and_changed_runs(self):
        base = {'project_id': self.pid, 'lesson_id': 'fractions', 'kind': 'check', 'created_at': self.now,
                'result': {'status': 'passed'}, 'input_changed': False}
        self.service.store.put('run', {**base, 'id': 'run_passed'})
        self.service.store.put('run', {**base, 'id': 'run_changed', 'input_changed': True})
        self.service.store.put('run', {**base, 'id': 'run_failed', 'result': {'status': 'failed'}})
        skill = self.skill('fractions')
        self.assertEqual(skill['evidence_count'], 1)
        self.assertEqual(skill['stability_days'], mastery.INITIAL_STABILITY['check'])


    def test_engagement_rates_constructive_and_active_evidence(self):
        empty = self.service.mastery.view(self.pid)['engagement']
        self.assertEqual(empty, {'constructive': 0, 'active': 0, 'total': 0, 'constructive_ratio': 0.0})
        self.complete('fractions')
        self.service.store.put('run', {'id': 'run_check', 'project_id': self.pid, 'lesson_id': 'fractions', 'kind': 'check',
                                       'input_changed': False, 'result': {'status': 'failed'}, 'created_at': self.now})
        self.advance(3)
        self.service.mastery.review(self.pid, 'review-1', 'fractions', self.answers('fractions'))
        engagement = self.service.mastery.view(self.pid)['engagement']
        self.assertEqual(engagement, {'constructive': 1, 'active': 2, 'total': 3, 'constructive_ratio': 0.3333})

    def test_corrupt_review_record_fails_explicitly(self):
        self.service.store.put('review', {'id': 'review_bad', 'project_id': self.pid,
                                          'lesson_id': 'fractions', 'created_at': self.now})
        with self.assertRaises(AcademyError) as caught:
            self.service.mastery.view(self.pid)
        self.assertEqual(caught.exception.code, 'store-invalid')

    def test_review_request_schema(self):
        body = {'request_id': 'r1', 'lesson_id': 'fractions', 'answers': [{'check_id': 'q1', 'option_id': 'right'}]}
        self.assertFalse(validate(body, SCHEMAS['AcademyReviewRequest']))
        self.assertTrue(validate({'request_id': 'r1', 'lesson_id': 'fractions'}, SCHEMAS['AcademyReviewRequest']))
        self.assertTrue(validate({**body, 'extra': 1}, SCHEMAS['AcademyReviewRequest']))
        self.assertFalse(validate({**body, 'answers': [{'check_id': 'q1', 'option_id': 'right', 'confidence': 'sure'}]}, SCHEMAS['AcademyReviewRequest']))
        # 置信度枚举在答案 schema 直接校验；请求内嵌 $ref 项由宿主校验兜底（与选项身份一致）。
        self.assertFalse(validate({'check_id': 'q1', 'option_id': 'right', 'confidence': 'sure'}, SCHEMAS['AcademyStudyAnswer']))
        self.assertTrue(validate({'check_id': 'q1', 'option_id': 'right', 'confidence': 'bogus'}, SCHEMAS['AcademyStudyAnswer']))


class MistakeJournalTests(unittest.TestCase):
    """错题本：当前答错与已修复盲点的持久投影、作答序列化与置信度/诊断携带。"""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / 'library'
        write_memory_fixture(self.library, with_steps=True)
        self.service = AcademyService(self.root / 'home', catalog_root=self.library)
        receipt = self.service.create_project('create', 'memory-fixture')
        self.pid = receipt['result']['project']['id']
        self.now = time.time()
        patcher = patch('time.time', side_effect=lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)
        project = self.service.project(self.pid)
        started = self.service.start_lesson(self.pid, 'start-fractions', 'fractions', project['revision'], project['fingerprint'])
        self.assertEqual(started['status'], 'ready')

    def test_wrong_sure_answer_becomes_blindspot_then_repaired(self):
        self.assertEqual(self.service.mastery.view(self.pid)['mistakes'], [])
        wrong = self.service.steps.answer(self.pid, 'ans-1', 'fractions', 's1', 9, confidence='sure', diagnosis='I counted unequal parts')
        self.assertEqual(wrong['status'], 'ready')
        mistakes = self.service.mastery.view(self.pid)['mistakes']
        self.assertEqual(len(mistakes), 1)
        entry = mistakes[0]
        self.assertEqual(entry['lesson_id'], 'fractions')
        self.assertEqual(entry['lesson_title'], 'fractions')
        self.assertEqual(entry['step_id'], 's1')
        self.assertEqual(entry['prompt'], 'How many equal parts?')
        self.assertEqual(entry['response'], '9')
        self.assertEqual(entry['confidence'], 'sure')
        self.assertEqual(entry['diagnosis'], 'I counted unequal parts')
        self.assertTrue(entry['blindspot'])
        self.assertFalse(entry['repaired'])
        fixed = self.service.steps.answer(self.pid, 'ans-2', 'fractions', 's1', 4, confidence='unsure')
        self.assertEqual(fixed['status'], 'ready')
        repaired = self.service.mastery.view(self.pid)['mistakes']
        self.assertEqual(len(repaired), 1)
        self.assertTrue(repaired[0]['repaired'])
        self.assertTrue(repaired[0]['blindspot'])
        self.assertEqual(repaired[0]['response'], '4')

    def test_low_confidence_wrong_is_journal_wrong_without_blindspot(self):
        self.service.steps.answer(self.pid, 'ans-1', 'fractions', 's1', 9, confidence='guess')
        entry = self.service.mastery.view(self.pid)['mistakes'][0]
        self.assertFalse(entry['blindspot'])
        self.assertFalse(entry['repaired'])
        self.assertEqual(entry['confidence'], 'guess')


class TransferRateTests(unittest.TestCase):
    """迁移通过率：已作答迁移步骤为分母，答对为分子；重试以最近作答投影。"""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / 'library'
        write_memory_fixture(self.library, with_steps=True)
        self.service = AcademyService(self.root / 'home', catalog_root=self.library)
        receipt = self.service.create_project('create', 'memory-fixture')
        self.pid = receipt['result']['project']['id']
        self.now = time.time()
        patcher = patch('time.time', side_effect=lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)
        project = self.service.project(self.pid)
        started = self.service.start_lesson(self.pid, 'start-fractions', 'fractions', project['revision'], project['fingerprint'])
        self.assertEqual(started['status'], 'ready')

    def test_transfer_pass_rate_counts_attempted_transfer_steps(self):
        self.assertEqual(self.service.mastery.view(self.pid)['transfer_attempts'], 0)
        self.service.steps.answer(self.pid, 't1', 'fractions', 's2', 'b', confidence='unsure')
        view = self.service.mastery.view(self.pid)
        self.assertEqual(view['transfer_attempts'], 1)
        self.assertEqual(view['transfers'], 0)
        self.service.steps.answer(self.pid, 't2', 'fractions', 's2', 'a', confidence='unsure')
        view = self.service.mastery.view(self.pid)
        self.assertEqual(view['transfer_attempts'], 1)
        self.assertEqual(view['transfers'], 1)


class MasteryApiTests(unittest.TestCase):
    def test_routes_authorization_and_validation_precede_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            class Config:
                home = directory
            class Harness:
                config = Config()
            class Context:
                harness = Harness()
            router = build_router(Context())
            missing = 'academy_' + '0' * 32
            handler, params = router.resolve('GET', '/api/v1/academy/projects/{project_id}/mastery')
            request = Request('GET', '/api/v1/academy/projects/' + missing + '/mastery', {}, {}, b'', client_ip='203.0.113.1')
            with self.assertRaises(ApiError) as caught:
                handler(request, **params)
            self.assertEqual(caught.exception.status, 403)
            request = Request('GET', '/api/v1/academy/projects/' + missing + '/mastery', {}, {}, b'', client_ip='127.0.0.1')
            with self.assertRaises(ApiError) as caught:
                handler(request, **params)
            self.assertEqual(caught.exception.status, 404)
            handler, params = router.resolve('POST', '/api/v1/academy/projects/{project_id}/reviews')
            request = Request('POST', '/api/v1/academy/projects/' + missing + '/reviews', {}, {},
                              b'{"request_id":"r1","lesson_id":"x"}', client_ip='127.0.0.1')
            with self.assertRaises(ApiError) as caught:
                handler(request, **params)
            self.assertEqual(caught.exception.code, 'academy/invalid-request')
            handler, params = router.resolve('GET', '/api/v1/academy/projects/{project_id}/reviews')
            request = Request('GET', '/api/v1/academy/projects/' + missing + '/reviews', {}, {}, b'', client_ip='127.0.0.1')
            with self.assertRaises(ApiError) as caught:
                handler(request, **params)
            self.assertEqual(caught.exception.status, 404)


if __name__ == '__main__':
    unittest.main()

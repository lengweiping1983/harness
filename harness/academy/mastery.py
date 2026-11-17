"""技能记忆引擎：从持久证据推导记忆状态，并调度延迟独立重测。

记忆曲线采用 FSRS 风格的幂律遗忘曲线，更新规则是其简化变体：
稳定性随成功提取增长，提取越困难（可提取性越低）增长越多，
失败使稳定性回落；常量集中声明，不声称与任何特定 FSRS 版本等价。
证据只来自持久记录：通过的独立代码检查、学习活动完成和重测回执。
练习中失败的检查不构成记忆降级，只有独立重测失败才降级。
"""
from __future__ import annotations

import math
import time
import uuid

from .errors import AcademyError

DAY = 86400.0
TARGET_RETENTION = 0.9
INITIAL_STABILITY = {'check': 3.0, 'study': 2.0, 'teach': 2.5, 'world': 2.0}
INITIAL_DIFFICULTY = 5.0
STABILITY_CAP = 730.0
STABILITY_FLOOR = 0.5
DURABLE_STABILITY = 21.0
DURABLE_DELAY_DAYS = 1.0
GROWTH_FACTOR = 1.0
GROWTH_DECAY = 0.2
RECALL_WEIGHT = 0.5
LAPSE_FACTOR = 0.4
DIFFICULTY_STEP = 0.25
QUEUE_LIMIT = 10
MAX_EVIDENCE_BYTES = 1024 * 1024


def retrievability(elapsed_days: float, stability: float) -> float:
    """幂律遗忘曲线；经过一个稳定性周期后可提取性恰为目标保持率。"""
    return (1.0 + max(0.0, elapsed_days) / (9.0 * stability)) ** -1


def succeed(stability: float, difficulty: float, recall: float) -> tuple[float, float]:
    """成功提取：低可提取性时的成功带来更大稳定性增长。"""
    growth = 1.0 + GROWTH_FACTOR * (11.0 - difficulty) * stability ** -GROWTH_DECAY * (math.exp(RECALL_WEIGHT * (1.0 - recall)) - 1.0)
    return min(STABILITY_CAP, stability * growth), max(1.0, difficulty - DIFFICULTY_STEP)


def lapse(stability: float, difficulty: float) -> tuple[float, float]:
    """独立重测失败：稳定性回落、难度上升，均保持在边界内。"""
    return max(STABILITY_FLOOR, stability * LAPSE_FACTOR), min(10.0, difficulty + 1.0)


def _new_state() -> dict:
    return {'stability': 0.0, 'difficulty': INITIAL_DIFFICULTY, 'last_at': None,
            'evidence_count': 0, 'attempts': 0, 'correct': 0, 'delayed_correct': 0}


def _apply(state: dict, event: dict) -> dict:
    """按时间顺序把一条证据折叠进技能状态；纯函数，可确定性重放。"""
    elapsed = None if state['last_at'] is None else max(0.0, (event['at'] - state['last_at']) / DAY)
    if state['evidence_count'] == 0:
        if event['kind'] not in INITIAL_STABILITY:
            raise AcademyError('store-invalid')
        state['stability'] = INITIAL_STABILITY[event['kind']]
    elif event['correct']:
        recall = retrievability(elapsed, state['stability'])
        state['stability'], state['difficulty'] = succeed(state['stability'], state['difficulty'], recall)
    else:
        state['stability'], state['difficulty'] = lapse(state['stability'], state['difficulty'])
    if event['kind'] == 'review':
        state['attempts'] += 1
        if event['correct']:
            state['correct'] += 1
            if elapsed is not None and elapsed >= DURABLE_DELAY_DAYS:
                state['delayed_correct'] += 1
    state['last_at'] = event['at']
    state['evidence_count'] += 1
    return state


def _events(store, project_id: str) -> list[dict]:
    """收集本项目的全部记忆证据；损坏记录保留原件并明确失败。"""
    events = []
    for run in store.list('run', max_bytes=MAX_EVIDENCE_BYTES):
        if run.get('project_id') != project_id:
            continue
        if (run.get('kind') == 'check' and isinstance(run.get('result'), dict)
                and run['result'].get('status') == 'passed' and run.get('input_changed') is False
                and isinstance(run.get('created_at'), (int, float)) and isinstance(run.get('lesson_id'), str)):
            events.append({'at': run['created_at'], 'id': run['id'], 'lesson_id': run['lesson_id'], 'kind': 'check', 'correct': True})
    for value in store.list('world', max_bytes=MAX_EVIDENCE_BYTES):
        if value.get('project_id') != project_id:
            continue
        if (value.get('status') == 'completed' and isinstance(value.get('created_at'), (int, float))
                and isinstance(value.get('lesson_id'), str)):
            events.append({'at': value['created_at'], 'id': value['id'], 'lesson_id': value['lesson_id'], 'kind': 'world', 'correct': True})
    for value in store.list('teach-quiz', max_bytes=MAX_EVIDENCE_BYTES):
        if value.get('project_id') != project_id:
            continue
        if (value.get('status') == 'completed' and value.get('correct') is True
                and isinstance(value.get('created_at'), (int, float)) and isinstance(value.get('lesson_id'), str)):
            events.append({'at': value['created_at'], 'id': value['id'], 'lesson_id': value['lesson_id'], 'kind': 'teach', 'correct': True})
    for kind in ('study-completion', 'review'):
        for value in store.list(kind, max_bytes=MAX_EVIDENCE_BYTES):
            if value.get('project_id') != project_id:
                continue
            if not isinstance(value.get('created_at'), (int, float)) or not isinstance(value.get('lesson_id'), str):
                raise AcademyError('store-invalid')
            if kind == 'review' and not isinstance(value.get('correct'), bool):
                raise AcademyError('store-invalid')
            events.append({'at': value['created_at'], 'id': value['id'], 'lesson_id': value['lesson_id'],
                           'kind': 'study' if kind == 'study-completion' else 'review',
                           'correct': True if kind == 'study-completion' else value['correct']})
    events.sort(key=lambda item: (item['at'], item['id']))
    return events


def fold(store, project_id: str) -> dict[str, dict]:
    """从证据账本重放每个技能的记忆状态；不依赖任何缓存或迁移。"""
    states: dict[str, dict] = {}
    for event in _events(store, project_id):
        _apply(states.setdefault(event['lesson_id'], _new_state()), event)
    return states


def _skill_view(lesson: dict, state: dict | None, now: float) -> dict:
    state = state or _new_state()
    if state['evidence_count'] == 0:
        label, recall, due_at = 'new', 1.0, None
    else:
        elapsed = max(0.0, (now - state['last_at']) / DAY)
        recall = retrievability(elapsed, state['stability'])
        due_at = state['last_at'] + state['stability'] * DAY
        durable = state['stability'] >= DURABLE_STABILITY and state['delayed_correct'] >= 1
        label = 'durable' if durable else 'learning'
    return {'lesson_id': lesson['id'], 'title': lesson['title'], 'stage': lesson.get('stage', ''),
            'state': label, 'stability_days': round(state['stability'], 2), 'difficulty': round(state['difficulty'], 2),
            'retrievability': round(recall, 4), 'due_at': due_at, 'last_evidence_at': state['last_at'],
            'evidence_count': state['evidence_count'],
            'reviews': {'attempts': state['attempts'], 'correct': state['correct'], 'delayed_correct': state['delayed_correct']}}


class AcademyMastery:
    """记忆视图、到期重测队列与独立重测评分；不请求模型、不执行代码。"""

    def __init__(self, service):
        self.service = service

    def _lessons(self, project_id: str) -> tuple[dict, list[dict]]:
        record = self.service._record(project_id)
        self.service._require_course(record)
        return record, self.service.course(record['course_id'])['lessons']

    def view(self, project_id: str) -> dict:
        record, lessons = self._lessons(project_id)
        states = fold(self.service.store, project_id)
        now = time.time()
        skills = [_skill_view(lesson, states.get(lesson['id']), now) for lesson in lessons]
        totals = {key: sum(skill['reviews'][key] for skill in skills) for key in ('attempts', 'correct', 'delayed_correct')}
        return {'skills': skills, 'due_count': sum(1 for skill in skills if skill['state'] != 'new' and skill['retrievability'] < TARGET_RETENTION),
                'reviews': totals}


    def profile(self, project_id: str, lesson_id: str) -> dict:
        """带练冻结的掌握度画像：当前技能、先修状态、到期重测与近期独立失败。"""
        record, lessons = self._lessons(project_id)
        states = fold(self.service.store, project_id)
        now = time.time()
        lesson = next(item for item in lessons if item['id'] == lesson_id)
        skill = _skill_view(lesson, states.get(lesson_id), now)
        prerequisites = []
        for prereq_id in lesson.get('prerequisites', []):
            prereq = next((item for item in lessons if item['id'] == prereq_id), None)
            if prereq is None:
                continue
            view = _skill_view(prereq, states.get(prereq_id), now)
            prerequisites.append({'lesson_id': prereq_id, 'state': view['state'], 'retrievability': view['retrievability']})
        review_due = []
        for item in lessons:
            state = states.get(item['id'])
            if state and state['evidence_count'] > 0:
                elapsed = max(0.0, (now - state['last_at']) / DAY)
                if retrievability(elapsed, state['stability']) < TARGET_RETENTION:
                    review_due.append(item['id'])
        recent_failures = [{'lesson_id': value['lesson_id'], 'at': value['created_at']}
                           for value in self.service.store.list('review', max_bytes=MAX_EVIDENCE_BYTES)
                           if value.get('project_id') == project_id and value.get('correct') is False][:3]
        return {'skill': skill, 'prerequisites': prerequisites, 'review_due': review_due[:5], 'recent_failures': recent_failures}

    def queue(self, project_id: str) -> list[dict]:
        """到期技能按阶段交错排列；公开题目不含答案、反馈与提示。"""
        record, lessons = self._lessons(project_id)
        states = fold(self.service.store, project_id)
        now = time.time()
        due = []
        for lesson in lessons:
            state = states.get(lesson['id'])
            if not state or state['evidence_count'] == 0 or not lesson.get('knowledge_checks'):
                continue
            elapsed = max(0.0, (now - state['last_at']) / DAY)
            recall = retrievability(elapsed, state['stability'])
            if recall >= TARGET_RETENTION:
                continue
            due.append({'lesson': lesson, 'recall': recall, 'overdue': elapsed - state['stability']})
        due.sort(key=lambda item: item['recall'])
        groups: dict[str, list] = {}
        for item in due:
            groups.setdefault(item['lesson'].get('stage', ''), []).append(item)
        queue = []
        while len(queue) < QUEUE_LIMIT and any(groups.values()):
            for stage in sorted(groups):
                if groups[stage] and len(queue) < QUEUE_LIMIT:
                    item = groups[stage].pop(0)
                    lesson = item['lesson']
                    queue.append({'lesson_id': lesson['id'], 'title': lesson['title'], 'goal': lesson['goal'],
                                  'stage': lesson.get('stage', ''), 'retrievability': round(item['recall'], 4),
                                  'overdue_days': round(max(0.0, item['overdue']), 2),
                                  'checks': [{'id': check['id'], 'prompt': check['prompt'],
                                              'options': [{'id': option['id'], 'label': option['label']} for option in check['options']]}
                                             for check in lesson['knowledge_checks']]})
        return queue

    def review(self, project_id: str, request_id: str, lesson_id: str, answers: list) -> dict:
        """宿主独立评分一次延迟重测；先记意图，回执幂等，不请求模型。"""
        def accept():
            record, lessons = self._lessons(project_id)
            lesson = next((item for item in lessons if item['id'] == lesson_id), None)
            if lesson is None:
                raise AcademyError('lesson-not-found')
            checks = lesson.get('knowledge_checks', [])
            if (not checks or not isinstance(answers, list)
                    or any(not isinstance(item, dict) or set(item) != {'check_id', 'option_id'}
                           or not isinstance(item['check_id'], str) or not isinstance(item['option_id'], str) for item in answers)):
                raise AcademyError('review-incomplete')
            actual = {answer['check_id']: answer['option_id'] for answer in answers}
            if len(actual) != len(answers) or set(actual) != {check['id'] for check in checks}:
                raise AcademyError('review-incomplete')
            options = {check['id']: {option['id'] for option in check['options']} for check in checks}
            if any(option not in options[check] for check, option in actual.items()):
                raise AcademyError('review-incomplete')
            states = fold(self.service.store, project_id)
            state = states.get(lesson_id)
            if not state or state['evidence_count'] == 0:
                raise AcademyError('review-not-ready')
            now = time.time()
            elapsed = max(0.0, (now - state['last_at']) / DAY)
            recall = retrievability(elapsed, state['stability'])
            results = [{'check_id': check['id'], 'correct': actual[check['id']] == check['answer_id']} for check in checks]
            correct = all(item['correct'] for item in results)
            value = {'id': 'review_' + uuid.uuid4().hex, 'project_id': project_id, 'lesson_id': lesson_id,
                     'request_id': request_id, 'answers': answers, 'results': results, 'correct': correct,
                     'elapsed_days': round(elapsed, 4), 'retrievability': round(recall, 4), 'created_at': now}
            self.service._mark_started()
            self.service.store.put('review', value)
            updated = _apply(state, {'at': now, 'id': value['id'], 'lesson_id': lesson_id, 'kind': 'review', 'correct': correct})
            return {'review': value, 'skill': _skill_view(lesson, updated, now)}
        return self.service._change(request_id, 'review', project_id, {'lesson_id': lesson_id, 'answers': answers}, accept)

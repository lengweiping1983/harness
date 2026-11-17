"""微世界内核与预测—操作—观察—解释会话；真值由宿主内核计算，不来自模型。"""
from __future__ import annotations

import time
import uuid

from .errors import AcademyError

MAX_OBSERVATIONS = 12
MAX_NOTE_CHARS = 4000
MAX_SESSION_BYTES = 512 * 1024
KINDS = ('fraction-bar', 'group-array', 'balance-scale')


def _int(params, key, low, high):
    value = params.get(key)
    if type(value) is not int or not low <= value <= high:
        raise AcademyError('world-invalid')
    return value


def fraction_state(params):
    """相等份数与涂色份数决定分数；份数必须相等才有分数意义。"""
    parts = _int(params, 'parts', 2, 12)
    shaded = _int(params, 'shaded', 0, parts)
    return {'parts': parts, 'shaded': shaded, 'equal': True, 'label': str(shaded) + '/' + str(parts), 'value': shaded / parts}


def groups_state(params):
    """几个几：同数连加与乘法表示同一个总数。"""
    groups = _int(params, 'groups', 1, 9)
    per_group = _int(params, 'per_group', 1, 9)
    return {'groups': groups, 'per_group': per_group, 'total': groups * per_group,
            'repeated': ' + '.join([str(per_group)] * groups), 'product': str(groups) + ' × ' + str(per_group)}


def balance_state(params):
    """天平平衡当且仅当两边相等；差值可正可负。"""
    left = _int(params, 'left', 0, 100)
    right = _int(params, 'right', 0, 100)
    return {'left': left, 'right': right, 'balanced': left == right, 'difference': left - right}


KERNELS = {'fraction-bar': fraction_state, 'group-array': groups_state, 'balance-scale': balance_state}
INITIAL = {'fraction-bar': {'parts': 4, 'shaded': 1}, 'group-array': {'groups': 2, 'per_group': 3}, 'balance-scale': {'left': 5, 'right': 3}}
TARGETS = {'fraction-bar': {'parts': 6, 'shaded': 2}, 'group-array': {'groups': 4, 'per_group': 3}, 'balance-scale': {'left': 7, 'right': 0}}


def challenge(kind):
    """挑战与选项由宿主从内核真值构造；错误选项对应常见误解。"""
    target = dict(TARGETS[kind])
    if kind == 'fraction-bar':
        state = fraction_state(target)
        prompt = 'Set the bar to ' + str(target['parts']) + ' equal parts and shade ' + str(target['shaded']) + '. What fraction is shaded?'
        labels = [state['label'], str(target['parts']) + '/' + str(target['shaded']), str(target['shaded']) + '/' + str(target['parts'] + target['shaded'])]
    elif kind == 'group-array':
        state = groups_state(target)
        prompt = 'Make ' + str(target['groups']) + ' groups of ' + str(target['per_group']) + '. How many objects in total?'
        labels = [str(state['total']), str(target['groups'] + target['per_group']), str(target['groups'])]
    else:
        target['right'] = target['left']
        state = balance_state(target)
        prompt = 'The left pan holds ' + str(target['left']) + ' g. What must the right pan hold to balance?'
        labels = [str(state['right']), str(target['left'] * 2), str(max(0, target['left'] - 1))]
    options = [{'id': 'option-' + str(index), 'label': label} for index, label in enumerate(labels)]
    return {'prompt': prompt, 'options': options, 'answer_id': 'option-0', 'target': target}


class AcademyWorlds:
    """每课一次微世界会话；预测先冻结，操作真值由内核计算，完成需要解释。"""

    def __init__(self, service):
        self.service = service
        self.store = service.store

    def _sessions(self, project_id):
        return [value for value in self.store.list('world', max_bytes=MAX_SESSION_BYTES) if value.get('project_id') == project_id]

    def view(self, project_id, lesson_id):
        self.service._record(project_id)
        with self.store.lock:
            session = next((value for value in self._sessions(project_id) if value.get('lesson_id') == lesson_id), None)
        if session is None:
            return {}
        return {'world': self._public(session)}

    def _public(self, session):
        public = {key: value for key, value in session.items() if key != 'challenge_answer'}
        public['challenge'] = {key: value for key, value in session['challenge'].items() if key != 'answer_id'}
        return public

    def start(self, project_id, request_id, lesson_id):
        def create():
            record = self.service._record(project_id)
            self.service._require_course(record)
            if record['active_lesson_id'] != lesson_id:
                raise AcademyError('lesson-not-active')
            lesson = next(item for item in self.service.course(record['course_id'])['lessons'] if item['id'] == lesson_id)
            task = lesson.get('world')
            if not task or task['kind'] not in KERNELS:
                raise AcademyError('world-unavailable')
            progress = next(row for row in record['progress'] if row['lesson_id'] == lesson_id)
            if progress['state'] != 'in_progress':
                raise AcademyError('world-done')
            if any(value.get('lesson_id') == lesson_id for value in self._sessions(project_id)):
                raise AcademyError('world-exists')
            kind = task['kind']
            value = {'id': 'world_' + uuid.uuid4().hex, 'project_id': project_id, 'lesson_id': lesson_id,
                     'request_id': request_id, 'kind': kind, 'params': dict(INITIAL[kind]),
                     'challenge': challenge(kind), 'prediction': None, 'status': 'predicting',
                     'observations': [], 'note': '', 'created_at': time.time(), 'completed_at': None}
            self.service._mark_started()
            self.store.put('world', value)
            return {'world': self._public(value)}
        return self.service._change(request_id, 'world-start', project_id, {'lesson_id': lesson_id}, create)

    def _session(self, project_id):
        record = self.service._record(project_id)
        with self.store.lock:
            session = next((value for value in self._sessions(project_id) if value.get('lesson_id') == record['active_lesson_id']), None)
        if session is None:
            raise AcademyError('world-not-found')
        return session

    def predict(self, project_id, request_id, option_id):
        if not isinstance(option_id, str) or not option_id:
            raise AcademyError('invalid-request')
        def write():
            session = self._session(project_id)
            if session['status'] != 'predicting':
                raise AcademyError('world-not-ready')
            if option_id not in {option['id'] for option in session['challenge']['options']}:
                raise AcademyError('world-invalid')
            session.update(prediction={'option_id': option_id, 'correct': option_id == session['challenge']['answer_id']}, status='observing')
            self.service._mark_started()
            self.store.put('world', session)
            return {'world': self._public(session)}
        return self.service._change(request_id, 'world-predict', project_id, {'option_id': option_id}, write)

    def observe(self, project_id, request_id, params):
        if not isinstance(params, dict):
            raise AcademyError('invalid-request')
        def write():
            session = self._session(project_id)
            if session['status'] != 'observing':
                raise AcademyError('world-not-ready')
            if len(session['observations']) >= MAX_OBSERVATIONS:
                raise AcademyError('world-limit')
            state = KERNELS[session['kind']](params)
            session['params'] = dict(params)
            session['observations'].append({'params': dict(params), 'state': state, 'at': time.time()})
            self.service._mark_started()
            self.store.put('world', session)
            return {'world': self._public(session)}
        return self.service._change(request_id, 'world-observe', project_id, {'params': params}, write)

    def complete(self, project_id, request_id, note):
        if not isinstance(note, str) or not note.strip() or len(note) > MAX_NOTE_CHARS:
            raise AcademyError('invalid-request')
        def write():
            session = self._session(project_id)
            if session['status'] == 'completed':
                return {'world': self._public(session)}
            if session['prediction'] is None or not session['observations']:
                raise AcademyError('world-not-ready')
            session.update(note=note, status='completed', completed_at=time.time())
            self.service._mark_started()
            self.store.put('world', session)
            return {'world': self._public(session)}
        return self.service._change(request_id, 'world-complete', project_id, {'note': note}, write)

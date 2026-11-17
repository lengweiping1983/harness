"""步骤式课程引擎：逐步即时反馈（step-based），宿主评分，例题→补全→独立。

反馈粒度对齐 VanLehn 的 step-based 辅导：每个微步骤独立评分与反馈，
而不是只判最终答案。步骤真值来自课程冻结数据，不来自模型。
"""
from __future__ import annotations

import time
import uuid

from .errors import AcademyError

STEP_KINDS = ('explain', 'numeric', 'choice', 'ordering', 'matching')
MAX_RESPONSE_CHARS = 2000
MAX_STEPS = 16
MAX_RECORD_BYTES = 256 * 1024


def public_step(step):
    """公开步骤描述剥离答案；排序项以反序呈现，避免显示即答案。"""
    public = {'id': step['id'], 'kind': step['kind'], 'prompt': step['prompt']}
    if step['kind'] == 'explain':
        public['content'] = step['content']
    elif step['kind'] == 'choice':
        public['options'] = [{'id': option['id'], 'label': option['label']} for option in step['options']]
    elif step['kind'] == 'ordering':
        public['items'] = [{'id': item['id'], 'label': item['label']} for item in reversed(step['items'])]
    elif step['kind'] == 'numeric':
        public['tolerance'] = step['tolerance']
    elif step['kind'] == 'matching':
        public['left'] = [{'id': item['id'], 'label': item['label']} for item in step['left']]
        public['right'] = [{'id': item['id'], 'label': item['label']} for item in reversed(step['right'])]
    if step.get('on_wrong'):
        public['on_wrong'] = step['on_wrong']
    if step.get('transfer'):
        public['transfer'] = True
    return public


def grade(step, response):
    """按步骤类型宿主评分；返回 (correct, normalized_response)。"""
    kind = step['kind']
    if kind == 'explain':
        if not isinstance(response, str) or not response.strip() or len(response) > MAX_RESPONSE_CHARS:
            raise AcademyError('step-invalid')
        return True, response.strip()
    if kind == 'numeric':
        if type(response) not in (int, float):
            raise AcademyError('step-invalid')
        return abs(response - step['answer']) <= step['tolerance'], response
    if kind == 'choice':
        if not isinstance(response, str) or response not in {option['id'] for option in step['options']}:
            raise AcademyError('step-invalid')
        return response == step['answer_id'], response
    if kind == 'ordering':
        if not isinstance(response, list) or any(not isinstance(item, str) for item in response):
            raise AcademyError('step-invalid')
        ids = [item['id'] for item in step['items']]
        if sorted(response) != sorted(ids):
            raise AcademyError('step-invalid')
        return response == step['answer'], response
    if kind == 'matching':
        if not isinstance(response, dict):
            raise AcademyError('step-invalid')
        left_ids = [item['id'] for item in step['left']]
        right_ids = {item['id'] for item in step['right']}
        if set(response) != set(left_ids) or any(not isinstance(value, str) or value not in right_ids for value in response.values()):
            raise AcademyError('step-invalid')
        return all(response[key] == step['answer'][key] for key in left_ids), response
    raise AcademyError('step-invalid')


class AcademySteps:
    """每课一条步骤进度记录；逐步作答，全部正确且解释完成记为独立步骤证据。"""

    def __init__(self, service):
        self.service = service
        self.store = service.store

    def _lesson(self, project_id, lesson_id):
        record = self.service._record(project_id)
        self.service._require_course(record)
        if record['active_lesson_id'] != lesson_id:
            raise AcademyError('lesson-not-active')
        lesson = next(item for item in self.service.course(record['course_id'])['lessons'] if item['id'] == lesson_id)
        steps = lesson.get('steps')
        if not steps:
            raise AcademyError('steps-unavailable')
        return record, lesson, steps

    def _progress(self, project_id, lesson_id):
        return next((value for value in self.store.list('step-progress', max_bytes=MAX_RECORD_BYTES)
                     if value.get('project_id') == project_id and value.get('lesson_id') == lesson_id), None)

    def view(self, project_id, lesson_id):
        record, lesson, steps = self._lesson(project_id, lesson_id)
        progress = self._progress(project_id, lesson_id)
        done = {item['step_id']: item for item in (progress['responses'] if progress else [])}
        return {'steps': [public_step(step) for step in steps],
                'responses': [dict({'step_id': step['id'], 'correct': done[step['id']]['correct'],
                                    'response': done[step['id']]['response'],
                                    'attempts': done[step['id']]['attempts'], 'at': done[step['id']]['at']},
                                   **({'confidence': done[step['id']]['confidence']} if 'confidence' in done[step['id']] else {}),
                                   **({'blindspot': True} if done[step['id']].get('blindspot') else {}),
                                   **({'repaired': True} if done[step['id']].get('repaired') else {}),
                                   **({'diagnosis': done[step['id']]['diagnosis']} if done[step['id']].get('diagnosis') else {}))
                              for step in steps if step['id'] in done],
                'completed': bool(progress and progress['status'] == 'completed')}

    def answer(self, project_id, request_id, lesson_id, step_id, response, confidence=None, diagnosis=None):
        def write():
            record, lesson, steps = self._lesson(project_id, lesson_id)
            step = next((item for item in steps if item['id'] == step_id), None)
            if step is None:
                raise AcademyError('step-not-found')
            correct, normalized = grade(step, response)
            progress = self._progress(project_id, lesson_id)
            if progress is None:
                progress = {'id': 'stepprogress_' + uuid.uuid4().hex, 'project_id': project_id, 'lesson_id': lesson_id,
                            'status': 'active', 'responses': [], 'created_at': time.time(), 'completed_at': None}
            if progress['status'] == 'completed':
                raise AcademyError('steps-completed')
            previous = next((item for item in progress['responses'] if item['step_id'] == step_id), None)
            entry = {'step_id': step_id, 'correct': correct, 'response': normalized,
                     'attempts': (previous['attempts'] + 1) if previous else 1, 'at': time.time()}
            if confidence is not None:
                entry['confidence'] = confidence
            if diagnosis is not None and diagnosis.strip():
                entry['diagnosis'] = diagnosis.strip()
            elif previous and previous.get('diagnosis'):
                entry['diagnosis'] = previous['diagnosis']
            # 盲点闭环：高确信错误记为盲点；之后答对记为修复，保留历史供界面与校准使用。
            if previous and previous.get('blindspot'):
                entry['blindspot'] = True
            if not correct and confidence == 'sure':
                entry['blindspot'] = True
            if correct and previous and previous.get('blindspot'):
                entry['repaired'] = True
            if previous:
                progress['responses'][progress['responses'].index(previous)] = entry
            else:
                progress['responses'].append(entry)
            answered = {item['step_id']: item for item in progress['responses']}
            support_ids = {step['on_wrong'] for step in steps if step.get('on_wrong')}
            main = [step for step in steps if step['id'] not in support_ids]
            complete = all(step['id'] in answered and answered[step['id']]['correct'] for step in main)
            if complete:
                # 迁移挑战答对才标记：新情境的成功才是更耐久的理解证据。
                progress.update(status='completed', completed_at=time.time(),
                                transfer=any(step.get('transfer') and answered[step['id']]['correct'] for step in steps))
            self.service._mark_started()
            self.store.put('step-progress', progress)
            feedback = step['feedback'] if correct else (step['hints'][0] if step.get('hints') else None)
            scaffold = None if correct else step.get('scaffold')
            number_line = None
            if step['kind'] == 'numeric' and step.get('number_line') and type(normalized) in (int, float):
                number_line = {'min': step['number_line']['min'], 'max': step['number_line']['max'], 'value': normalized}
            # 元认知校准：高确信错误是最该修复的缺口；低确信答对说明仍靠猜测，需要巩固。
            calibration = None
            if correct and previous and previous.get('blindspot'):
                calibration = 'repaired'
            elif confidence is not None:
                if correct:
                    calibration = 'calibrated' if confidence == 'sure' else 'lucky'
                else:
                    calibration = 'blindspot' if confidence == 'sure' else 'expected'
            # 空值省略而非 null：客户端严格校验不接受未声明 null 的可选字段。
            result = {'correct': correct, 'completed': complete, 'step': public_step(step), 'response': entry}
            if feedback is not None:
                result['feedback'] = feedback
            if scaffold is not None:
                result['scaffold'] = scaffold
            if number_line is not None:
                result['number_line'] = number_line
            if calibration is not None:
                result['calibration'] = calibration
            # 回执契约：步骤结果嵌套在 result.step，公开步骤在其 step 字段。
            return {'step': result}
        frozen = {'lesson_id': lesson_id, 'step_id': step_id, 'response': response}
        if confidence is not None:
            frozen['confidence'] = confidence
        if diagnosis is not None and diagnosis.strip():
            frozen['diagnosis'] = diagnosis.strip()
        return self.service._change(request_id, 'step-answer', project_id, frozen, write)

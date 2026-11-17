"""挣扎优先：先尝试后讲解；最小挣扎时间内不提前揭示，揭示后冻结尝试。"""
from __future__ import annotations

import time
import uuid

from .errors import AcademyError

MAX_ATTEMPT_CHARS = 4000
MAX_SESSION_BYTES = 256 * 1024


class AcademyStruggle:
    """每课一次挣扎会话；尝试原文冻结，揭示只由显式操作或窗口到期开放。"""

    def __init__(self, service):
        self.service = service
        self.store = service.store

    def _sessions(self, project_id):
        return [value for value in self.store.list('struggle', max_bytes=MAX_SESSION_BYTES)
                if value.get('project_id') == project_id]

    def current(self, project_id):
        self.service._record(project_id)
        with self.store.lock:
            sessions = self._sessions(project_id)
        return sessions[0] if sessions else None

    def view(self, project_id, lesson_id):
        self.service._record(project_id)
        with self.store.lock:
            session = next((value for value in self._sessions(project_id) if value.get('lesson_id') == lesson_id), None)
        if session is None:
            return {'remaining_seconds': 0}
        remaining = max(0, session['window_minutes'] * 60 - int(time.time() - session['started_at']))
        return {'struggle': session, 'remaining_seconds': remaining if session['status'] != 'revealed' else 0}

    def start(self, project_id, request_id, lesson_id):
        def create():
            record = self.service._record(project_id)
            self.service._require_course(record)
            if record['active_lesson_id'] != lesson_id:
                raise AcademyError('lesson-not-active')
            lesson = next(item for item in self.service.course(record['course_id'])['lessons'] if item['id'] == lesson_id)
            task = lesson.get('struggle')
            if not task:
                raise AcademyError('struggle-unavailable')
            progress = next(row for row in record['progress'] if row['lesson_id'] == lesson_id)
            if progress['state'] != 'in_progress':
                raise AcademyError('struggle-done')
            if any(value.get('lesson_id') == lesson_id for value in self._sessions(project_id)):
                raise AcademyError('struggle-exists')
            value = {'id': 'struggle_' + uuid.uuid4().hex, 'project_id': project_id, 'lesson_id': lesson_id,
                     'request_id': request_id, 'challenge': task['challenge'], 'window_minutes': task['window_minutes'],
                     'status': 'struggling', 'attempt': '', 'started_at': time.time(), 'attempt_at': None, 'revealed_at': None}
            self.service._mark_started()
            self.store.put('struggle', value)
            return {'struggle': value}
        return self.service._change(request_id, 'struggle-start', project_id, {'lesson_id': lesson_id}, create)

    def attempt(self, project_id, request_id, text):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_ATTEMPT_CHARS:
            raise AcademyError('invalid-request')
        def write():
            record = self.service._record(project_id)
            session = self.current(project_id)
            if session is None:
                raise AcademyError('struggle-not-found')
            if session['lesson_id'] != record['active_lesson_id']:
                raise AcademyError('lesson-not-active')
            if session['status'] == 'revealed':
                raise AcademyError('struggle-revealed')
            session.update(attempt=text, attempt_at=time.time(), status='attempted')
            self.service._mark_started()
            self.store.put('struggle', session)
            return {'struggle': session}
        return self.service._change(request_id, 'struggle-attempt', project_id, {'text': text}, write)

    def reveal(self, project_id, request_id):
        def end():
            record = self.service._record(project_id)
            session = self.current(project_id)
            if session is None:
                raise AcademyError('struggle-not-found')
            if session['lesson_id'] != record['active_lesson_id']:
                raise AcademyError('lesson-not-active')
            if session['status'] == 'revealed':
                return {'struggle': session}
            elapsed = time.time() - session['started_at']
            if session['status'] != 'attempted' and elapsed < session['window_minutes'] * 60:
                raise AcademyError('struggle-too-early')
            session.update(status='revealed', revealed_at=time.time())
            self.service._mark_started()
            self.store.put('struggle', session)
            return {'struggle': session}
        return self.service._change(request_id, 'struggle-reveal', project_id, {}, end)

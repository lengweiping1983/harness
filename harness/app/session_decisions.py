"""会话审批使用现有问答内核；决定日志是回执，不是工具执行成功证据。"""
from __future__ import annotations

import hashlib
import json
import re

from ..core.errors import HarnessError
from .collaboration_errors import collaboration_error_message
from ..interaction.questions import QuestionUnavailableError

_KIND = 'collaboration/decision'


def fail(code):
    raise HarnessError(collaboration_error_message(code), code=code)


class SessionDecisions:
    """仅由已验证的接入身份调用；权限与持久记录复用原会话。"""

    def __init__(self, access, questions):
        self.access, self.questions = access, questions

    def pending(self, session_id, actor):
        self.access.require(session_id, actor, 'read')
        return [row for row in self.questions.pending(session_id) if row['session_id'] == session_id]

    def receipt(self, session_id, actor, request_id):
        state = self.access.require(session_id, actor, 'control')
        if not isinstance(request_id, str) or not re.fullmatch('[A-Za-z0-9_-]{16,128}', request_id):
            fail('decision/invalid-request-id')
        for event in self.access.sessions.get(session_id).events_of_type('note'):
            row = event.data
            if (row.get('kind') == _KIND and row.get('session_id') == session_id
                    and row.get('actor') == actor and row.get('request_id') == request_id
                    and row.get('member_epoch') == state['member_epochs'].get(actor)):
                return {key: row[key] for key in ('question_id', 'request_id', 'actor', 'revision', 'status', 'request_hash')}
        return None

    async def answer(self, session_id, question_id, *, actor, request_id, expected_revision, answers):
        """并发只提交一个当前版本；结果未知由原请求回执确认，不自动重答。"""
        initial = self.access.require(session_id, actor, 'control')
        member_epoch = initial['member_epochs'][actor]
        previous = self.receipt(session_id, actor, request_id)
        if not isinstance(expected_revision, str) or not re.fullmatch('[0-9a-f]{64}', expected_revision):
            fail('decision/invalid-revision')
        encoded = json.dumps([question_id, expected_revision, answers], sort_keys=True, ensure_ascii=False)
        request_hash = hashlib.sha256(encoded.encode()).hexdigest()
        if previous:
            if previous['request_hash'] != request_hash:
                fail('decision/request-conflict')
            return {key: value for key, value in previous.items() if key != 'request_hash'}
        receipt = None

        async def commit(record, normalized):
            nonlocal receipt
            async with self.access.mutation_lock:
                state = self.access.require(session_id, actor, 'control')
                if state['member_epochs'].get(actor) != member_epoch:
                    fail('access/denied')
                if record.revision != expected_revision:
                    fail('decision/stale-revision')
                # 同一请求号即使并发回答不同问题，也不能被复用。
                if self.receipt(session_id, actor, request_id):
                    fail('decision/request-conflict')
                receipt = {'kind': _KIND, 'session_id': session_id, 'question_id': question_id,
                    'request_id': request_id, 'actor': actor, 'member_epoch': state['member_epochs'][actor],
                    'revision': expected_revision, 'status': 'recorded', 'request_hash': request_hash,
                    'answers': normalized}
                await self.access.sessions.get(session_id).append('note', receipt)

        try:
            result = await self.questions.answer_and_notify(question_id, answers,
                session_id=session_id, before_resolve=commit)
        except QuestionUnavailableError:
            fail('decision/closed')
        if receipt is None:
            fail('decision/closed')
        return {**{key: receipt[key] for key in ('question_id', 'request_id', 'actor', 'revision', 'status')},
                'resolved': result['resolved']}

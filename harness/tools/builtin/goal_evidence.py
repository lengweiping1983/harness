"""目标完成回执，以确切的已提交工具结果序号为依据。"""
from __future__ import annotations

from harness.i18n import tr

import json
from collections.abc import Mapping
from typing import Any

from ...core.errors import ToolError
from ...core.jsonutil import to_jsonable

BOOKKEEPING = frozenset({'search_tools', 'get_goal', 'create_goal', 'update_goal', 'todo_read', 'todo_write', 'todo_update', 'todo_bind',
                        'exit_plan_mode', 'spawn_agent', 'followup_task', 'list_agents', 'send_message', 'wait_agent', 'wait_agents', 'interrupt_agent',
                        'job_list', 'job_kill'})
MAX_CRITERIA = 12
MAX_EVIDENCE_PER_CHECK = 10
MAX_CHECK_SUMMARY_CHARS = 2000
CANDIDATE_PAGE_SIZE = 20


def completion_guidance() -> str:
    return (tr('python.goal_evidence.0667121b256b', p0=f'{MAX_EVIDENCE_PER_CHECK}', p1=f'{MAX_CHECK_SUMMARY_CHARS}'))


def invalid(message: str, code: str = 'GOAL_INVALID_EVIDENCE') -> ToolError:
    return ToolError(message, code=code)


def criteria(value: Any, objective: str) -> tuple[str, ...]:
    if value is None:
        return (objective,)
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= MAX_CRITERIA:
        raise invalid(tr('python.goal_evidence.786ca23b4a1a'), 'GOAL_INVALID_CRITERIA')
    if any(not isinstance(text, str) or not text.strip() or len(text) > 2000 for text in value):
        raise invalid(tr('python.goal_evidence.f2337e0fb1fb'), 'GOAL_INVALID_CRITERIA')
    normalized = tuple(text.strip() for text in value)
    if len(set(normalized)) != len(normalized):
        raise invalid(tr('python.goal_evidence.59d06b1d9dcf'), 'GOAL_INVALID_CRITERIA')
    return normalized


def evidence_events(session: Any, event_type: str):
    """团队证据保存在日志中，不向主会话模型插入未配对的工具消息。"""
    return [event for event in session.events if event.type == event_type or
            event.type == 'note' and event.data.get('kind') == 'team-goal/' + event_type]


def result_reference(session: Any, goal: Any, seq: int) -> dict[str, Any]:
    if type(seq) is not int or seq <= goal.evidence_after:
        raise invalid(tr('python.goal_evidence.be3aff0da9d3'))
    event = session.event_at(seq)
    if event is None or not (event.type == 'tool/result' or event.type == 'note' and event.data.get('kind') == 'team-goal/tool/result') or event.data.get('name') in BOOKKEEPING or event.data.get('pruned'):
        raise invalid(tr('python.goal_evidence.a1ec2ea6094d'))
    data, call_id = event.data, event.data.get('call_id')
    rows = data.get('results', ())
    if not rows or any(not isinstance(row, Mapping) or row.get('is_error') is not False or row.get('tool_use_id') != call_id for row in rows):
        raise invalid(tr('python.goal_evidence.738a05b741fc'))
    start = next((candidate for candidate in reversed(evidence_events(session, 'tool/call'))
                  if goal.evidence_after < candidate.seq < seq and candidate.data.get('status') == 'running'
                  and candidate.data.get('call_id') == call_id and candidate.data.get('turn') == data.get('turn')
                  and candidate.data.get('name') == data.get('name')), None)
    if start is None:
        raise invalid(tr('python.goal_evidence.ec0443e1f874'))
    display = data.get('display')
    if isinstance(display, Mapping) and display.get('background'):
        raise invalid(tr('python.goal_evidence.06edb21ae8d2'))
    if data.get('name') == 'terminal_send':
        raise invalid(tr('python.goal_evidence.3b449cf8297e'))
    if data.get('name') == 'job_output':
        job = display.get('job') if isinstance(display, Mapping) else None
        if not isinstance(job, Mapping) or job.get('kind') == 'pty-send' or job.get('completionScope') == 'terminal-wait' or job.get('archived') or job.get('status') != 'completed' or job.get('exitCode') != 0:
            raise invalid(tr('python.goal_evidence.1f6a426302dd'))
    text = '\n'.join(str(row.get('content', '')) for row in rows)
    return {'sourceSeq': seq, 'callId': call_id, 'name': data.get('name'),
            'input': json.dumps(to_jsonable(start.data.get('input')), ensure_ascii=False)[:240],
            'preview': text[:240], 'truncated': len(text) > 240}


def completion_checks(session: Any, goal: Any, raw: Any) -> tuple[dict[str, Any], ...]:
    expected = goal.criteria or (goal.objective,)
    if session is None or not isinstance(raw, list) or len(raw) != len(expected):
        raise invalid(tr('python.goal_evidence.ee0345052795'), 'GOAL_COMPLETION_CHECKS_REQUIRED')
    checks, seen = [], set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {'criterion', 'summary', 'evidence_seqs'}:
            raise invalid(tr('python.goal_evidence.93fc7c2f6bd8'))
        index, summary, seqs = item['criterion'], item['summary'], item['evidence_seqs']
        if type(index) is not int or not 1 <= index <= len(expected) or index in seen:
            raise invalid(tr('python.goal_evidence.65f8b07bab1c'))
        if not isinstance(summary, str) or not summary.strip() or len(summary) > MAX_CHECK_SUMMARY_CHARS:
            raise invalid(tr('python.goal_evidence.9406722068ea', p0=f'{MAX_CHECK_SUMMARY_CHARS}'))
        if (not isinstance(seqs, list) or not 1 <= len(seqs) <= MAX_EVIDENCE_PER_CHECK or
                any(type(seq) is not int or seq < 0 for seq in seqs) or len(set(seqs)) != len(seqs)):
            raise invalid(tr('python.goal_evidence.bc9124add58f') + completion_guidance())
        seen.add(index)
        checks.append({'criterion': index, 'summary': summary.strip(),
                       'evidence': [result_reference(session, goal, seq) for seq in seqs]})
    # 最新待办列表仍有未完成任务，与全部完成的声明矛盾。
    todo = next((e for e in reversed(session.events_of_type('todo/write')) if e.seq > goal.evidence_after), None)
    if todo and any(row.get('status') != 'completed' for row in todo.data.get('todos', ())):
        raise invalid(tr('python.goal_evidence.0bbd80d84e37'), 'GOAL_PENDING_TASKS')
    return tuple(sorted(checks, key=lambda item: item['criterion']))


def candidates(session: Any, goal: Any, before: int | None = None) -> dict[str, Any]:
    if before is not None and (type(before) is not int or before < 0):
        raise invalid(tr('python.goal_evidence.2da8d8fb7076'))
    refs = []
    for event in reversed(evidence_events(session, 'tool/result')):
        if event.seq <= goal.evidence_after or before is not None and event.seq >= before:
            continue
        try:
            refs.append(result_reference(session, goal, event.seq))
        except ToolError:
            continue
        if len(refs) == CANDIDATE_PAGE_SIZE + 1:
            break
    return {'evidenceCandidates': refs[:CANDIDATE_PAGE_SIZE],
            'nextEvidenceBefore': refs[CANDIDATE_PAGE_SIZE - 1]['sourceSeq'] if len(refs) > CANDIDATE_PAGE_SIZE else None}


def evidence_page(session: Any, goal: Any, seq: int, offset: int = 0, limit: int = 20000) -> dict[str, Any]:
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20000:
        raise invalid(tr('python.goal_evidence.fba391c27b69'))
    references = [ref for check in goal.completion for ref in check['evidence'] if ref['sourceSeq'] == seq]
    if not references:
        raise invalid(tr('python.goal_evidence.229f3603fb72'), 'GOAL_EVIDENCE_NOT_FOUND')
    ref = result_reference(session, goal, seq)
    if any(ref['callId'] != saved['callId'] or ref['name'] != saved['name'] for saved in references):
        raise invalid(tr('python.goal_evidence.e622ad353ab4'))
    event = session.event_at(seq)
    text = '\n'.join(str(row.get('content', '')) for row in event.data['results'])
    if offset > len(text):
        raise invalid(tr('python.goal_evidence.ad07ea8127fb'))
    end = min(offset + limit, len(text))
    return {**ref, 'text': text[offset:end], 'offset': offset, 'totalChars': len(text),
            'nextOffset': end if end < len(text) else None}

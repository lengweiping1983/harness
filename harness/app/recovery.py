"""从已提交执行和会话记录中读取有界恢复事实，仅做只读查询。"""
from __future__ import annotations

from harness.i18n import tr

import hashlib
import json
from copy import deepcopy
from ..core.runs import run_error_copy
from collections.abc import Mapping
from typing import Any

from ..core.errors import HarnessError
from ..session.tool_activity import confirmed_tool_result, tool_invocations
from ..session.turn_scope import turn_events


def execution_for_turn(harness: Any, session_id: str, turn: int) -> Any:
    """依据已提交标识定位，不使用提示词相似度或近似时间。"""
    if type(turn) is not int or not 1 <= turn <= 2**53 - 1:
        raise HarnessError(tr('python.recovery.a77c5b2a365d'), code='run/invalid')
    session = harness.get_session(session_id)
    try:
        events = turn_events(session.events, turn=turn)
    except ValueError as exc:
        raise HarnessError(tr('python.recovery.195e85e6360a'), code='run/ambiguous-turn') from exc
    if events:
        run_id = events[0].data.get('run_id')
        if run_id is not None:
            record = harness.runtime.records.get(run_id) if isinstance(run_id, str) else None
            if record is not None and record.session_id == session_id:
                return record
            raise HarnessError(tr('python.recovery.743e63b2a9f2'), code='run/turn-not-found')
    raise HarnessError(tr('python.recovery.6c40e5bef141'), code='run/turn-not-found')


def _recovery_tool(invocation: dict[str, Any]) -> dict[str, Any]:
    """生命周期由共享投影识别；恢复只采纳配对且无歧义的原始证据。"""
    start = invocation['_start_event']
    result_event, result = invocation['_result_event'], invocation['_result']
    data = start.data if start is not None else {}
    text = result.get('content') if isinstance(result, Mapping) else None
    text = text if isinstance(text, str) else ''
    row = {'call_id': invocation['call_id'], 'name': str(data.get('name') or invocation['name'])[:256],
           'effect': str(data.get('effect', 'unknown')), 'idempotent': data.get('idempotent') is True,
           'status': 'unknown', 'source_seq': invocation['seq'], 'arguments': '',
           'result': text[:2000], 'result_truncated': len(text) > 2000}
    if 'input' in data:
        row['arguments'] = json.dumps(start.to_dict()['data']['input'], ensure_ascii=False)[:8000]
    valid = (invocation['_result_count'] == 1 and isinstance(result, Mapping)
             and result.get('tool_use_id') == invocation['call_id']
             and type(result.get('is_error')) is bool and isinstance(result.get('content'), str)
             and result_event.data.get('call_id', invocation['call_id']) == invocation['call_id']
             and (start is None or confirmed_tool_result(invocation)))
    if valid:
        display = result_event.data.get('display')
        display = display if isinstance(display, Mapping) else {}
        if any(display.get(flag) is True for flag in ('not_executed', 'recovery_restricted', 'steering_deferred', 'task_restricted')):
            row['status'] = 'not_executed'
        elif start is not None and display.get('interrupted') is not True:
            row['status'] = 'reported_error' if result['is_error'] else 'reported_success'
    return row


def recovery_preview(harness: Any, session_id: str, run_id: str, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise HarnessError(tr('python.recovery.19c245ce6a5c'), code='run/invalid')
    session = harness.get_session(session_id)
    record = harness.runtime.get(run_id)
    if record.session_id != session_id:
        raise HarnessError(tr('python.recovery.47c29ab69a98'), code='run/not-found')
    if record.status not in {'failed', 'interrupted'} or (record.error or {}).get('code') in {'run/removed', 'run/steered'}:
        raise HarnessError(tr('python.recovery.f89272b5b819'), code='run/not-retryable')
    try:
        scoped = turn_events(session.events, run_id=run_id)
    except ValueError:
        # 歧义时只能提供核实入口，不能从重复日志中挑选看似成功的一份。
        scoped = []
    scope_known = bool(scoped)
    turn = scoped[0].data['turn'] if scoped else None
    rows = [_recovery_tool(invocation) for invocation in tool_invocations(scoped)]
    uncertain = sum(row['effect'] != 'read' and row['status'] in {'unknown', 'reported_error'} for row in rows)
    origin = record
    seen = {record.id}
    for _ in range(64):
        parent = harness.runtime.records.get(origin.retry_of) if origin.retry_of else None
        if parent is None or parent.session_id != session_id or parent.id in seen:
            break
        seen.add(parent.id)
        origin = parent
    version = hashlib.sha256(json.dumps({'run': record.to_dict(), 'events': [event.to_dict() for event in scoped]},
                                       sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    end = len(rows) if len(rows) - offset - limit < 5 else offset + limit
    return {'run_id': record.id, 'session_id': session_id, 'status': record.status, 'turn': turn,
        'task': origin.text[:16000], 'task_truncated': len(origin.text) > 16000,
        'error': str((record.error or {}).get('message') or (record.error or {}).get('error') or (record.result or {}).get('error') or '')[:4000],
        **({'error_copy': deepcopy(copy)} if (copy := run_error_copy(record.error)) is not None else {}),
        'scope_known': scope_known, 'version': version, 'tools': rows[offset:end],
        'total_tools': len(rows), 'uncertain_operations': uncertain,
        'verification_required': not scope_known or uncertain > 0,
        'next_offset': end if end < len(rows) else None,
        'continuation_text': tr('python.recovery.fc3c78952508'),
        'inspection_text': tr('python.recovery.75649d4f503b'),
        'known_continuations': {mode: known.id for mode, key in [('continue', 'continue:' + run_id), ('inspect', 'inspect:' + run_id)]
            if (known := harness.runtime.submission(session_id, key)) is not None}}

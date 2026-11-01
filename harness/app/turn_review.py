"""审阅指定回合已提交的文件操作，不读取当前 Git 状态。

差异表示连续操作，不是合成后的净补丁。读取时不打开文件，
不修改模型消息表面，也不调用工具或模型。
"""
from __future__ import annotations

from harness.i18n import tr

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..core.diff import patch_counts
from ..core.errors import HarnessError
from ..session.turn_scope import turn_events

MAX_OPERATIONS = 200
MAX_PATCH = 262144


def _events(session: Any, turn: int) -> list[Any]:
    if type(turn) is not int or not 1 <= turn <= 2**53 - 1:
        raise HarnessError(tr('python.turn_review.a77c5b2a365d'), code='changes/invalid')
    try:
        events = turn_events(session.events, turn=turn)
    except ValueError as exc:
        raise HarnessError(tr('python.turn_review.f8ff09864288'), code='changes/ambiguous-turn') from exc
    if not events:
        raise HarnessError(tr('python.turn_review.cbcbf6bb6a73'), code='changes/turn-not-found')
    return events


def _operations(events: list[Any]):
    calls: dict[str, Mapping[str, Any]] = {}
    settled: set[str] = set()
    for event in events:
        data = event.data
        if not isinstance(data, Mapping):
            continue
        call_id = data.get('call_id')
        if not isinstance(call_id, str) or not call_id:
            continue
        if event.type == 'tool/call' and data.get('status') == 'running':
            calls[call_id] = data
            settled.discard(call_id)
        elif event.type == 'tool/result' and call_id in calls and call_id not in settled:
            settled.add(call_id)
            call = calls[call_id]
            name = call.get('name')
            input = call.get('input')
            input = input if isinstance(input, Mapping) else {}
            display = data.get('display')
            display = display if isinstance(display, Mapping) else {}
            results = data.get('results')
            if not isinstance(results, (list, tuple)) or len(results) != 1 or not isinstance(results[0], Mapping):
                continue
            result = results[0]
            if result.get('tool_use_id') != call_id or result.get('is_error') is not False:
                continue
            if name not in {'write', 'edit'}:
                continue
            if display.get('changed') is False or display.get('diff') == '' and display.get('changed') is not True:
                continue
            path = display.get('path') or input.get('path')
            if not isinstance(path, str) or not path.strip():
                continue
            yield {'source_seq': event.seq, 'call_id': call_id, 'name': name, 'path': path,
                   'command': str(input.get('command') or name), 'time': event.time,
                   '_display': display}


def _details(operation: dict[str, Any]) -> dict[str, Any]:
    # 只生成请求的操作或页面，不生成全部历史补丁。
    display = operation['_display']
    diff = display.get('diff')
    patch = diff[:MAX_PATCH] if isinstance(diff, str) else None
    truncated = bool(display.get('diff_truncated')) or isinstance(diff, str) and len(diff) > MAX_PATCH
    counts = None
    if patch is not None and not truncated:
        added, removed = patch_counts(patch)
        counts = {'added': added, 'removed': removed}
    return {**{key: value for key, value in operation.items() if key != '_display'},
            'patch': patch, 'truncated': truncated, 'counts': counts,
            'unavailable': str(display.get('diff_unavailable') or tr('python.turn_review.81f158142e61')) if patch is None else None}


def turn_changes(session: Any, turn: int, source_seq: int | None = None, *, offset: int = 0, through: int | None = None) -> dict[str, Any]:
    events = _events(session, turn)
    if source_seq is not None:
        if type(source_seq) is not int or not 0 <= source_seq <= 2**53 - 1:
            raise HarnessError(tr('python.turn_review.809b52b7304b'), code='changes/invalid')
        operation = next((item for item in _operations(events) if item['source_seq'] == source_seq), None)
        if operation is None:
            raise HarnessError(tr('python.turn_review.b5db60966f6a'), code='changes/operation-not-found')
        return {'session_id': session.id, 'turn': turn, **_details(operation)}
    if type(offset) is not int or not 0 <= offset <= 2**53 - 1:
        raise HarnessError(tr('python.turn_review.ad4b9385db1b'), code='changes/invalid')
    if through is not None:
        if type(through) is not int or not any(event.seq == through for event in events):
            raise HarnessError(tr('python.turn_review.ad3b19187702'), code='changes/invalid')
        events = [event for event in events if event.seq <= through]
    files: dict[str, dict[str, Any]] = {}
    total = 0
    for operation in _operations(events):
        total += 1
        if total <= offset or total > offset + MAX_OPERATIONS:
            continue
        path = operation['path']
        key = path.replace('\\', '/').lower() if len(path) > 1 and path[1] == ':' else path
        file = files.setdefault(key, {'path': path, 'operations': []})
        file['operations'].append({key: value for key, value in _details(operation).items() if key != 'patch'})
    root = session.header.cwd
    if root:
        try:
            root = str(Path(root).expanduser().resolve())
        except (OSError, RuntimeError, ValueError):
            root = None
    return {'session_id': session.id, 'turn': turn, 'workspace_root': root,
            'through': events[-1].seq, 'ended': any(event.type == 'turn/end' for event in events),
            'files': list(files.values()), 'total_operations': total, 'offset': offset,
            'next_offset': offset + MAX_OPERATIONS if total > offset + MAX_OPERATIONS else None,
            'truncated': total > offset + MAX_OPERATIONS}

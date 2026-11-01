"""根据已提交文件工具快照链计算文本净变化，不依赖 Git。"""
from __future__ import annotations

from harness.i18n import tr

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..core.diff import patch_counts
from ..core.errors import HarnessError
from ..tools.builtin.file_diff import file_diff
from .turn_review import _events, _operations, MAX_OPERATIONS


def _uncertain(events):
    pending = {}
    uncertain = 0
    for event in events:
        data = event.data
        if not isinstance(data, Mapping):
            continue
        call = data.get('call_id')
        if not isinstance(call, str) or not call:
            continue
        if event.type == 'tool/call' and data.get('status') == 'running' and data.get('name') in {'write', 'edit'}:
            # 复用的调用标识不能抹除先前尚未确认的写入。
            uncertain += int(call in pending)
            pending[call] = True
        elif event.type == 'tool/result' and call in pending:
            results = data.get('results', ())
            display = data.get('display')
            skipped = isinstance(display, Mapping) and (display.get('not_executed') is True or display.get('steering_deferred') is True)
            safe = skipped or isinstance(results, (list, tuple)) and len(results) == 1 and isinstance(results[0], Mapping) and results[0].get('tool_use_id') == call and results[0].get('is_error') is False
            uncertain += int(not safe)
            pending.pop(call, None)
    return uncertain + len(pending)


def _net_file(session, operations, uncertain, *, patch=False):
    first, last = operations[0], operations[-1]
    result = {'path': first['path'], 'first_seq': first['source_seq'], 'last_seq': last['source_seq'],
              'operations': len(operations), 'available': False, 'kind': 'unknown', 'counts': None, 'truncated': False, 'reason': None}
    if patch:
        result['patch'] = None
    if uncertain:
        result['reason'] = tr('python.turn_net_changes.78b6763ae0fe')
        return result
    snapshots = []
    previous = None
    try:
        for operation in operations:
            display = operation['_display']
            snapshot = display.get('snapshot')
            if not isinstance(snapshot, Mapping) or type(snapshot.get('version')) is not int or snapshot.get('version') != 1:
                result['reason'] = str(display.get('snapshot_unavailable') or tr('python.turn_net_changes.5c4db1b150a9'))
                return result
            before = session.file_evidence.identity(snapshot.get('before'))
            after = session.file_evidence.identity(snapshot.get('after'))
            if not after[2]:
                raise ValueError(tr('python.turn_net_changes.7fc91882339b'))
            if previous is not None and previous != before:
                result['reason'] = tr('python.turn_net_changes.1b8b3583aed2')
                return result
            snapshots.append(snapshot)
            previous = after
        original = session.file_evidence.read(snapshots[0]['before'])
        final = session.file_evidence.read(snapshots[-1]['after'])
    except (OSError, ValueError, UnicodeError):
        result['reason'] = tr('python.turn_net_changes.a5de48cc0713')
        return result
    existed = snapshots[0]['before']['exists']
    unchanged = existed and session.file_evidence.identity(snapshots[0]['before']) == previous
    diff = file_diff(original, final, Path(first['path']), changed=not unchanged, before_exists=existed)
    text = diff.get('diff')
    if not isinstance(text, str):
        result['reason'] = tr('python.turn_net_changes.3d1e99e2d6eb')
        return result
    truncated = bool(diff.get('diff_truncated'))
    counts = None
    if not truncated:
        added, removed = patch_counts(text)
        counts = {'added': added, 'removed': removed}
    result.update(available=True, kind='unchanged' if unchanged else 'created' if not existed else 'modified', counts=counts, truncated=truncated)
    if patch:
        result['patch'] = text
    return result


def turn_net_changes(session: Any, turn: int, source_seq: int | None = None, *, offset: int = 0, through: int | None = None):
    events = _events(session, turn)
    if type(offset) is not int or not 0 <= offset <= 2**53 - 1 or through is not None and (type(through) is not int or not any(event.seq == through for event in events)):
        raise HarnessError(tr('python.turn_net_changes.0b1e6afa6224'), code='changes/invalid')
    if through is not None:
        events = [event for event in events if event.seq <= through]
    groups = {}
    for operation in _operations(events):
        path = operation['path']
        key = path.replace('\\', '/').lower() if len(path) > 1 and path[1] == ':' else path
        groups.setdefault(key, []).append(operation)
    files = list(groups.values())
    uncertain = _uncertain(events)
    if source_seq is not None:
        if type(source_seq) is not int or not 0 <= source_seq <= 2**53 - 1:
            raise HarnessError(tr('python.turn_net_changes.1eacd92afe30'), code='changes/invalid')
        selected = next((operations for operations in files if operations[0]['source_seq'] == source_seq), None)
        if selected is None:
            raise HarnessError(tr('python.turn_net_changes.aed34c8360c8'), code='changes/operation-not-found')
        return {'session_id': session.id, 'turn': turn, 'through': events[-1].seq, **_net_file(session, selected, uncertain, patch=True)}
    page = [_net_file(session, operations, uncertain) for operations in files[offset:offset + MAX_OPERATIONS]]
    return {'session_id': session.id, 'turn': turn, 'through': events[-1].seq,
            'files': page, 'total_files': len(files), 'offset': offset,
            'next_offset': offset + MAX_OPERATIONS if len(files) > offset + MAX_OPERATIONS else None,
            'ended': any(event.type == 'turn/end' for event in events), 'uncertain_operations': uncertain}

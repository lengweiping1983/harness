"""用于对话快照和实时事件的精简已提交钩子证据。"""
from __future__ import annotations

from harness.i18n import tr

from collections.abc import Mapping
from typing import Any

SUMMARY_KEYS = ('id', 'root', 'session_id', 'call_id', 'turn', 'step', 'event', 'tool',
                'label', 'plugin_id', 'handler_type', 'status', 'started', 'finished', 'duration_ms')


def hook_summary(data: Mapping[str, Any], source_seq: int, *, active_turn: int | None) -> dict[str, Any]:
    result = {key: data[key] for key in SUMMARY_KEYS if key in data}
    result.update(source_seq=source_seq, reason=str(data.get('reason') or '')[:320])
    if data.get('origin_session_id') and data['origin_session_id'] != data.get('session_id'):
        result['label'] = (str(result.get('label') or '')[:144] + tr('python.hook_runs.bb65fdc1518d'))
    if result.get('status') == 'running' and result.get('turn') != active_turn:
        result.update(status='interrupted', reason=tr('python.hook_runs.e6e62dcfc8a2'))
    return result


def hook_results(events: list[Any], *, active_turn: int | None) -> dict[int, dict[str, list[dict[str, Any]]]]:
    """将证据绑定到每次调用，即使后续回合复用相同调用标识。"""
    calls: dict[str, dict[str, dict[str, Any]]] = {}
    results = {}
    for event in events:
        data = event.data
        if not isinstance(data, Mapping):
            continue
        call = str(data.get('call_id') or '')
        if event.type == 'tool/call' and data.get('status') == 'running':
            calls[call] = {}
        elif event.type == 'note' and data.get('kind') == 'hook_run' and data.get('event') != 'Stop' and call in calls:
            calls[call][str(data['id'])] = hook_summary(data, event.seq, active_turn=active_turn)
        elif event.type == 'tool/result':
            results[event.seq] = {str(row.get('tool_use_id')): list(calls.get(str(row.get('tool_use_id')), {}).values())
                                  for row in data.get('results', ()) if isinstance(row, Mapping)}
    return results

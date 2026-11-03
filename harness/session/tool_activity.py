"""每个已提交工具生命周期对应一次调用，独立于模型压缩。"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def confirmed_tool_result(invocation: Mapping[str, Any] | None) -> bool:
    """唯一结果必须与真实开始按序配对，冗余身份冲突时保持未知。"""
    if invocation is None:
        return False
    start, result = invocation['_start_event'], invocation['_result_event']
    value = invocation['_result']
    return bool(start is not None and result is not None and start.seq < result.seq
                and invocation['_result_count'] == 1 and invocation['call_id']
                and isinstance(start.data.get('name'), str) and start.data['name']
                and result.data.get('name') == start.data['name']
                and result.data.get('call_id', invocation['call_id']) == invocation['call_id']
                and isinstance(value, Mapping) and type(value.get('is_error')) is bool
                and value.get('tool_use_id') == invocation['call_id'])


def tool_invocations(events: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    latest: dict[tuple[int, str], dict[str, Any]] = {}
    current_turn = 0
    open_rows: dict[int, set[int]] = {}
    for event in events:
        data = event.data
        if not isinstance(data, Mapping):
            continue
        if event.type == 'turn/start':
            current_turn = data.get('turn') if type(data.get('turn')) is int else current_turn + 1
            continue
        if event.type == 'turn/end':
            ended = data.get('turn') if type(data.get('turn')) is int else current_turn
            for index in open_rows.pop(ended, set()):
                rows[index]['status'] = 'interrupted'
            continue
        if event.type not in {'tool/call', 'tool/result'}:
            continue
        if event.source_event_seqs:
            # 压缩和裁剪是模型表面的替换，不是另一份执行结果。
            continue
        scope = data.get('turn') if type(data.get('turn')) is int else current_turn
        results = data.get('results', ()) if event.type == 'tool/result' else (data,)
        if not isinstance(results, (list, tuple)):
            continue
        for result in results:
            if not isinstance(result, Mapping):
                continue
            call = str((result.get('tool_use_id') if event.type == 'tool/result' else data.get('call_id')) or '')
            key = (scope, call)
            row = latest.get(key) if call else None
            running = event.type == 'tool/call' and data.get('status') == 'running'
            # 调用标识被不同步骤复用时，在上次结果之后建立一次新调用。
            # 同一步骤中重复的运行状态更新保留原调用标识。
            if row is None or running and (row['status'] != 'running' or
                    data.get('step') is not None and row.get('step') != data['step']):
                row = {'seq': event.seq, 'time': event.time, 'call_id': call, 'turn': scope,
                       'name': data.get('name') or result.get('name') or '?', 'status': 'running',
                       'input': None, 'display': None, 'duration_ms': None,
                       'is_error': False, 'counted': True, '_index': len(rows),
                       '_start_event': None, '_result_event': None, '_result': None, '_result_count': 0}
                rows.append(row)
                if call:
                    latest[key] = row
            if data.get('name'):
                row['name'] = data['name']
            for field in ('input', 'display', 'duration_ms', 'step'):
                if field in data:
                    row[field] = data[field]
            if 'display' not in data and 'output' in data:
                row['display'] = data['output']
            if running:
                if row['_start_event'] is None:
                    row['_start_event'] = event
                open_rows.setdefault(row['turn'], set()).add(row['_index'])
            elif event.type == 'tool/result':
                row['_result_count'] += 1
                if row['_result_event'] is None:
                    # 保留原始不可变事件的引用；投影消费者不复制完整结果正文。
                    row['_result_event'], row['_result'] = event, result
                # 规范化结果具有权威性，即使冗余的调用完成记录
                # 状态不一致或缺少 is_error。
                row.update(status='error' if result.get('is_error') is True else 'done',
                           is_error=result.get('is_error') is True)
            elif row['_result_event'] is None:
                failed = data.get('status') == 'error' or data.get('is_error') is True
                row.update(status='error' if failed else 'done', is_error=failed)
            if row['status'] != 'running':
                open_rows.get(row['turn'], set()).discard(row['_index'])
            display = row.get('display')
            not_executed = isinstance(display, Mapping) and display.get('not_executed') is True
            # 已进入执行管道的调用，即使执行前被拒绝也计数。
            # 未获准进入管道便收尾的模型提议则不计数。
            row['counted'] = row['_start_event'] is not None or not not_executed
    return rows

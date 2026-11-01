"""从唯一轮次内的持久工具事实生成结果摘要，不推断业务验收。"""
from __future__ import annotations

from collections.abc import Mapping

from .tool_activity import confirmed_tool_result, tool_invocations


def delivery_summary(events, session=None):
    """统计已记录执行；重复、缺失或不匹配的结果保持未确认。"""
    rows = tool_invocations(events)
    failed = unknown = 0
    artifacts = {}
    for row in rows:
        result = row['_result_event']
        value = row['_result']
        confirmed = confirmed_tool_result(row)
        if not confirmed:
            unknown += 1
        elif value['is_error']:
            failed += 1
        elif row['name'] == 'present':
            display = result.data.get('display')
            files = display.get('files') if isinstance(display, Mapping) else None
            for file in files if isinstance(files, (list, tuple)) else ():
                if isinstance(file, Mapping) and isinstance(file.get('path'), str) and file['path']:
                    artifact = {'path': file['path'], 'source_seq': result.seq}
                    for key in ('name', 'description'):
                        if isinstance(file.get(key), str):
                            artifact[key] = file[key]
                    if 'workspace_root' in display and (display['workspace_root'] is None or isinstance(display['workspace_root'], str)):
                        artifact['workspace_root'] = display['workspace_root']
                    artifacts[file['path']] = artifact
    start = events[0]
    from .verification import verification_summary
    verification, checks = verification_summary(session, events) if session is not None else ('unverified', [])
    return {'run_id': start.data.get('run_id'), 'start_seq': start.seq,
            'tool_executions': sum(row['counted'] for row in rows),
            'failed_tools': failed, 'unknown_tools': unknown,
            'artifacts': list(artifacts.values())[:32], 'artifact_count': len(artifacts),
            'verification': verification, 'checks': checks}

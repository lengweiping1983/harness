"""只读上下文组成和固定版本的压缩证据。"""

from harness.i18n import tr
import asyncio
from collections import Counter

from .errors import BadRequest, NotFound

CHUNK_CHARS = 24000


async def resolve_context(harness, root_session_id, agent_id=None):
    root = harness.get_session(root_session_id)
    if agent_id is None:
        return root
    state = harness.subagents.summary(agent_id)
    if state is None or (state['parent_session_id'] != root_session_id and state.get('root_session_id') != root_session_id):
        raise NotFound(tr('python.context_audit.ae30c16aed96'))
    contexts = harness.subagents.contexts
    child_id = state.get('child_session_id')
    if not state.get('context_persisted') or contexts is None or not child_id or not contexts.has(child_id):
        raise NotFound(tr('python.context_audit.3ee449cf5407'))
    return await asyncio.to_thread(contexts.get, child_id)


def _attempts(events):
    active = None
    for event in events:
        data = event.data
        if event.type == 'compaction/start':
            if active is not None:
                yield active
            active = {'id': event.seq, 'time': event.time, 'turn': data.get('turn'),
                      'status': 'pruned' if data.get('stage') == 'tool-result-prune' else 'running',
                      'before_approx_tokens': data.get('approx_tokens'),
                      'pruned_results': data.get('pruned_results', 0), 'chars_removed': data.get('chars_removed', 0)}
            for key in ('capacity_before', 'trigger'):
                if key in data:
                    active[key] = data[key]
        elif event.type == 'compaction/progress' and active is not None and data.get('compaction_id') == active['id']:
            active.update({key: data[key] for key in ('completed_chunks', 'total_chunks', 'stage', 'merge_level',
                          'current_chunk', 'attempt', 'max_attempts', 'retry_in_seconds') if key in data})
        elif event.type == 'compaction/summary' and active is not None:
            seqs = data.get('shadowed_seqs', ())
            active.update(summary_seq=event.seq, summary_preview=str(data.get('summary', ''))[:160],
                          shadowed_count=data.get('shadowed_count', len(seqs)),
                          retained_nodes=data.get('retained_nodes'), policy_version=data.get('summary_policy_version'),
                          source_first=seqs[0] if seqs else None, source_last=seqs[-1] if seqs else None)
        elif event.type == 'compaction/end':
            if active is None:
                continue
            status = ('cancelled' if data.get('cancelled') else 'failed' if data.get('error') else 'pruned' if data.get('after_prune') else
                      'skipped' if data.get('skipped') else 'completed')
            active.update(status=status, end_seq=event.seq, finished_at=event.time,
                          error=str(data['error'])[:400] if data.get('error') else None)
            for key in ('before_approx_tokens', 'after_approx_tokens', 'shadowed_count', 'capacity_after', 'pruned_results', 'chars_removed'):
                if key in data:
                    active[key] = data[key]
            yield active
            active = None
    if active is not None:
        yield active


def context_audit(session, *, through=None, before=None, limit=20, snapshot=None, subagent_count=None, request_sequence=None):
    events, messages, surface_nodes = snapshot or (session.events, None, len(session.surface))
    last = events[-1].seq if events else -1
    first_page = through is None
    through = last if through is None else through
    if type(through) is not int or not -1 <= through <= last:
        raise BadRequest(tr('python.context_audit.a85543990aa8'))
    before = through + 1 if before is None else before
    if type(before) is not int or not 0 <= before <= through + 1:
        raise BadRequest(tr('python.context_audit.4b5ffac340dc'))
    if type(limit) is not int or not 1 <= limit <= 50:
        raise BadRequest(tr('python.context_audit.96f5a568c0b3'))
    # 即使包含多年压缩历史，响应内存也须保持有界。
    rows, count = [], 0
    for row in _attempts(event for event in events if event.seq <= through):
        if row['id'] < before:
            count += 1
            rows.append(row)
            if len(rows) > limit + 4:
                rows.pop(0)
    if count - limit >= 5:
        rows = rows[-limit:]
    current = None
    if first_page:
        from .views import _event_stats
        requests = session.request_traces.context_snapshot(session.id, through=last, max_sequence=request_sequence)
        tree_cost = session.request_traces.cost_summary(session.id, max_sequence=request_sequence)
        stats = _event_stats(events)
        messages = session.build_messages() if messages is None else messages
        from ..session.tool_projection import tool_projection_statistics
        current = {'through': last, 'event_count': len(events), 'surface_nodes': surface_nodes,
                   'tool_result_projection': tool_projection_statistics(events, messages),
                   'message_count': len(messages),
                   'roles': dict(Counter(type(message).__name__ for message in messages)),
                   'latest_request': requests.pop('latest_request'),
                   'latest_reported_request': requests.pop('latest_reported_request'),
                   'statistics': {**requests, 'tree_cost': tree_cost, 'tool_calls': stats['tool_calls'], 'tool_errors': stats['tool_errors'],
                                  'subagents': subagent_count}}
    return {'session_id': session.id, 'through': through, 'current': current, 'compactions': rows,
            'has_more': count > len(rows), 'next_before': rows[0]['id'] if count > len(rows) else None}


def read_compaction(session, start_seq, *, through, part='summary', offset=0):
    if type(start_seq) is not int or type(through) is not int or type(offset) is not int or offset < 0:
        raise BadRequest(tr('python.context_audit.528f03da85d7'))
    last = session.events[-1].seq if session.event_count else -1
    if not 0 <= start_seq <= through <= last:
        raise BadRequest(tr('python.context_audit.62f7d3e9d356'))
    # 使用与列表相同的边界，防止后续摘要或结束事件
    # 悄悄改变已经打开的读取器所依据的证据。
    row = next((row for row in _attempts(e for e in session.events if e.seq <= through) if row['id'] == start_seq), None)
    if row is None:
        raise NotFound(tr('python.context_audit.71af3908e88a'))
    summary = session.event_at(row.get('summary_seq', -1))
    if part == 'summary':
        terminal = session.event_at(row.get('end_seq', -1))
        text = str(summary.data.get('summary', '')) if summary else (
            str(terminal.data.get('error')) if terminal and terminal.data.get('error') else tr('python.context_audit.c8eb5691914a'))
    elif part == 'sources':
        from .evidence_reader import read_sources
        seqs = summary.data.get('shadowed_seqs', ()) if summary else ()
        return read_sources(session, row.get('summary_seq'), start_seq, seqs, offset)
    else:
        raise BadRequest(tr('python.context_audit.3d4bd8f6ebf1'))
    if offset > len(text):
        raise BadRequest(tr('python.context_audit.354de65fab4f'))
    end = min(len(text), offset + CHUNK_CHARS)
    return {'text': text[offset:end], 'offset': offset, 'total_chars': len(text),
            'next_offset': end if end < len(text) else None}

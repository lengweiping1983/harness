"""从持久化证据投影执行时间线，不重放或推断执行结果。"""

import asyncio
import json
from collections.abc import Mapping

from harness.core.jsonutil import to_jsonable
from harness.i18n import tr
from .errors import BadRequest, NotFound


def agents_for(manager, root, live):
    """读取归档及尚在内存中的代理，保留真实父子关系。"""
    rows, before = {}, None
    while True:
        page = manager.history_page(root, before=before, limit=100, include_descendants=True)
        rows.update((row['agent_id'], row) for row in page['agents'])
        if not page['has_more']:
            break
        before = page['next_before']
    rows.update((row['agent_id'], row) for row in live)
    return list(rows.values())


async def snapshot(harness, root, agent_session=None):
    """先验证归属，再取得事件快照；上下文不可用时仍可读取独立保存的请求证据。"""
    session = harness.get_session(root)
    live = [harness.subagents.summary(state.id) for state in harness.subagents.list_states()
            if state.parent_session_id == root or state.root_session_id == root]
    agents = await asyncio.to_thread(agents_for, harness.subagents, root, live)
    owner = agent_session or root
    missing = False
    if owner != root:
        agent = next((row for row in agents if row.get('child_session_id') == owner), None)
        if agent is None:
            raise NotFound(tr('ui.trace.unavailable'))
        from .context_audit import resolve_context
        try:
            session = await resolve_context(harness, root, agent['agent_id'])
        except NotFound:
            session = None
            missing = True
    events = session.events if session is not None else ()
    archive = harness.sessions.request_traces
    def requests():
        rows, before = [], None
        while True:
            page = archive.page(root, include_children=True, agent_session_id=owner, before=before, limit=100)
            rows.extend(page['requests'])
            if not page['has_more']:
                return rows
            before = page['next_before']
    traces = await asyncio.to_thread(requests)
    if owner == root:
        task = getattr(getattr(harness, 'runtime', None), 'active', {}).get(root)
        running = task is not None and not task.done()
    else:
        running = agent['status'] == 'running' and not agent.get('recovered')
    return owner, events, traces, agents, missing, running


def project(owner, events, traces, agents, running=False):
    """按调用标识合并终态，关联只采用持久化的请求及代理标识。"""
    rows = []
    requests = {}
    for trace in sorted(traces, key=lambda row: row['seq']):
        requests[trace['request_id']] = trace
        rows.append({'id': 'request:' + trace['id'], 'kind': 'request', 'time': trace['time'],
                     'order': trace['seq'], 'name': trace['model'], 'status': trace['status'], 'request': trace})
    origins, calls, results, starts = {}, {}, {}, {}
    for event in events:
        data = event.data
        if event.type == 'assistant/message':
            for block in data.get('content', ()):
                if block.get('type') == 'tool_use':
                    origins[block['id']] = data.get('request_id')
        elif event.type == 'tool/call':
            call_id = data.get('call_id')
            if not call_id:
                continue
            if data.get('status') == 'running':
                starts.setdefault(call_id, []).append(data.get('run_id'))
            if call_id not in calls:
                calls[call_id] = {'id': 'tool:' + str(event.seq), 'kind': 'tool', 'time': event.time,
                                  'order': event.seq, 'call_id': call_id, 'status': 'unknown', 'turn': data.get('turn'),
                                  'name': data.get('name'), 'description': data.get('description', '')}
            row = calls[call_id]
            if isinstance(data.get('description'), str):
                row['description'] = data['description']
            row.update({key: data[key] for key in ('input', 'duration_ms', 'display') if key in data})
            if data.get('status') in {'done', 'error'}:
                row['status'] = data['status']
        elif event.type == 'tool/result':
            for result in data.get('results', ()):
                results[result['tool_use_id']] = (result, data)
    ended_turns = {event.data.get('turn') for event in events if event.type == 'turn/end'}
    linked_agents = set()
    children = {row['agent_id']: {key: row.get(key) for key in ('agent_id', 'child_session_id', 'description', 'status', 'created_at', 'source_call_id')}
                for row in agents if row.get('parent_session_id') == owner}
    creation_calls = {agent['source_call_id']: agent for agent in children.values() if agent.get('source_call_id')}
    for call_id, row in calls.items():
        # 完成事件不补猜运行归属；复用调用 ID 时明确标记无法唯一归属。
        run_ids = starts.get(call_id, [])
        row['run_id'] = run_ids[0] if len(run_ids) == 1 and isinstance(run_ids[0], str) else None
        row['run_attribution_ambiguous'] = len(run_ids) > 1
        if running and row['turn'] not in ended_turns and row['status'] == 'unknown':
            row['status'] = 'running'
        origin = origins.get(call_id)
        trace = requests.get(origin)
        row.update(request_id=origin, trace_id=trace['id'] if trace else None)
        if call_id in results:
            result, data = results[call_id]
            row.update(status='error' if result.get('is_error') else 'done', result=result.get('content'),
                       duration_ms=data.get('duration_ms'), display=data.get('display'))
        # 仅创建工具的结构化结果可证明创建关联；其他工具也可能返回代理信息。
        if call_id in creation_calls:
            row['agent'] = creation_calls[call_id]
            linked_agents.add(row['agent']['agent_id'])
        display = row.get('display')
        if isinstance(display, Mapping) and display.get('steering_deferred') is True:
            row['status'] = 'skipped'
        if row['name'] == 'followup_task' and isinstance(display, Mapping) and display.get('started') is True:
            display = display.get('run')
        if row['name'] in {'spawn_agent', 'followup_task'} and isinstance(display, Mapping) and display.get('agent_id') in children:
            agent = children[display['agent_id']]
            row['agent'] = agent
            linked_agents.add(agent['agent_id'])
        row.pop('display', None)
        rows.append(row)
    for agent_id, agent in children.items():
        if agent_id not in linked_agents:
            rows.append({'id': 'agent:' + agent_id, 'kind': 'agent', 'time': agent['created_at'],
                         'order': 0, 'name': agent.get('description') or agent_id, 'status': agent['status'], 'agent': agent})
    return sorted(rows, key=lambda row: (row['time'], row['kind'], row['order'], row['id']))


def page(rows, *, after=None, limit=50):
    """游标使用不可变开始坐标，终态更新不会移动节点。"""
    if not 1 <= limit <= 100:
        raise BadRequest(tr('ui.trace.invalidCursor'))
    def key(row):
        return [row['time'], row['kind'], row['order'], row['id']]
    if after:
        try:
            cursor = json.loads(after)
            if not isinstance(cursor, list) or len(cursor) != 4 or type(cursor[0]) is not int or not isinstance(cursor[1], str) or type(cursor[2]) is not int or not isinstance(cursor[3], str):
                raise ValueError
            rows = [row for row in rows if key(row) > cursor]
        except (ValueError, TypeError):
            raise BadRequest(tr('ui.trace.invalidCursor')) from None
    end = len(rows) if len(rows) - limit < 5 else limit
    selected = rows[:end]
    # 参数和结果由详情接口分段提供，列表不携带大正文。
    summaries = [{k: v for k, v in row.items() if k not in {'input', 'result'}} for row in selected]
    return {'entries': summaries, 'has_more': len(rows) > end,
            'next_after': json.dumps(key(selected[-1])) if selected else after}


async def read(harness, root, *, agent_session=None, after=None, limit=50, entry=None, part='input', offset=0):
    owner, events, traces, agents, missing, running = await snapshot(harness, root, agent_session)
    from .read_work import shared_read
    # 相同事件与元数据快照的轮询、分页、工具详情共用在途投影。
    key = ('execution', root, owner, len(events), events[-1].seq if events else -1,
           json.dumps(traces, sort_keys=True, ensure_ascii=False),
           json.dumps(agents, sort_keys=True, ensure_ascii=False), running)
    rows = await asyncio.to_thread(shared_read, harness.sessions.request_traces, key,
                                   lambda: project(owner, events, traces, agents, running))
    if entry is not None:
        row = next((row for row in rows if row['id'] == entry and row['kind'] == 'tool'), None)
        if row is None:
            raise NotFound(tr('ui.trace.unavailable'))
        if part not in {'input', 'result'} or offset < 0:
            raise BadRequest(tr('ui.trace.invalidCursor'))
        value = row.get(part)
        text = (value if isinstance(value, str) else json.dumps(to_jsonable(value), ensure_ascii=False, indent=2)) if part in row else tr('ui.trace.noInput' if part == 'input' else 'ui.trace.noResult')
        end = min(len(text), offset + 24000)
        return {'text': text[offset:end], 'offset': offset, 'total_chars': len(text),
                'next_offset': end if end < len(text) else None}
    return {**page(rows, after=after, limit=limit), 'session_id': owner, 'context_missing': missing}

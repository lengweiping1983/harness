"""直接委派者按真实运行读取成员工具证据，不暴露私有推理或其他运行。"""
from ..i18n import tr

import hashlib
import json

from collections import Counter
from collections.abc import Mapping

from ..core.errors import ToolExecutionError
from ..core.jsonutil import to_jsonable
from ..tools.builtin.tool_result_tools import read_tool_result
from ..tools.registry import ToolDefinition


def build_member_evidence_tool(manager):
    """授权在宿主关系上校验，证据归属取自持久工具调用事件。"""
    def execute(args, ctx):
        try:
            state = manager.target_for(args['agent_id'], ctx.session_id)
        except KeyError:
            # 只读历史证据允许原直接委派者跨回合核对，不扩展运行控制或同伴可见性。
            state = manager.get(args['agent_id'])
            if state is None or state.parent_session_id != ctx.session_id or not state.settled:
                raise ToolExecutionError(tr('member.evidence.unavailable')) from None
        if state.parent_session_id != ctx.session_id or not state.settled:
            raise ToolExecutionError(tr('member.evidence.settled'))
        session = state.session
        if session is None and manager.contexts is not None:
            session = manager.contexts.get(state.session_id)
        if session is None or session.id != state.session_id:
            raise ToolExecutionError(tr('member.evidence.context'))
        starts = [event for event in session.events_of_type('tool/call')
                  if event.data.get('status') == 'running']
        calls = {event.data.get('call_id') for event in starts if event.data.get('run_id') == state.id}
        counts = Counter(event.data.get('call_id') for event in starts)
        ambiguous = {call_id for call_id in calls if counts[call_id] != 1}
        results = [event for event in session.events_of_type('tool/result')
                   if event.data.get('call_id') in calls]
        identity = {'agent_id': state.id, 'context_session_id': session.id, 'status': state.status,
                    'evidence_scope': 'member-run',
                    'parent_run_id': getattr(state, 'parent_run_id', None),
                    'run_scope': getattr(state, 'scope', None),
                    'limitation': tr('member.evidence.limitation')}
        call_id = args.get('call_id')
        # 原文读取器按调用 ID 定位；同上下文复用 ID 时不能把旧结果改标为本轮。
        if call_id in ambiguous or call_id is None and ambiguous:
            raise ToolExecutionError(tr('member.evidence.ambiguous'))
        if call_id is not None:
            if call_id not in calls:
                raise ToolExecutionError(tr('member.evidence.call'))
            start = next(event for event in starts if event.data.get('call_id') == call_id)
            # 参数来自原始调用而非成员自述；限制预览长度，截断不能冒充完整参数。
            payload = json.dumps(to_jsonable(start.data.get('input')), ensure_ascii=False, sort_keys=True)
            invocation = {'event_seq': start.seq, 'name': start.data.get('name'),
                          'effect': start.data.get('effect', 'unknown'),
                          'input_available': 'input' in start.data,
                          'input_json': payload[:4096], 'input_total_chars': len(payload),
                          'input_truncated': len(payload) > 4096,
                          'input_sha256': hashlib.sha256(payload.encode('utf-8')).hexdigest()}
            return {**identity, 'call': invocation, 'result': read_tool_result(session, call_id,
                    offset=args.get('offset', 0), limit=args.get('limit', 4096), query=args.get('query'))}
        after = args.get('after', -1)
        if type(after) is not int or after < -1:
            raise ToolExecutionError(tr('member.evidence.cursor'))
        rows = []
        by_call = {event.data.get('call_id'): event for event in starts}
        returned = {event.data.get('call_id') for event in results}
        for event in results:
            outcomes = event.data.get('results') or ()
            rows.append({'call_id': event.data.get('call_id'), 'result_seq': event.seq,
                         'call_seq': by_call[event.data.get('call_id')].seq,
                         'evidence_seq': event.seq, 'result_available': True,
                         'name': event.data.get('name'),
                         'is_error': not outcomes or any(not isinstance(row, Mapping) or row.get('is_error') is not False for row in outcomes)})
        # 已落定成员仍可能缺工具结果；开始记录只证明尝试，不能推断成功、失败或未执行。
        for identifier in calls - returned:
            event = by_call[identifier]
            rows.append({'call_id': identifier, 'call_seq': event.seq, 'evidence_seq': event.seq,
                         'result_seq': None, 'result_available': False,
                         'name': event.data.get('name'), 'is_error': None})
        rows = sorted((row for row in rows if row['evidence_seq'] > after), key=lambda row: row['evidence_seq'])
        more = len(rows) > 20
        rows = rows[:20]
        return {**identity, 'tools': rows, 'has_more': more,
                'next_after': rows[-1]['evidence_seq'] if more else None}

    return ToolDefinition({'name': 'member_evidence', 'description':
        tr('member.evidence.description'), 'description_key': 'member.evidence.description',
        'parameter_keys': {('properties', 'call_id', 'description'): 'member.evidence.call_description'},
        'parameters': {'type': 'object', 'properties': {
            'agent_id': {'type': 'string', 'minLength': 1},
            'call_id': {'type': 'string', 'minLength': 1, 'description': tr('member.evidence.call_description')},
            'after': {'type': 'integer', 'minimum': -1},
            'offset': {'type': 'integer', 'minimum': 0},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 8192},
            'query': {'type': 'string', 'minLength': 1, 'maxLength': 256}},
            'required': ['agent_id'], 'additionalProperties': False}}, execute,
        effect='read', idempotent=True, category='session')

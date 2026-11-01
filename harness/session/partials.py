"""仅供展示的中断响应证据，不作为模型消息。"""
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from ..core.jsonutil import deep_freeze


def is_response_evidence(event: Any) -> bool:
    """选择不可变恢复证据，不索引普通历史。"""
    if event.type not in {'note', 'request/attempt', 'assistant/message'}:
        return False
    data = event.data
    if not isinstance(data, Mapping):
        return False
    if event.type == 'note':
        return isinstance(data.get('kind'), str) and data['kind'] in {'assistant_checkpoint', 'assistant_partial'}
    return isinstance(data.get('request_id'), str) and (
        event.type == 'assistant/message' or data.get('status') == 'started')


def is_partial_response(event: Any) -> bool:
    if event.type != 'note':
        return False
    data = event.data
    return (isinstance(data, Mapping)
            and data.get('kind') == 'assistant_partial'
            and isinstance(data.get('status'), str) and data['status'] in {'stopped', 'failed', 'interrupted'}
            and any(isinstance(data.get(key), str) and data[key] for key in ('text', 'thinking')))


def project_partial_response(event: Any) -> dict[str, Any]:
    data = event.data
    return {'kind': 'PartialAssistantResponse', 'role': 'assistant',
            'text': data.get('text', '') if isinstance(data.get('text'), str) else '',
            'thinking': data.get('thinking', '') if isinstance(data.get('thinking'), str) else '',
            'partial': data['status'], 'model': data.get('model'),
            'request_id': data.get('request_id'), 'turn': data.get('turn'),
            'recovered_checkpoint': data.get('recovered_checkpoint') is True,
            'checkpoint_gap': data.get('checkpoint_gap') is True,
            'source_seq': event.seq, 'source_time': event.time,
            'tool_uses': [], 'usage': None,
            **({'author': dict(data['author'])} if isinstance(data.get('author'), Mapping) else {}),
            **({'speaker': dict(data['speaker'])} if isinstance(data.get('speaker'), Mapping) else {})}


def resolved_response_ids(events: Any) -> set[str]:
    return {event.data['request_id'] for event in events
            if (event.type == 'assistant/message' or is_partial_response(event))
            and isinstance(event.data, Mapping)
            and isinstance(event.data.get('request_id'), str)}


def partial_response_events(events: Any, *, active_turn: int | None = None,
                            resolved: set[str] | None = None) -> list[Any]:
    """将只追加的片段检查点合并为一个可恢复响应。

    检查点偏移量以 Unicode 码点计。只接受具有真实请求开始锚点的连续片段，
    不能猜测无效缺口的内容。完成或终态证据会屏蔽对应检查点，
    即使固定边界的历史页结束于完成记录之前也如此。当前实时草稿
    由实时通道渲染，不重复展示为中断记录。
    """
    # 解码片段前先判断完成状态，已结束的流无需重建。
    # 固定边界的历史页面也须识别边界之后的完成记录。
    finished = resolved_response_ids(events) if resolved is None else set(resolved)
    requests: dict[str, Any] = {}
    pending: dict[str, dict[str, Any]] = {}
    partials = []
    for event in events:
        if event.type not in {'note', 'request/attempt', 'assistant/message'}:
            continue
        data = event.data
        if not isinstance(data, Mapping):
            continue
        terminal = is_partial_response(event)
        if terminal:
            partials.append(event)
        if not isinstance(data.get('request_id'), str):
            continue
        rid = data['request_id']
        if terminal or event.type == 'assistant/message':
            finished.add(rid)
            pending.pop(rid, None)
            requests.pop(rid, None)
            continue
        if rid in finished:
            continue
        if event.type == 'request/attempt' and data.get('status') == 'started':
            requests[rid] = event
        if event.type != 'note' or data.get('kind') != 'assistant_checkpoint':
            continue
        request = requests.get(rid)
        if (request is None or type(data.get('request_seq')) is not int or data['request_seq'] != request.seq
                or type(data.get('turn')) is not int or data['turn'] != request.data.get('turn')
                or type(data.get('step')) is not int or data['step'] != request.data.get('step')):
            continue
        state = pending.setdefault(rid, {'text': [], 'thinking': [], 'text_end': 0, 'thinking_end': 0, 'broken': False})
        if state['broken']:
            continue
        valid = all(isinstance(data.get(key), str) and type(data.get(key + '_offset')) is int
                    and data[key + '_offset'] == state[key + '_end'] for key in ('text', 'thinking'))
        if not valid:
            state['broken'] = True
            continue
        for key in ('text', 'thinking'):
            state[key].append(data[key])
            state[key + '_end'] += len(data[key])
        state['event'] = event
    for rid, state in pending.items():
        event = state.get('event')
        if event is None or event.data.get('turn') == active_turn:
            continue
        text, thinking = ''.join(state['text']), ''.join(state['thinking'])
        if text or thinking:
            partials.append(replace(event, data=deep_freeze({
                'kind': 'assistant_partial', 'status': 'interrupted', 'text': text, 'thinking': thinking,
                'request_id': rid, 'turn': event.data.get('turn'), 'model': event.data.get('model'),
                'recovered_checkpoint': True, 'checkpoint_gap': state['broken'],
                **({'speaker': dict(event.data['speaker'])} if isinstance(event.data.get('speaker'), Mapping) else {}),
            })))
    return sorted(partials, key=lambda event: event.seq)


def member_partial_reply(session: Any, agent_id: str) -> dict[str, Any] | None:
    """从当前回合的持久片段生成成员交付，不将内存预览当正式结果。"""
    if session is None:
        return None
    for event in reversed(partial_response_events(getattr(session, 'response_evidence', ()))):
        if event.data.get('turn') != session.current_turn:
            continue
        text = event.data.get('text')
        if isinstance(text, str) and text.strip():
            return {'text': text, 'partial': event.data['status'],
                    'partial_request_id': f"member-partial:{agent_id}:{event.data.get('request_id', event.seq)}",
                    'member_response_seq': event.seq, 'context_session_id': session.id,
                    'checkpoint_gap': event.data.get('checkpoint_gap') is True}
    return None

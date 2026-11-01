"""按显式调用身份关联请求输入与发送时事件，不以正文猜测。"""
from collections import defaultdict

from ..llm.request_input import request_input
from .session import project_surface


def prompt_input_sources(body, session, checkpoint, *, surface=None):
    """仅在归档正文与发送边界策略完全一致时返回分段大小排行。"""
    through = checkpoint.get('event_seq') if isinstance(checkpoint, dict) else None
    identity = checkpoint.get('system_seq') if isinstance(checkpoint, dict) else None
    if type(through) is not int or type(identity) is not int or not 0 <= identity <= through:
        return {}
    if surface is None:
        surface = project_surface(event for event in session.events if event.seq <= through)
    event = next((event for event in surface if event.seq == identity and event.type == 'system/message'), None)
    if event is None:
        return {}
    texts = {'stable': event.data.get('cache_prefix', event.data.get('text', '')),
             'runtime': event.data.get('runtime_text', '')}
    sections = {}
    for part, text in texts.items():
        if not isinstance(text, str) or not text:
            continue
        offset, rows = 0, []
        for span in event.data.get('context_sources', {}).get(part, ()):
            start, end = span.get('start'), span.get('end')
            if type(start) is not int or type(end) is not int or start != offset or not start <= end <= len(text):
                return {}
            chunk = text[start:end]
            rows.append({'event_seq': identity, 'source': span['source'], 'part': part,
                         'chars': len(chunk), 'bytes': len(chunk.encode('utf-8'))})
            offset = end
        if offset == len(text):
            sections[part] = rows
    inputs = request_input(body)
    output = {}
    if 'messages' not in body:
        expected = '\n\n'.join(text for text in texts.values() if text)
        candidates = [(index, list(sections.values())) for index, (_, value, path) in enumerate(inputs)
                      if path == '/instructions' and value == expected]
    else:
        candidates = []
        for part, rows in sections.items():
            if texts['stable'] == texts['runtime']:
                continue
            roles = {'system'} if part == 'stable' else {'system', 'developer'}
            matches = [index for index, (actual_role, value, _) in enumerate(inputs)
                       if actual_role in roles and isinstance(value, dict) and value.get('content') == texts[part]]
            if len(matches) == 1:
                candidates.append((matches[0], [rows]))
    for index, groups in candidates:
        rows = [row for group in groups for row in group]
        if rows:
            output[index] = {'prompt_sources': sorted(rows, key=lambda row: -row['bytes'])[:8],
                             'prompt_source_count': len(rows)}
    return output


def tool_input_sources(body, session, checkpoint, *, surface=None):
    """只返回唯一匹配的工具调用／结果；缺失边界和歧义均不推断。"""
    through = checkpoint.get('event_seq') if isinstance(checkpoint, dict) else None
    if type(through) is not int or through < 0:
        return {}
    if surface is None:
        surface = project_surface(event for event in session.events if event.seq <= through)
    calls, results = defaultdict(list), defaultdict(list)
    for event in surface:
        if event.type == 'assistant/message':
            for block in event.data.get('content', ()):
                if block.get('type') == 'tool_use':
                    calls[block.get('id')].append((event, block.get('name')))
        elif event.type == 'tool/result':
            for result in event.data.get('results', ()):
                results[result.get('tool_use_id')].append(event)
    output = {}
    for index, (_, item, _) in enumerate(request_input(body)):
        if not isinstance(item, dict):
            continue
        if item.get('type') in {'function_call', 'function_call_output'}:
            identities = [(item.get('call_id'), item['type'] == 'function_call_output')]
        elif item.get('role') == 'tool':
            identities = [(item.get('tool_call_id'), True)]
        else:
            identities = [(call.get('id'), False) for call in item.get('tool_calls', ())]
        sources = []
        for identity, result in identities[:8]:
            if not isinstance(identity, str) or len(calls[identity]) != 1:
                continue
            call_event, name = calls[identity][0]
            if not isinstance(name, str) or not name:
                continue
            if result:
                if len(results[identity]) != 1:
                    continue
                event = results[identity][0]
            else:
                event = call_event
            sources.append({'call_id': identity, 'tool': name, 'call_seq': call_event.seq, 'event_seq': event.seq,
                            'kind': 'result' if result else 'call'})
        if sources:
            output[index] = sources
    return output

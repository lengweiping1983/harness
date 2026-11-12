"""有界读取归档的传输请求，不重建过去的提示词。"""

from harness.i18n import tr
import json

from .errors import BadRequest, NotFound
from ..llm.request_input import request_input


CHUNK_CHARS = 24000


def read_trace(archive, session_id, trace_id, *, part=None, index=0, offset=0, sessions=None):
    from .read_work import shared_read
    # 取消 HTTP 等待不会停止线程；重复读取仍共享同一在途计算。
    return shared_read(archive, (session_id, trace_id, part, index, offset, id(sessions)),
                       lambda: _read_trace(archive, session_id, trace_id, part=part,
                                           index=index, offset=offset, sessions=sessions))


def _read_trace(archive, session_id, trace_id, *, part=None, index=0, offset=0, sessions=None):
    record = archive.read(session_id, trace_id)
    if record is None:
        raise NotFound(tr('python.request_traces.16ee3070e2fa'))
    row, body = record
    if offset < 0:
        raise BadRequest(tr('python.request_traces.f9cba1b06d7d'))
    if part == 'body':
        # 原始正文分页不应解析全部 JSON、重建输入或执行诊断。
        return _text_page(body, offset)
    parsed = json.loads(body)
    inputs = request_input(parsed) if part in {None, 'summary', 'message'} else ()
    if part is None or part == 'summary':
        from ..llm.request_composition import request_composition
        from ..llm.prompt_cache import compare_prefix
        previous = archive.previous(row) if part is None else None
        comparison = None
        if previous is not None:
            comparison = {'previous_id': previous[0]['id'], **compare_prefix(json.loads(previous[1]), parsed,
                same_endpoint=previous[0]['endpoint'] == row['endpoint'])}
        messages = []
        sources = {}
        prompts = {}
        if sessions is not None and part is None:
            from ..session.request_sources import prompt_input_sources, tool_input_sources
            from ..session.session import project_surface
            owner = sessions.get(row['session_id'])
            checkpoint = row.get('context_checkpoint')
            through = checkpoint.get('event_seq') if isinstance(checkpoint, dict) else None
            surface = project_surface(event for event in owner.events if event.seq <= through) if type(through) is int and through >= 0 else []
            sources = tool_input_sources(parsed, owner, checkpoint, surface=surface)
            prompts = prompt_input_sources(parsed, owner, checkpoint, surface=surface)
        for i, (role, message, _) in enumerate(inputs):
            text = json.dumps(message, ensure_ascii=False)
            content = message.get('content', message) if isinstance(message, dict) else message
            if isinstance(message, dict) and message.get('type') in {'reasoning', 'compaction'}:
                content = {'type': message['type'], 'id': message.get('id')}
            preview = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            messages.append({'index': i, 'role': role,
                             'chars': len(text), 'preview': preview[:120], 'tool_sources': sources.get(i, []),
                             **prompts.get(i, {'prompt_sources': [], 'prompt_source_count': 0})})
        tool_definitions = []
        for tool_index, tool in enumerate(parsed.get('tools', [])):
            function = tool.get('function', {}) if isinstance(tool, dict) else {}
            if not isinstance(function, dict):
                function = {}
            tool_definitions.append({
                'index': tool_index,
                'name': function.get('name') or (tool.get('name') if isinstance(tool, dict) else '') or (tool.get('type', '') if isinstance(tool, dict) else ''),
                'description': function.get('description') or (tool.get('description', '') if isinstance(tool, dict) else ''),
            })
        return {'request': row, 'messages': messages, 'cache_comparison': comparison,
                'composition': request_composition(parsed),
                'tool_names': [tool['name'] for tool in tool_definitions],
                'tool_definitions': tool_definitions,
                'parameters': {k: v for k, v in parsed.items() if k not in {'messages', 'tools', 'input', 'instructions'}}}
    if part == 'message':
        if index < 0 or index >= len(inputs):
            raise BadRequest(tr('python.request_traces.7c079289002f'))
        text = json.dumps(inputs[index][1], ensure_ascii=False, indent=2)
    elif part == 'tools':
        text = json.dumps(parsed.get('tools', []), ensure_ascii=False, indent=2)
    elif part == 'tool':
        tools = parsed.get('tools', [])
        if index < 0 or index >= len(tools):
            raise BadRequest(tr('request.trace.invalidTool'))
        text = json.dumps(tools[index], ensure_ascii=False, indent=2)
    elif part == 'body_formatted':
        text = json.dumps(parsed, ensure_ascii=False, indent=2)
    else:
        raise BadRequest(tr('python.request_traces.d2bcd2148a66'), code='request-trace/unsupported-part',
                         details={'part': part, 'supported': ['summary', 'body', 'body_formatted', 'message', 'tools', 'tool']})
    return _text_page(text, offset)


def _text_page(text, offset):
    """按 Unicode 字符分页，保留既有范围与总数语义。"""
    end = min(len(text), offset + CHUNK_CHARS)
    return {'text': text[offset:end], 'offset': offset, 'total_chars': len(text),
            'next_offset': end if end < len(text) else None}

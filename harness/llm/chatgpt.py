"""ChatGPT Codex Responses 与 Harness 聊天流之间的协议转换。"""
from __future__ import annotations

import json
from contextlib import aclosing

from ..core.errors import LlmResponseError
from ..i18n import tr
from .transport import parse_sse_stream


def responses_body(body):
    """无状态 Responses 请求约束。"""
    items, instructions = [], []
    for message in body['messages']:
        role, content = message['role'], message.get('content')
        if role in {'system', 'developer'}:
            instructions.append(content if isinstance(content, str) else '\n'.join(p.get('text', '') for p in content or []))
            continue
        if role == 'tool':
            items.append({'type': 'function_call_output', 'call_id': message['tool_call_id'], 'output': content or ''})
            continue
        if role == 'assistant':
            for detail in message.get('reasoning_details', []):
                if detail.get('type') == 'response.reasoning':
                    item = detail.get('item')
                    if (not isinstance(item, dict) or item.get('type') != 'reasoning'
                            or not isinstance(item.get('encrypted_content'), str)
                            or not isinstance(item.get('id'), str) or not item['id']):
                        raise LlmResponseError(tr('chatgpt.protocol.reasoning_invalid'), code='llm/chatgpt-reasoning')
                    items.append(item)
        parts = []
        for part in ([{'type': 'text', 'text': content}] if isinstance(content, str) else content or []):
            if part.get('type') == 'text':
                parts.append({'type': 'output_text' if role == 'assistant' else 'input_text', 'text': part['text']})
            elif part.get('type') == 'image_url':
                parts.append({'type': 'input_image', 'image_url': part['image_url']['url']})
            else:
                raise LlmResponseError(tr('chatgpt.protocol.content_unsupported'), code='llm/chatgpt-content')
        if parts:
            items.append({'role': role, 'content': parts})
        for call in message.get('tool_calls', []):
            items.append({'type': 'function_call', 'call_id': call['id'], 'name': call['function']['name'],
                          'arguments': call['function']['arguments']})
    result = {'model': body['model'].removeprefix('chatgpt/'), 'instructions': '\n\n'.join(instructions),
              'input': items, 'stream': True, 'store': False, 'parallel_tool_calls': True,
              'include': ['reasoning.encrypted_content']}
    if body.get('tools'):
        # Responses 省略 strict 时会尝试把可选字段变成必填；保留聊天工具的省略语义。
        # 调用方显式声明的严格模式仍按原值传递，宿主参数校验始终独立执行。
        result['tools'] = [{'type': 'function', 'strict': False, **tool['function']} for tool in body['tools']]
        result['tool_choice'] = 'auto'
    if body.get('reasoning'):
        result['reasoning'] = {k: v for k, v in body['reasoning'].items() if k in {'effort', 'summary'}}
    if body.get('prompt_cache_key'):
        result['prompt_cache_key'] = body['prompt_cache_key']
    return result


async def chat_chunks(raw):
    """把 Responses 增量转换为既有消费器，沿用超时、计量和输出限制。"""
    calls = {}
    preserved = set()

    def reasoning_delta(item):
        # 只保存服务端完成的加密状态；展示摘要由原来的增量路径处理。
        if item.get('type') != 'reasoning' or not isinstance(item.get('encrypted_content'), str):
            return None
        identity = item.get('id')
        if not isinstance(identity, str) or not identity:
            raise LlmResponseError(tr('chatgpt.protocol.reasoning_identity'), code='llm/chatgpt-reasoning')
        if identity in preserved:
            return None
        preserved.add(identity)
        return {'reasoning_details': [{'type': 'response.reasoning', 'item': item}]}

    async with aclosing(raw):
        async for event in parse_sse_stream(raw):
            kind = event.get('type')
            delta = None
            if kind == 'response.output_text.delta':
                delta = {'content': event.get('delta', '')}
            elif kind in {'response.reasoning_summary_text.delta', 'response.reasoning_text.delta'}:
                delta = {'reasoning_content': event.get('delta', '')}
            elif kind == 'response.output_item.done' and event.get('item', {}).get('type') == 'reasoning':
                delta = reasoning_delta(event['item'])
            elif kind == 'response.output_item.added' and event.get('item', {}).get('type') == 'function_call':
                item = event['item']
                index = event['output_index']
                calls[index] = {'arguments': bool(item.get('arguments'))}
                delta = {'tool_calls': [{'index': index, 'id': item['call_id'],
                         'function': {'name': item['name'], 'arguments': item.get('arguments', '')}}]}
            elif kind == 'response.function_call_arguments.delta':
                index = event['output_index']
                calls.setdefault(index, {})['arguments'] = True
                delta = {'tool_calls': [{'index': index, 'function': {'arguments': event.get('delta', '')}}]}
            elif kind == 'response.output_item.done' and event.get('item', {}).get('type') == 'function_call':
                item, index = event['item'], event['output_index']
                if index not in calls:
                    delta = {'tool_calls': [{'index': index, 'id': item['call_id'], 'function': {'name': item['name'], 'arguments': item.get('arguments', '{}')}}]}
                elif not calls[index].get('arguments'):
                    delta = {'tool_calls': [{'index': index, 'function': {'arguments': item.get('arguments', '{}')}}]}
                calls.setdefault(index, {})['arguments'] = True
            elif kind in {'response.completed', 'response.incomplete'}:
                response = event.get('response', {})
                for item in response.get('output', []):
                    extra = reasoning_delta(item)
                    if extra is not None:
                        yield ('data: ' + json.dumps({'choices': [{'delta': extra}]}) + '\n\n').encode()
                reason = 'length' if kind == 'response.incomplete' else 'tool_calls' if calls else 'stop'
                packet = {'choices': [{'delta': {}, 'finish_reason': reason}]}
                if response.get('usage'):
                    packet['usage'] = response['usage']
                yield ('data: ' + json.dumps(packet) + '\n\n').encode()
                return
            elif kind in {'response.failed', 'error'}:
                response = event.get('response', {})
                error = response.get('error') or event.get('error') or event
                code = error.get('code', '') if isinstance(error, dict) else ''
                if code in {'context_length_exceeded', 'context_window_exceeded'}:
                    from ..core.errors import LlmContextLengthError
                    raise LlmContextLengthError(tr('chatgpt.protocol.context_full'), status=400)
                raise LlmResponseError(tr('chatgpt.protocol.request_failed'), code='llm/chatgpt-response')
            if delta is not None:
                yield ('data: ' + json.dumps({'choices': [{'delta': delta}]}) + '\n\n').encode()

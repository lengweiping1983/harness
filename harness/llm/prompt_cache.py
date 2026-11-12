"""Chat Completions 传输中按供应商区分的缓存默认值。

不能仅凭模型名猜测代理接口能力。原生 OpenAI 使用自动缓存，
OpenRouter Claude 支持文档规定的 cache_control。
调用方显式选项始终优先，保留时间采用供应商默认值。
"""

import hashlib
import json
from urllib.parse import urlsplit


def apply_prompt_cache(body, base_url, *, mode='auto', session_id=None, headers=None):
    body = dict(body)
    if mode == 'off':
        return body
    host = urlsplit(base_url).hostname
    model = str(body.get('model', '')).lstrip('~')
    if host == 'openrouter.ai' and isinstance(session_id, str) and session_id:
        explicit_header = any(str(key).lower() == 'x-session-id' for key in (headers or {}))
        # 请求体中的 session_id 优先于 x-session-id 和 prompt_cache_key。
        # 不能覆盖调用方显式指定的路由选项，包括显式 null。
        if not explicit_header and 'session_id' not in body and 'prompt_cache_key' not in body:
            body['session_id'] = 'harness:' + hashlib.sha256(session_id.encode('utf-8')).hexdigest()[:32]
    if host == 'openrouter.ai' and model.startswith('anthropic/claude'):
        body.setdefault('cache_control', {'type': 'ephemeral'})
    elif host == 'api.openai.com':
        # 较早的模型通过稳定的工具族共享路由；不能使用随机请求标识、
        # 时间戳或子代理标识使缓存碎片化。
        tools = json.dumps(body.get('tools', []), ensure_ascii=False, sort_keys=True)
        body.setdefault('prompt_cache_key', 'harness:' + hashlib.sha256(tools.encode('utf-8')).hexdigest()[:32])
    if host in {'api.openai.com', 'openrouter.ai'} and body.get('stream'):
        options = dict(body.get('stream_options') or {})
        options.setdefault('include_usage', True)
        body['stream_options'] = options
    return body


def _shared_text_prefix(old, new):
    """按块比较公共前缀，避免大提示词逐字符进入 Python 循环。"""
    end = min(len(old), len(new))
    position = 0
    block = 65536
    while position < end:
        stop = min(end, position + block)
        if old[position:stop] != new[position:stop]:
            low, high = position, stop
            while low < high:
                middle = (low + high + 1) // 2
                if old[position:middle] == new[position:middle]:
                    low = middle
                else:
                    high = middle - 1
            return low
        position = stop
    return end


def _first_difference(old, new, path='', depth=0):
    if depth >= 32:
        return None if json.dumps(old, ensure_ascii=False) == json.dumps(new, ensure_ascii=False) else {'path': path[:512], 'kind': 'container'}
    if type(old) is not type(new):
        return {'path': path[:512], 'kind': 'type'}
    if isinstance(old, dict):
        if list(old) != list(new):
            return {'path': path[:512], 'kind': 'key_order' if old.keys() == new.keys() else 'keys'}
        for key in old:
            escaped = key.replace('~', '~0').replace('/', '~1')
            diff = _first_difference(old[key], new[key], path + '/' + escaped, depth + 1)
            if diff:
                return diff
    elif isinstance(old, list):
        for index, (left, right) in enumerate(zip(old, new)):
            diff = _first_difference(left, right, path + '/' + str(index), depth + 1)
            if diff:
                return diff
        if len(old) != len(new):
            return {'path': (path + '/' + str(min(len(old), len(new))))[:512], 'kind': 'length'}
    elif old != new:
        diff = {'path': path[:512], 'kind': 'value'}
        if isinstance(old, str):
            diff['text_offset'] = _shared_text_prefix(old, new)
        return diff
    return None


def compare_prefix(previous, current, *, same_endpoint=True):
    """本地结构证据，不预测账单中的缓存词元数量。"""
    from .request_input import request_input
    changes = []
    if not same_endpoint:
        changes.append('endpoint')
    if previous.get('model') != current.get('model'):
        changes.append('model')
    tool_difference = _first_difference(previous.get('tools', []), current.get('tools', []), '/tools')
    if tool_difference:
        changes.append('tools')
    settings = ('reasoning', 'reasoning_effort', 'thinking', 'output_config', 'temperature', 'top_p',
                'tool_choice', 'response_format', 'prompt_cache_key', 'session_id', 'cache_control', 'prompt_cache_retention', 'provider')
    changed_settings = [key for key in settings if (key in previous) != (key in current)
                        or _first_difference(previous.get(key), current.get(key))]
    if changed_settings:
        changes.append('settings')
    old_inputs, new_inputs = request_input(previous), request_input(current)
    old_messages = [item for _, item, _ in old_inputs]
    new_messages = [item for _, item, _ in new_inputs]
    matched = 0
    for (_, old, old_path), (_, new, new_path) in zip(old_inputs, new_inputs):
        if old_path != new_path or _first_difference(old, new):
            break
        matched += 1
    # 规范化的本地序列化可定位首个变化字符；
    # 供应商自行渲染提示词并分词，其方式可能不同。
    chat = 'messages' in previous and 'messages' in current
    old = json.dumps([previous.get('tools', []), old_messages if chat else [(path, item) for _, item, path in old_inputs]], ensure_ascii=False, separators=(',', ':'))
    new = json.dumps([current.get('tools', []), new_messages if chat else [(path, item) for _, item, path in new_inputs]], ensure_ascii=False, separators=(',', ':'))
    shared = _shared_text_prefix(old, new)
    sequence = ('modified' if matched < min(len(old_messages), len(new_messages)) else
                'appended' if len(new_messages) > len(old_messages) else
                'truncated' if len(new_messages) < len(old_messages) else 'identical')
    difference = None
    if matched < min(len(old_inputs), len(new_inputs)):
        _, old_item, old_path = old_inputs[matched]
        _, new_item, new_path = new_inputs[matched]
        difference = ({'path': new_path, 'kind': 'type'} if old_path != new_path else
                      _first_difference(old_item, new_item, new_path))
    elif len(old_inputs) != len(new_inputs):
        difference = {'path': (new_inputs if len(new_inputs) > matched else old_inputs)[matched][2], 'kind': 'length'}
    return {'shared_messages': matched, 'shared_prefix_chars': shared,
            'first_changed_message': matched if sequence != 'identical' else None,
            'sequence_change': sequence,
            'first_difference': tool_difference or difference,
            'changed_settings': changed_settings,
            'changes': changes}

"""从实际传输体读取有序输入，供只读审计使用。"""


def request_input(body):
    """保留原始字段值和路径，不把 Responses 状态改写成聊天消息。"""
    if 'messages' in body:
        return [(item.get('role', 'unknown'), item, f'/messages/{index}')
                for index, item in enumerate(body['messages'])]
    result = []
    if 'instructions' in body and body['instructions'] is not None:
        result.append(('system', body['instructions'], '/instructions'))
    items = body.get('input', [])
    if isinstance(items, str):
        result.append(('user', items, '/input'))
    else:
        for index, item in enumerate(items):
            kind = item.get('type')
            role = item.get('role') or ('tool' if kind == 'function_call_output' else
                                       'assistant' if kind in {'function_call', 'reasoning', 'compaction'} else 'unknown')
            result.append((role, item, f'/input/{index}'))
    return result

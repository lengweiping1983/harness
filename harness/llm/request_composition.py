"""显式读取请求详情时计算归档结构大小，不进入模型发送热路径。"""
import json

from .request_input import request_input


def _size(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def request_composition(body):
    """按统一 JSON 格式计算可加总的字节；不反推图片原载荷或供应商 token。"""
    inputs = [{'index': index, 'role': role, 'path': path, 'bytes': _size(value)}
              for index, (role, value, path) in enumerate(request_input(body))]
    tools = []
    for index, tool in enumerate(body.get('tools', [])):
        function = tool.get('function', {}) if isinstance(tool, dict) else {}
        if not isinstance(function, dict):
            function = {}
        tools.append({'index': index,
                      'name': function.get('name') or (tool.get('name') if isinstance(tool, dict) else '') or (tool.get('type', '') if isinstance(tool, dict) else ''),
                      'description': function.get('description') or (tool.get('description', '') if isinstance(tool, dict) else ''),
                      'bytes': _size(tool)})
    indexed = {row['path']: row['bytes'] for row in inputs}
    groups = {'input': 0, 'tools': 0, 'parameters': 0, 'envelope': 2}
    for key, value in body.items():
        if key == 'tools':
            size = 2 + sum(row['bytes'] for row in tools) + 2 * max(0, len(tools) - 1)
        elif key in {'messages', 'input'} and isinstance(value, list) and all(f'/{key}/{index}' in indexed for index in range(len(value))):
            size = 2 + sum(indexed[f'/{key}/{index}'] for index in range(len(value))) + 2 * max(0, len(value) - 1)
        else:
            size = indexed.get('/' + key)
            if size is None:
                size = _size(value)
        group = 'input' if key in {'messages', 'input', 'instructions'} else 'tools' if key == 'tools' else 'parameters'
        groups[group] += size
        groups['envelope'] += _size(key) + 2
    groups['envelope'] += 2 * max(0, len(body) - 1)
    return {'normalized_bytes': sum(groups.values()),
            'groups': [{'kind': key, 'bytes': value} for key, value in groups.items()],
            'largest_inputs': inputs,
            'largest_tools': sorted(tools, key=lambda row: (-row['bytes'], row['index']))[:8]}

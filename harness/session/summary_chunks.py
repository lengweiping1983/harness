"""有界的逐行 JSON 交接，为超大记录显式保留来源。"""

from harness.i18n import tr

import json
from ..core.jsonutil import to_jsonable


def summary_record(seq, kind, data):
    """紧凑编码结构空白，字符串内部的空白和全部字段保持不变。"""
    return json.dumps(to_jsonable({'seq': seq, 'kind': kind, 'data': data}),
                      ensure_ascii=False, separators=(',', ':'))


def summary_chunks(lines, max_chars):
    """保留完整记录，只将超大数据拆为 JSON 片段。

    偏移量是序列化 ``data`` 中的 Unicode 字符数，不是词元数或字节数。
    每个片段都是有效 JSON，保留原始角色和来源标识。
    ``record_index`` 用于区分同一事件中的不同数据块。
    """
    if type(max_chars) is not int or max_chars < 1:
        raise ValueError(tr('python.summary_chunks.96c7da08c16e'))
    chunks, pending, size = [], [], 0

    def append(line):
        nonlocal pending, size
        if pending and size + 1 + len(line) > max_chars:
            chunks.append('\n'.join(pending))
            pending, size = [], 0
        size += len(line) + bool(pending)
        pending.append(line)

    for record_index, line in enumerate(lines):
        if len(line) <= max_chars:
            append(line)
            continue
        record = json.loads(line)
        data = record['data']
        serialized = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        identity = {key: data[key] for key in ('tool_use_id', 'is_error', 'id', 'name', 'source') if key in data}
        offset = 0
        while offset < len(serialized):
            def fragment(end):
                return json.dumps({'seq': record['seq'], 'kind': record['kind'], 'record_index': record_index,
                    'identity': identity, 'data_json_fragment': {'offset': offset, 'end': end,
                        'total_chars': len(serialized), 'text': serialized[offset:end]}}, ensure_ascii=False, separators=(',', ':'))

            # JSON 转义同样消耗预算；若先切分原始字符并仅检查长度，
            # 引号和换行会导致预算超限。
            low, high = offset, min(len(serialized), offset + max_chars)
            while low < high:
                middle = (low + high + 1) // 2
                if len(fragment(middle)) <= max_chars:
                    low = middle
                else:
                    high = middle - 1
            if low == offset:
                raise ValueError(tr('python.summary_chunks.bf1d8aea55ff'))
            append(fragment(low))
            offset = low
    if pending:
        chunks.append('\n'.join(pending))
    return chunks

"""对话工具结果的轻量投影与只读原文分页，原始事件不改写。"""
from harness.i18n import tr

import hashlib
from collections.abc import Mapping

from ..llm.vision import IMAGE_TOOL_NAMES
from .errors import BadRequest, Conflict, NotFound

INLINE_CHARS = 16384


def project_result(result, *, event_seq, index=0, name=None):
    """只折叠普通长文本；交互、图片和文件差异保持既有结构。"""
    value = dict(result)
    text = value.get('content')
    if (not isinstance(text, str) or len(text) <= INLINE_CHARS or type(event_seq) is not int or event_seq < 0
            or name in {*IMAGE_TOOL_NAMES, 'ask_user_question', 'edit', 'write'}
            or 'data:image/' in text):
        return value
    value['content'] = text[:1024] + '\n…\n' + text[-256:]
    value['content_ref'] = {'event_seq': event_seq, 'result_index': index,
                            'total_chars': len(text), 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}
    return value


def read_result(session, event_seq, *, index=0, offset=0, limit=8192, expected_hash=None, query=None):
    """按不可变事件和结果序号取原文，不以同名工具或重复调用标识猜测。"""
    if (type(event_seq) is not int or event_seq < 0 or type(index) is not int or index < 0
            or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 8192):
        raise BadRequest(tr('python.catalog.validation.toolRange'), code='tool-result/invalid-range')
    event = session.event_at(event_seq)
    if event is None or event.type != 'tool/result':
        raise NotFound(tr('python.catalog.validation.toolEvidence'), code='tool-result/missing')
    values = event.data.get('results', ())
    if index >= len(values) or not isinstance(values[index], Mapping) or not isinstance(values[index].get('content'), str):
        raise NotFound(tr('python.catalog.validation.toolTextEvidence'), code='tool-result/missing')
    text = values[index]['content']
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    if expected_hash is not None and expected_hash != digest:
        raise Conflict(tr('python.catalog.validation.toolEvidenceChanged'), code='tool-result/changed')
    if offset > len(text):
        raise BadRequest(tr('python.catalog.validation.toolOffset'), code='tool-result/invalid-range')
    match = None
    if query is not None:
        if not isinstance(query, str) or not 1 <= len(query) <= min(256, limit):
            raise BadRequest(tr('python.catalog.validation.toolSearch'), code='tool-result/invalid-query')
        found = text.find(query, offset)
        match = found if found >= 0 else None
        if found >= 0:
            # 上下文不能挤出命中文本，也不能越过调用方的搜索游标回退。
            offset = max(offset, found - min(256, (limit - len(query)) // 2))
    end = min(len(text), offset + limit)
    return {'event_seq': event_seq, 'result_index': index, 'sha256': digest, 'total_chars': len(text),
            'offset': offset, 'next_offset': end if end < len(text) and not (query is not None and match is None) else None,
            'text': '' if query is not None and match is None else text[offset:end],
            'is_error': bool(values[index].get('is_error')), 'match_offset': match}

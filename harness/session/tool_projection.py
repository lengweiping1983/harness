"""首次提交时固定工具结果的模型投影；完整证据保留在原始事件中。"""
from __future__ import annotations

import hashlib
import json
import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

from ..llm.vision import IMAGE_TOOL_NAMES
from ..i18n import tr

DEFAULT_TOOL_RESULT_MAX_CHARS = 12000


@dataclass(frozen=True)
class ToolProjectionChange:
    """一次已预检的单节点替换，不更改调用及错误身份。"""

    seq: int
    generation: int
    data: dict
    results: int
    chars_before: int
    chars_after: int


def plan_tool_result_projections(session, max_chars: int | None) -> tuple[ToolProjectionChange, ...]:
    """预检当前表面已有结果；只缩小，不还原或扩张已精简的模型文本。"""
    if max_chars is None:
        return ()
    if type(max_chars) is not int or max_chars < 512:
        raise ValueError(tr('tool.result.ef15eeb94ce3'))
    names = {}
    for event in session.events:
        if event.type == 'assistant/message':
            for block in event.data.get('content', ()):
                if block.get('type') == 'tool_use':
                    names[block['id']] = block.get('name')
    changes = []
    for event in session.surface:
        if event.type != 'tool/result':
            continue
        results, count, before, after = [], 0, 0, 0
        for item in event.data.get('results', ()):
            current = model_result_content(item)
            call_id = item.get('tool_use_id')
            roots = session.tool_results.origins(call_id)
            name = names.get(call_id) or event.data.get('name')
            if (isinstance(current, str) and len(current) > max_chars and len(roots) == 1
                    and isinstance(name, str) and isinstance(roots[0][1].get('content'), str)):
                original = roots[0][1]['content']
                projection = project_tool_result(original, name=name, call_id=call_id, max_chars=max_chars)
                if projection and len(projection['model_content']) < len(current):
                    before += len(current)
                    after += len(projection['model_content'])
                    count += 1
                    item = {**item, 'content': original, **projection}
            results.append(item)
        if count:
            changes.append(ToolProjectionChange(event.seq, session.history_generation,
                {**event.data, 'results': results}, count, before, after))
    return tuple(changes)


async def apply_tool_result_projections(session, changes: tuple[ToolProjectionChange, ...], *, check_cancelled=None) -> None:
    """逐节点追加替换证据；取消或失败后的已提交节点仍然可安全恢复。"""
    from ..core.errors import SessionInvariantError
    from .events import ReplaceOp
    for change in changes:
        if check_cancelled is not None:
            check_cancelled()
        if session.history_generation != change.generation or change.seq not in {event.seq for event in session.surface}:
            raise SessionInvariantError(tr('tool.result.8162dd2236cf'))
        await session.append('compaction/prune', {'shadowed_range': {'start': change.seq, 'end': change.seq},
            'shadowed_seqs': [change.seq], 'chars_removed': change.chars_before - change.chars_after,
            'stage': 'tool-result-projection'})
        await session.append('tool/result', change.data, surface_op=ReplaceOp(change.seq, change.seq),
                             source_event_seqs=[change.seq])
        # 成对提交后主动让出调度，在下一节点开始前采用普通停止标志。
        await asyncio.sleep(0)


def project_tool_result(text: str, *, name: str, call_id: str,
                        max_chars: int | None = DEFAULT_TOOL_RESULT_MAX_CHARS) -> dict:
    """为超限文本保留首尾与精确取回入口，不更改原始内容及错误状态。"""
    if max_chars is not None and (type(max_chars) is not int or max_chars < 512):
        raise ValueError(tr('tool.result.ef15eeb94ce3'))
    if max_chars is None or len(text) <= max_chars or name in {*IMAGE_TOOL_NAMES, 'skill', 'read_tool_result'}:
        return {}
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    marker = tr('tool.projection.marker', count=len(text), call_id=json.dumps(call_id), digest=digest)
    available = max_chars - len(marker)
    if available < 2:
        # 异常长的调用身份仍可由工具结果的配对关系取回，不能撑破预算。
        marker = tr('tool.projection.short_marker', digest=digest)
        available = max_chars - len(marker)
    head = available // 2
    projected = text[:head] + marker + text[-(available - head):]
    return {'model_content': projected, 'projection': {
        'original_chars': len(text), 'model_chars': len(projected), 'sha256': digest}}


def model_result_content(item: Mapping):
    """所有模型装配与摘要统一读取已经提交的投影，不重新加载原件。"""
    if 'model_content' in item and not isinstance(item['model_content'], str):
        raise ValueError(tr('tool.result.21c67c25b899'))
    return item.get('model_content', item.get('content', ''))


def tool_projection_statistics(events, messages) -> dict:
    """按同一事件／模型消息快照汇总文本大小，不冒充供应商 token。"""
    from ..llm.types import ToolResultMessage
    originals = {}
    for event in events:
        if event.type == 'tool/result':
            for item in event.data.get('results', ()):
                if event.data.get('name') not in IMAGE_TOOL_NAMES and isinstance(item.get('content'), str):
                    originals.setdefault(item['tool_use_id'], len(item['content']))
    result = {'results': 0, 'abbreviated_results': 0, 'original_chars': 0, 'model_chars': 0}
    for message in messages:
        if isinstance(message, ToolResultMessage):
            for item in message.results:
                if item.tool_use_id not in originals or not isinstance(item.content, str):
                    continue
                original, current = originals[item.tool_use_id], len(item.content)
                result['results'] += 1
                result['abbreviated_results'] += current < original
                result['original_chars'] += original
                result['model_chars'] += current
    return result

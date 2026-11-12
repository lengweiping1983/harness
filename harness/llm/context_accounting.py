"""请求输入的统一分类估算；估算值不是供应商计量。"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar

from ..core.jsonutil import to_jsonable
from .types import AssistantMessage, DeveloperMessage, ImageBlock, SystemMessage, ThinkingBlock, ToolResultMessage, UserMessage
from .vision import IMAGE_INPUT_TOKEN_ESTIMATE, project_image_result, omit_image_data, image_projection_plan, reused_image_result


CJK = re.compile(r'[一-鿿]+')
_scan_cache = ContextVar('context_token_scan_cache', default=None)


@contextmanager
def token_estimate_scan():
    """一次候选扫描内复用长文本计数；退出即释放，不常驻用户正文。"""
    token = _scan_cache.set([{}, 0])
    try:
        yield
    finally:
        _scan_cache.reset(token)


def _count_text(text):
    """缓存限制为 256 段、合计两百万字符，超出后继续准确计算。"""
    if len(text) <= 8192:
        return _small_text_tokens(text)
    scope = _scan_cache.get()
    if scope is not None and text in scope[0]:
        return scope[0][text]
    tokens = text_tokens(text)
    if scope is not None and len(scope[0]) < 256 and scope[1] + len(text) <= 2_000_000:
        scope[0][text] = tokens
        scope[1] += len(text)
    return tokens


def text_tokens(text: str) -> int:
    """沿用本地中英文启发式，不解析或计算图片编码长度。"""
    cjk = sum(match.end() - match.start() for match in CJK.finditer(text))
    return int(cjk / 1.5) + (len(text) - cjk) // 4


@lru_cache(maxsize=2048)
def _small_text_tokens(text):
    """仅缓存小片段，限制长期会话的缓存保留量。"""
    return text_tokens(text)


def reasoning_mode(adapter):
    """未知自定义适配器保守计入全部状态，正式适配器声明传输口径。"""
    return adapter.context_reasoning_mode() if callable(getattr(adapter, 'context_reasoning_mode', None)) else 'all'


def estimate_tokens(messages, *, reasoning='all') -> int:
    """从统一来源投影取预算估算，思考口径由调用方明确选择。"""
    return context_composition(messages, reasoning=reasoning)['estimated_tokens']


def context_composition(messages, *, tools=(), runtime=(), reasoning='all'):
    """同一遍遍历生成预算和来源投影，不返回正文或思考内容。"""
    messages = tuple(messages)
    image_results, retained_images, repeated_images = image_projection_plan(messages)
    groups = defaultdict(lambda: {'tokens': 0, 'items': 0})
    call_sources = {}

    def add(category, source, text='', images=0, tokens=None):
        row = groups[(category, source)]
        row['tokens'] += (tokens if tokens is not None else _count_text(text)) + images * IMAGE_INPUT_TOKEN_ESTIMATE
        row['items'] += 1

    def visit(message, temporary=False, message_index=-1):
        source = getattr(message, 'source', message.type if hasattr(message, 'type') else 'tool-schema')
        if isinstance(message, AssistantMessage):
            add('assistant', source, message.text)
            details = message.reasoning_details
            if reasoning == 'responses':
                thought = json.dumps([to_jsonable(detail['item']) for detail in details
                                     if detail.get('type') == 'response.reasoning'], ensure_ascii=False) if any(
                                         detail.get('type') == 'response.reasoning' for detail in details) else ''
            elif details:
                selected = [detail for detail in details if reasoning != 'chat' or detail.get('type') != 'response.reasoning']
                thought = json.dumps(to_jsonable(selected), ensure_ascii=False) if selected else ''
            else:
                thought = message.thinking + ''.join(b.signature or '' for b in message.content if isinstance(b, ThinkingBlock))
            if thought:
                add('reasoning', source, thought)
            for block in message.tool_uses:
                call_sources[block.id] = block.name
                add('tool_arguments', block.name, json.dumps([block.name, to_jsonable(block.input)], ensure_ascii=False))
            for block in message.content:
                if isinstance(block, ImageBlock):
                    add('images', source, images=1)
        elif isinstance(message, ToolResultMessage):
            for result_index, result in enumerate(message.results):
                result_key = (message_index, result_index)
                content = str(result.content)
                if result_key in image_results:
                    if result_key in retained_images:
                        content, urls = project_image_result(content)
                        if urls:
                            add('images', 'tool-result', images=len(urls))
                    else:
                        content = (reused_image_result(content, repeated_images[result_key])
                                   if result_key in repeated_images else omit_image_data(content))
                tool_name = call_sources.get(result.tool_use_id, 'tool-result')
                add('skills' if tool_name == 'skill' else 'tool_results', tool_name, content)
        elif hasattr(message, 'parameters'):
            add('tools', message.name, json.dumps([message.name, message.description, to_jsonable(message.parameters)], ensure_ascii=False))
        else:
            category = 'runtime' if temporary or source == 'system-runtime' else 'rules' if isinstance(message, (SystemMessage, DeveloperMessage)) else 'body'
            parts = getattr(message, 'context_parts', ())
            if isinstance(message, UserMessage) and message.context_spans:
                # 消息只保留位置元数据，避免为长技能常驻额外正文副本。
                offset = 0
                for span in message.context_spans:
                    if (len(span) != 3 or not isinstance(span[0], str) or type(span[1]) is not int
                            or type(span[2]) is not int or span[1] != offset or not offset <= span[2] <= len(message.text)):
                        break
                    offset = span[2]
                else:
                    if offset == len(message.text):
                        parts = ((name, message.text[start:end]) for name, start, end in message.context_spans)
            if parts:
                cjk, other, previous = 0, 0, 0
                for name, text in parts:
                    tokens = None
                    if isinstance(message, UserMessage):
                        # 分段只改变归属，跨段保留取整余数，避免改变原有整条预算。
                        count = sum(match.end() - match.start() for match in CJK.finditer(text))
                        cjk, other = cjk + count, other + len(text) - count
                        total = int(cjk / 1.5) + other // 4
                        tokens, previous = total - previous, total
                    add('memory' if name == 'project:memory' else 'skills' if name == 'skills:catalog' or name.startswith('skill:') else category, name, text, tokens=tokens)
            else:
                add(category, source, getattr(message, 'text', '') or '')

    for message_index, message in enumerate(messages):
        visit(message, message_index=message_index)
    for message in tools:
        visit(message)
    for message in runtime:
        visit(message, True)
    rows = [{'category': category, 'source': source, **counts} for (category, source), counts in groups.items()]
    return {'estimated_tokens': sum(row['tokens'] for row in rows), 'sources': rows}

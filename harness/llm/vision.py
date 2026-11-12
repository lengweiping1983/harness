"""内联图片工具结果的共享投影，不截断图片数据。"""
from __future__ import annotations

from harness.i18n import tr

import re
import hashlib
import json
from itertools import islice

IMAGE_TOOL_NAMES = frozenset({'read_image', 'read_images'})
INLINE_IMAGE = re.compile(r'^data:image/(?:png|jpeg|jpg|gif|webp|bmp);base64,[A-Za-z0-9+/]+={0,2}\r?$', re.MULTILINE)
# 用量取决于供应商、模型和细节级别；此处仅作软预算估算，不用于定价。
IMAGE_INPUT_TOKEN_ESTIMATE = 4096


def project_image_result(text: str) -> tuple[str, list[str]]:
    urls: list[str] = []

    def capture(match: re.Match[str]) -> str:
        url = match.group(0).rstrip('\r')
        if len(urls) >= 8 or len(url) > 7 * 1024 * 1024:
            return tr('python.vision.c2732ce9b635')
        urls.append(url)
        return tr('python.vision.ad3b49efbb52', p0=f'{len(urls)}')

    return INLINE_IMAGE.sub(capture, text), urls


def omit_image_data(text: str) -> str:
    return INLINE_IMAGE.sub(lambda match: tr('python.vision.5e8e9746c1f6'), text)


def reused_image_result(text, source_call_id):
    """重复观察保留原路径和说明，明确引用当前请求仍保留的视觉输入。"""
    marker = tr('python.vision.reused', call_id=json.dumps(source_call_id, ensure_ascii=False))
    return INLINE_IMAGE.sub(lambda match: marker, text)


def image_projection_plan(messages):
    """返回图片结果位置、保留位置及重复位置到来源调用 ID 的映射。

    位置是消息索引与结果索引；同组复用稳定来源，独特图片沿用容量规则。
    """
    from .types import AssistantMessage, ToolResultMessage
    messages = tuple(messages)
    # ID 只在其调用位置有效；后续复用不能改变较早结果的工具类型。
    call_names: dict[str, str] = {}
    image_results = {}
    for message_index, message in enumerate(messages):
        if isinstance(message, AssistantMessage):
            for block in message.tool_uses:
                call_names[block.id] = block.name
        elif isinstance(message, ToolResultMessage):
            for result_index, result in enumerate(message.results):
                if call_names.get(result.tool_use_id) in IMAGE_TOOL_NAMES and isinstance(result.content, str):
                    image_results[(message_index, result_index)] = result
    retained_results: set[tuple[int, int]] = set()
    signatures = {}
    image_count = image_chars = 0
    for result_key, result in reversed(image_results.items()):
        matches = list(islice(INLINE_IMAGE.finditer(result.content), 9))
        spans = [(m.start(), m.end()) for m in matches[:8]]
        count, chars = len(spans), sum(end - start for start, end in spans)
        signature = None
        if 0 < len(matches) <= 8 and all(end - start <= 7 * 1024 * 1024 for start, end in spans):
            signature = tuple(hashlib.sha256(result.content[start:end].encode('ascii')).digest()
                              for start, end in spans)
            if signature in signatures:
                members = signatures[signature]
                retained_results.remove(members[-1])
                retained_results.add(result_key)
                members.append(result_key)
                continue
        if count and (not retained_results or (image_count + count <= 8 and image_chars + chars <= 8 * 1024 * 1024)):
            retained_results.add(result_key)
            image_count += count
            image_chars += chars
            if signature is not None:
                signatures[signature] = [result_key]
    duplicates = {key: image_results[members[-1]].tool_use_id
                  for members in signatures.values() for key in members[:-1]}
    return set(image_results), retained_results, duplicates

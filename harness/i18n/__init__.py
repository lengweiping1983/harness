"""集中维护双语产品文案；原始用户内容和第三方内容不经过翻译。"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=2)
def resources(language: str) -> dict[str, str]:
    """加载随包分发的语言资源。"""
    return json.loads((Path(__file__).parent / 'locales' / f'{language}.json').read_text(encoding='utf-8'))


def tr(key: str, **values) -> str:
    """缺失资源立即报错；只插入调用方显式提供的模板参数。"""
    from ..core.language import current_language
    text = resources(current_language.get())[key]
    return text.format_map(values) if values else text

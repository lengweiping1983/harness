"""Kilo 公共免费池：匿名请求与远端目录映射，不读取账号凭据。"""
from __future__ import annotations

from .adapter import OpenRouterAdapter
from .providers import invalid, normalize_model_catalog

BASE_URL = 'https://api.kilo.ai/api/gateway'
BOOTSTRAP_MODEL = {'id': 'kilo-auto/free', 'name': 'Auto Free', 'api': 'openai-completions', 'input': ['text']}


def free_models(entries):
    """只接受上游明确标为免费的条目，不根据名称或价格猜测。"""
    if not isinstance(entries, list):
        raise invalid()
    result = []
    seen = set()
    for row in entries:
        if not isinstance(row, dict):
            raise invalid()
        identity = row.get('id')
        if not isinstance(identity, str) or not identity.strip() or identity in seen:
            raise invalid()
        seen.add(identity)
        if row.get('isFree') is not True:
            continue
        item = {'id': identity, 'name': row.get('name', identity), 'api': 'openai-completions', 'baseUrl': BASE_URL}
        top = row.get('top_provider') or {}
        architecture = row.get('architecture') or {}
        if not isinstance(top, dict) or not isinstance(architecture, dict):
            raise invalid()
        for target, value in (('contextWindow', row.get('context_length', top.get('context_length'))), ('maxTokens', top.get('max_completion_tokens'))):
            if value is not None:
                item[target] = value
        if 'input_modalities' in architecture:
            modalities = architecture['input_modalities']
            if not isinstance(modalities, list) or any(not isinstance(value, str) for value in modalities):
                raise invalid()
            item['input'] = [value for value in ('text', 'image', 'video') if value in modalities]
        # 只采用网关明确列出的档位；支持 reasoning 参数不等于支持任意强度。
        opencode = row.get('opencode') or {}
        if not isinstance(opencode, dict):
            raise invalid()
        variants = opencode.get('variants', {})
        if not isinstance(variants, dict):
            raise invalid()
        mapping = {}
        for variant in variants.values():
            reasoning = variant.get('reasoning') if isinstance(variant, dict) else None
            if not isinstance(reasoning, dict):
                continue
            effort = reasoning.get('effort')
            if reasoning.get('enabled') is False:
                mapping['off'] = None
            elif effort in ('minimal', 'low', 'medium', 'high', 'xhigh', 'max'):
                mapping[effort] = effort
        if mapping:
            item['thinkingLevelMap'] = mapping
        result.append(item)
    return normalize_model_catalog(result)


class KiloFreeAdapter(OpenRouterAdapter):
    """复用既有流解析及恢复边界，线上请求不携带 Authorization。"""

    def _headers(self, config, api_key=None):
        headers = super()._headers(config, api_key)
        return {name: value for name, value in headers.items()
                if name.lower() not in {'authorization', 'x-api-key', 'x-dsh-auth-token'}}

    async def resolve_capabilities(self, model=None):
        """能力来自已保存的免费目录，不额外发现或混入付费目录。"""
        return dict(self.model_info)

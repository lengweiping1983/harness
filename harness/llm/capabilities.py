"""按端点区分的模型能力，发现能力时不发出模型请求。"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import math
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .routes import atomic_json
from ..i18n import tr


def positive(value):
    return value if type(value) is int and value > 0 else None


def minimum(*values):
    present = [value for value in values if positive(value) is not None]
    return min(present) if present else None


def endpoint_key(url, model):
    p = urlsplit(url.rstrip('/'))
    return f'{p.scheme.lower()}://{p.netloc.lower()}{p.path}|{model}'


# 仅使用已验证的端点级回退，不能仅凭模型名应用。
SEEDS = {
    endpoint_key('https://opencode.ai/zen/v1', 'space-bunny-free'): {'max_output_tokens': 524288},
    endpoint_key('https://openrouter.ai/api/v1', 'nvidia/nemotron-3-ultra-550b-a55b:free'): {'max_output_tokens': 65536},
}


class CapabilityRegistry:
    def __init__(self, home: Path | None = None, *, discover=True):
        self.path = home / 'model-capabilities.json' if home else None
        self.discover = discover
        self.lock = threading.RLock()
        self.cache = {}
        self.learned = {}
        self.pending = {}
        self.checked = {}
        if self.path:
            data = self._read(self.path)
            if data is not None:
                self.learned = data['learned']
                self.cache = data['models']

    @staticmethod
    def _read(path):
        """只接受完整有效的当前缓存，不丢弃损坏字段。"""
        try:
            text = path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return None
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ValueError(tr('capabilities.invalid_cache')) from exc
        if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1:
            raise ValueError(tr('capabilities.invalid_cache'))
        for section in ('models', 'learned'):
            rows = data.get(section)
            if not isinstance(rows, dict):
                raise ValueError(tr('capabilities.invalid_cache'))
            for key, row in rows.items():
                if not isinstance(key, str) or '|' not in key or not all(key.rsplit('|', 1)) or not isinstance(row, dict):
                    raise ValueError(tr('capabilities.invalid_cache'))
                for name, value in row.items():
                    if name in ('context_window', 'max_output_tokens'):
                        valid = value is None or positive(value) is not None
                    elif name == 'fetched_at':
                        valid = type(value) in (int, float) and 0 <= value <= time.time() and math.isfinite(value)
                    elif name in ('supported_parameters', 'supported_efforts'):
                        valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
                    elif name in ('reasoning_mandatory', 'supports_reasoning_budget'):
                        valid = type(value) is bool
                    else:
                        valid = False
                    if not valid:
                        raise ValueError(tr('capabilities.invalid_cache'))
        return data

    @staticmethod
    def _clean(value):
        result = {name: positive(value.get(name)) for name in ('context_window', 'max_output_tokens')}
        stamp = value.get('fetched_at')
        result['fetched_at'] = stamp if type(stamp) in (int, float) and 0 <= stamp <= time.time() and math.isfinite(stamp) else 0
        supported = value.get('supported_parameters')
        if isinstance(supported, list) and all(isinstance(v, str) for v in supported):
            result['supported_parameters'] = supported
        efforts = value.get('supported_efforts')
        if isinstance(efforts, list) and all(isinstance(v, str) for v in efforts):
            result['supported_efforts'] = efforts
        for name in ('reasoning_mandatory', 'supports_reasoning_budget'):
            if type(value.get(name)) is bool:
                result[name] = value[name]
        return result

    def peek(self, url, model, manual=None):
        key = endpoint_key(url, model)
        with self.lock:
            sources = [('endpoint', SEEDS.get(key, {})), ('provider', self.cache.get(key, {})),
                       ('learned', self.learned.get(key, {})), ('manual', manual or {})]
            result = {name: minimum(*(values.get(name) for _, values in sources))
                      for name in ('context_window', 'max_output_tokens')}
            result['sources'] = [source for source, values in sources if any(positive(values.get(n)) for n in result)]
            result['confirmed'] = any(result.get(n) for n in ('context_window', 'max_output_tokens'))
            supported = self.cache.get(key, {}).get('supported_parameters')
            if supported is not None:
                result['supported_parameters'] = list(supported)
            for name in ('supported_efforts', 'reasoning_mandatory', 'supports_reasoning_budget'):
                if name in self.cache.get(key, {}):
                    result[name] = self.cache[key][name]
            if (manual or {}).get('supports_thinking') is False:
                result['supports_thinking'] = False
            # OpenRouter 文档规定的 Anthropic 思考预算范围。
            if urlsplit(url).hostname == 'openrouter.ai' and model.startswith('anthropic/'):
                result['reasoning_min_tokens'] = 1024
                result['reasoning_max_tokens'] = 128000
            return result

    def learn(self, url, model, *, context_window=None, max_output_tokens=None):
        key = endpoint_key(url, model)
        with self.lock:
            row = dict(self.learned.get(key, {}))
            for name, value in [('context_window', context_window), ('max_output_tokens', max_output_tokens)]:
                if value is not None and positive(value) is None:
                    raise ValueError(tr('capabilities.invalid_cache'))
                if value is not None:
                    row[name] = minimum(row.get(name), value)
            learned = {**self.learned, key: row}
            if self.path:
                self._read(self.path)
                atomic_json(self.path, {'version': 1, 'models': self.cache, 'learned': learned})
            self.learned = learned

    async def resolve(self, url, model, manual=None):
        key = endpoint_key(url, model)
        owner = False
        with self.lock:
            fresh = time.time() - self.cache.get(key, {}).get('fetched_at', 0) < 86400
            if not self.discover or fresh or time.time() - self.checked.get(key, 0) < 60:
                return self.peek(url, model, manual)
            future = self.pending.get(key)
            if future is None:
                future = concurrent.futures.Future()
                self.pending[key] = future
                owner = True
        if not owner:
            await asyncio.shield(asyncio.wrap_future(future))
            return self.peek(url, model, manual)
        try:
            try:
                async with asyncio.timeout(10):
                    result = await asyncio.to_thread(self._fetch, url, model)
            except (OSError, ValueError, TypeError, AttributeError, TimeoutError):
                result = None  # 远端发现失败保留原能力，不改写本地缓存。
            if result and (any(positive(result.get(n)) for n in ('context_window', 'max_output_tokens')) or
                    isinstance(result.get('supported_parameters'), list) or isinstance(result.get('supported_efforts'), list)):
                with self.lock:
                    cache = {**self.cache, key: {**self._clean(result), 'fetched_at': time.time()}}
                    if self.path:
                        self._read(self.path)
                        atomic_json(self.path, {'version': 1, 'models': cache, 'learned': self.learned})
                    self.cache = cache
            with self.lock:
                self.checked[key] = time.time()
                future.set_result(None)
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()  # 所有者直接传播；等待者仍从同一Future取得错误。
            raise
        finally:
            with self.lock:
                self.pending.pop(key, None)
        return self.peek(url, model, manual)

    @staticmethod
    def _fetch(url, model):
        deadline = time.monotonic() + 10
        def get(target):
            req = urllib.request.Request(target, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=max(.01, deadline - time.monotonic())) as response:
                return json.loads(response.read(4 * 1024 * 1024))
        catalog = get(url.rstrip('/') + '/models')
        row = next((m for m in catalog.get('data', []) if isinstance(m, dict) and m.get('id') == model), {})
        top = row.get('top_provider') or {}
        result = {'context_window': minimum(row.get('context_length'), top.get('context_length')),
                  'max_output_tokens': positive(top.get('max_completion_tokens') or row.get('max_output_tokens'))}
        if isinstance(row.get('supported_parameters'), list):
            result['supported_parameters'] = row['supported_parameters']
        reasoning = row.get('reasoning')
        if isinstance(reasoning, dict):
            if 'supported_efforts' in reasoning and reasoning['supported_efforts'] is not None:
                result['supported_efforts'] = reasoning['supported_efforts']
            result['reasoning_mandatory'] = reasoning.get('mandatory') is True
            if type(reasoning.get('supports_max_tokens')) is bool:
                result['supports_reasoning_budget'] = reasoning['supports_max_tokens']
        if urlsplit(url).hostname == 'openrouter.ai':
            try:
                endpoints = get(url.rstrip('/') + '/models/' + model + '/endpoints').get('data', {}).get('endpoints', [])
                for name, field in [('context_window', 'context_length'), ('max_output_tokens', 'max_completion_tokens')]:
                    result[name] = minimum(result[name], *(e.get(field) for e in endpoints if isinstance(e, dict)))
                lists = [set(e['supported_parameters']) for e in endpoints if isinstance(e.get('supported_parameters'), list)]
                if lists:
                    result['supported_parameters'] = sorted(set.intersection(*lists))
                if not endpoints:
                    raise ValueError(tr('runtime.validation.87a137600eee'))
                for name, field in [('context_window', 'context_length'), ('max_output_tokens', 'max_completion_tokens')]:
                    if any(not isinstance(e, dict) or positive(e.get(field)) is None for e in endpoints):
                        result[name] = None
            except (OSError, ValueError, TypeError, AttributeError):
                # 模型级或顶层供应商容量不能代表所有可能的路由端点。
                # 此时保留之前的缓存。
                raise ValueError(tr('runtime.validation.f771eb13e799'))
        return result

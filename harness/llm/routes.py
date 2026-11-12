"""有序的模型与凭据路由，公开快照不包含凭据。"""
from __future__ import annotations

from harness.i18n import tr

import asyncio
import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit


def mask_api_key(value: str) -> str:
    """统一使用六个星号加四位尾号；短密钥不暴露完整值。"""
    return "******" + value[-4:] if len(value) > 4 else "******" if value else ""


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            if os.name != 'nt':
                os.chmod(temporary, 0o600)
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ModelRoutes:
    def __init__(self, models: list[dict], home: Path | None = None):
        self.home = home
        self.capabilities = None
        self.lock = threading.RLock()
        self.models: list[dict] = []
        self.failed: set[str] = set()
        self.current_id = ''
        self.cycle = 1
        self.generation = 0
        self.wait_until = 0.0
        self.in_use: Counter = Counter()
        self.leases: dict[str, tuple[dict, dict]] = {}
        self.persistence_error = False
        self.update(models, persist=False)
        if home:
            path = home / 'model-route-state.json'
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                return
            except (OSError, ValueError):
                raise ValueError(tr('llm.state.routes_invalid')) from None
            # 状态只接受当前写入格式，不补造字段或覆盖损坏原件。
            identity = lambda value: isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None
            if (not isinstance(data, dict) or set(data) != {'version', 'failed', 'current', 'cycle'}
                    or type(data['version']) is not int or data['version'] != 1
                    or not isinstance(data['failed'], list) or not all(identity(value) for value in data['failed'])
                    or len(set(data['failed'])) != len(data['failed'])
                    or data['current'] != '' and not identity(data['current'])
                    or type(data['cycle']) is not int or data['cycle'] < 1):
                raise ValueError(tr('llm.state.routes_invalid'))
            known = {self.identity(m, k) for m in self.models for k in m['keys']}
            self.failed = set(data['failed']) & known
            self.current_id = data['current'] if data['current'] in known else ''
            self.cycle = data['cycle']

    @staticmethod
    def identity(model: dict, key: dict) -> str:
        return hashlib.sha256((model['id'] + '\0' + model['model'] + '\0' + model['base_url'] + '\0' + key['id'] + '\0' + key['value']).encode()).hexdigest()

    def slots(self) -> list[tuple[dict, dict, str]]:
        return [(m, k, self.identity(m, k)) for m in self.models if m['enabled'] for k in m['keys']]

    @property
    def size(self) -> int:
        return len(self.slots())

    def update(self, models: list[dict], *, persist: bool = True, keys_only: bool = False) -> None:
        with self.lock:
            if not isinstance(models, list) or not models:
                raise ValueError(tr('python.routes.701f5d7191e5'))
            old = {m['id']: m for m in self.models}
            normalized = []
            ids = set()
            for raw in models:
                if not isinstance(raw, dict):
                    raise ValueError(tr('python.routes.9c077bc4edb7'))
                mid = raw.get('id') or uuid.uuid4().hex
                if not isinstance(raw.get('model'), str) or not isinstance(raw.get('base_url'), str):
                    raise ValueError(tr('python.routes.0abf8b684527'))
                model = raw['model'].strip()
                url = raw['base_url'].strip().rstrip('/')
                enabled = raw.get('enabled', True)
                parsed = urlsplit(url)
                if not isinstance(mid, str) or mid in ids or not model or type(enabled) is not bool or parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError(tr('python.routes.0abf8b684527'))
                ids.add(mid)
                old_keys = {k['id']: k for k in old.get(mid, {}).get('keys', [])}
                keys = []
                key_ids = set()
                values = set()
                raw_keys = raw.get('keys', [])
                if not isinstance(raw_keys, list):
                    raise ValueError(tr('python.routes.8090351caf7c'))
                for item in raw_keys:
                    if not isinstance(item, dict):
                        raise ValueError(tr('python.routes.f1031c771e96'))
                    kid = item.get('id') or uuid.uuid4().hex
                    value = item.get('value', old_keys.get(kid, {}).get('value', ''))
                    if not isinstance(kid, str) or kid in key_ids or not isinstance(value, str) or not value.strip() or '•' in value or '*' in value:
                        raise ValueError(tr('python.routes.8ef946dc7cb7'))
                    key_ids.add(kid)
                    value = value.strip()
                    if value not in values:
                        values.add(value)
                        keys.append({'id': kid, 'value': value})
                if not keys:
                    raise ValueError(tr('python.routes.8f7b8a9d8a26'))
                limits = {}
                for field in ('context_window', 'max_output_tokens'):
                    value = raw.get(field, old.get(mid, {}).get(field))
                    if value is not None and (type(value) is not int or value <= 0):
                        raise ValueError(tr('python.routes.352774059048', p0=f'{field}'))
                    limits[field] = value
                normalized.append({'id': mid, 'model': model, 'base_url': url, 'enabled': enabled, 'keys': keys, **limits})
            if not any(m['enabled'] for m in normalized):
                raise ValueError(tr('python.routes.58e077b0781a'))
            def topology(items):
                return [(m['id'], m['model'], m['base_url'], m['enabled'], m.get('context_window'), m.get('max_output_tokens')) for m in items]
            changed_topology = topology(normalized) != topology(self.models)
            # 运行中允许移除模型；已发出的请求由租约保留，后续请求只选择剩余模型。
            surviving_topology = topology([m for m in self.models if m['id'] in ids])
            if keys_only and topology(normalized) != surviving_topology:
                raise ValueError(tr('python.routes.4c284f5c37c9'))
            old_slots = self.slots()
            old_known = {self.identity(m, k) for m in self.models for k in m['keys']}
            if persist and self.home:
                atomic_json(self.home / 'connection-secrets.json', {'version': 1, 'models': normalized})
            self.models = normalized
            known = {self.identity(m, k) for m in normalized for k in m['keys']}
            self.failed &= known
            if changed_topology:
                self.generation += 1
            slots = self.slots()
            available = {identity for _, _, identity in slots if identity not in self.failed}
            if self.current_id not in available:
                start = next((i for i, s in enumerate(old_slots) if s[2] == self.current_id), -1)
                survivors = [old_slots[(start + n) % len(old_slots)][2] for n in range(1, len(old_slots) + 1)] if old_slots else []
                self.current_id = next((identity for identity in survivors if identity in available),
                                       next((s[2] for s in slots if s[2] in available), ''))
            if available - old_known:
                self.wait_until = 0  # 新增健康凭据无需等待上一轮耗尽后的冷却。
            if persist:
                self.save_state()

    def save_state(self) -> None:
        if self.home:
            try:
                atomic_json(self.home / 'model-route-state.json', {'version': 1, 'failed': sorted(self.failed), 'current': self.current_id, 'cycle': self.cycle})
                self.persistence_error = False
            except OSError:
                self.persistence_error = True

    def reset(self, *, automatic: bool = False) -> None:
        with self.lock:
            self.failed.clear()
            self.current_id = self.slots()[0][2]
            self.generation += 1
            self.cycle = self.cycle + 1 if automatic else 1
            self.wait_until = time.monotonic() + 5 if automatic else 0
            self.save_state()

    def select(self) -> tuple[dict, dict, str, int]:
        with self.lock:
            slots = self.slots()
            available = [s for s in slots if s[2] not in self.failed]
            if not available:
                self.reset(automatic=True)
                available = slots
            slot = next((s for s in available if s[2] == self.current_id), available[0])
            self.current_id = slot[2]
            return (*slot, self.generation)

    async def acquire(self, *, exclude: set[str] | None = None) -> tuple[dict, dict, str, int] | None:
        while True:
            with self.lock:
                candidates = [s for s in self.slots() if not exclude or s[2] not in exclude]
                if not candidates:
                    return None
                if exclude:
                    # 单次请求不重复失败凭据；健康凭据耗尽后也检查尚未尝试的槽位。
                    available = [s for s in candidates if s[2] not in self.failed]
                    slot = next((s for s in available if s[2] == self.current_id),
                                available[0] if available else candidates[0])
                    self.current_id = slot[2]
                    selected = (*slot, self.generation)
                else:
                    selected = self.select()
                remaining = self.wait_until - time.monotonic()
                if remaining <= 0:
                    self.in_use[selected[2]] += 1
                    self.leases[selected[2]] = (selected[0], selected[1])
                    return selected
            await asyncio.sleep(min(remaining, .2))  # 及时观察实时新增密钥和手动重置。

    def release(self, identity: str) -> None:
        with self.lock:
            self.in_use[identity] -= 1
            if self.in_use[identity] <= 0:
                del self.in_use[identity]
                self.leases.pop(identity, None)

    def fail(self, identity: str, generation: int) -> None:
        with self.lock:
            if generation != self.generation:
                return  # 延迟到达的响应不能撤销重置或配置变化。
            if not any(s[2] == identity for s in self.slots()):
                return  # 已移除或替换的凭据可以完成请求，但不能重新进入池中。
            self.failed.add(identity)
            slots = self.slots()
            if all(s[2] in self.failed for s in slots):
                # 保留全池失败供界面和重启后读取；下一次实际请求才启动新一轮。
                self.current_id = ''
                self.save_state()
                return
            if self.current_id == identity:
                start = next((i for i, s in enumerate(slots) if s[2] == identity), -1)
                self.current_id = next(slots[(start + n) % len(slots)][2] for n in range(1, len(slots) + 1) if slots[(start + n) % len(slots)][2] not in self.failed)
            self.save_state()

    def succeed(self, identity: str, generation: int) -> None:
        """尚未尝试的旧失败凭据恢复成功时，仅恢复该凭据的可用状态。"""
        with self.lock:
            if generation != self.generation or identity not in self.failed:
                return
            if not any(s[2] == identity for s in self.slots()):
                return
            self.failed.discard(identity)
            self.save_state()

    def set_available(self, model_id: str, key_id: str, available: bool) -> None:
        with self.lock:
            slot = next(((m, k, self.identity(m, k)) for m in self.models for k in m['keys'] if m['id'] == model_id and k['id'] == key_id), None)
            if slot is None:
                raise ValueError(tr('python.routes.7e963b120129'))
            if available == (slot[2] not in self.failed):
                return
            if not available and slot[0]['enabled'] and not any(
                identity != slot[2] and identity not in self.failed for _, _, identity in self.slots()
            ):
                raise ValueError(tr('python.routes.last_available'))
            # 手动切换不是请求失败，不触发全池自动恢复；旧请求不能撤销用户操作。
            self.generation += 1
            if available:
                self.failed.discard(slot[2])
                self.wait_until = 0
            else:
                self.failed.add(slot[2])
                if self.current_id == slot[2]:
                    slots = self.slots()
                    start = next(i for i, item in enumerate(slots) if item[2] == slot[2])
                    self.current_id = next(slots[(start + step) % len(slots)][2]
                                           for step in range(1, len(slots) + 1)
                                           if slots[(start + step) % len(slots)][2] not in self.failed)
            self.save_state()

    def snapshot(self) -> dict:
        with self.lock:
            slots = self.slots()
            current = next((s for s in slots if s[2] == self.current_id and s[2] not in self.failed), next((s for s in slots if s[2] not in self.failed), slots[0]))
            return {'models': [{'id': m['id'], 'model': m['model'], 'base_url': m['base_url'], 'enabled': m['enabled'], 'context_window': m.get('context_window'), 'max_output_tokens': m.get('max_output_tokens'),
                'capabilities': self.capabilities.peek(m['base_url'], m['model'], m) if self.capabilities else {}, 'keys': [
                {'id': k['id'], 'mask': mask_api_key(k['value']), 'available': self.identity(m, k) not in self.failed,
                 'current': self.identity(m, k) == current[2], 'in_use': self.in_use[self.identity(m, k)] > 0} for k in m['keys']]} for m in self.models],
                'current': {'model_id': current[0]['id'], 'model': current[0]['model'], 'key_id': current[1]['id']},
                'cycle': self.cycle, 'retry_in_seconds': max(0, self.wait_until - time.monotonic()), 'state_save_failed': self.persistence_error,
                'retiring': [{'model_id': m['id'], 'model': m['model'], 'key_id': k['id'], 'mask': mask_api_key(k['value'])} for identity, (m, k) in self.leases.items()
                             if identity not in {s[2] for s in slots}]}

    def redact(self, value):
        if isinstance(value, str):
            secrets = [k['value'] for m in self.models for k in m['keys']]
            secrets.extend(k['value'] for _, k in self.leases.values())
            for secret in secrets:
                value = value.replace(secret, '[redacted]')
            return value
        if isinstance(value, dict):
            return {self.redact(k): self.redact(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact(v) for v in value]
        return value


def load_models(home: Path, model: str, base_url: str, keys: list[str]) -> list[dict]:
    path = home / 'connection-secrets.json'
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1 or not isinstance(data.get('models'), list):
                raise ValueError
            # 读取原件不能复用新建入口的自动 ID，否则重启会改变凭据归属。
            for entry in data['models']:
                if (not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or not entry['id'].strip()
                        or not isinstance(entry.get('keys'), list)
                        or any(not isinstance(key, dict) or not isinstance(key.get('id'), str)
                               or not key['id'].strip() for key in entry['keys'])):
                    raise ValueError
            return ModelRoutes(data['models']).models
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            # 保留损坏文件；错误不得包含密钥正文或原始解析片段。
            raise ValueError(tr('python.routes.b6a0c64934e6')) from None
    if not keys:
        return []
    return [{'id': 'default', 'model': model, 'base_url': base_url, 'enabled': True,
             'keys': [{'id': hashlib.sha256(k.encode()).hexdigest()[:24], 'value': k} for k in dict.fromkeys(keys)]}]

"""供应商、接口级凭据池及模型身份；公开投影不包含凭据。"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from harness.i18n import tr
from .key_pool_lock import state_lock
from .routes import atomic_json, mask_api_key

RUNTIME = Path(__file__).with_name('runtime')
PROTOCOLS = ('', 'openai-completions', 'openai-responses', 'anthropic-messages')


def invalid():
    """配置错误不包含原始凭据或解析片段。"""
    return ValueError(tr('models.config.invalid'))


def decode(raw):
    """有界解码并拒绝重复字段、非法数字及编码。"""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise invalid()
            result[key] = value
        return result
    try:
        if len(raw) > 8 * 1024 * 1024:
            raise invalid()
        result = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(invalid()))
        json.dumps(result, ensure_ascii=False).encode('utf-8')
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise invalid() from None


def directory():
    """目录来自固定版本供应商运行库，接口只在用户保存后创建。"""
    rows = decode((RUNTIME / 'catalog.json').read_bytes())
    for row in rows:
        if row['id'] == 'opencode':
            row['name'] = 'OpenCode'
    local_models = [{'id': 'deepseek-v4.1-flash-free', 'name': 'deepseek-v4.1-flash-free', 'input': ['text']}]
    rows.append({'id': 'opencode-zen', 'name': 'OpenCode Zen', 'base_url': 'https://opencode.ai/zen/v1', 'api_key': True, 'oauth': False, 'models': local_models})
    rows.append({'id': 'opencode-local', 'name': 'OpenCode Local', 'base_url': 'https://opencode.ai/inference/openai/v1', 'api_key': False, 'oauth': False, 'models': local_models})
    from .kilo_free import BASE_URL, BOOTSTRAP_MODEL
    rows.append({'id': 'kilo-free', 'name': 'Kilo Free', 'base_url': BASE_URL, 'api_key': False, 'oauth': False, 'models': [copy.deepcopy(BOOTSTRAP_MODEL)]})
    rows.append({'id': 'custom', 'name': 'Custom API', 'base_url': '', 'api_key': True, 'oauth': False, 'models': []})
    return rows


def normalize_model_catalog(rows):
    """模型目录只接收有界能力元数据，不保存凭据或任意上游字段。"""
    if not isinstance(rows, list) or len(rows) > 20000:
        raise invalid()
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'].strip() or row['id'] != row['id'].strip() or len(row['id']) > 4096 or row['id'] in seen:
            raise invalid()
        seen.add(row['id'])
        item = {'id': row['id'], 'name': row.get('name', row['id'])}
        if not isinstance(item['name'], str) or not item['name'].strip():
            raise invalid()
        if 'compat' in row:
            if not isinstance(row['compat'], dict):
                raise invalid()
            item['compat'] = copy.deepcopy(row['compat'])
        if 'cost' in row:
            cost = row['cost']
            names = {'input', 'output', 'cacheRead', 'cacheWrite'}
            if not isinstance(cost, dict) or set(cost) - (names | {'tiers'}):
                raise invalid()
            entries = [{key: value for key, value in cost.items() if key != 'tiers'}]
            if 'tiers' in cost:
                if not isinstance(cost['tiers'], list) or len(cost['tiers']) > 100 or any(not isinstance(tier, dict) or set(tier) - (names | {'inputTokensAbove'}) for tier in cost['tiers']):
                    raise invalid()
                entries.extend(cost['tiers'])
            if any(type(value) not in (int, float) or value < 0 or (value > 9007199254740991 if type(value) is int else not math.isfinite(value)) for entry in entries for value in entry.values()):
                raise invalid()
            item['cost'] = dict(cost)
        for field in ('api', 'baseUrl'):
            if field in row:
                if not isinstance(row[field], str):
                    raise invalid()
                if field == 'baseUrl' and row[field]:
                    url = urlsplit(row[field])
                    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
                        raise invalid()
                item[field] = row[field]
        for field in ('contextWindow', 'maxTokens'):
            if field in row:
                if type(row[field]) is not int or row[field] < 1:
                    raise invalid()
                item[field] = row[field]
        if 'input' in row:
            values = row['input']
            if not isinstance(values, list) or not values or any(value not in ('text', 'image', 'video') for value in values) or len(set(values)) != len(values):
                raise invalid()
            item['input'] = list(values)
        for field in ('enabled', 'supported'):
            if field in row:
                if type(row[field]) is not bool:
                    raise invalid()
                item[field] = row[field]
        if 'reasoning' in row:
            if type(row['reasoning']) is not bool:
                raise invalid()
            item['reasoning'] = row['reasoning']
        levels = {'off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}
        if 'thinkingLevelMap' in row:
            mapping = row['thinkingLevelMap']
            if not isinstance(mapping, dict) or any(key not in levels or value is not None and not isinstance(value, str) for key, value in mapping.items()):
                raise invalid()
            item['thinkingLevelMap'] = dict(mapping)
        if 'reasoningEfforts' in row:
            efforts = row['reasoningEfforts']
            if not isinstance(efforts, list) or any(not isinstance(value, str) or value not in levels for value in efforts) or len(set(efforts)) != len(efforts):
                raise invalid()
            item['reasoningEfforts'] = list(efforts)
        result.append(item)
    return result


def model_identity(provider, connection, model):
    """远端模型名称变化不改变本地模型身份。"""
    return f'harness/{provider}/{connection}/{model}'


class ProviderConnections:
    """唯一配置文档以版本比较发布，Key 状态按接口共享。"""

    def __init__(self, home):
        self.home = Path(home)
        self.path = self.home / 'model-providers.json'
        self.lock = threading.RLock()
        self.leases = {}
        self.providers = []
        self.raw = None
        self.reload()

    @property
    def revision(self):
        return hashlib.sha256(self.raw or b'').hexdigest()

    def reload(self):
        with self.lock:
            try:
                raw = self.path.read_bytes()
            except FileNotFoundError:
                raw = None
            if raw == self.raw:
                return
            if raw is None:
                self.providers, self.raw = [], None
                return
            value = decode(raw)
            if not isinstance(value, dict) or set(value) != {'version', 'providers'} or type(value['version']) is not int or value['version'] != 2:
                raise invalid()
            normalized = self.normalize(value['providers'], stored=True)
            self.providers, self.raw = normalized, raw

    def normalize(self, providers, *, stored=False, authorizations=None):
        if not isinstance(providers, list):
            raise invalid()
        old = {c['id']: c for p in self.providers for c in p['connections']}
        known = {p['id']: p for p in directory()}
        ids = set()
        def identity(value):
            if value is None and not stored:
                value = uuid.uuid4().hex
            if not isinstance(value, str) or not re.fullmatch('[a-zA-Z0-9_-]{1,96}', value) or value in ids:
                raise invalid()
            ids.add(value)
            return value
        def text(value, *, empty=False):
            if not isinstance(value, str) or not empty and not value.strip():
                raise invalid()
            return value.strip()
        def boolean(value):
            if type(value) is not bool:
                raise invalid()
            return value
        result = []
        for provider in providers:
            if not isinstance(provider, dict) or set(provider) - {'id', 'type', 'name', 'connections'}:
                raise invalid()
            pid = identity(provider.get('id'))
            kind = provider.get('type')
            if not isinstance(kind, str) or kind not in known or not isinstance(provider.get('connections'), list):
                raise invalid()
            entry = {'id': pid, 'type': kind, 'name': text(provider.get('name')), 'connections': []}
            for connection in provider['connections']:
                if not isinstance(connection, dict) or set(connection) - {'id', 'name', 'protocol', 'base_url', 'enabled', 'auth', 'keys', 'models', 'headers', 'compat', 'timeout_ms', 'max_retries', 'model_catalog'}:
                    raise invalid()
                cid = identity(connection.get('id'))
                before = old.get(cid, {})
                url = text(connection.get('base_url', ''), empty=True).rstrip('/')
                parsed = urlsplit(url)
                if url and (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
                    raise invalid()
                protocol = connection.get('protocol', '')
                if protocol not in PROTOCOLS or kind == 'custom' and (not protocol or not url):
                    raise invalid()
                auth = connection.get('auth', {'type': 'environment', 'env': {}})
                if not isinstance(auth, dict) or set(auth) - {'type', 'env', 'credential', 'configured', 'authorization_id'} or not isinstance(auth.get('type'), str) or auth.get('type') not in {'api_key', 'oauth', 'environment', 'opencode_local', 'anonymous'}:
                    raise invalid()
                if auth['type'] == 'oauth' and not known[kind].get('oauth') or auth['type'] == 'opencode_local' and kind != 'opencode-local' or auth['type'] == 'api_key' and not known[kind].get('api_key'):
                    raise invalid()
                if kind == 'kilo-free':
                    from .kilo_free import BASE_URL
                    if auth['type'] != 'anonymous' or auth.get('env') or connection.get('keys') or url != BASE_URL or protocol not in ('', 'openai-completions'):
                        raise invalid()
                elif auth['type'] == 'anonymous':
                    raise invalid()
                if not stored and 'credential' in auth:
                    raise invalid()
                env = auth.get('env', {})
                if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()):
                    raise invalid()
                previous_env = before.get('auth', {}).get('env', {})
                env = {k: previous_env.get(k, '') if v == '' else v for k, v in env.items()}
                normalized_auth = {'type': auth['type'], 'env': env}
                credential = auth.get('credential') if stored else before.get('auth', {}).get('credential')
                if not stored and auth.get('authorization_id'):
                    if not isinstance(auth['authorization_id'], str):
                        raise invalid()
                    grant = (authorizations or {}).get(auth['authorization_id'])
                    if not grant or grant['connection'] != cid or grant['provider'] != kind or grant['status'] != 'completed' or not grant.get('credential'):
                        raise invalid()
                    credential = grant['credential']
                if credential is not None and auth['type'] == 'oauth':
                    if not isinstance(credential, dict) or credential.get('type') != 'oauth':
                        raise invalid()
                    normalized_auth['credential'] = copy.deepcopy(credential)
                keys = []
                previous_keys = {k['id']: k for k in before.get('keys', [])}
                if not isinstance(connection.get('keys', []), list):
                    raise invalid()
                for key in connection.get('keys', []):
                    if not isinstance(key, dict) or set(key) - {'id', 'value', 'enabled', 'failed', 'mask', 'available', 'current', 'in_use'}:
                        raise invalid()
                    kid = identity(key.get('id'))
                    previous = previous_keys.get(kid, {})
                    value = text(key.get('value', previous.get('value', '')))
                    if '*' in value or '•' in value or any(k['value'] == value for k in keys):
                        raise invalid()
                    keys.append({'id': kid, 'value': value, 'enabled': boolean(key.get('enabled', True)), 'failed': boolean(key.get('failed', False) if stored else previous.get('failed', False) if value == previous.get('value') else False)})
                if auth['type'] == 'api_key' and not keys:
                    raise invalid()
                models = []
                if not isinstance(connection.get('models'), list):
                    raise invalid()
                remote_ids = set()
                for model in connection['models']:
                    if not isinstance(model, dict) or set(model) - {'id', 'model', 'name', 'enabled', 'context_window', 'max_output_tokens', 'input', 'reasoning_efforts', 'compat'}:
                        raise invalid()
                    mid = identity(model.get('id'))
                    remote = text(model.get('model'))
                    if remote in remote_ids:
                        raise invalid()
                    remote_ids.add(remote)
                    item = {'id': mid, 'model': remote, 'name': text(model.get('name') or remote), 'enabled': boolean(model.get('enabled', True))}
                    for field in ('context_window', 'max_output_tokens'):
                        value = model.get(field)
                        if value is not None:
                            if type(value) is not int or value <= 0:
                                raise invalid()
                            item[field] = value
                    modalities = model.get('input', ['text'])
                    if not isinstance(modalities, list) or not modalities or any(m not in ('text', 'image', 'video') for m in modalities) or len(set(modalities)) != len(modalities):
                        raise invalid()
                    item['input'] = modalities
                    efforts = model.get('reasoning_efforts')
                    if efforts is not None:
                        if efforts is not False and (not isinstance(efforts, dict) or not efforts or any(k not in {'off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'} or v is not None and not isinstance(v, str) for k, v in efforts.items())):
                            raise invalid()
                        item['reasoning_efforts'] = efforts
                    if 'compat' in model:
                        if not isinstance(model['compat'], dict):
                            raise invalid()
                        item['compat'] = model['compat']
                    models.append(item)
                entry['connections'].append({'id': cid, 'name': text(connection.get('name') or kind), 'protocol': protocol, 'base_url': url,
                    'enabled': boolean(connection.get('enabled', True)), 'auth': normalized_auth, 'keys': keys, 'models': models})
                target = entry['connections'][-1]
                if 'model_catalog' in connection:
                    target['model_catalog'] = normalize_model_catalog(connection['model_catalog'])
                for field in ('headers', 'compat'):
                    value = connection.get(field, {})
                    if not isinstance(value, dict) or field == 'headers' and any(not isinstance(k, str) or re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", k) is None or not isinstance(v, str) or '\n' in v or '\r' in v or k.lower() in {'authorization', 'x-api-key', 'host', 'x-dsh-auth-token'} for k, v in value.items()):
                        raise invalid()
                    target[field] = {key: before.get(field, {}).get(key, '') if item == '' else item for key, item in value.items()} if field == 'headers' else value
                for field, default in (('timeout_ms', 300000), ('max_retries', 2)):
                    value = connection.get(field, default)
                    if type(value) is not int or value < (0 if field == 'max_retries' else 1) or field == 'max_retries' and value > 10:
                        raise invalid()
                    target[field] = value
            result.append(entry)
        try:
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(encoded) > 8 * 1024 * 1024:
                raise invalid()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise invalid() from None
        return result

    def save(self, providers, revision, *, authorizations=None):
        with self.lock, state_lock(self.path):
            self.reload()
            if revision != self.revision:
                raise ValueError(tr('models.config.changed'))
            normalized = self.normalize(providers, authorizations=authorizations)
            self._publish(normalized)
            return self.snapshot()

    def _publish(self, providers):
        value = {'version': 2, 'providers': providers}
        atomic_json(self.path, value)
        self.providers, self.raw = providers, json.dumps(value, ensure_ascii=False).encode('utf-8')

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.providers)
            for provider in result:
                for connection in provider['connections']:
                    connection['headers'] = {key: '' for key in connection.get('headers', {})}
                    auth = connection['auth']
                    auth['configured'] = bool(auth.pop('credential', None))
                    auth['env'] = {key: '' for key in auth['env']}
                    for key in connection['keys']:
                        key['mask'] = mask_api_key(key.pop('value'))
                        key['available'] = key['enabled'] and not key.pop('failed')
                        key['in_use'] = self.leases.get((connection['id'], key['id']), 0) > 0
                        key['current'] = False
            return {'version': 2, 'revision': self.revision, 'providers': result, 'models': [], 'current': None, 'cycle': 1}

    def resolve(self, identity):
        with self.lock:
            for provider in self.providers:
                for connection in provider['connections']:
                    if not connection['enabled']:
                        continue
                    for model in connection['models']:
                        if model['enabled'] and model_identity(provider['id'], connection['id'], model['id']) == identity:
                            return copy.deepcopy((provider, connection, model))
        raise ValueError(tr('models.model.missing'))

    def catalog(self):
        with self.lock:
            result = []
            for provider in self.providers:
                for connection in provider['connections']:
                    if not connection['enabled']:
                        continue
                    template = next((p for p in directory() if p['id'] == provider['type']), None)
                    known = {m['id']: m for m in connection.get('model_catalog', template['models'] if template else [])}
                    for model in connection['models']:
                        if not model['enabled']:
                            continue
                        base = known.get(model['model'], {})
                        efforts = model.get('reasoning_efforts')
                        mapping = efforts if isinstance(efforts, dict) else base.get('thinkingLevelMap', {})
                        if efforts is False:
                            levels = []
                        elif isinstance(efforts, dict):
                            levels = [level for level, value in efforts.items() if value is not None or level == 'off']
                        elif 'reasoningEfforts' in base:
                            levels = list(base['reasoningEfforts'])
                        else:
                            levels = [level for level, value in mapping.items() if value is not None or level == 'off']
                        result.append({'id': model_identity(provider['id'], connection['id'], model['id']), 'label': model['name'],
                            'provider': provider['name'] if provider['type'] == 'kilo-free' else provider['name'] + ' / ' + connection['name'], 'description': model['model'],
                            'context_window': model.get('context_window', base.get('contextWindow', 0)),
                            'max_output': model.get('max_output_tokens', base.get('maxTokens', 0)),
                            'supports_reasoning': bool(levels), 'reasoning_efforts': levels})
            return result

    def key_status(self, connection_id, key_id, enabled=None, reset=False):
        with self.lock, state_lock(self.path):
            self.reload()
            providers = copy.deepcopy(self.providers)
            found = False
            for provider in providers:
                for connection in provider['connections']:
                    if connection['id'] == connection_id:
                        for key in connection['keys']:
                            if key_id is None or key['id'] == key_id:
                                if enabled is not None:
                                    key['enabled'] = enabled
                                if reset:
                                    key['failed'] = False
                                found = True
            if not found:
                raise invalid()
            self._publish(providers)
            return self.snapshot()

    def fail_key(self, connection, key, revision=None):
        with self.lock, state_lock(self.path):
            self.reload()
            if revision is not None and revision != self.revision:
                return
            providers = copy.deepcopy(self.providers)
            for provider in providers:
                for current in provider['connections']:
                    if current['id'] == connection['id'] and current['base_url'] == connection['base_url']:
                        for value in current['keys']:
                            if value['id'] == key['id'] and value['value'] == key['value']:
                                value['failed'] = True
            self._publish(providers)

    def credential(self, connection_id, value, expected=None):
        with self.lock, state_lock(self.path):
            self.reload()
            providers = copy.deepcopy(self.providers)
            for provider in providers:
                for connection in provider['connections']:
                    if connection['id'] == connection_id:
                        if connection['auth']['type'] != 'oauth' or connection['auth'].get('credential') != expected:
                            raise invalid()
                        connection['auth']['credential'] = value
                        self._publish(providers)
                        return
            raise invalid()

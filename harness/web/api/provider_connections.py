"""供应商配置、草稿模型探测和交互式授权。"""
from __future__ import annotations

import asyncio
from contextlib import aclosing
import uuid
import time
import re
import urllib.error
import urllib.request

from harness.i18n import tr
from ...core.language import current_language
from ...core.errors import LlmError
from ...llm.providers import directory, invalid, normalize_model_catalog, decode
from ...llm.provider_runtime import credential_bridge, bridge
from ..errors import BadRequest
from ..responses import json_response
from ..router import Router

DISCOVERY_PROTOCOLS = ('openai-completions', 'openai-responses', 'anthropic-messages')


class ProviderAuthorizations:
    """授权任务归属于明确接口，关闭宿主时等待所有任务回收。"""

    def __init__(self, store):
        self.store = store
        self.operations = {}

    def snapshot(self, operation):
        row = self.operations[operation]
        return {'id': operation, 'connection_id': row['connection'], 'status': row['status'],
                'events': row['events'], 'prompt': row['prompt'], 'error': row['error']}

    async def start(self, provider_id, connection_id, auth_type, *, draft=None):
        if any(row['connection'] == connection_id and row['status'] == 'pending' for row in self.operations.values()):
            raise invalid()
        for identity, item in list(self.operations.items()):
            if item['status'] != 'pending' and time.monotonic() - item['created'] > 900:
                del self.operations[identity]
        if len(self.operations) >= 64:
            raise invalid()
        provider = next((p for p in self.store.providers if p['id'] == provider_id), None)
        connection = next((c for c in provider['connections'] if c['id'] == connection_id), None) if provider else None
        if draft is not None:
            provider = self.store.normalize([draft])[0]
            connection = next((c for c in provider['connections'] if c['id'] == connection_id), None)
        if connection is None or connection['auth']['type'] != 'oauth' or auth_type not in {'oauth', 'api_key'}:
            raise invalid()
        operation = uuid.uuid4().hex
        row = {'created': time.monotonic(), 'provider': provider['type'], 'credential': None, 'connection': connection_id, 'status': 'pending', 'events': [], 'prompt': None, 'error': '', 'answer': None}
        self.operations[operation] = row
        async def work():
            try:
                async def interact(value):
                    row['prompt'] = value['prompt']
                    row['answer'] = asyncio.get_running_loop().create_future()
                    try:
                        return await row['answer']
                    finally:
                        row['prompt'], row['answer'] = None, None
                request = {'operation': 'login', 'provider': provider['type'], 'auth_type': auth_type,
                    'credential': connection['auth'].get('credential'), 'device_id': hashlib_device(self.store.home), 'client_version': '1.0.0', 'language': current_language.get()}
                async def stage(value):
                    row['credential'] = value
                operation_stream = bridge(request, commit=stage, interact=interact) if draft is not None else credential_bridge(self.store, connection_id, request, interact=interact)
                async with aclosing(operation_stream) as stream:
                    async for value in stream:
                        if value['type'] == 'auth':
                            row['events'] = [*row['events'][-15:], value['event']]
                row['status'] = 'completed'
            except asyncio.CancelledError:
                row['status'] = 'cancelled'
                raise
            except (LlmError, ValueError, OSError) as error:
                row['status'], row['error'] = 'failed', str(error)
        row['task'] = asyncio.create_task(work())
        return self.snapshot(operation)

    async def close(self):
        tasks = [row['task'] for row in self.operations.values() if not row['task'].done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def hashlib_device(home):
    """安装身份只传散列，不向第三方发送本机路径。"""
    import hashlib
    return str(uuid.UUID(hashlib.sha256(str(home).encode('utf-8')).hexdigest()[:32]))


def discovery_protocol(template, kind):
    """内置供应商按目录中的统一协议探测，协议混杂或无法识别时只使用内置目录。"""
    if kind in {'custom', 'deepseek-official', 'deepseek-account', 'openrouter', 'opencode', 'opencode-go', 'opencode-zen', 'kilo-free'}:
        return 'openai-completions'
    if not template or not template.get('models'):
        return ''
    protocols = {model.get('api') for model in template['models']}
    if protocols and protocols <= {'openai-completions', 'openai-responses'}:
        return 'openai-completions'
    value = protocols.pop() if len(protocols) == 1 else ''
    return value if value in DISCOVERY_PROTOCOLS else ''


def series_capabilities(model_id, catalog):
    """新模型沿用同供应商目录中最长前缀匹配系列的思考能力，不猜测窗口和输出上限。"""
    dated = re.fullmatch(r'(.+)-(?:[01][0-9][0-3][0-9]|20[0-9]{6}|20[0-9]{2}-[0-9]{2}-[0-9]{2})', model_id)
    if dated is None:
        return {}
    base = dated.group(1)
    for row in reversed(catalog):
        original = re.sub(r'-(?:[01][0-9][0-3][0-9]|20[0-9]{6}|20[0-9]{2}-[0-9]{2}-[0-9]{2})$', '', row['id'])
        if original == base:
            return {key: row[key] for key in ('input', 'reasoning', 'reasoningEfforts', 'thinkingLevelMap') if key in row}
    return {}


def merged_models(remote, catalog, protocol='', base_url=''):
    """成功目录以远端成员为准；内置元数据只补充同名模型，不复活下架成员。"""
    known = {row['id']: row for row in catalog}
    result = []
    reported = []
    for source in remote:
        # 仅映射接口明确返回的能力；不按模型名字猜测容量。
        row = dict(source)
        for target, aliases in (('contextWindow', ('context_length', 'context_window', 'max_input_tokens')), ('maxTokens', ('max_output_tokens', 'max_completion_tokens'))):
            if target not in row:
                for alias in aliases:
                    if source.get(alias) is not None:
                        row[target] = source[alias]
                        break
        top = source.get('top_provider')
        if isinstance(top, dict):
            for target, alias in (('contextWindow', 'context_length'), ('maxTokens', 'max_completion_tokens')):
                if target not in row and top.get(alias) is not None:
                    row[target] = top[alias]
        architecture = source.get('architecture')
        if 'input' not in row and isinstance(architecture, dict) and 'input_modalities' in architecture:
            modalities = architecture['input_modalities']
            if not isinstance(modalities, list) or any(not isinstance(value, str) for value in modalities):
                raise invalid()
            supported = [value for value in ('text', 'image', 'video') if value in modalities]
            if supported:
                row['input'] = supported
        reported.append(row)
    for row in normalize_model_catalog(reported):
        previous = known.get(row['id'], series_capabilities(row['id'], catalog))
        item = {**previous, **row}
        if row.get('reasoning') is False:
            item.pop('thinkingLevelMap', None)
            item.pop('reasoningEfforts', None)
        elif 'reasoningEfforts' in row and 'thinkingLevelMap' not in row and previous.get('thinkingLevelMap'):
            mapping = previous['thinkingLevelMap']
            item['thinkingLevelMap'] = {level: None if level == 'off' else mapping.get(level) or level for level in row['reasoningEfforts']}
        if row['name'] == row['id'] and previous.get('name'):
            item['name'] = previous['name']
        if protocol and not item.get('api'):
            item['api'] = protocol
        if base_url and not item.get('baseUrl'):
            item['baseUrl'] = base_url
        result.append(item)
    return normalize_model_catalog(result)


def build_router(ctx):
    """全部配置写入绑定版本，探测只修改返回草稿。"""
    router = Router()
    from .chatgpt import build_router as account_router
    router.include(account_router(ctx))
    store = ctx.harness.provider_connections
    authorizations = ProviderAuthorizations(store)
    ctx.harness.provider_authorizations = authorizations

    def run(operation):
        async def task():
            try:
                return await asyncio.to_thread(operation)
            except (ValueError, OSError) as error:
                raise BadRequest(str(error), code='connection/invalid') from None
        return json_response(ctx.call(task()))

    @router.get('/api/v1/connection/providers')
    def providers(request):
        return json_response({'providers': directory()}, accept_encoding=request.header('Accept-Encoding'))

    @router.get('/api/v1/connection')
    def status(request):
        return run(lambda: (store.reload(), store.snapshot())[1])

    @router.patch('/api/v1/connection')
    def save(request):
        body = request.json()
        if not isinstance(body, dict) or set(body) != {'providers', 'revision'}:
            raise BadRequest(tr('models.config.invalid'), code='connection/invalid')
        def update():
            snapshot = store.save(body['providers'], body['revision'], authorizations=authorizations.operations)
            configured = store.catalog()
            current = ctx.harness.config.model
            if (configured or current.startswith('harness/')) and not any(m['id'] == current for m in configured):
                selected = configured[0]['id'] if configured else ''
                ctx.harness.config.model = selected
                ctx.harness.agent.config = ctx.harness.agent.config.merged(model=selected)
            return {'routing': snapshot, 'config': ctx.harness.config.to_dict()}
        return run(update)

    @router.patch('/api/v1/connection/status')
    def key_status(request):
        body = request.json()
        if not isinstance(body, dict) or set(body) != {'connection_id', 'key_id', 'enabled'} or type(body['enabled']) is not bool:
            raise BadRequest(tr('models.config.invalid'), code='connection/invalid')
        return run(lambda: store.key_status(body['connection_id'], body['key_id'], enabled=body['enabled']))

    @router.post('/api/v1/connection/reset')
    def reset(request):
        body = request.json()
        if not isinstance(body, dict) or set(body) != {'connection_id'}:
            raise BadRequest(tr('models.config.invalid'), code='connection/invalid')
        return run(lambda: store.key_status(body['connection_id'], None, reset=True))

    @router.post('/api/v1/connection/discover')
    def discover(request):
        body = request.json()
        if not isinstance(body, dict) or set(body) != {'provider', 'connection'}:
            raise BadRequest(tr('models.config.invalid'), code='connection/invalid')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                """凭据仅发送到用户选择的接口地址。"""
                return None
        def probe():
            candidate = {'id': 'probe-provider', 'name': body['provider'], 'type': body['provider'], 'connections': [body['connection']]}
            normalized = store.normalize([candidate], authorizations=authorizations.operations)[0]['connections'][0]
            template = next((p for p in directory() if p['id'] == body['provider']), None)
            catalog = normalized.get('model_catalog', [dict(row) for row in template['models']] if template else [])
            protocol = normalized['protocol'] or discovery_protocol(template, body['provider'])
            if not protocol:
                return {'models': catalog, 'fallback': True}
            base = normalized['base_url'].rstrip('/') or (template.get('base_url', '') if template else '').rstrip('/')
            if not base:
                if not catalog:
                    raise ValueError(tr('models.discovery.failed')) from None
                return {'models': catalog, 'fallback': True}
            headers = dict(normalized['headers'])
            key = next((k['value'] for k in normalized['keys'] if k['enabled']), '')
            if protocol == 'anthropic-messages':
                base = base[:-3] if base.endswith('/v1') else base
                url = base + '/v1/models?limit=1000'
                headers.update({'anthropic-version': '2023-06-01', 'x-api-key': key})
            else:
                url = base + '/models'
                if key:
                    headers['Authorization'] = 'Bearer ' + key
            try:
                with urllib.request.build_opener(NoRedirect()).open(urllib.request.Request(url, headers=headers), timeout=15) as response:
                    payload = decode(response.read(8 * 1024 * 1024 + 1))
                if not isinstance(payload, dict) or payload.get('has_more') or payload.get('next_page_token'):
                    raise invalid()
                if isinstance(payload.get('data'), list):
                    entries = payload['data']
                elif isinstance(payload.get('models'), dict):
                    entries = [{'id': k, **v} for k, v in payload['models'].items() if isinstance(v, dict)]
                else:
                    raise invalid()
                if body['provider'] == 'kilo-free':
                    from ...llm.kilo_free import free_models
                    return {'models': free_models(entries), 'fallback': False}
                models, ids = [], set()
                for value in entries:
                    if not isinstance(value, dict) or not isinstance(value.get('id'), str) or not value['id'].strip() or value['id'] in ids:
                        raise invalid()
                    ids.add(value['id'])
                    models.append(value)
                return {'models': merged_models(models, [*(template['models'] if template else []), *catalog], protocol, base), 'fallback': False}
            except (OSError, ValueError) as failure:
                if isinstance(failure, urllib.error.HTTPError):
                    failure.close()
                if not catalog:
                    raise ValueError(tr('models.discovery.failed')) from None
                return {'models': catalog, 'fallback': True}
        template = next((entry for entry in directory() if entry['id'] == body['provider']), None)
        if template and template.get('dynamic'):
            async def dynamic_probe():
                candidate = {'id': 'probe-provider', 'name': body['provider'], 'type': body['provider'], 'connections': [body['connection']]}
                try:
                    connection = store.normalize([candidate], authorizations=authorizations.operations)[0]['connections'][0]
                    key = next((entry['value'] for entry in connection['keys'] if entry['enabled']), None)
                    request = {'operation': 'discover', 'provider': body['provider'], 'connection': connection,
                               'credential': connection['auth'].get('credential') or {'type': 'api_key', 'env': connection['auth']['env'], **({'key': key} if key else {})}}
                    saved = any(c['id'] == connection['id'] for p in store.providers for c in p['connections'])
                    async def stage(value):
                        row = authorizations.operations.get(body['connection'].get('auth', {}).get('authorization_id'))
                        if row is None:
                            raise invalid()
                        row['credential'] = value
                    operation = credential_bridge(store, connection['id'], request) if connection['auth']['type'] == 'oauth' and saved and not body['connection'].get('auth', {}).get('authorization_id') else bridge(request, commit=stage)
                    async with aclosing(operation) as stream:
                        async for value in stream:
                            if value['type'] == 'result':
                                return {'models': normalize_model_catalog(value['value']), 'fallback': False}
                    raise invalid()
                except (LlmError, ValueError):
                    raise BadRequest(tr('models.discovery.failed'), code='connection/discovery') from None
            return json_response(ctx.call(dynamic_probe(), timeout=65))
        return run(probe)

    @router.post('/api/v1/connection/authorization')
    def login(request):
        body = request.json()
        async def task():
            try:
                return await authorizations.start(body['provider_id'], body['connection_id'], 'oauth', draft=body.get('draft'))
            except (ValueError, KeyError):
                raise BadRequest(tr('models.config.invalid'), code='connection/invalid') from None
        return json_response(ctx.call(task()))

    @router.get('/api/v1/connection/authorization/{operation_id}')
    def auth_status(request, operation_id):
        if operation_id not in authorizations.operations:
            raise BadRequest(tr('models.config.invalid'), code='connection/invalid')
        return json_response(authorizations.snapshot(operation_id))

    @router.post('/api/v1/connection/authorization/{operation_id}/answer')
    def auth_answer(request, operation_id):
        body = request.json()
        async def answer():
            row = authorizations.operations.get(operation_id)
            if row is None or row['answer'] is None or row['answer'].done() or not isinstance(body.get('value'), str):
                raise BadRequest(tr('models.config.invalid'), code='connection/invalid')
            row['answer'].set_result(body['value'])
            return authorizations.snapshot(operation_id)
        return json_response(ctx.call(answer()))

    @router.delete('/api/v1/connection/authorization/{operation_id}')
    def auth_cancel(request, operation_id):
        async def cancel():
            row = authorizations.operations.get(operation_id)
            if row is None:
                raise BadRequest(tr('models.config.invalid'), code='connection/invalid')
            row['task'].cancel()
            await asyncio.gather(row['task'], return_exceptions=True)
            return authorizations.snapshot(operation_id)
        return json_response(ctx.call(cancel()))
    return router

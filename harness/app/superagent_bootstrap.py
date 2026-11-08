"""生产入口仅装配统一服务，模型执行和信道复用既有原生边界。"""
from __future__ import annotations

import os
from contextlib import ExitStack
from dataclasses import fields
from pathlib import Path

from ..core.cleanup import raise_cleanup_errors
from ..i18n import tr
from ..session.write_lock import event_write_lock
from .config import HarnessConfig, _default_owned_processes
from .harness import Harness
from .superagent_common import _error
from .superagent_config import read_config, config_identity, local_identity
from .superagent_coordinator_store import CoordinatorStore
from .superagent_coordinator_host import _legacy_state_present
from .superagent_executor import HarnessExecutorAdapter, WorkerProfile
from .superagent_feishu_http import FeishuClient
from .superagent_github_http import GitHubClient
from .superagent_http import WebhookServer
from .superagent_models import CoordinatorModels
from .superagent_state import configure


async def open_service(cls, config_path, *, worker_transport=None, coordinator_transport=None):
    spec = read_config(config_path)
    root = Path(spec['state_dir'])
    if _legacy_state_present(root):
        raise _error('conflict')
    database = root / 'superagent.sqlite3'
    if database.exists() or database.is_symlink():
        CoordinatorStore.open_existing(database, instance_id=spec['instance_id'], identity=config_identity(spec),
                                       read_only=True, current_schema=False)
    api_key = os.environ.get('HARNESS_API_KEY') or os.environ.get('OPENROUTER_API_KEY', '')
    if not api_key:
        raise _error('credentials-required')
    def secret(reference):
        if not isinstance(reference, str) or not reference.startswith('secret://'):
            raise _error('invalid', field='secret_ref')
        env = spec['secrets'].get(reference[9:])
        value = os.environ.get(env, '') if env is not None else ''
        if not value or '\x00' in value or len(value.encode('utf-8')) > 4096:
            raise _error('credentials-required')
        return value.encode('utf-8')
    # 在创建宿主前读取所有已启用信道需要的显式凭据。
    clients = {}
    for source_id, source in spec['sources'].items():
        if not source['enabled'] or source['kind'] == 'local':
            continue
        if source['kind'] == 'feishu':
            try:
                from importlib import import_module
                import_module('cryptography.hazmat.primitives.ciphers')
            except ImportError as exc:
                raise _error('feishu-dependency') from exc
        for key, reference in source['config'].items():
            if key.endswith('_ref'):
                secret(reference)
        transport = source['transport']
        credential = transport.get('credential_ref')
        if credential:
            value = secret(credential).decode('utf-8')
            clients[source_id] = (GitHubClient(transport['api_url'], value) if source['kind'] == 'github'
                else FeishuClient({'api_url': transport['api_url'], 'app_id': source['config']['app_id']}, value))
    profiles = [WorkerProfile(profile_id=key, **profile) for key, profile in spec['profiles'].items()]
    owned, service = [], None
    stack = ExitStack()
    try:
        root.mkdir(parents=True, exist_ok=True)
        try:
            stack.enter_context(event_write_lock(root / 'superagent-runtime'))
        except OSError as exc:
            raise _error('runtime-busy') from exc
        CoordinatorStore(database, instance_id=spec['instance_id'], identity=config_identity(spec))
        for name, transport in (('runtime', worker_transport), ('coordinator-runtime', coordinator_transport)):
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            if name == 'runtime':
                flags.update(enable_fs=True, enable_shell=True, enable_todo=True,
                             enable_worktrees=True, enable_owned_processes=_default_owned_processes())
            config = HarnessConfig(home=root / name, cwd=root, api_key=api_key, model=spec['model'],
                base_url=spec['base_url'], permission_policy=spec['permission_policy'], language=spec['language'],
                max_steps=64 if name == 'runtime' else 1, max_retries=0, **flags)
            owned.append(Harness(config, transport=transport, codex_gateway_home=root / (name + '-gateway')))
        worker, roles = owned
        if spec['permission_policy'] == 'read_only':
            def read_only(tool, args, ctx):
                return tr('superagent.read-only') if tool.effect != 'read' or tool.dangerous or tool.approval_required else None
            worker.tools.add_restriction(read_only)
            worker.tools.add_execution_check(read_only)
        owners = [h for h in worker.sessions.list_sessions() if h.metadata.get('superagent_owner') == spec['instance_id']]
        if len(owners) > 1:
            raise _error('conflict')
        owner = worker.get_session(owners[0].id) if owners else worker.create_session(metadata={'superagent_owner': spec['instance_id']})
        executor = HarnessExecutorAdapter(worker, instance_id=spec['instance_id'], owner_session_id=owner.id,
            profiles=profiles, max_workers=spec['max_workers'], default_profiles=spec['default_profiles'])
        models = CoordinatorModels(roles, [p for p in profiles if p.profile_id not in spec['system_profiles'].values()])
        service = await cls.open_coordinator(root, instance_id=spec['instance_id'], identity=config_identity(spec),
            executor_adapter=executor, assessor=models, planner=models, system_profiles=spec['system_profiles'],
            local_identity_verifier=lambda event: local_identity(spec, event), secret_resolver=secret,
            provider_clients=clients, watchdog_limits=spec['watchdog'], interval=spec['interval'], once_timeout=spec['once_timeout'], _runtime_stack=stack)
        service._owned_harnesses = owned
        service._production_config = spec
        models.bind(service.coordinator)
        configure(service.coordinator._store, spec, secret_resolver=secret)
        for source_id, source in spec['sources'].items():
            if source['kind'] != 'local' and service.coordinator.sources.get_instance(source_id).enabled:
                kind = source['kind']
                server = WebhookServer(service.source_ingress(source_id), source['transport']['webhook_port'], spec['language'],
                    path='/' + kind + '/' + source_id, prefix=kind, success_status=202 if kind == 'github' else 200)
                service.listeners.append({'source_id': source_id, 'kind': kind, 'server': server})
                await server.start()
        return service
    except BaseException as original:
        errors = [original]
        if service is not None:
            try:
                await service.aclose()
            except BaseException as exc:
                errors.append(exc)
        else:
            for host in reversed(owned):
                try:
                    await host.close()
                except BaseException as exc:
                    errors.append(exc)
        try:
            stack.close()
        except BaseException as exc:
            errors.append(exc)
        raise_cleanup_errors(errors)

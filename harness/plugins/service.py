"""插件业务装配：能力预览、安装、运行停止与持久状态一致。"""
from __future__ import annotations

from harness.i18n import tr

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from .packages import _snapshot, read_package
from .provider import connector_name


def _app_identities(metadata: dict[str, Any]) -> list[str]:
    """读取与绑定共享身份校验，不将字符串子串匹配当作应用授权。"""
    from ..core.errors import ConnectorError
    identities = metadata.get('app_ids', [])
    if not isinstance(identities, list) or not all(isinstance(item, str) for item in identities):
        raise ConnectorError(tr('plugin.validation.e02c59d38c60'))
    return identities


class PackageService:
    """所有写操作在应用事件循环中串行，不重放未知副作用。"""

    def __init__(self, harness: Any):
        self.harness = harness
        self.store = harness.packages
        self._lock = asyncio.Lock()
        from .sources import PackageSources
        self.sources = PackageSources(self.store.root)
        from .marketplaces import MarketplaceStore
        self.marketplaces = MarketplaceStore(harness.config.home, harness.config.cwd or Path.cwd())

    async def marketplace_catalog(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.marketplaces.catalog)

    async def register_marketplace(self, source: str) -> dict[str, Any]:
        return await self._source_mutation(self.marketplaces.add, source)

    async def unregister_marketplace(self, source: str) -> dict[str, Any]:
        return await self._source_mutation(self.marketplaces.remove, source)

    async def preview_marketplace(self, source: str, name: str, fingerprint: str) -> dict[str, Any]:
        row = await asyncio.to_thread(self.marketplaces.select, source, name, fingerprint)
        if row.get('git_source'):
            from .git_sources import materialize
            async with self._lock:
                operation = asyncio.create_task(asyncio.to_thread(materialize, row['git_source'], Path(self.harness.config.home) / 'plugin-sources/git'))
                row['path'] = str(await join_cleanup(operation))
            await asyncio.to_thread(self.marketplaces.select, source, name, fingerprint)
        result = await self.preview(row['path'])
        if result['name'] != name:
            from .packages import PackageError
            raise PackageError(tr('plugin.validation.55345448d7f7'))
        return {'path': row['path'], 'package': result}

    async def source_files(self, source: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.sources.listing, source)

    async def source_file(self, source: str, name: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.sources.snapshot, source, name)

    async def save_source(self, source: str, name: str, content: str, revision: str, executable: bool) -> dict[str, Any]:
        return await self._source_mutation(self.sources.save, source, name, content, revision, executable)

    async def remove_source(self, source: str, name: str, revision: str) -> dict[str, Any]:
        return await self._source_mutation(self.sources.remove, source, name, revision)

    async def _source_mutation(self, action: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
        """源码写入开始后等待同一次提交，取消请求不能提前释放版本比较锁。"""
        async with self._lock:
            operation = asyncio.create_task(asyncio.to_thread(action, *args))
            return await join_cleanup(operation)

    def catalog(self) -> dict[str, Any]:
        manager = self.harness.connectors
        connectors = {row['name']: row for row in manager.catalog()} if manager is not None else {}
        identities = {name: _app_identities(item.get('metadata', {})) for name, item in connectors.items()}
        rows = []
        for spec, state in self.store.entries():
            row = {**spec.preview(), 'enabled': state['enabled'], 'digest': state['digest'], 'path': str(spec.root)}
            for app in row['apps']:
                matches = [item for item in connectors.values() if app['id'] in identities[item['name']]]
                ready = [item for item in matches if item['enabled'] and item['bound'] and item['connected']]
                app.update(available=bool(matches), connected=bool(ready),
                           status='ready' if ready else 'not_connected' if matches else 'unavailable',
                           connectors=[item['name'] for item in matches],
                           candidates=[item['name'] for item in connectors.values() if not item.get('metadata', {}).get('plugin_id')])
            row['dependencies_ready'] = all(not app['required'] or app['connected'] for app in row['apps'])
            row['hooks_trusted'] = self.harness.hooks.plugin_definition_trusted(spec.name, row['hooks_fingerprint'])
            row['connectors'] = [{**item, 'connector_name': connector_name(spec.name, item['name']),
                                  'connected': connectors.get(connector_name(spec.name, item['name']), {}).get('connected', False),
                                  'bound': state['connectors'][item['name']]['bound'],
                                  'enabled': state['enabled'] and state['connectors'][item['name']]['enabled']}
                                 for item in row['connectors']]
            rows.append(row)
        return {'packages': rows}

    async def preview(self, source: str) -> dict[str, Any]:
        def preview():
            with tempfile.TemporaryDirectory(prefix='harness-plugin-preview-') as temporary:
                target = Path(temporary)
                digest = _snapshot(Path(source).expanduser().resolve(), target)
                spec = read_package(target)
                result = {**spec.preview(), 'digest': digest}
                for old_spec, state in self.store.entries():
                    if old_spec.name == spec.name:
                        skills = {row['name'] for row in result['skills']}
                        result['update'] = {
                            'current_digest': state['digest'], 'current_version': old_spec.version,
                            'changed_connectors': sorted(name for name in spec.servers if name in old_spec.servers and spec.servers[name] != old_spec.servers[name]),
                            'removed_connectors': sorted(set(old_spec.servers) - set(spec.servers)),
                            'removed_skills': sorted(row['name'] for row in old_spec.preview()['skills'] if row['name'] not in skills),
                            'hooks_changed': old_spec.hooks != spec.hooks,
                        }
                        break
                return result
        return await asyncio.to_thread(preview)

    async def read_draft(self, parent: str, name: str) -> dict[str, Any]:
        """按同一确定路径读回源码，不重放创建、不安装或自动修复。"""
        from .authoring import plugin_source
        path = plugin_source(parent, name)
        if not path.is_dir():
            raise KeyError(str(path))
        return {'path': str(path), 'package': await self.preview(str(path))}

    async def create_draft(self, parent: str, name: str, description: str, instructions: str) -> dict[str, Any]:
        """页面创建复用系统技能的源码生成器和正式预览。"""
        from .authoring import create_plugin, plugin_source
        from .packages import PackageError
        async with self._lock:
            if plugin_source(parent, name).is_relative_to(self.store.root.resolve()):
                raise PackageError(tr('plugin.validation.8e6b4b098da7'))
            async def create():
                await asyncio.to_thread(create_plugin, parent, name, description, instructions)
                return await self.read_draft(parent, name)
            return await join_cleanup(asyncio.create_task(create()))

    async def install(self, source: str, expected_digest: str | None = None) -> dict[str, Any]:
        async with self._lock:
            def install():
                # 统一采用连接器锁 → 插件锁，避免投影读取与安装回调锁序反转。
                with self.harness.connector_store.locked():
                    def validate(spec):
                        from ..core.errors import ConnectorError
                        names = {record.name for record in self.harness.connector_store.list()}
                        names.update(supervisor.server_name for supervisor in self.harness.mcp.supervisors)
                        if names & {connector_name(spec.name, server) for server in spec.servers}:
                            raise ConnectorError(tr('plugin.validation.1e82fe0d05de'))
                    return self.store.install(source, validate=validate, expected_digest=expected_digest)
            async def apply():
                result = await asyncio.to_thread(install)
                self.harness.refresh_skills()
                await self._changed('install', result['name'])
                return result
            return await join_cleanup(asyncio.create_task(apply()))

    async def _disconnect(self, name: str) -> None:
        entry = next(((spec, state) for spec, state in self.store.entries() if spec.name == name), None)
        if entry is None:
            raise KeyError(name)
        await self._disconnect_names(connector_name(name, server) for server in entry[0].servers)

    async def _disconnect_names(self, names: Iterable[str]) -> None:
        """逐项尝试所属连接的收尾，失败不遗落其他句柄且保留原异常。"""
        manager = self.harness.connectors
        if manager is None:
            return
        errors: list[BaseException] = []
        for name in names:
            try:
                await manager.disconnect_runtime(name)
            except BaseException as exc:
                errors.append(exc)
        raise_cleanup_errors(errors)

    async def _resume(self, name: str) -> None:
        """按当前唯一声明恢复已启用、已绑定的服务。"""
        manager = self.harness.connectors
        if manager is not None:
            errors = []
            for record in self.harness.connector_store.list():
                if record.metadata.get('plugin_id') == name and record.enabled and record.bound:
                    try:
                        await manager._connect_and_register(record)
                    except BaseException as error:
                        errors.append(error)
            raise_cleanup_errors(errors)

    async def update(self, name: str, source: str, digest: str, current_digest: str) -> dict[str, Any]:
        """审核后的更新共用原子包写入；失败时恢复当前已绑定服务。"""
        from .packages import PackageConflict, PackageError
        async with self._lock:
            preview = await self.preview(source)
            if preview['name'] != name or preview['digest'] != digest:
                raise PackageError(tr('plugin.validation.6da013183387'))
            entry = next(((spec, row) for spec, row in self.store.entries() if spec.name == name), None)
            if entry is None:
                raise KeyError(name)
            old_spec, previous = entry
            if previous['digest'] != current_digest:
                raise PackageConflict(tr('plugin.validation.0bfb3b92a20b'))
            changed = digest != current_digest
            names = {connector_name(name, server) for server in old_spec.servers}

            def put():
                with self.harness.connector_store.locked():
                    def validate(spec):
                        from ..core.errors import ConnectorError
                        occupied = {row.name for row in self.harness.connector_store.list() if row.metadata.get('plugin_id') != name}
                        occupied.update(supervisor.server_name for supervisor in self.harness.mcp.supervisors if supervisor.server_name not in names)
                        if occupied & {connector_name(name, server) for server in spec.servers}:
                            raise ConnectorError(tr('plugin.validation.1e82fe0d05de'))
                    return self.store.update(name, source, expected_digest=digest,
                                             expected_current=current_digest, validate=validate)

            async def apply():
                """切换开始后完整收尾，外层请求取消不截断提交后的运行时恢复。"""
                result, errors = None, []
                try:
                    if changed:
                        await self._disconnect(name)
                    result = await asyncio.to_thread(put)
                except BaseException as error:
                    errors.append(error)
                disconnected, refreshed = not changed, False
                if changed:
                    # 清理未确认时仍刷新目录，但不启动可能与旧连接重叠的新实例。
                    try:
                        await self._disconnect_names(sorted(names))
                        disconnected = True
                    except BaseException as error:
                        errors.append(error)
                if changed or result is not None:
                    try:
                        self.harness.refresh_skills()
                        refreshed = True
                    except BaseException as error:
                        errors.append(error)
                if changed and disconnected and refreshed:
                    try:
                        await self._resume(name)
                    except BaseException as error:
                        errors.append(error)
                if result is not None:
                    try:
                        await self._changed('update', name)
                    except BaseException as error:
                        errors.append(error)
                raise_cleanup_errors(errors)
                return result

            operation = asyncio.create_task(apply())
            return await join_cleanup(operation)

    async def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        async with self._lock:
            async def apply():
                # 启停开始后完整收尾，取消不能留下声明与运行时不一致。
                if not enabled:
                    await self._disconnect(name)
                self.store.set_enabled(name, enabled)
                self.harness.refresh_skills()
                if enabled:
                    await self._resume(name)
                await self._changed('enable' if enabled else 'disable', name)
                return self.catalog()
            return await join_cleanup(asyncio.create_task(apply()))

    async def uninstall(self, name: str) -> dict[str, Any]:
        async with self._lock:
            async def apply():
                # 断开、删除和通知共用同一次操作，不重放未知副作用。
                await self._disconnect(name)
                self.store.uninstall(name)
                self.harness.refresh_skills()
                await self._changed('uninstall', name)
                return {'removed': True, 'name': name}
            return await join_cleanup(asyncio.create_task(apply()))

    async def link_app(self, name: str, alias: str, connector: str) -> dict[str, Any]:
        """把已注册应用身份绑定到既有连接器，不复制连接、凭据或工具。"""
        from ..core.errors import ConnectorError
        async with self._lock:
            spec = next((spec for spec, _ in self.store.entries() if spec.name == name), None)
            if spec is None or alias not in spec.apps:
                raise KeyError(name + ':' + alias)
            with self.harness.connector_store.locked():
                if self.harness.connector_store.is_plugin(connector):
                    raise ConnectorError(tr('plugin.validation.590df6ea87e6'))
                record = self.harness.connector_store.get(connector)
                from dataclasses import replace
                identities = _app_identities(record.metadata)
                identity = spec.apps[alias]['id']
                if identity not in identities:
                    metadata = {**record.metadata, 'app_ids': [*identities, identity]}
                    self.harness.connector_store.upsert(replace(record, metadata=metadata))
            await self._changed('app-link', name)
            return self.catalog()

    async def set_hook_trust(self, name: str, expected: str, trusted: bool) -> dict[str, Any]:
        """插件页只是统一 Hooks 信任服务的入口。"""
        async with self._lock:
            self.harness.hooks.set_plugin_trust(name, expected, trusted)
            await self._changed('hook-trust', name)
            return self.catalog()

    async def _changed(self, action: str, name: str) -> None:
        await self.harness.bus.emit('plugin/changed', {'kind': 'package/' + action, 'name': name})

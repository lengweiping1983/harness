"""插件能力投影：复用技能注册表与连接器管理，不复制用户声明。"""
from __future__ import annotations

from harness.i18n import tr

import hashlib
from dataclasses import replace

from ..connectors.store import ConnectorRecord
from ..skills.parser import parse_skill_file
from .packages import PackageStore


def connector_name(package: str, server: str) -> str:
    """为插件服务提供稳定且不超过 MCP 限制的独立名称。"""
    digest = hashlib.sha256(f'{package}:{server}'.encode()).hexdigest()[:12]
    return f'p_{package[:16]}_{digest}'


class PackageSkillProvider:
    """插件技能按所有者限定名称，启用策略留给统一过滤器。"""

    name = 'plugins'

    def __init__(self, store: PackageStore):
        self.store = store

    def discover(self):
        result = []
        for spec, _ in self.store.entries():
            for path in spec.skills:
                skill = parse_skill_file(path, source='plugin', provider='plugins')
                result.append(replace(skill, name=f'{spec.name}:{skill.name}', plugin_id=spec.name, rank=400))
        return result


class PackageConnectorProvider:
    """把包服务及用户连接偏好映射到现有连接器运行时。"""

    def __init__(self, store: PackageStore):
        self.store = store

    def list(self) -> list[ConnectorRecord]:
        result = []
        for spec, row in self.store.entries():
            for name, config in spec.servers.items():
                state = row['connectors'][name]
                # 包路径占位符由宿主解析，普通环境变量仍由统一连接器处理。
                def expand(value):
                    if isinstance(value, str):
                        for key, replacement in {'PLUGIN_ROOT': str(spec.root), 'CLAUDE_PLUGIN_ROOT': str(spec.root),
                                                 'PLUGIN_DATA': str(self.store.root / 'data' / spec.name),
                                                 'CLAUDE_PLUGIN_DATA': str(self.store.root / 'data' / spec.name)}.items():
                            value = value.replace('${' + key + '}', replacement)
                        return value
                    if isinstance(value, list):
                        return [expand(item) for item in value]
                    if isinstance(value, dict):
                        return {key: expand(item) for key, item in value.items()}
                    return value
                config = expand(config)
                remote = 'url' in config
                command = config.get('command')
                if command and (command.startswith('.') or '/' in command or '\\' in command):
                    command = str(spec.root / command)
                env = {key: '${' + key + '}' for key in config.get('env_vars', [])}
                env.update(config.get('env', {}))
                result.append(ConnectorRecord(
                    name=connector_name(spec.name, name), transport='streamable-http' if remote else 'stdio',
                    command=command, args=list(config.get('args', [])), env=env,
                    cwd=str(spec.root / config.get('cwd', '.')) if not remote else None,
                    url=config.get('url'), headers=dict(config.get('headers', {})),
                    enabled=row['enabled'] and state['enabled'], bound=state['bound'],
                    metadata={'plugin_id': spec.name, 'plugin_enabled': row['enabled'],
                              'display_name': f'{spec.display_name} / {name}', 'description': spec.description,
                              'mcp_options': {key: config[key] for key in ('startup_timeout_sec', 'tool_timeout_sec', 'enabled_tools', 'disabled_tools') if key in config}},
                ))
        return result

    def update(self, public_name: str, *, enabled: bool | None = None, bound: bool | None = None) -> ConnectorRecord:
        for spec, row in self.store.entries():
            for server in spec.servers:
                if connector_name(spec.name, server) == public_name:
                    if not row['enabled'] and (enabled or bound):
                        from ..core.errors import ConnectorError
                        raise ConnectorError(tr('plugin.validation.ad1923c9c901'))
                    self.store.set_connector(spec.name, server, enabled=enabled, bound=bound)
                    return next(item for item in self.list() if item.name == public_name)
        raise KeyError(public_name)

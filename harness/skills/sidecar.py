"""声明只提供元数据，不启动连接器。"""
from pathlib import Path

from ..i18n import tr

from ..core.errors import SkillParseError
from .yaml_config import load_mapping


def read_sidecar(root: Path) -> tuple[dict, dict, dict, list[str]]:
    """原生格式优先读取 agents/openai.yaml，缺失时不猜测其它配置。"""
    path = root / 'agents/openai.yaml'
    if not path.exists() and not path.is_symlink():
        return {}, {}, {}, []
    try:
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(tr('skill.sidecar.outside'))
        with path.open('rb') as stream:
            raw = stream.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError(tr('skill.sidecar.too_large'))
        data = load_mapping(raw.decode('utf-8'))
        values = []
        for field in ('interface', 'policy', 'dependencies'):
            value = data.get(field, {})
            if not isinstance(value, dict):
                raise ValueError(tr('config.field.mapping', field=field))
            values.append(value)
        interface, policy, dependencies = values
        interface = dict(interface)
        for key in ('display_name', 'short_description', 'icon_small', 'icon_large', 'brand_color', 'default_prompt'):
            if key in interface and not isinstance(interface[key], str):
                raise ValueError(tr('config.field.string', field='interface.' + key))
        for key in ('display_name', 'short_description'):
            if key in interface:
                interface[key] = ' '.join(interface[key].split())
        if 'allow_implicit_invocation' in policy and type(policy['allow_implicit_invocation']) is not bool:
            raise ValueError(tr('config.field.boolean', field='policy.allow_implicit_invocation'))
        if 'products' in policy and (not isinstance(policy['products'], list) or not policy['products']
                                     or any(value not in ('CHAT', 'CODEX') for value in policy['products'])):
            raise ValueError(tr('skill.sidecar.products'))
        tools = dependencies.get('tools', [])
        if not isinstance(tools, list) or any(not isinstance(tool, dict)
            or not all(isinstance(tool.get(key), str) and tool[key].strip() for key in ('type', 'value')) for tool in tools):
            raise ValueError(tr('skill.sidecar.tools'))
        return interface, policy, dependencies, []
    except (OSError, ValueError, SkillParseError) as exc:
        # 保留可显式使用的技能正文，同时把配置错误返回管理页；不修改原文件。
        return {}, {}, {}, [str(path) + ': ' + str(exc)[:2000]]

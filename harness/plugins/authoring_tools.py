"""模型创建入口复用正式文件生成器，不另建技能或插件运行时。"""
from pathlib import Path
from ..i18n import tr

from ..skills.authoring import create_skill, skill_name
from ..skills.scope import checkout_root
from ..skills.paths import installation_root
from ..tools.registry import ToolDefinition
from .authoring import create_plugin
from .packages import read_package


def build_authoring_tools(harness):
    """只在当前调用的工作目录创建源码，沿用工具审批与写入范围检查。"""
    def directory(args, ctx, kind):
        if not ctx.cwd:
            raise ValueError(tr('plugin.authoring.cwd'))
        root = checkout_root(Path(ctx.cwd).expanduser())
        name = skill_name(args['name'])
        return (installation_root('skill', 'project', cwd=root) if kind == 'skill' else root / '.harness/plugin-sources') / name

    def create(args, ctx, kind):
        if not args['description'].strip() or not args['instructions'].strip():
            raise ValueError(tr('plugin.authoring.complete'))
        target = directory(args, ctx, kind)
        if kind == 'skill':
            result = create_skill(target.parent, args['name'], args['description'], args['instructions'])
            harness.refresh_skills()
            return {**result, 'status': 'created', 'kind': kind}
        source = create_plugin(target.parent, args['name'], args['description'], args['instructions'])
        return {'path': str(source), 'kind': kind, 'status': 'source_created',
                'package': read_package(source).preview(), 'installed': False}

    tools = []
    for kind in ('skill', 'plugin'):
        if kind == 'skill' and not harness.config.enable_skills:
            continue
        tools.append(ToolDefinition(
            schema={'name': 'author_' + kind,
                    'description_key': 'plugin.authoring.' + kind + '_tool',
                    'description': tr('plugin.authoring.' + kind + '_tool'),
                    'parameters': {'type': 'object', 'properties': {
                        'name': {'type': 'string', 'pattern': '^[a-z0-9]+(?:-[a-z0-9]+)*$', 'maxLength': 64},
                        'description': {'type': 'string', 'minLength': 1, 'maxLength': 1024},
                        'instructions': {'type': 'string', 'minLength': 1, 'maxLength': 64000}},
                        'required': ['name', 'description', 'instructions'], 'additionalProperties': False}},
            execute=lambda args, ctx, kind=kind: create(args, ctx, kind),
            describe=(lambda args, result: {'kind': 'plugin_source', 'path': result['path'], 'name': args['name']}) if kind == 'plugin' else None,
            write_paths=lambda args, ctx, kind=kind: [directory(args, ctx, kind)],
            effect='workspace_write', concurrency_safe=False, category='skill', hook_names=('Write',),
        ))
    return tools

"""技能创建与校验入口，复用正式解析器和统一安装目录。"""
from __future__ import annotations

from collections.abc import Sequence

from ..i18n import tr

import argparse
import json
from pathlib import Path
import re
import tempfile

from .parser import parse_skill_file


def skill_name(value: str) -> str:
    """创建使用标准小写名称；不改变已发布技能的读取规则。"""
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', value) or len(value) > 64:
        raise ValueError(tr('skill.authoring.ef22c0bbe2b8'))
    return value


def validate_skill(path: str | Path) -> dict:
    """验证可实际加载的技能，同时拒绝尚未完成的脚手架占位。"""
    root = Path(path).expanduser().resolve()
    skill = parse_skill_file(root / 'SKILL.md')
    if skill.configuration_errors:
        raise ValueError('; '.join(skill.configuration_errors))
    if not skill.description.strip() or not skill.content.strip():
        raise ValueError(tr('skill.authoring.3d31323a8a32'))
    if len(skill.description) > 1024 or '<' in skill.description or '>' in skill.description:
        raise ValueError(tr('skill.authoring.890f2340e81f'))
    if skill.description.startswith('[TODO:'):
        raise ValueError(tr('skill.authoring.1d84b8495239'))
    fence = None
    for line in skill.content.splitlines():
        marker = re.match(r'^\s*(?:[-+*]\s+|\d+[.)]\s+)?(`{3,}|~{3,})(.*)$', line)
        if marker:
            if fence is None:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
        elif fence is None and re.fullmatch(r' {0,3}\[TODO:[^\n]*\]\s*', line):
            raise ValueError(tr('skill.authoring.41c01280de60'))
    return {'path': str(root), 'name': skill.name, 'description': skill.description, 'content': skill.content}


def interface_text(name: str, overrides: Sequence[str] = ()) -> str:
    """生成可供其他 Agent 读取的界面元数据，仅接受已声明字段。"""
    fields = {'display_name': name.replace('-', ' ').title(), 'short_description': tr('skill.authoring.default_description', name=name)[:64]}
    allowed = {'display_name', 'short_description', 'icon_small', 'icon_large', 'brand_color', 'default_prompt'}
    for item in overrides:
        key, separator, value = item.partition('=')
        if not separator or key not in allowed or not value.strip():
            raise ValueError(tr('skill.authoring.bb0bb940629a', override=item))
        fields[key] = value
    if not 25 <= len(fields['short_description']) <= 64:
        raise ValueError(tr('skill.authoring.e5799571dc15'))
    return 'interface:\n' + ''.join('  ' + key + ': ' + json.dumps(value, ensure_ascii=False) + '\n' for key, value in fields.items())


def create_skill(parent: str | Path, name: str, description: str, instructions: str, *,
                 resources: tuple[str, ...] = (), interface: Sequence[str] = (), scaffold: bool = False,
                 examples: bool = False) -> dict:
    """先在隔离目录校验，再独占创建目标；永不覆盖已有用户目录。"""
    skill_name(name)
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise ValueError(tr('skill.authoring.62112f48a1fa'))
    if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 64000:
        raise ValueError(tr('skill.authoring.79ea969147fd'))
    if set(resources) - {'scripts', 'references', 'assets'}:
        raise ValueError(tr('skill.authoring.3939458ecef9'))
    metadata = interface_text(name, interface)
    parent = Path(parent).expanduser().resolve()
    target = parent / name
    parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    with tempfile.TemporaryDirectory(prefix='.skill-draft-', dir=parent) as temporary:
        draft = Path(temporary) / name
        (draft / 'agents').mkdir(parents=True)
        (draft / 'agents/openai.yaml').write_text(metadata, encoding='utf-8')
        text = '---\nname: ' + name + '\ndescription: ' + json.dumps(description.strip(), ensure_ascii=False) + '\n---\n\n' + instructions + '\n'
        (draft / 'SKILL.md').write_text(text, encoding='utf-8')
        for resource in dict.fromkeys(resources):
            (draft / resource).mkdir()
            if examples:
                if resource == 'scripts':
                    (draft / resource / 'example.py').write_text('#!/usr/bin/env python3\n"""技能辅助脚本示例；请替换为实际逻辑。"""\nprint(' + json.dumps(tr('skill.authoring.example_operation'), ensure_ascii=False) + ')\n', encoding='utf-8')
                    (draft / resource / 'example.py').chmod(0o755)
                elif resource == 'references':
                    (draft / resource / 'api_reference.md').write_text(tr('skill.authoring.example_reference'), encoding='utf-8')
                else:
                    (draft / resource / 'example_asset.txt').write_text(tr('skill.authoring.example_asset'), encoding='utf-8')
        if not scaffold:
            validate_skill(draft)
        target.mkdir()
        # 最后公开 SKILL.md，避免扫描到缺少资源的中间产物；失败保留目标供检查。
        for child in sorted(draft.iterdir(), key=lambda entry: entry.name == 'SKILL.md'):
            child.rename(target / child.name)
    return snapshot(target) if not scaffold else {'path': str(target), 'name': name}


def snapshot(path: str | Path) -> dict:
    """未知创建结果只读回查，元数据未完成时明确报错。"""
    root = Path(path).expanduser().resolve()
    if not (root / 'SKILL.md').is_file():
        raise FileNotFoundError(root / 'SKILL.md')
    result = validate_skill(root)
    if not (root / 'agents/openai.yaml').is_file():
        raise ValueError(tr('skill.authoring.84b6d63568f6'))
    return result


def main(argv: list[str] | None = None) -> None:
    """UI 与系统技能共享的命令行装配。"""
    parser = argparse.ArgumentParser(description=tr('skill.authoring.a365ca12160f'))
    commands = parser.add_subparsers(dest='action', required=True)
    create = commands.add_parser('create')
    create.add_argument('name')
    create.add_argument('--path', required=True)
    create.add_argument('--description', required=True)
    create.add_argument('--instructions', required=True)
    create.add_argument('--resources', default='')
    create.add_argument('--interface', action='append', default=[])
    validate = commands.add_parser('validate')
    validate.add_argument('path')
    initialize = commands.add_parser('init')
    initialize.add_argument('name')
    initialize.add_argument('--path', required=True)
    initialize.add_argument('--resources', default='')
    initialize.add_argument('--examples', action='store_true')
    initialize.add_argument('--interface', action='append', default=[])
    metadata = commands.add_parser('interface')
    metadata.add_argument('path')
    metadata.add_argument('--name')
    metadata.add_argument('--interface', action='append', default=[])
    args = parser.parse_args(argv)
    if args.action == 'create':
        result = create_skill(args.path, args.name, args.description, args.instructions,
                              resources=tuple(item.strip() for item in args.resources.split(',') if item.strip()), interface=args.interface)
    elif args.action == 'init':
        name = re.sub(r'[^a-z0-9]+', '-', args.name.strip().lower()).strip('-')
        result = create_skill(args.path, name, tr('skill.authoring.e2b3f8350bb9'),
                              tr('skill.authoring.ce7aa1686e48'),
                              resources=tuple(item.strip() for item in args.resources.split(',') if item.strip()),
                              interface=args.interface, scaffold=True, examples=args.examples)
    elif args.action == 'interface':
        root = Path(args.path).expanduser().resolve()
        name = args.name or parse_skill_file(root / 'SKILL.md').name
        text = interface_text(name, args.interface)
        directory = root / 'agents'
        if not directory.resolve().is_relative_to(root) or not (directory / 'openai.yaml').resolve().is_relative_to(root):
            raise ValueError(tr('skill.authoring.056eb2a0d65b'))
        directory.mkdir(exist_ok=True)
        target = directory / 'openai.yaml'
        if target.is_symlink():
            raise ValueError(tr('skill.authoring.9a2a5f7520fe'))
        if target.exists():
            from .yaml_editing import update_interface
            from .yaml_config import load_mapping
            text = update_interface(target.read_bytes().decode('utf-8'), load_mapping(text)['interface'],
                                    {item.partition('=')[0] for item in args.interface})
        # 同目录原子替换，定点更新后的原文仍保留其它字段、注释和换行。
        with tempfile.NamedTemporaryFile(dir=directory, mode='w', encoding='utf-8', newline='', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        try:
            if target.exists():
                temporary.chmod(target.stat().st_mode & 0o777)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        result = {'path': str(directory / 'openai.yaml')}
    else:
        result = validate_skill(args.path)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()

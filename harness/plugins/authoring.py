"""插件源码创建与校验；所有产物复用正式包解析器。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tempfile

from ..i18n import tr
from .packages import PackageError, read_package

def plugin_source(parent: str | Path, name: str) -> Path:
    """创建与结果回查共用名称和路径规则。"""
    normalized = re.sub(r'[^a-z0-9]+', '-', name.strip().lower()).strip('-')
    if not normalized or len(normalized) > 64:
        raise PackageError(tr('plugin.authoring.name'))
    return Path(parent).expanduser().resolve() / normalized


def create_plugin(parent: str | Path, name: str, description: str, instructions: str = '') -> Path:
    """在明确指定的源码目录创建可安装插件，不覆盖或自动安装。"""
    target = plugin_source(parent, name)
    normalized = target.name
    if not description.strip():
        raise PackageError(tr('plugin.authoring.description_required'))
    manifest = {'$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json',
                'name': normalized, 'version': '0.1.0', 'description': description.strip(),
                'extensions': {'com.openai': {'interface': {'displayName': name.strip()}}}}
    frontmatter = '---\nname: ' + normalized + '\ndescription: ' + json.dumps(description.strip(), ensure_ascii=False) + '\n---\n\n'
    body = instructions if instructions.strip() else description.strip()
    return publish_source(target, {
        'plugin.json': json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
        f'skills/{normalized}/SKILL.md': frontmatter + '# ' + name.strip() + '\n\n' + body + '\n',
    })


def publish_source(target: Path, files: dict[str, str]) -> Path:
    """校验插件源码后独占创建目录，不覆盖已有产物。"""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.plugin-draft-', dir=target.parent) as temporary:
        draft = Path(temporary) / 'source'
        draft.mkdir()
        for name, text in files.items():
            path = draft / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        read_package(draft)
        target.mkdir()
        # 只向本次独占创建的目录发布；失败保留可检查的产物，不删除源码。
        for child in draft.iterdir():
            child.rename(target / child.name)
    return target


def main() -> None:
    """供统一系统技能调用的本地 authoring 命令入口。"""
    parser = argparse.ArgumentParser(description=tr('plugin.authoring.cli_description'))
    commands = parser.add_subparsers(dest='action', required=True)
    create = commands.add_parser('create')
    create.add_argument('name')
    create.add_argument('--path', required=True)
    create.add_argument('--description', required=True)
    create.add_argument('--instructions', default='')
    validate = commands.add_parser('validate')
    validate.add_argument('path')
    args = parser.parse_args()
    if args.action == 'create':
        path = create_plugin(args.path, args.name, args.description, args.instructions)
        print(json.dumps({'path': str(path), **read_package(path).preview()}, ensure_ascii=False))
    else:
        print(json.dumps(read_package(args.path).preview(), ensure_ascii=False))


if __name__ == '__main__':
    main()

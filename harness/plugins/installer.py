"""本地目录安装 / 卸载（技能与Agent）。

本模块只管理独立技能与Agent目录；聚合插件使用独立的包生命周期。
技能安装进入 .agents/skills，系统 .harness/skills 不提供卸载。

安全纪律（Agent 不直接安装）：

* 安装入口只暴露给 REST API（由用户在管理界面触发），不给模型注册
  任何 install 工具；
* 安装前必须通过 :func:`validate` —— 清单存在、名字合法、解析通过、
  未声明禁止字段；
* 卸载仅允许 user / project scope。
"""

from __future__ import annotations

from harness.i18n import tr

import logging
import os
import stat as file_stat
from contextlib import contextmanager
import tempfile
import re
import shutil
from pathlib import Path
from typing import Literal

from ..core.errors import ValidationError
from ..core.file_lock import lock_file, unlock_file
from ..agents.parser import parse_agent_file
from ..skills.parser import is_valid_skill_name, parse_skill_file
from ..skills.paths import installation_path, installation_root, validate_installation

logger = logging.getLogger(__name__)

Kind = Literal["skill", "agent"]
Scope = Literal["user", "project"]

#: 各 kind 对应的清单文件名
MANIFEST_NAMES: dict[str, str] = {"skill": "SKILL.md", "agent": "AGENTS.md"}

#: 目录名合法性（kebab-case，与技能/Agent名同规则）
DIR_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@contextmanager
def _installation_lock(root: Path, name: str):
    """安装和卸载共用稳定旁路锁，竞争时拒绝，不删除锁文件。"""
    with (root / ('.' + name + '.install.lock')).open('a+b') as stream:
        lock_file(stream)
        try:
            yield
        finally:
            unlock_file(stream)


def validate(src: Path, kind: Kind) -> dict:
    """校验一个待安装目录，返回解析出的元数据。

    :raises ValidationError: 清单缺失、名字非法或解析失败。
    """
    validate_installation(kind, "user")
    if not src.is_dir():
        raise ValidationError(tr('python.installer.b5969a577f92', p0=f'{src}'))
    manifest = src / MANIFEST_NAMES[kind]
    if not manifest.exists():
        raise ValidationError(tr('python.installer.1fd65a41b5c4', p0=f'{MANIFEST_NAMES[kind]}', p1=f'{src}'))
    if not (is_valid_skill_name(src.name) if kind == "skill" else DIR_NAME_RE.fullmatch(src.name)):
        raise ValidationError(
            tr('python.installer.70c3571c1f03', p0=f'{src.name!r}')
        )
    if kind == "skill":
        metadata = parse_skill_file(manifest, source="install")
    else:
        metadata = parse_agent_file(manifest, source="install")
    if metadata.name != src.name:
        raise ValidationError(
            tr('python.installer.8a190399329a', p0=f'{metadata.name!r}', p1=f'{src.name!r}')
        )
    return metadata.to_dict()


def install_from_directory(
    src: str | Path,
    *,
    kind: Kind,
    scope: Scope = "user",
    cwd: str | Path | None = None,
    home: str | Path | None = None,
) -> Path:
    """把一个本地目录安装为技能/Agent，返回安装后的目录。

    :raises ValidationError: 校验失败（清单缺失、名字非法、解析失败）。
    :raises FileExistsError: 目标位置已存在同名项。
    """
    validate_installation(kind, scope)
    source = installation_path(src, 'src')
    if source is None:
        raise ValidationError(tr('plugin.installation.invalidPath', field='src'))
    validate(source, kind)
    target_root = installation_root(kind, scope, cwd=cwd, home=home)
    target = target_root / source.name
    if target_root.resolve().is_relative_to(source):
        raise ValidationError(tr('plugin.installation.recursiveSource'))
    target_root.mkdir(parents=True, exist_ok=True)
    expected_root = target_root.stat()
    with _installation_lock(target_root, source.name):
        directory = None
        try:
            if os.open in os.supports_dir_fd and os.rename in os.supports_dir_fd:
                directory = os.open(target_root.resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                opened = os.fstat(directory)
                if (opened.st_dev, opened.st_ino) != (expected_root.st_dev, expected_root.st_ino):
                    raise ValidationError(tr('plugin.installation.rootChanged'))
            if target.exists() or target.is_symlink():
                raise FileExistsError(tr('python.installer.6210945e1c45', p0=tr('common.skill') if kind == 'skill' else tr('common.agent'), p1=str(target)))
            # 暂存放在安装根的同级，根目录被替换不会重定向复制或清理。
            with tempfile.TemporaryDirectory(prefix='.install-', dir=target_root.parent) as folder:
                staged = Path(folder) / source.name
                shutil.copytree(source, staged)
                validate(staged, kind)
                current_root = target_root.stat()
                if (current_root.st_dev, current_root.st_ino) != (expected_root.st_dev, expected_root.st_ino):
                    raise ValidationError(tr('plugin.installation.rootChanged'))
                if target.exists() or target.is_symlink():
                    raise FileExistsError(tr('python.installer.6210945e1c45', p0=tr('common.skill') if kind == 'skill' else tr('common.agent'), p1=str(target)))
                if directory is not None:
                    os.rename(staged, source.name, dst_dir_fd=directory)
                else:
                    staged.rename(target)
                current_root = target_root.stat()
                if (current_root.st_dev, current_root.st_ino) != (expected_root.st_dev, expected_root.st_ino):
                    # 已发布结果不自动重放或沿新路径撤销，I/O失败保持结果未确认。
                    raise OSError(tr('plugin.installation.rootChanged'))
        finally:
            if directory is not None:
                os.close(directory)
    logger.info(tr('log.6c4054fd3de8'), tr('python.installer.99aea2f9131a') if kind == "skill" else tr('python.installer.c3a73c5eecea'), source, target)
    return target


def uninstall(
    name: str,
    *,
    kind: Kind,
    scope: Scope = "user",
    cwd: str | Path | None = None,
    home: str | Path | None = None,
) -> None:
    """卸载一个技能/Agent（仅 user / project scope）。

    :raises ValidationError: 名字非法。
    :raises FileNotFoundError: 目标不存在。
    :raises PermissionError: 目标位于随包内置目录。
    """
    validate_installation(kind, scope)
    if not isinstance(name, str) or not (is_valid_skill_name(name) if kind == "skill" else DIR_NAME_RE.fullmatch(name)):
        raise ValidationError(tr('python.installer.441462c83d99', p0=f'{name!r}'))
    target_root = installation_root(kind, scope, cwd=cwd, home=home)
    target = target_root / name
    if not target_root.is_dir():
        raise FileNotFoundError(tr('python.installer.c92ab54106e9', p0=str(target)))
    with _installation_lock(target_root, name):
        if not target.is_dir():
            raise FileNotFoundError(tr('python.installer.c92ab54106e9', p0=f'{target}'))
        if kind == "skill":
            from ..skills.paths import SYSTEM_SKILL_ROOT as SKILL_BUNDLED

            if target == SKILL_BUNDLED / name:
                raise PermissionError(tr('python.installer.5b689d0c15e5'))
        if os.open in os.supports_dir_fd:
            # 删除绑定已确认根目录，外部改名或换链不能重定向到其他目录。
            expected = target_root.stat()
            descriptor = os.open(target_root.resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
                    raise ValidationError(tr('plugin.installation.rootChanged'))
                entry = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if not file_stat.S_ISDIR(entry.st_mode):
                    raise ValidationError(tr('plugin.installation.rootChanged'))
                shutil.rmtree(name, dir_fd=descriptor)
            finally:
                os.close(descriptor)
        else:
            shutil.rmtree(target)
    logger.info(tr('log.06fd159823aa'), tr('python.installer.99aea2f9131a') if kind == "skill" else tr('python.installer.c3a73c5eecea'), target)


__all__ = [
    "install_from_directory",
    "uninstall",
    "validate",
]

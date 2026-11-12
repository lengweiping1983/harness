"""执行器独立配置；不读取或要求任何模型凭据。"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re

from ..core.errors import ConfigError
from ..i18n import tr


def _path(value: object, key: str) -> Path:
    """拒绝错误路径类型、空文本和NUL，不用默认路径掩盖非法输入。"""
    if not isinstance(value, (str, os.PathLike)) or isinstance(value, str) and not value.strip():
        raise ConfigError(tr(key))
    try:
        path = Path(value).expanduser()
        if '\0' in str(path):
            raise ValueError('NUL in path')
        return path
    except (TypeError, ValueError, RuntimeError) as exc:
        raise ConfigError(tr(key)) from exc


@dataclass(frozen=True)
class ProjectConfig:
    """由本机用户登记的项目与权限。"""

    root: Path
    principals: tuple[str, ...] = ("local",)
    writable: bool = False
    commands: bool = False
    commits: bool = False
    verification: dict[str, tuple[str, ...]] = field(default_factory=dict)
    display_name: str = ''

    def __post_init__(self):
        if not isinstance(self.display_name, str):
            raise ConfigError(tr('execution.config.display_name'))
        try:
            root = _path(self.root, 'execution.config.root').resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ConfigError(tr('execution.config.root')) from exc
        if not root.is_dir():
            raise ConfigError(tr('execution.config.root'))
        if not isinstance(self.principals, (list, tuple)) or not self.principals or any(not isinstance(p, str) or not p for p in self.principals):
            raise ConfigError(tr('execution.config.principals'))
        if any(type(value) is not bool for value in (self.writable, self.commands, self.commits)):
            raise ConfigError(tr('execution.config.permissions'))
        if self.commits and not self.writable:
            raise ConfigError(tr('execution.config.commits'))
        if not isinstance(self.verification, dict):
            raise ConfigError(tr('execution.config.verification'))
        for name, command in self.verification.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or not isinstance(command, (list, tuple)) or not command or any(not isinstance(a, str) or not a for a in command):
                raise ConfigError(tr('execution.config.verification'))
        object.__setattr__(self, "root", root)


@dataclass(frozen=True)
class ExecutionConfig:
    """单进程执行服务配置；命令按服务进程的操作系统权限运行。"""

    home: Path
    projects: dict[str, ProjectConfig]
    max_output_chars: int = 32768
    max_command_ms: int = 600000
    max_file_bytes: int = 8 * 1024 * 1024
    workspace_registry: Path | None = None
    language: str = "auto"
    skill_home: Path | None = None

    def __post_init__(self):
        from ..core.language import normalize_language
        object.__setattr__(self, "language", normalize_language(self.language))
        object.__setattr__(self, "home", _path(self.home, 'execution.config.home').resolve())
        if self.skill_home is not None:
            object.__setattr__(self, 'skill_home', _path(self.skill_home, 'execution.config.skill_home').resolve())
        if self.workspace_registry is not None:
            object.__setattr__(self, 'workspace_registry', _path(self.workspace_registry, 'execution.config.workspace_registry').resolve())
        if not isinstance(self.projects, dict):
            raise ConfigError(tr('execution.config.projects'))
        if not self.projects and self.workspace_registry is None:
            raise ConfigError(tr('execution.config.required'))
        for key, project in self.projects.items():
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key) or not isinstance(project, ProjectConfig):
                raise ConfigError(tr('execution.config.projects'))
            if project.root == self.home or project.root.is_relative_to(self.home):
                raise ConfigError(tr('execution.config.private'))
        roots = [project.root for project in self.projects.values()]
        for index, root in enumerate(roots):
            for other in roots[index + 1:]:
                if self.workspace_registry is None and root != other and (root.is_relative_to(other) or other.is_relative_to(root)):
                    raise ConfigError(tr('execution.config.nested'))
        for value in (self.max_output_chars, self.max_command_ms, self.max_file_bytes):
            if type(value) is not int or value <= 0:
                raise ConfigError(tr('execution.config.limits'))
        if self.max_output_chars > 32768:
            raise ConfigError(tr('execution.config.output'))

    @classmethod
    def load(cls, filename: str | Path) -> ExecutionConfig:
        """加载显式配置，拒绝模型设置和未知字段。"""
        path = Path(filename).resolve(strict=True)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data, base=path.parent)

    @classmethod
    def from_dict(cls, data: object, *, base: Path) -> ExecutionConfig:
        """校验已读取的当前配置快照，路径按配置所在目录解析。"""
        base = Path(base).resolve()
        if not isinstance(data, dict) or set(data) - {"home", "projects", "max_output_chars", "max_command_ms", "max_file_bytes", "workspace_registry", "language", "skill_home"}:
            raise ConfigError(tr('execution.config.fields'))
        raw_projects = data.get("projects", {})
        if not isinstance(raw_projects, dict):
            raise ConfigError(tr('execution.config.projects'))
        projects = {}
        for name, entry in raw_projects.items():
            if not isinstance(entry, dict):
                raise ConfigError(tr('execution.config.projects'))
            if set(entry) - {"root", "principals", "writable", "commands", "commits", "verification", "display_name"}:
                raise ConfigError(tr('execution.config.fields'))
            if not isinstance(entry.get("root"), str) or not entry["root"].strip():
                raise ConfigError(tr('execution.config.root'))
            value = dict(entry)
            root = Path(value.pop("root"))
            principals = value.get("principals", ["local"])
            verification = value.get("verification", {})
            if not isinstance(principals, list):
                raise ConfigError(tr('execution.config.principals'))
            if not isinstance(verification, dict) or any(not isinstance(argv, list) for argv in verification.values()):
                raise ConfigError(tr('execution.config.verification'))
            value["principals"] = tuple(principals)
            value["verification"] = {key: tuple(argv) for key, argv in verification.items()}
            projects[name] = ProjectConfig(root=root if root.is_absolute() else base / root, **value)
        raw_home = data.get('home', '.harness-executor')
        raw_source = data.get('workspace_registry')
        raw_skill_home = data.get('skill_home')
        if raw_skill_home is not None and not isinstance(raw_skill_home, str):
            raise ConfigError(tr('execution.config.skill_home'))
        skill_home = _path(raw_skill_home, 'execution.config.skill_home') if raw_skill_home is not None else None
        if not isinstance(raw_home, str):
            raise ConfigError(tr('execution.config.home'))
        if raw_source is not None and not isinstance(raw_source, str):
            raise ConfigError(tr('execution.config.workspace_registry'))
        home = _path(raw_home, 'execution.config.home')
        source = _path(raw_source, 'execution.config.workspace_registry') if raw_source is not None else None
        return cls(home=home if home.is_absolute() else base / home, projects=projects,
                   skill_home=(skill_home if skill_home.is_absolute() else base / skill_home) if skill_home is not None else None,
                   workspace_registry=(source if source.is_absolute() else base / source) if source else None,
                   **{key: data[key] for key in ("max_output_chars", "max_command_ms", "max_file_bytes", "language") if key in data})

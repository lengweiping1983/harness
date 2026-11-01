"""SKILL.md 解析器。

一个技能就是一个目录，里面有一个 ``SKILL.md``：

.. code-block:: markdown

    ---
    name: pdf-processing
    description: 处理 PDF 的提取、合并与拆分
    when_to_use: 用户提到 PDF 文档操作时
    metadata:
      author: team-a
      version: 2
    ---

    # PDF 处理

    正文（Markdown）……

本模块负责：
* 通过同一有界 YAML 数据解析器读取 frontmatter 与 sidecar；
* 校验 ``name`` 必须是 kebab-case；
* 拆出 ``content``（正文）与 ``metadata``（结构化附加信息）。
"""

from __future__ import annotations


from harness.i18n import tr

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import SkillParseError

#: 保留发布技能名称的大小写；禁止路径字符与限定名分隔符。
SKILL_NAME_PATTERN = r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*"
SKILL_NAME_RE = re.compile("^" + SKILL_NAME_PATTERN + "$")
#: frontmatter 分隔符
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    """一个技能的完整定义。"""

    name: str
    description: str
    content: str
    path: Path | None = None
    when_to_use: str | None = None
    source: str = "project"
    provider: str = "filesystem"
    metadata: dict[str, Any] = field(default_factory=dict)
    #: 同名冲突时的优先级，rank 小的胜出（由 provider 在注册时设置）
    rank: int = 600
    #: 是否允许模型主动调用
    model_invocable: bool = True
    #: 是否允许用户通过命令调用
    user_invocable: bool = True
    #: 聚合插件所有者；独立技能为空。
    plugin_id: str | None = None
    interface: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    dependencies: dict[str, Any] = field(default_factory=dict)
    configuration_errors: list[str] = field(default_factory=list)

    def summary_line(self, *, max_description: int = 500) -> str:
        """渲染成目录里的一行（名称 + 描述）。"""
        description = self.description.strip().replace("\n", " ")
        if len(description) > max_description:
            description = description[: max_description - 1] + "…"
        if self.when_to_use:
            return tr('catalog.skill.withUsage', name=self.name, description=description,
                      usage=self.when_to_use)
        return tr('catalog.skill.single', name=self.name, description=description)

    def to_dict(self) -> dict[str, Any]:
        """转成可 JSON 化的字典（供 UI 展示）。"""
        return {
            "name": self.name,
            "plugin_id": self.plugin_id,
            "description": self.description,
            "when_to_use": self.when_to_use,
            "content": self.content,
            "path": str(self.path) if self.path else None,
            "source": self.source,
            "provider": self.provider,
            "metadata": dict(self.metadata),
            "rank": self.rank,
            "model_invocable": self.model_invocable,
            "user_invocable": self.user_invocable,
            "interface": dict(self.interface),
            "policy": dict(self.policy),
            "dependencies": dict(self.dependencies),
            "configuration_errors": list(self.configuration_errors),
        }


def is_valid_skill_name(name: str) -> bool:
    """判断技能裸名是否合法，保留已发布名称的大小写。"""
    return bool(SKILL_NAME_RE.fullmatch(name))


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """把文本拆成 ``(frontmatter 字典, 正文)``。

    使用共享的有界 YAML 映射解析器，支持多行标量与嵌套数据。

    :raises SkillParseError: frontmatter 结构非法。
    """
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    raw = match.group(1)
    body = text[match.end() :]
    from .yaml_config import load_mapping
    return load_mapping(raw), body


def parse_skill_file(path: Path, *, source: str = "project", provider: str = "filesystem") -> SkillMetadata:
    """从 ``SKILL.md`` 文件解析一个技能。

    :raises SkillParseError: 文件不存在、缺少必填字段或名字非法。
    """
    if not path.exists():
        raise SkillParseError(tr('python.parser.0fb9967cea9f', p0=f'{path}'))
    try:
        with path.open('rb') as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError(tr('config.validation.message_17'))
        text = raw.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    except (OSError, ValueError) as exc:
        raise SkillParseError(str(path) + ': ' + str(exc)) from exc
    data, body = parse_frontmatter(text)

    if 'name' in data and not isinstance(data['name'], str):
        raise SkillParseError(tr('config.validation.message_18'))
    name = data.get("name", "").strip()
    if not name:
        # 允许省略 name，用目录名兜底
        name = path.parent.name
    if not is_valid_skill_name(name):
        raise SkillParseError(
            tr('python.parser.734004771437', p0=f'{name!r}'),
            name=name,
        )

    if 'description' in data and not isinstance(data['description'], str):
        raise SkillParseError(tr('config.validation.message_19'))
    description = data.get("description", "").strip()
    if not description:
        raise SkillParseError(tr('python.parser.157ecd05f648', p0=f'{name!r}'), name=name)

    if 'metadata' in data and not isinstance(data['metadata'], dict):
        raise SkillParseError(tr('config.field.mapping', field='metadata'))
    if 'when_to_use' in data and data['when_to_use'] is not None and not isinstance(data['when_to_use'], str):
        raise SkillParseError(tr('config.field.string', field='when_to_use'))
    metadata_raw = data.get("metadata")
    metadata = dict(metadata_raw) if metadata_raw is not None else {}
    for key in ('model_invocable', 'user_invocable'):
        if key in data and type(data[key]) is not bool:
            raise SkillParseError(tr('config.field.boolean', field=key))
    from .sidecar import read_sidecar
    interface, policy, dependencies, configuration_errors = read_sidecar(path.parent)

    return SkillMetadata(
        name=name,
        description=description,
        when_to_use=(data["when_to_use"].strip() if data.get("when_to_use") else None),
        content=body.strip(),
        path=path,
        source=source,
        provider=provider,
        metadata=metadata,
        model_invocable=data.get("model_invocable", True) and policy.get('allow_implicit_invocation', True) and not configuration_errors,
        user_invocable=data.get("user_invocable", True),
        interface=interface, policy=policy, dependencies=dependencies, configuration_errors=configuration_errors,
    )


__all__ = [
    "SkillMetadata",
    "parse_frontmatter",
    "parse_skill_file",
    "is_valid_skill_name",
    "SKILL_NAME_RE",
]

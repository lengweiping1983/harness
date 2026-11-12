"""AGENTS.md 解析器。

一个Agent就是一个目录，里面有一个 ``AGENTS.md``（结构与 SKILL.md 同构，
复用 :mod:`harness.skills.parser` 的 frontmatter YAML 子集解析器）。

frontmatter 字段：

* ``name``            —— 必填，kebab-case（与技能同规则），缺省时用目录名兜底；
* ``description``     —— 可选，一句话说明职责（模型据此判断是否召唤）；
* ``agent_type``     —— ``agent``（单Agent，默认）或 ``team``（Agent 团队）；
* ``model``           —— 可选，该Agent使用的模型覆盖；
* ``skills``          —— 可选技能名列表，省略或 null 继承默认，空列表不启用技能；
* ``members``         —— team 型必填，成员列表，每项 ``name`` 必填，
  ``role``（lead/member）、``agent``（引用另一个单Agent）、``prompt``
  （内联成员指令）、``model``（成员级模型覆盖）、``depends_on``（前序成员名数组）可选；
* ``metadata``        —— 任意附加键值。

声明约束：**禁止声明 ``tools`` 字段**——
工具权限由系统统一分配。team 型缺 ``members`` 视为解析失败。
"""

from __future__ import annotations

from harness.i18n import tr

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import AgentParseError, SkillParseError
from ..skills.parser import parse_frontmatter

#: 合法的Agent名：kebab-case（与技能同规则）
from ..skills.parser import is_valid_skill_name as _is_valid_name

#: 允许的Agent类型
AGENT_TYPES = ("agent", "team")

#: 团队统一由主会话使用通用原语调度，不再接受固定三阶段配置。

#: frontmatter 里被禁止的字段（工具权限由系统统一分配）
FORBIDDEN_FIELDS = ("tools", "allowed-tools", "allowed_tools")

#: team 型Agent的成员数上限（防止Agent 团队失控）
MAX_TEAM_MEMBERS = 8

@dataclass(frozen=True, slots=True)
class AgentMember:
    """Agent 团队的一个成员。

    :param name:   成员名（kebab-case）。
    :param role:   ``lead``（主理人，负责拆解与汇总）或 ``member``。
    :param agent: 引用另一个已定义的单Agent名（可选）。
    :param prompt: 成员专属指令（可选；与 ``agent`` 二选一或叠加）。
    :param model:  成员级模型覆盖（可选；如 ``deepseek/deepseek-r1``，
        让"重推理成员用 R1、轻量成员用快模型"这类混编成为可能）。
    :param depends_on: 必须成功完成的前序执行成员，结果传入当前任务。
    :param runner: 独立 ACP 编码 Agent 预设；仅执行成员可指定，模型由外部 CLI 管理。
    :param access: 执行期的访问级别。``inherit`` 表示不额外收权；
        ``read`` 表示由引擎强制只读——该成员运行期间所有非只读工具调用都会被拒绝。
        只读成员不能使用独立 ACP 执行器（外部进程不受宿主只读闸约束）。
    """

    name: str
    role: str = "member"
    agent: str | None = None
    prompt: str = ""
    model: str | None = None
    depends_on: tuple[str, ...] = ()
    #: 独立编码 Agent 预设；未指定时按 execution 继承Agent或使用聊天模型。
    runner: str | None = None
    #: 明确选择 model 时不继承引用Agent的执行器及固定模型。
    execution: str = 'inherit'
    display_name: str | None = None
    #: 执行期访问级别：``inherit``（不额外收权）或 ``read``（引擎强制只读）。
    access: str = 'inherit'
    #: 成员显式选择优先于引用Agent和团队的技能配置。
    skills: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "agent": self.agent,
            "prompt": self.prompt,
            "model": self.model,
            **({"display_name": self.display_name} if self.display_name else {}),
            **({"execution": self.execution} if self.execution != "inherit" else {}),
            **({"depends_on": list(self.depends_on)} if self.depends_on else {}),
            **({"runner": self.runner} if self.runner else {}),
            **({"access": self.access} if self.access != "inherit" else {}),
            **({"skills": list(self.skills)} if self.skills is not None else {}),
        }


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """一个Agent的完整定义。"""

    name: str
    description: str
    #: 正文（agent 型 = 人设 + 方法论；team 型 = 团队协作协议）
    system_prompt: str = ""
    #: ``agent``（单Agent）或 ``team``（Agent 团队）
    agent_type: str = "agent"
    #: 可选：该Agent使用的模型覆盖（如 ``deepseek/deepseek-r1``）
    model: str | None = None
    #: team 型的成员列表
    members: tuple[AgentMember, ...] = ()
    #: 团队使用主会话原语调度；不接受固定三阶段配置。
    #: （提示词驱动：主理人成为会话主 agent 现场编排）。
    path: Path | None = None
    source: str = "user"
    provider: str = "filesystem"
    metadata: dict[str, Any] = field(default_factory=dict)
    #: 同名冲突时的优先级，rank 小的胜出（由 provider 在注册时设置）
    rank: int = 500
    #: None 继承默认Agent的技能，空元组表示明确不使用技能。
    skills: tuple[str, ...] | None = None

    @property
    def is_team(self) -> bool:
        """是否是Agent 团队。"""
        return self.agent_type == "team"

    def summary_line(self, *, max_description: int = 500) -> str:
        """渲染成目录里的一行（名称 + 描述）。"""
        description = self.description.strip().replace("\n", " ")
        if len(description) > max_description:
            description = description[: max_description - 1] + "…"
        return tr('catalog.agent.team' if self.is_team else 'catalog.agent.single',
                  name=self.name, description=description)

    def to_dict(self) -> dict[str, Any]:
        """转成可 JSON 化的字典（供 UI 展示）。"""
        return {
            "name": self.name,
            "description": self.description,
            "agent_type": self.agent_type,
            "system_prompt": self.system_prompt,
            "model": self.model,
            "skills": list(self.skills) if self.skills is not None else None,
            **({"runner": self.metadata["runner"]} if self.metadata.get("runner") else {}),
            "members": [member.to_dict() for member in self.members],
            "path": str(self.path) if self.path else None,
            "source": self.source,
            "provider": self.provider,
            "metadata": dict(self.metadata),
            "rank": self.rank,
        }


def is_valid_agent_name(name: str) -> bool:
    """判断Agent名是否满足 kebab-case 语法（与技能同规则）。"""
    return name == name.lower() and _is_valid_name(name)


def parse_skill_selection(raw: Any, *, name: str) -> tuple[str, ...] | None:
    """校验可移植技能名列表，保留继承与空选择的区别。"""
    if raw is None:
        return None
    if (not isinstance(raw, list) or len(raw) > 1000
            or any(not isinstance(value, str) or len(value) > 200
                   or not all(_is_valid_name(part) for part in value.split(':'))
                   or value.count(':') > 1 for value in raw)
            or len(set(raw)) != len(raw)):
        raise AgentParseError(tr('agent.authoring.invalid', field='skills'), name=name)
    return tuple(raw)


def _parse_members(raw: Any, *, name: str) -> tuple[AgentMember, ...]:
    """把 frontmatter 的 ``members`` 列表解析成 :class:`AgentMember` 元组。"""
    if not isinstance(raw, list) or not raw:
        raise AgentParseError(tr('python.parser.c5dab8adf9c1', p0=f'{name!r}'), name=name)
    if len(raw) > MAX_TEAM_MEMBERS:
        raise AgentParseError(
            tr('python.parser.d8d46aa54813', p0=f'{name!r}', p1=f'{MAX_TEAM_MEMBERS}', p2=f'{len(raw)}'),
            name=name,
        )
    members: list[AgentMember] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise AgentParseError(
                tr('python.parser.cbb4b36f81cb', p0=f'{name!r}', p1=f'{index + 1}', p2=f'{type(item).__name__}'),
                name=name,
            )
        for key in ('name', 'role', 'prompt'):
            if key in item and not isinstance(item[key], str):
                raise AgentParseError(tr('config.field.string', field='members.' + key), name=name)
        for key in ('agent', 'model'):
            if key in item and item[key] is not None and not isinstance(item[key], str):
                raise AgentParseError(tr('config.field.string', field='members.' + key), name=name)
        member_name = item.get("name", "").strip()
        if not is_valid_agent_name(member_name):
            raise AgentParseError(
                tr('python.parser.4a75c4358f14', p0=f'{name!r}', p1=f'{index + 1}', p2=f'{member_name!r}'),
                name=name,
            )
        role = str(item.get("role", "member")).strip().lower()
        if role not in ("lead", "member"):
            raise AgentParseError(
                tr('python.parser.fe18a2167b7c', p0=f'{name!r}', p1=f'{member_name!r}', p2=f'{role!r}'),
                name=name,
            )
        unknown = set(item) - {'name', 'role', 'agent', 'prompt', 'model', 'depends_on', 'runner', 'execution', 'display_name', 'access', 'skills'}
        if unknown:
            raise AgentParseError(tr('agent.definition.fields_invalid', p0=', '.join(sorted(unknown))), name=name)
        agent_ref = item.get("agent")
        agent_ref = str(agent_ref).strip() if agent_ref else None
        if agent_ref and not is_valid_agent_name(agent_ref):
            raise AgentParseError(
                tr('python.parser.d4b017cd39c5', p0=f'{name!r}', p1=f'{member_name!r}', p2=f'{agent_ref!r}'),
                name=name,
            )
        prompt = item.get("prompt")
        prompt = str(prompt).strip() if prompt else ""
        model = item.get("model")
        model = str(model).strip() if model else None
        display_name = item.get('display_name')
        if display_name is not None and (not isinstance(display_name, str) or len(display_name) > 120):
            raise AgentParseError(tr('agent.team.display_name_invalid'), name=name)
        display_name = display_name.strip() or None if isinstance(display_name, str) else None
        execution = item.get('execution', 'inherit')
        if execution not in ('inherit', 'model'):
            raise AgentParseError(tr('agent.team.execution_invalid'), name=name)
        runner = item.get('runner')
        if runner is not None and (not isinstance(runner, str) or runner not in {'codex', 'claude', 'opencode'}):
            raise AgentParseError(tr('acp.team.invalid_runner'), name=name)
        if runner and (role == 'lead' or model or execution == 'model'):
            raise AgentParseError(tr('acp.team.invalid_member'), name=name)
        access = item.get('access', 'inherit')
        if access not in ('inherit', 'read'):
            raise AgentParseError(tr('agent.team.access_invalid'), name=name)
        # 只读成员不能用独立执行器：外部进程不受宿主只读闸约束，声明只会是空保证。
        if access == 'read' and runner:
            raise AgentParseError(tr('agent.team.access_runner_conflict'), name=name)
        skills = parse_skill_selection(item.get('skills'), name=name)
        if runner and skills:
            raise AgentParseError(tr('ui.agent.skills.externalHint'), name=name)
        dependencies = item.get('depends_on', [])
        if not isinstance(dependencies, list) or any(not isinstance(dep, str) for dep in dependencies):
            raise AgentParseError(tr('agent.team.dependencies_invalid'), name=name)
        members.append(
            AgentMember(
                name=member_name, role=role, agent=agent_ref, prompt=prompt, model=model,
                depends_on=tuple(dependencies),
                runner=runner, execution=execution, display_name=display_name,
                access=access,
                skills=skills,
            )
        )
    names = [member.name for member in members]
    if len(set(names)) != len(names):
        raise AgentParseError(tr('python.parser.3ce94c4047cf', p0=f'{name!r}'), name=name)
    if not any(member.role == "lead" for member in members):
        # 没有显式 lead 时第一个成员默认为主理人。
        members[0] = dataclasses.replace(members[0], role="lead")
    if any(member.runner and member.role == 'lead' for member in members):
        raise AgentParseError(tr('acp.team.invalid_member'), name=name)
    from .dependencies import validate_dependencies
    try:
        validate_dependencies(members)
    except ValueError as exc:
        raise AgentParseError(tr('agent.team.dependencies_invalid'), name=name) from exc
    return tuple(members)


def parse_agent_file(
    path: Path, *, source: str = "user", provider: str = "filesystem"
) -> AgentDefinition:
    """从 ``AGENTS.md`` 文件解析一个Agent。

    :raises AgentParseError: 文件不存在、缺少必填字段、声明了禁止字段或
        结构非法。
    """
    if not path.exists():
        raise AgentParseError(tr('python.parser.422090a76412', p0=f'{path}'))
    try:
        text = path.read_text("utf-8")
    except (OSError, UnicodeError) as exc:
        raise AgentParseError(tr('agent.definition.read_failed', path=str(path)), path=str(path)) from exc
    return parse_agent_text(text, path=path, source=source, provider=provider)


def parse_agent_text(text: str, *, path: Path = Path("imported-agent/AGENTS.md"), source: str = "user", provider: str = "filesystem") -> AgentDefinition:
    """解析导入或编辑中的Agent文本，不要求先创建文件。"""
    try:
        data, body = parse_frontmatter(text)
    except SkillParseError as exc:
        raise AgentParseError(exc.message, path=str(path)) from exc

    unknown = set(data) - {'name', 'description', 'agent_type', 'model', 'members', 'metadata', 'skills', *FORBIDDEN_FIELDS}
    if unknown:
        raise AgentParseError(tr('agent.definition.fields_invalid', p0=', '.join(sorted(unknown))), path=str(path))
    for forbidden in FORBIDDEN_FIELDS:
        if forbidden in data:
            raise AgentParseError(
                tr('python.parser.d250be88e470', p0=f'{forbidden!r}'),
                path=str(path),
            )

    for key in ('name', 'description', 'agent_type'):
        if key in data and not isinstance(data[key], str):
            raise AgentParseError(tr('config.field.string', field=key), path=str(path))
    if 'model' in data and data['model'] is not None and not isinstance(data['model'], str):
        raise AgentParseError(tr('config.field.string', field='model'), path=str(path))
    if 'metadata' in data and not isinstance(data['metadata'], dict):
        raise AgentParseError(tr('config.field.mapping', field='metadata'), path=str(path))
    name = data.get("name", "").strip()
    if not name:
        # 允许省略 name，用目录名兜底（与技能一致）
        name = path.parent.name
    if not is_valid_agent_name(name):
        raise AgentParseError(
            tr('python.parser.63b9d62c3cc0', p0=f'{name!r}'),
            name=name,
        )

    description = data.get("description", "").strip()

    agent_type = str(data.get("agent_type", "agent")).strip().lower() or "agent"
    if agent_type not in AGENT_TYPES:
        raise AgentParseError(
            tr('python.parser.e8b1247d708b', p0=f'{name!r}', p1=f"{'/'.join(AGENT_TYPES)}", p2=f'{agent_type!r}'),
            name=name,
        )

    model = data.get("model")
    model = str(model).strip() if model else None

    members: tuple[AgentMember, ...] = ()
    if agent_type == "team":
        members = _parse_members(data.get("members"), name=name)

    metadata_raw = data.get("metadata")
    metadata = dict(metadata_raw) if metadata_raw is not None else {}
    if agent_type == 'team' and 'member_context' in metadata and metadata['member_context'] not in ('persistent', 'ephemeral'):
        raise AgentParseError(tr('agent.team.context_mode_invalid'), name=name)
    if agent_type == 'team':
        from .limits import member_wait_timeout
        try:
            member_wait_timeout(metadata)
        except ValueError as exc:
            raise AgentParseError(tr('agent.team.timeout_invalid'), name=name) from exc
    runner = metadata.get('runner')
    if runner is not None and (runner not in ('codex', 'claude', 'opencode') or agent_type != 'agent' or model):
        raise AgentParseError(tr('agent.execution.invalid'), name=name)
    skills = parse_skill_selection(data.get('skills'), name=name)
    if runner and skills:
        raise AgentParseError(tr('ui.agent.skills.externalHint'), name=name)
    if 'personality_prompt' in metadata:
        from .personality import normalize_prompt
        from ..core.errors import HarnessError
        try:
            metadata['personality_prompt'] = normalize_prompt(metadata['personality_prompt'])
        except HarnessError as exc:
            raise AgentParseError(exc.message, name=name) from exc

    return AgentDefinition(
        name=name,
        description=description,
        system_prompt=body.strip(),
        agent_type=agent_type,
        model=model,
        members=members,
        path=path,
        source=source,
        provider=provider,
        metadata=metadata,
        skills=skills,
    )


__all__ = [
    "AgentMember",
    "AgentDefinition",
    "is_valid_agent_name",
    "parse_agent_file",
    "parse_agent_text",
    "AGENT_TYPES",
    "MAX_TEAM_MEMBERS",
]

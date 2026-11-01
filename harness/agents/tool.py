"""模型可见的Agent召唤工具。

与 :mod:`harness.skills.tool` 对称的两段式设计：

1. **目录**：小目录直接列名与描述；自动模式的大目录提供
   ``agent(action="search", name="关键词")`` 分页入口，不加载完整角色提示词；
2. **召唤**：模型调用 ``agent(name=...)``——

   * ``agent`` 型：把该Agent挂载进当前会话（人格切换），此后整个会话
     以该Agent身份工作；再次召唤即切换，``name=""`` 退场；
   * ``team`` 型·团队运行：召唤即挂载——
     主理人成为会话主 agent，用 spawn_agent / send_message 等通用原语现场
     编排，纪律写在提示词里。

用户侧常驻入口是 Agent / 团队选择控件；斜杠命令
``/agent <name> <任务>`` 只执行一次，不等价于模型侧的挂载操作。
"""

from __future__ import annotations

from harness.i18n import tr

from typing import Any, Callable
import json
import re

from ..core.errors import AgentError, AgentNotFoundError
from ..core.events import EventBus
from ..llm.types import TextBlock
from ..tools.registry import ToolDefinition, ToolRunContext, ToolResult
from .mount import AgentMountController
from .registry import AgentRegistry

#: 渲染给模型的目录块
CATALOG_HEADER = "<available_agents>"
CATALOG_FOOTER = "</available_agents>"
CATALOG_INLINE_CHARS = 1600


def render_catalog(registry: AgentRegistry, *, max_description: int = 500, searchable: bool = False) -> str:
    """渲染Agent目录块。"""
    if not registry.list():
        return ""
    text = (
        tr('python.tool.c83d88ddae93', p0=f'{CATALOG_HEADER}', p1=f'{registry.catalog(max_description=max_description)}', p2=f'{CATALOG_FOOTER}')
    )
    if searchable and len(text) > CATALOG_INLINE_CHARS:
        return tr('agent.catalog.discovery', count=len(registry.list()))
    return text


def search_catalog(registry, query, limit=5, offset=0):
    """只搜索当前会话可见的角色摘要，不挂载、执行或返回完整提示词。"""
    if (not isinstance(query, str) or len(query) > 1000 or type(limit) is not int
            or not 1 <= limit <= 10 or type(offset) is not int or offset < 0):
        raise AgentError(tr('agent.catalog.invalid'))
    query = query.strip().casefold()
    words = re.findall(r'[\w]+', query)
    ranked = []
    for agent in registry.list():
        name = agent.name.casefold()
        description = agent.description.casefold()
        kind = agent.agent_type.casefold()
        scores = [10 * (word in name) + 3 * (word in kind) + (word in description) for word in words]
        if words and not all(scores):
            continue
        ranked.append((name == query, sum(scores), agent.name, agent))
    ranked.sort(key=lambda row: (-row[0], -row[1], row[2]))
    selected = ranked[offset:offset + limit]
    return {'agents': [{'name': agent.name, 'agent_type': agent.agent_type,
                         'description': agent.description[:240]} for _, _, _, agent in selected],
            'total_matches': len(ranked), 'next_offset': offset + limit if offset + limit < len(ranked) else None}


def build_agent_tool(
    registry: AgentRegistry,
    mount: AgentMountController,
    *,
    bus: EventBus | None = None,
    registry_for_session: Callable[[Any], AgentRegistry] | None = None,
    prompt_activation: Callable[..., Any] | None = None,
) -> list[ToolDefinition]:
    """构造 ``agent`` 召唤工具。"""

    async def summon_agent(args: Any, ctx: ToolRunContext) -> ToolResult:
        """召唤 / 切换 / 退场一个Agent。"""
        action = args.get('action', 'mount')
        if not isinstance(action, str) or action not in {'mount', 'search'}:
            raise AgentError(tr('agent.catalog.invalid'))
        if action == 'search':
            session = ctx.services.get('session') or mount.session_of(ctx.session_id)
            if session is None:
                raise AgentError(tr('agent.session_missing'))
            active_registry = registry_for_session(session) if registry_for_session else registry
            result = search_catalog(active_registry, args.get('name', ''), args.get('limit', 5), args.get('offset', 0))
            return ToolResult(content=[TextBlock(json.dumps(result, ensure_ascii=False))])
        name = str(args.get("name", "")).strip()
        task = str(args.get("task", "")).strip()
        session = ctx.services.get("session") or mount.session_of(ctx.session_id)
        if session is None:
            raise AgentError(tr("agent.session_missing"), agent=name)

        # 退场
        if not name:
            await mount.set(session, None)
            await _emit(bus, ctx.session_id, None, "off")
            return ToolResult(
                content=[TextBlock(text=tr('python.tool.6577cb93ba5b'))],
                display={"agent": None, "action": "off"},
            )

        try:
            active_registry = registry_for_session(session) if registry_for_session else registry
            agent = active_registry.get(name)
        except AgentNotFoundError as exc:
            return ToolResult(
                content=[TextBlock(text=str(exc))],
                is_error=True,
                display={"error": str(exc), "requested": name},
            )

        if agent.is_team:
            from .prompt_team import render_activation_notice
            # 会话已选中同名团队时，校验选择时写入的当前编排方式，
            # 使「一次性召唤」与「常驻选择」对同一团队保持一致语义。
            active = mount.active_projection(ctx.session_id)
            # team 型·提示词驱动：召唤即挂载——主理人成为会话主 agent，
            # 用通用原语现场编排。激活通知带上完整片段，
            # 使当次回合即可开始编排（系统提示片段下一回合才常驻）。
            same_selection = active is not None and active.agent == agent.name
            await mount.set(session, agent.name,
                team_worktrees=active.team_worktrees if same_selection else False,
                team_working_copy=active.team_working_copy if same_selection else False)
            await _emit(bus, ctx.session_id, agent, "mount-team-prompt")
            immediate = await prompt_activation(session, agent) if prompt_activation else True
            notice = render_activation_notice(agent, active_registry, task) if immediate else tr('agent.prompt_team.deferred', p0=agent.name)
            return ToolResult(
                content=[TextBlock(text=notice)],
                display={"agent": agent.name, "action": "mount-team-prompt",
                         "members": [m.name for m in agent.members]},
            )

        # agent 型：挂载人格（本回合结束后下一请求即以Agent身份工作）
        await mount.set(session, agent.name)
        await _emit(bus, ctx.session_id, agent, "mount")
        body = (
            tr('python.tool.e34b88b55c54', p0=f'{agent.name}', p1=f'{agent.description}')
        )
        return ToolResult(
            content=[TextBlock(text=body)],
            display={
                "agent": agent.name,
                "action": "mount",
                "description": agent.description,
            },
        )

    return [
        ToolDefinition(
            schema={
                "description_key": 'python.tool.faca2299649c',
                "parameter_keys": {
                    ('properties', 'action', 'description'): 'agent.catalog.action',
                    ('properties', 'name', 'description'): 'python.tool.d6e6f14499d6',
                    ('properties', 'task', 'description'): 'python.tool.54da243a35ba',
                },
                "name": "agent",
                "description": (
                    tr('python.tool.faca2299649c')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["mount", "search"], "default": "mount",
                                   "description": tr('agent.catalog.action')},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                        "offset": {"type": "integer", "minimum": 0, "default": 0},
                        "name": {
                            "type": "string",
                            "description": tr('python.tool.d6e6f14499d6'),
                        },
                        "task": {
                            "type": "string",
                            "description": tr('python.tool.54da243a35ba'),
                        },
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
            execute=summon_agent,
            effect="read",
            idempotent=True,
            concurrency_safe=True,
        )
    ]


async def _emit(bus: EventBus | None, session_id: str, agent: Any, action: str) -> None:
    """广播 ``agent/loaded`` 事件（UI 展示召唤状态）。"""
    if bus is None:
        return
    payload: dict[str, Any] = {"action": action}
    if agent is not None:
        payload.update(
            {
                "name": agent.name,
                "description": agent.description,
                "agent_type": agent.agent_type,
                "path": str(agent.path) if agent.path else None,
            }
        )
    await bus.emit("agent/loaded", session_id, payload)


__all__ = ["build_agent_tool", "render_catalog", "search_catalog", "CATALOG_HEADER", "CATALOG_FOOTER"]

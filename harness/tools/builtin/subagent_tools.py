"""子代理工具。

对外暴露八个工具：``spawn_agent`` / ``list_agents`` / ``wait_agent`` / ``wait_agents`` /
``send_message`` / ``followup_task`` / ``interrupt_agent`` / ``member_evidence``。它们都是 :class:`SubagentManager`
的薄封装——真正的生命周期管理在 :mod:`harness.subagent.manager`，
这里只负责参数校验、异常翻译与给模型看的文本整形。

``spawn_agent`` 的 ``agent`` 可选参数接收已定义 Agent 名，由宿主把其人设附加到 prompt
头部，调用方只写任务本身——提示词驱动Agent 团队的主理人据此派工成员，
不必在 prompt 里手抄人设原文。
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
import json
from typing import Any

from ...core.cleanup import raise_cleanup_errors
from ...core.errors import ToolExecutionError
from ...llm.types import TextBlock
from ...subagent.manager import SubagentManager, validate_spawn_text, validate_wait_ids, validate_wait_timeout
from ...subagent.archive import subagent_summary
from ...session.agent_messages import validate_message_text
from ...core.runs import AgentRun, ACTIVE_STATES, TERMINAL_STATES
from ..registry import ToolDefinition, ToolRunContext, ToolResult

#: wait_agent 默认等待秒数
DEFAULT_WAIT_TIMEOUT = 120.0
#: wait_agent 最大等待秒数（防止单次工具调用长时间挂住事件循环）
MAX_WAIT_TIMEOUT = 600.0
#: list_agents 默认返回条数
DEFAULT_LIMIT = 50
_RUN_STATUSES = ACTIVE_STATES | TERMINAL_STATES



def _state_lines(states: list[AgentRun]) -> str:
    if not states:
        return tr('python.subagent_tools.f8ca99907123')
    return "\n".join(s.summary_line() for s in states)


def build_subagent_tools(manager: SubagentManager, *, agent_persona: Any = None, configured_spawn: Any = None, configured_followup: Any = None) -> list[ToolDefinition]:
    """构造协作工具，绑定到给定的 :class:`SubagentManager`。

    :param agent_persona: 可选的人设解析器 ``(agent_name, session_id) -> str | None``；
        由宿主（Agent系统）提供，供 ``spawn_agent`` 工具的 ``agent`` 参数把Agent
        人设附加到 prompt 头部。未提供时该参数不可用。
    """

    def resolve_persona(name: str, session_id: str) -> str | None:
        if agent_persona is None:
            return None
        try:
            return agent_persona(name, session_id)
        except Exception:
            return None

    def target(agent_id: str, ctx: ToolRunContext, *, interrupt: bool = False) -> AgentRun:
        """工具层只翻译授权错误，身份和作用域规则由管理层统一执行。"""
        try:
            return manager.target_for(agent_id, ctx.session_id, interrupt=interrupt)
        except KeyError:
            raise ToolExecutionError(tr('python.subagent_tools.aebb26001e55', agent_id=agent_id)) from None

    async def spawn_agent(args: Any, ctx: ToolRunContext) -> ToolResult:
        """启动一个后台子代理，独立上下文执行任务。"""
        try:
            prompt, description = validate_spawn_text(args.get('prompt'), args.get('description', ''))
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc
        if args.get('fork_turns') == 'all' and (args.get('model') is not None or args.get('reasoning_effort') is not None):
            raise ValueError('agent/full-context-model-override')
        agent_name = str(args.get("agent", "") or "").strip()
        state = await configured_spawn({**args, "prompt": prompt, "description": description}, ctx) if configured_spawn else None
        if state is None:
            if args.get('member') or args.get('dependencies'):
                raise ToolExecutionError(tr('agent.prompt_team.constraint', p0='team_required'))
            if agent_name:
                persona = resolve_persona(agent_name, ctx.session_id)
                if persona is None:
                    raise ToolExecutionError(tr('subagent.agent.unavailable', p0=agent_name))
                prompt = f'<agent name="{agent_name}">\n{persona.strip()}\n</agent>\n\n{prompt}'
            state = manager.spawn(prompt=prompt, description=description,
                parent_session_id=ctx.session_id, source_call_id=ctx.call_id,
                model=args.get('model'), reasoning_effort=args.get('reasoning_effort'), fork_turns=args.get('fork_turns', 'none'))
        text = (
            tr('python.subagent_tools.fd180939377c', p0=f'{state.id}', p1=f'{state.status}', p2=f'{state.description}')
        )
        return ToolResult(
            content=[TextBlock(text=text)],
            display=state.to_dict(),
            data=state.to_dict(),
        )

    async def list_agents(args: Any, ctx: ToolRunContext) -> ToolResult:
        """列出所有子代理及其状态。"""
        states = [state for state in manager.list_states() if manager.visible_to(state, ctx.session_id)]
        if 'status' in args:
            wanted = args['status']
            if not isinstance(wanted, str) or wanted not in _RUN_STATUSES:
                raise ToolExecutionError(tr('subagent.list.invalid_status'))
            states = [state for state in states if state.status == wanted]
        limit_num = args.get('limit', DEFAULT_LIMIT)
        if type(limit_num) is not int:
            raise ToolExecutionError(tr('python.subagent_tools.98d238b75b0f', p0=repr(limit_num)))
        if limit_num <= 0:
            raise ToolExecutionError(tr('python.subagent_tools.f8e280da49c4'))
        total = len(states)
        shown = states[:limit_num]
        header = tr('python.subagent_tools.b109bbed36b5', p0=f'{total}')
        if total > len(shown):
            header += tr('python.subagent_tools.18e3b15704d9', p0=f'{len(shown)}')
        text = tr('subagent.list_summary', header=header, states=_state_lines(shown))
        return ToolResult(
            content=[TextBlock(text=text)],
            display=[s.to_dict() for s in shown],
            data=[s.to_dict() for s in shown],
        )

    async def wait_agent(args: Any, ctx: ToolRunContext) -> ToolResult:
        """等待运行状态或新消息；只结束等待，不停止目标。"""
        agent_id = str(args.get("agent_id", "") or "").strip()
        if 'agent_id' in args and not agent_id:
            raise ToolExecutionError(tr('python.subagent_tools.eed9a368bbac'))
        timeout = args.get("timeout", DEFAULT_WAIT_TIMEOUT)
        try:
            wait = min(validate_wait_timeout(timeout), MAX_WAIT_TIMEOUT)
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc
        if not agent_id:
            session = manager.session_for(ctx.session_id)
            if session is None:
                raise ToolExecutionError(tr('agent.message.no_session'))
            scope = manager.scope_resolver(ctx.session_id) if manager.scope_resolver else None
            if scope is not None and not session.agent_messages.pending():
                outstanding = [state.id for state in manager.list_states()
                               if state.parent_session_id == ctx.session_id and state.scope == scope
                               and not getattr(state, '_wait_consumed', False)]
                if outstanding:
                    raise ToolExecutionError(tr('agent.message.wait_target_required',
                                                agents=', '.join(outstanding)))
            if wait > 0 and not session.agent_messages.pending():
                try:
                    await asyncio.wait_for(session.agent_messages.wait_pending(), wait)
                except asyncio.TimeoutError:
                    pass
            receipts = [session.agent_messages.receipt(event.data['message_id'])
                        for event in session.agent_messages.pending()]
            data = {'incoming_messages': len(receipts), 'receipts': receipts}
            return ToolResult(content=[TextBlock(text=tr('agent.message.pending_count', count=len(receipts)))],
                              display=data, data=data)
        target(agent_id, ctx)
        session = manager.session_for(ctx.session_id)
        if session is None:
            state = await manager.wait(agent_id, timeout=wait)
        else:
            waiting = asyncio.create_task(manager.wait(agent_id, timeout=wait))
            incoming = asyncio.create_task(session.agent_messages.wait_pending())
            try:
                await asyncio.wait((waiting, incoming), return_when=asyncio.FIRST_COMPLETED)
                state = waiting.result() if waiting.done() else manager.get(agent_id)
            finally:
                for pending in (waiting, incoming):
                    if not pending.done():
                        pending.cancel()
                await asyncio.gather(waiting, incoming, return_exceptions=True)
        raise_cleanup_errors(manager._wait_errors(state))
        data = state.to_dict()
        data['settled'] = state.settled
        session = manager.session_for(ctx.session_id)
        incoming_count = len(session.agent_messages.pending()) if session is not None else 0
        data['incoming_messages'] = incoming_count
        if state.settled:
            state._wait_consumed = True
            body = state.output or state.error_message or tr('python.subagent_tools.8ae64c2c8ce5')
            text = f"{state.summary_line()}\n{body}"
        else:
            text = (
                tr('python.subagent_tools.548b0e95c89b', p0=f'{state.summary_line()}', p1=f'{wait:g}')
            )
        if incoming_count:
            text += '\n' + tr('agent.message.pending_count', count=incoming_count)
        return ToolResult(
            content=[TextBlock(text=text)],
            display=data,
            data=data,
        )

    async def wait_agents(args: Any, ctx: ToolRunContext) -> ToolResult:
        """批量等待一组成员；只结束观察，不停止仍在运行的成员。"""
        try:
            agent_ids = validate_wait_ids(args.get('agent_ids'))
            wait = min(validate_wait_timeout(args.get('timeout', DEFAULT_WAIT_TIMEOUT)), MAX_WAIT_TIMEOUT)
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc
        for agent_id in agent_ids:
            target(agent_id, ctx)
        states = await manager.wait_many(agent_ids, timeout=wait)
        snapshots = [{**subagent_summary(state), 'settled': state.settled} for state in states]
        for state in states:
            if state.settled:
                state._wait_consumed = True
        data = {'agents': snapshots,
                'completed': sum(state.settled and state.status == 'completed' for state in states),
                'failed': sum(state.settled and state.status in {'failed', 'interrupted'} for state in states),
                'pending': sum(not state.settled for state in states),
                'timed_out': [state.id for state in states if not state.settled]}
        text = tr('agent.message.wait_many.summary', completed=data['completed'], failed=data['failed'], pending=data['pending'])
        # content 是模型实际读取的通道，data/display 仅供宿主与界面使用。
        text += '\n' + json.dumps(data, ensure_ascii=False)
        return ToolResult(content=[TextBlock(text=text)], display=data, data=data)

    async def send_message(args: Any, ctx: ToolRunContext) -> ToolResult:
        """向协作者或直接委派者投递有来源与回执的消息。"""
        agent_id = str(args.get("agent_id", "") or "").strip()
        if not agent_id:
            raise ToolExecutionError(tr('python.subagent_tools.eed9a368bbac'))
        try:
            validate_message_text(args.get('message'))
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc
        message = args['message'].strip()
        try:
            receipt = await manager.send_message(agent_id, message,
                sender_session_id=ctx.session_id, call_id=ctx.call_id, turn=ctx.turn)
        except KeyError:
            raise ToolExecutionError(tr('python.subagent_tools.aebb26001e55', agent_id=agent_id)) from None
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from None
        state = manager.get(agent_id) if agent_id != 'parent' else None
        data = {**(state.to_dict() if state is not None else {'agent_id': 'parent'}), 'receipt': receipt}
        text = tr('agent.message.sent', message_id=receipt['message_id'], status=receipt['status'])
        return ToolResult(content=[TextBlock(text=text)], display=data, data=data)

    async def followup_task(args: Any, ctx: ToolRunContext) -> ToolResult:
        """追加任务与普通通信分开；启动、回执和幂等结果由原运行管理器提供。"""
        async def start(previous, text, metadata):
            return await configured_followup(previous, text, metadata, ctx)
        try:
            data = await manager.followup_task(args['agent_id'], args['message'],
                sender_session_id=ctx.session_id, call_id=ctx.call_id, turn=ctx.turn,
                start=start if configured_followup else None)
        except KeyError:
            raise ToolExecutionError(tr('python.subagent_tools.aebb26001e55', agent_id=args['agent_id'])) from None
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from None
        return ToolResult(content=[TextBlock(text=json.dumps(data, ensure_ascii=False))], data=data, display=data)

    async def interrupt_agent(args: Any, ctx: ToolRunContext) -> ToolResult:
        """中断正在运行的子代理。"""
        agent_id = str(args.get("agent_id", "") or "").strip()
        if not agent_id:
            raise ToolExecutionError(tr('python.subagent_tools.eed9a368bbac'))
        reason = str(args.get("reason", "") or "").strip() or None
        try:
            target(agent_id, ctx, interrupt=True)
            state = await manager.interrupt(agent_id, reason=reason)
        except KeyError:
            raise ToolExecutionError(tr('python.subagent_tools.aebb26001e55', agent_id=agent_id)) from None
        text = tr('python.subagent_tools.ed06e9240321', p0=f'{state.id}', p1=f'{state.status}')
        return ToolResult(
            content=[TextBlock(text=text)],
            display=state.to_dict(),
            data=state.to_dict(),
        )

    agent_id_prop = {"type": "string", "description": tr('python.subagent_tools.761fda564d42')}
    no_extra = {"additionalProperties": False}

    from ...subagent.evidence import build_member_evidence_tool

    return [
        build_member_evidence_tool(manager),
        ToolDefinition(
            schema={
                "description_key": 'python.subagent_tools.c6ff3974432f',
                "parameter_keys": {
                    ('properties', 'prompt', 'description'): 'python.subagent_tools.df8fdaf59a82',
                    ('properties', 'fork_turns', 'description'): 'agent.context.fork',
                    ('properties', 'model', 'description'): 'agent.context.model',
                    ('properties', 'description', 'description'): 'python.subagent_tools.1f140d31ec45',
                    ('properties', 'member', 'description'): 'subagent.member.param',
                    ('properties', 'dependencies', 'description'): 'subagent.dependencies.param',
                    ('properties', 'agent', 'description'): 'subagent.agent.param',
                },
                "name": "spawn_agent",
                "description": (
                    tr('python.subagent_tools.c6ff3974432f')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": tr('python.subagent_tools.df8fdaf59a82'),
                        },
                        "fork_turns": {"oneOf": [{"type": "string", "enum": ["none", "all"]}, {"type": "integer", "minimum": 1}],
                            "description": tr('agent.context.fork')},
                        "model": {"type": "string", "minLength": 1, "description": tr('agent.context.model')},
                        "reasoning_effort": {"type": "string", "enum": ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]},
                        "description": {
                            "type": "string",
                            "description": tr('python.subagent_tools.1f140d31ec45'),
                        },
                        "member": {"type": "string", "description": tr("subagent.member.param")},
                        "dependencies": {"type": "array", "items": {"type": "string"}, "description": tr("subagent.dependencies.param")},
                        "agent": {
                            "type": "string",
                            "description": tr('subagent.agent.param'),
                        },
                    },
                    "required": ["prompt"],
                    **no_extra,
                },
            },
            execute=spawn_agent,
            hook_names=('Agent',),
            concurrency_safe=True,
            timeout=30.0,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.subagent_tools.580bdf8813b2',
                "parameter_keys": {
                    ('properties', 'status', 'description'): 'python.subagent_tools.5c9fe5ec5406',
                    ('properties', 'limit', 'description'): ('python.subagent_tools.260f73651e38', {'p0': f'{DEFAULT_LIMIT}'}),
                },
                "name": "list_agents",
                "description": tr('python.subagent_tools.580bdf8813b2'),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": sorted(_RUN_STATUSES),
                            "description": tr('python.subagent_tools.5c9fe5ec5406'),
                        },
                        "limit": {"type": "integer", "minimum": 1, "description": tr('python.subagent_tools.260f73651e38', p0=f'{DEFAULT_LIMIT}')},
                    },
                    **no_extra,
                },
            },
            execute=list_agents,
            concurrency_safe=True,
        ),
        ToolDefinition(
            schema={
                "description_key": 'agent.message.wait',
                "parameter_keys": {
                    ('properties', 'agent_id', 'description'): 'python.subagent_tools.761fda564d42',
                    ('properties', 'timeout', 'description'): 'python.subagent_tools.da50ab1dbd92',
                },
                "name": "wait_agent",
                "description": tr('agent.message.wait'),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_id": agent_id_prop,
                        "timeout": {"type": "number", "minimum": 0, "description": tr('python.subagent_tools.da50ab1dbd92')},
                    },
                    "required": [],
                    **no_extra,
                },
            },
            execute=wait_agent,
            concurrency_safe=True,
            timeout=MAX_WAIT_TIMEOUT + 30.0,
        ),
        ToolDefinition(
            schema={
                "description_key": 'agent.message.wait_many',
                "parameter_keys": {
                    ('properties', 'agent_ids', 'description'): 'agent.message.wait_many.ids',
                    ('properties', 'timeout', 'description'): 'python.subagent_tools.da50ab1dbd92',
                },
                "name": "wait_agents",
                "description": tr('agent.message.wait_many'),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_ids": {"type": "array", "minItems": 1, "maxItems": 32,
                                      "items": {"type": "string", "minLength": 1},
                                      "description": tr('agent.message.wait_many.ids')},
                        "timeout": {"type": "number", "minimum": 0,
                                    "description": tr('python.subagent_tools.da50ab1dbd92')},
                    },
                    "required": ["agent_ids"],
                    **no_extra,
                },
            },
            execute=wait_agents,
            concurrency_safe=True,
            timeout=MAX_WAIT_TIMEOUT + 30.0,
        ),
        ToolDefinition(
            schema={
                "description_key": 'agent.message.send',
                "parameter_keys": {
                    ('properties', 'agent_id', 'description'): 'agent.message.target',
                    ('properties', 'message', 'description'): 'python.subagent_tools.2679bc095416',
                },
                "name": "send_message",
                "description": tr('agent.message.send'),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string", "description": tr('agent.message.target')},
                        "message": {"type": "string", "description": tr('python.subagent_tools.2679bc095416')},
                    },
                    "required": ["agent_id", "message"],
                    **no_extra,
                },
            },
            execute=send_message,
            concurrency_safe=True,
        ),
        ToolDefinition(schema={
            "description_key": 'agent.followup.description',
            "parameter_keys": {
                ('properties', 'agent_id', 'description'): 'python.subagent_tools.761fda564d42',
            },
            "name": "followup_task", "description": tr('agent.followup.description'),
            "parameters": {"type": "object", "properties": {
                "agent_id": agent_id_prop, "message": {"type": "string", "minLength": 1}},
                "required": ["agent_id", "message"], **no_extra}}, execute=followup_task,
            concurrency_safe=True, timeout=30.0),
        ToolDefinition(
            schema={
                "description_key": 'python.subagent_tools.73d554b37c26',
                "parameter_keys": {
                    ('properties', 'agent_id', 'description'): 'python.subagent_tools.761fda564d42',
                    ('properties', 'reason', 'description'): 'python.subagent_tools.6f546bb09173',
                },
                "name": "interrupt_agent",
                "description": tr('python.subagent_tools.73d554b37c26'),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_id": agent_id_prop,
                        "reason": {"type": "string", "description": tr('python.subagent_tools.6f546bb09173')},
                    },
                    "required": ["agent_id"],
                    **no_extra,
                },
            },
            execute=interrupt_agent,
            concurrency_safe=True,
        ),
    ]


__all__ = ["build_subagent_tools", "DEFAULT_WAIT_TIMEOUT", "MAX_WAIT_TIMEOUT"]

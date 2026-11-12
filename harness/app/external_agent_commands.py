"""把明确的外部 Agent 命令作为正常聊天回合执行，无需主模型中转。"""
from __future__ import annotations

import asyncio
import logging
import uuid

from ..acp.runners import PRESETS
from ..core.turn import TurnResult
from ..core.errors import HarnessError, PersistenceError
from ..core.language import current_language
from ..i18n import tr
from ..llm.types import ToolUseBlock
from ..session.events import AppendOp, TurnEndReason
from .goal_budget import resolve_execution_budget

logger = logging.getLogger(__name__)


async def external_agent_command(harness, session, text, context=None):
    """只匹配完整命令名；参数错误不启动进程，也不提交给模型猜测。"""
    parts = text.strip().split(maxsplit=2)
    if not parts or parts[0] != '/runner':
        return None
    if not harness.config.enable_fs or not harness.config.enable_shell:
        return '', tr('acp.command.disabled')
    if len(parts) != 3 or parts[1] not in {item.id for item in PRESETS}:
        return '', tr('acp.command.usage')
    return await run_external_agent_command(harness, session, parts[1], parts[2], context or {})


async def run_external_agent_command(harness, session, runner, task, context, *, agent=None):
    """复用工具日志与权限流水线，保留正常回合、失败和取消的成对事件。"""
    parent = agent if agent is not None else harness._agent_for(session)
    parent.reset()
    parent.language = current_language.get()
    parent.request_id = context.get('request_id') or uuid.uuid4().hex
    parent.run_id = context.get('run_id')
    parent.recovery_read_only = bool((agent is not None and parent.recovery_read_only) or context.get('recovery_read_only', False))
    if agent is None:
        parent.budget = resolve_execution_budget(harness, session)
    parent._todo_progress = None
    call_id = 'external_' + uuid.uuid4().hex
    args = {'agent': runner, 'task': task}
    start = await session.start_turn(run_id=parent.run_id, language=parent.language)
    turn = session.current_turn
    parent.status.session_id = session.id
    parent.status.turn = turn
    parent.status.last_error = None

    async def finish(reason, final_seq=None):
        event = await session.end_turn(reason, final_response_seq=final_seq)
        await harness.bus.emit('turn/end', {'session_id': session.id, **event.data,
                                          'source_seq': event.seq, 'source_time': event.time})

    speaker = {'speaker': dict(context['speaker'])} if context.get('speaker') else {}
    try:
        user = await session.append('user/message', {**harness.runtime.message_origin(session.id), 'text': context.get('user_text', '/runner ' + runner + ' ' + task),
            'source': context.get('source', 'user'), 'turn': turn,
            **({'attachments': context['attachments']} if context.get('attachments') else {}),
            **({'goal_id': context['goal_id']} if context.get('goal_id') else {})}, surface_op=AppendOp())
        if context.get('source', 'user') == 'user':
            reminder = parent._reminder_for()
            if reminder is not None:
                reminder.reset(session.id)
        await harness.bus.emit('user/message', {'session_id': session.id, **user.data,
                                              'source_seq': user.seq, 'source_time': user.time})
        await harness.bus.emit('turn/start', {'session_id': session.id, **start.data,
                                            'source_seq': start.seq, 'source_time': start.time})
        if parent.recovery_read_only or parent.task_read_only:
            raise HarnessError(tr('acp.tool.read_only'), code='acp/read-only')
        # 显式命令作为可信调用来源，不伪造模型响应；工具结果仍必须有配对调用。
        invocation = await session.append('assistant/message', {'role': 'assistant',
            'source': 'external-agent-command', 'turn': turn, **speaker, 'content': [
                {'type': 'tool_use', 'id': call_id, 'name': 'delegate_agent', 'input': args}]}, surface_op=AppendOp())
        await harness.bus.emit('assistant/message', {'session_id': session.id, **invocation.data,
            'source_seq': invocation.seq, 'source_time': invocation.time})
        parent.status.status = 'tool'
        await parent._execute_tools(session, [ToolUseBlock(call_id, 'delegate_agent', args)], turn=turn, step=1)
        result = next(event.data['results'][0] for event in reversed(session.events)
                      if event.type == 'tool/result' and event.data.get('call_id') == call_id)
        body = result['content']
        reply = await session.append('assistant/message', {'role': 'assistant', 'source': 'external-agent-command',
            'turn': turn, **speaker, 'content': [{'type': 'text', 'text': body}]}, surface_op=AppendOp())
        await harness.bus.emit('assistant/message', {'session_id': session.id, 'text': body, 'turn': turn, **speaker,
            'source_seq': reply.seq, 'source_time': reply.time})
        error = {'code': 'acp/task-failed', 'message': body} if result['is_error'] else None
        reason = 'error' if error else 'completed'
        await finish(TurnEndReason(kind=reason, error=error), reply.seq if not error else None)
        return TurnResult(session.id, turn, reason, text=body, tool_calls=[ToolUseBlock(call_id, 'delegate_agent', args)],
                          error=body if error else None, error_info=error)
    except asyncio.CancelledError:
        if session.open_turn is not None and not session.persistence_error:
            await parent._settle_unfinished_tools(session)
            await finish(TurnEndReason(kind='aborted', cause='user'))
        raise
    except PersistenceError:
        raise
    except Exception as exc:
        if not isinstance(exc, HarnessError):
            logger.exception(tr('log.4e838a9e1f46'))
        error = exc.to_dict() if isinstance(exc, HarnessError) else {'code': 'acp/command-failed', 'message': tr('acp.command.failed')}
        if session.open_turn is not None and not session.persistence_error:
            await parent._settle_unfinished_tools(session)
            await finish(TurnEndReason(kind='error', error=error))
        return TurnResult(session.id, turn, 'error', error=error['message'], error_info=error)
    finally:
        parent.status.status = 'idle'
        parent.status.current_tool = None

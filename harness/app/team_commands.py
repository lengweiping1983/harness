"""把命令入口的团队执行保存为可恢复的正式聊天回合。"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from collections.abc import Mapping
from harness.i18n import tr
from ..core.turn import TurnResult
from ..core.errors import HarnessError, PersistenceError
from ..core.language import current_language
from ..session.events import AppendOp, TurnEndReason
from ..collaboration.history import message_text, message_author
from .goal_budget import resolve_execution_budget


def _clip_text(value: str) -> tuple[str, bool]:
    """超长正文只保留首尾，避免历史占满成员上下文。"""
    clipped = len(value) > 1280
    return (value[:640] + '…' + value[-640:] if clipped else value), clipped


def team_conversation_context(session: Any) -> str:
    """只传递当前会话近期文字背景，限制历史大小并保留截取标记。"""
    rows = []
    for event in reversed(session.surface):
        if event.type not in ('user/message', 'assistant/message'):
            continue
        text, truncated = _clip_text(message_text(event))
        if not text:
            continue
        author = message_author(event)
        rows.append({'role': 'user' if event.type == 'user/message' else 'assistant',
                     **({'author': author} if author else {}),
                     'text': text, 'truncated': truncated})
        if len(rows) >= 6:
            break
    if not rows:
        return ''
    rows.reverse()
    latest = session.latest('turn/end')
    reason = latest.data.get('reason', {}) if latest else {}
    last_outcome = reason.get('kind') if isinstance(reason, Mapping) else reason
    last_outcome = last_outcome if isinstance(last_outcome, str) else None
    while rows:
        encoded = json.dumps({'messages': rows, 'last_outcome': last_outcome}, ensure_ascii=False)
        if len(encoded) <= 8192:
            return encoded
        rows.pop(0)
    return ''


async def run_team_command(harness: Any, session: Any, team: Any, task: str, context: dict[str, Any] | None = None) -> TurnResult:
    """记录指定成员的直接交付回合；自动派工统一通过 Harness.run_async。"""
    context = context or {}
    if not context.get('recipients'):
        raise HarnessError(tr('collaboration.invalid_recipients'), code='team/invalid-recipients')
    conversation_context = team_conversation_context(session) if context.get('team_session') is True else ''
    parent = harness._agent_for(session)
    parent.reset()
    parent.language = current_language.get()
    parent.request_id = context.get('request_id') or uuid.uuid4().hex
    parent.run_id = context.get('run_id')
    parent.recovery_read_only = context.get('recovery_read_only', False)
    overrides = {key: context[key] for key in ('model', 'reasoning_effort') if context.get(key)}
    parent.config = parent.config.merged(**overrides) if overrides else parent.config
    parent.budget = resolve_execution_budget(harness, session)
    start = await session.start_turn(run_id=parent.run_id, language=parent.language)
    turn = session.current_turn

    async def finish(reason: TurnEndReason, final_seq: int | None = None):
        async with parent.steering_lock:
            parent.accepting_steering = False
            if steering is not None:
                steering.close()
            event = await session.end_turn(reason, final_response_seq=final_seq)
        await harness.bus.emit('turn/end', {'session_id': session.id, **event.data, 'source_seq': event.seq, 'source_time': event.time})
        await harness.bus.emit('steer/state', {'session_id': session.id,
            'receipts': session.steering.recent(turn, active_turn=None)})

    steering = None
    team_goal = None
    try:
        working_copy = context.get('team_working_copy') is True
        isolated = context.get('team_worktrees') is True or working_copy
        flags = ' --worktrees' + (' --working-copy' if working_copy else '') if isolated else ''
        team_name = context.get('team_name') or team.name
        recipient_snapshot = None
        if team is not None and context.get('recipients'):
            from ..agents.presentation import team_recipient_snapshot
            recipient_snapshot = team_recipient_snapshot(team, harness.agents_for(session), context['recipients'])
        user = await session.append('user/message', {**harness.runtime.message_origin(session.id), 'text': context.get('user_text', '/agent ' + team_name + flags + ' ' + task),
            'source': context.get('source', 'user'), 'turn': turn,
            **({'attachments': context['attachments']} if context.get('attachments') else {}),
            **({'goal_id': context['goal_id']} if context.get('goal_id') else {}),
            **({'recipients': context['recipients']} if context.get('recipients') else {}),
            **({'team_recipients': recipient_snapshot} if recipient_snapshot else {})}, surface_op=AppendOp())
        await harness.bus.emit('user/message', {'session_id':session.id, **user.data, 'source_seq':user.seq, 'source_time':user.time})
        await harness.bus.emit('turn/start', {'session_id':session.id, **start.data, 'source_seq':start.seq, 'source_time':start.time})
        if team is None:
            raise HarnessError(tr('agent.team.selection_unavailable', name=team_name), code='agent/team-selection-unavailable')
        from types import SimpleNamespace
        from .prompt_team_run import PromptTeamRun
        run = PromptTeamRun(harness, session, parent, team,
            SimpleNamespace(team_worktrees=isolated, team_working_copy=working_copy),
            recipients=context['recipients'], direct=True,
            persistent_context=context.get('team_session') is True,
            conversation_context=conversation_context)
        summary = await run.run_direct(task)
        steering, team_goal = run.steering, run.goal
        replies = [event for event in session.events_of_type('assistant/message')
            if event.data.get('turn') == turn and event.data.get('author', {}).get('team') == team.name]
        await finish(TurnEndReason(kind='completed'), replies[-1].seq if replies else None)
        return TurnResult(session.id, turn, 'completed', text=summary,
            tool_calls=team_goal.calls if team_goal else [])
    except asyncio.CancelledError:
        if session.open_turn is not None and not session.persistence_error:
            await finish(TurnEndReason(kind='aborted', cause='user'))
        raise
    except PersistenceError:
        raise
    except Exception as exc:
        error = exc.to_dict() if isinstance(exc, HarnessError) else {'code':'agent/team-run', 'message':tr('agent.team.execution_failed')}
        if session.open_turn is not None and not session.persistence_error:
            await finish(TurnEndReason(kind='error', error=error))
        return TurnResult(session.id, turn, 'error', error=error['message'], error_info=error)
    finally:
        if team_goal is not None:
            team_goal.closed = True
        if steering is not None:
            steering.close()

"""会话级目标、用户控制及空闲执行边界上的自动续接。"""
from __future__ import annotations

from harness.i18n import tr

import asyncio
import logging
import random
import time
from typing import Any

from ..core.errors import HarnessError, SessionNotFoundError
from ..core.language import current_language, resolve_language
from ..llm.retry import retry_delay
from ..tools.builtin.goal_tools import _validate_ref
from ..session.events import AppendOp


logger = logging.getLogger(__name__)

GOAL_RETRY_BASE_SECONDS = 5.0
GOAL_RETRY_MAX_SECONDS = 60.0
# 这些工具只读取或整理任务自身；调用它们不能证明业务工作有进展。
GOAL_BOOKKEEPING_TOOLS = frozenset({'get_goal', 'create_goal', 'update_goal',
                                  'todo_read', 'todo_write', 'todo_update', 'todo_bind'})


class GoalRuntime:
    def __init__(self, harness: Any) -> None:
        self.harness = harness
        self.closed = False
        self._scheduled: dict[str, asyncio.Task] = {}
        self._publishing: dict[str, asyncio.Task] = {}
        # 仅托管的续接尝试注册等待权限；SDK 读取或模式切换
        # 不能启动仅为单回合创建的目标。
        self._waiting: dict[str, str] = {}
        # 允许模型先整理计划；连续三轮无工具活动才判定停滞。
        self._idle_rounds: dict[str, tuple[str, int]] = {}
        self._retry_wakes: dict[str, asyncio.Event] = {}
        self._readiness_events = ('queue_state', 'plan_mode', 'user_question/closed')
        for event in self._readiness_events:
            harness.bus.on(event, self._readiness_changed)

    async def _readiness_changed(self, payload: dict[str, Any]) -> None:
        session_id = payload.get('session_id')
        if self.closed or session_id not in self._waiting and session_id not in self._retry_wakes:
            return
        owner = self.harness.runtime.claim_loop(asyncio.get_running_loop())
        if owner is not asyncio.get_running_loop():
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self._readiness_changed(payload), owner))
            return
        self.wake(session_id)
        if session_id not in self._waiting:
            return
        # 同时唤醒已经排队的自动回合和尚未派发的续接任务。
        # 两者在获准执行前仍须重新检查相同的准入条件。
        self.harness.runtime._notify_queue(session_id)
        goal = self.harness.goals.get(session_id)
        if goal is None or goal.id != self._waiting.get(session_id):
            self._waiting.pop(session_id, None)
            return
        self.schedule(session_id)

    def wait_for_readiness(self, session_id: str, goal_id: str) -> None:
        if not self.closed:
            self._waiting[session_id] = goal_id

    def wake(self, session_id: str) -> None:
        """在所属事件循环内唤醒退避等待，重新检查用户控制和准入条件。"""
        event = self._retry_wakes.get(session_id)
        if event is not None:
            event.set()

    async def _record_control(self, session: Any, action: str, previous: Any) -> None:
        h = self.harness
        goal = h.goals.get(session.id)
        if action == 'edit':
            changed = previous.objective != goal.objective or previous.criteria != goal.criteria
            limits_changed = previous.token_budget != goal.token_budget or previous.max_goal_rounds != goal.max_goal_rounds
            if not changed and not limits_changed:
                return
            text = tr('goal.control.edit', objective=goal.objective) if changed else tr('goal.control.limits')
            if limits_changed:
                text += '\n\n' + tr('goal.control.budget',
                    rounds=goal.max_goal_rounds if goal.max_goal_rounds is not None else tr('goal.control.unlimited'),
                    tokens=goal.token_budget if goal.token_budget is not None else tr('goal.control.unlimited'))
        else:
            text = tr(f'goal.control.{action}')
        agent = h._agents.get(session.id)
        if agent is None:
            await self._append_control_message(session, text, action, previous.id)
            return
        async with agent.steering_lock:
            turn = session.open_turn
            if turn is None:
                await self._append_control_message(session, text, action, previous.id)
                return
            controller = agent.team_steering
            available = agent.accepting_steering and not agent.cancelled and (
                controller is None or controller.supported and controller.can_accept())
            request_id = f'goal-control:{previous.id}:{previous.revision}:{action}'
            await session.append('note', {'kind': 'steering/accepted', 'request_id': request_id,
                'turn': turn, 'text': text, 'goal_action': action, 'goal_id': previous.id,
                **({} if available else {'unapplied_reason': tr('goal.control.unapplied')})}, ignorable=True)
            await h.bus.emit('steer/state', {'session_id': session.id,
                'receipt': session.steering.receipt(request_id, active_turn=turn, summary=False)})

    async def _append_control_message(self, session: Any, text: str, action: str, goal_id: str) -> None:
        event = await session.append('user/message', {'text': text, 'source': 'goal-control',
            'goal_action': action, 'goal_reference_id': goal_id}, surface_op=AppendOp())
        await self.harness.bus.emit('user/message', {'session_id': session.id, **event.data,
            'source_seq': event.seq, 'source_time': event.time})

    async def control(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        h = self.harness
        owner = h.runtime.claim_loop(asyncio.get_running_loop())
        if owner is not asyncio.get_running_loop():
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.control(session_id, payload), owner))
        if not h.config.enable_goal:
            raise HarnessError(tr('python.goal_runtime.8499cc8dd81e'), code="goal/disabled")
        h.get_session(session_id)
        action = payload.get("action")
        origin = payload.get('source_seq')
        session = h.get_session(session_id)
        previous = h.goals.get(session_id)
        if origin is not None and (type(origin) is not int or not any(
            event.seq == origin and event.type == 'user/message' and event.data.get('source') != 'goal'
            for event in session.events
        )):
            raise HarnessError(tr('goal.message.invalid'), code='goal/invalid-source')
        if action == "create":
            if not isinstance(payload.get("objective"), str):
                raise HarnessError(tr('python.goal_runtime.1b96bb9ad64a'), code="goal/invalid-objective")
            h.goals.create(session_id, objective=payload["objective"], success_criteria=payload.get("success_criteria"), token_budget=payload.get("token_budget"),
                           **({"max_goal_rounds": payload["max_goal_rounds"]} if "max_goal_rounds" in payload else {}))
        else:
            goal_id, revision = _validate_ref(payload)
            # 清除操作也受 CAS 保护，避免其他标签页清除新目标。
            if action == "clear":
                h.goals.clear(session_id, goal_id=goal_id, revision=revision)
            elif action == "edit":
                h.goals.edit(session_id, goal_id=goal_id, revision=revision,
                             objective=payload.get("objective"),
                             **({"success_criteria": payload["success_criteria"]} if "success_criteria" in payload else {}),
                             **({"max_goal_rounds": payload["max_goal_rounds"]} if "max_goal_rounds" in payload else {}),
                             **({"token_budget": payload["token_budget"]} if "token_budget" in payload else {}))
            elif action in {"pause", "resume"}:
                getattr(h.goals, action)(session_id, goal_id=goal_id, revision=revision)
            else:
                raise HarnessError(tr('python.goal_runtime.9c361fa30958'), code="goal/invalid-action")
        try:
            if action == 'create':
                goal = h.goals.get(session_id)
                if origin is not None:
                    links = {**session.header.metadata.get('goal_messages', {}), str(origin): goal.id}
                    h.sessions.update_header(session_id, metadata={'goal_messages': links})
                    await h.bus.emit('goal/message', {'session_id': session_id, 'source_seq': origin, 'goal_id': goal.id})
                else:
                    event = await session.append('user/message', {'text': goal.objective, 'source': 'goal-command', 'goal_id': goal.id}, surface_op=AppendOp())
                    await h.bus.emit('user/message', {'session_id': session_id, **event.data, 'source_seq': event.seq, 'source_time': event.time})
            if action in {"pause", "clear"}:
                self._waiting.pop(session_id, None)
                for queued in tuple(h.runtime._pending.get(session_id, {}).values()):
                    if queued.goal_id:
                        h.runtime.remove(queued.id)
            if action != 'create':
                await self._record_control(session, action, previous)
            await h.goals.publish(session_id)
            if action in {"create", "resume"}:
                self._idle_rounds.pop(session_id, None)
                self.schedule(session_id)
            elif action in {"pause", "clear"}:
                self._idle_rounds.pop(session_id, None)
            self.wake(session_id)
            return h.goals.snapshot(session_id)
        except Exception as exc:
            # 主状态已提交，后续失败不能伪装成明确拒绝，也不能重放副作用。
            raise HarnessError(tr('goal.control.incomplete'), code='goal/control-incomplete',
                               session_id=session_id, action=action) from exc

    async def command(self, session_id: str, text: str) -> tuple[str, str] | None:
        parts = text.strip().split(maxsplit=1)
        command = parts[0] if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        if command != "/goal":
            return None
        rest = rest.strip()
        goal = self.harness.goals.get(session_id)
        if not rest or rest == "status":
            return "", goal.describe() if goal else tr('python.goal_runtime.d56266933350')
        if rest == "help" or rest == "edit":
            return "", tr('python.goal_runtime.d56266933350')
        # split() 同样接受命令与内容之间的换行和制表符。
        edit_parts = rest.split(maxsplit=1)
        if edit_parts[0] == "edit" and len(edit_parts) == 2:
            if goal is None:
                raise HarnessError(tr('python.goal_runtime.528ba8b57614'), code="GOAL_NOT_FOUND")
            objective = edit_parts[1].strip()
            criteria = {"success_criteria": [objective]} if tuple(goal.criteria) == (goal.objective,) else {}
            await self.control(session_id, {"action": "edit", "goal_id": goal.id, "revision": goal.revision,
                "objective": objective, **criteria})
            return "", tr('python.goal_runtime.341d79ef948f')
        if rest in {"pause", "resume", "clear", "delete"}:
            if goal is None:
                raise HarnessError(tr('python.goal_runtime.528ba8b57614'), code="GOAL_NOT_FOUND")
            # 斜杠控制命令在串行化回合内执行，无需调用模型。
            action = "clear" if rest == "delete" else rest
            await self.control(session_id, {"action": action, "goal_id": goal.id, "revision": goal.revision})
            return "", {"pause": tr('python.goal_runtime.bf995cbb495d'), "resume": tr('python.goal_runtime.998b5a687af0'), "clear": tr('python.goal_runtime.509fec8180f3')}[action]
        self.harness.goals.create(session_id, objective=rest, max_goal_rounds=None)
        await self.harness.goals.publish(session_id)
        return rest, tr('python.goal_runtime.662847777417')

    def schedule(self, session_id: str) -> None:
        h = self.harness
        if self.closed or h.runtime.closed or session_id in self._scheduled:
            return
        async def dispatch():
            # 让排队的用户输入和控制请求有机会优先执行。
            await asyncio.sleep(0)
            goal = h.goals.get(session_id)
            if goal is None or goal.phase != "active" or goal.activation != "armed" or h.runtime.closed:
                self._waiting.pop(session_id, None)
                return
            if (session_id in h.runtime.active or h.runtime.queue(session_id) or
                    h.runtime.queue_control(session_id)["paused"] or h.plan.is_active_session_id(session_id) or
                    h.questions.pending(session_id)):
                self.wait_for_readiness(session_id, goal.id)
                return
            self._waiting.pop(session_id, None)
            if goal.token_budget is not None and goal.tokens_used + sum(goal.budget_reservations.values()) >= goal.token_budget:
                h.goals.disarm(session_id, 'token-budget')
                await h.goals.publish(session_id)
                return
            if goal.max_goal_rounds is not None and goal.rounds_started >= goal.max_goal_rounds:
                h.goals.disarm(session_id, "round-limit")
                await h.goals.publish(session_id)
                return
            if goal.retry_at is not None and goal.retry_at > time.time():
                return goal.retry_at - time.time()
            # 退避任务继承创建时的上下文，内部续接提示须采用派发时的宿主语言。
            with current_language.set(resolve_language(h.config.language).effective_language):
                h.runtime.submit(session_id,
                    tr('python.goal_runtime.48cf5c7b3b57'),
                    goal_id=goal.id)
        async def supervised():
            try:
                while True:
                    delay = await dispatch()
                    if delay is None:
                        break
                    wake = asyncio.Event()
                    self._retry_wakes[session_id] = wake
                    try:
                        await asyncio.wait_for(wake.wait(), timeout=max(0, delay))
                    except TimeoutError:
                        pass
                    finally:
                        if self._retry_wakes.get(session_id) is wake:
                            self._retry_wakes.pop(session_id, None)
            except SessionNotFoundError:
                return
            except Exception:
                logger.exception(tr('feedback.0fa1cfb86fa7'), session_id)
                try:
                    h.goals.disarm(session_id, "dispatch-error")
                except Exception:
                    logger.exception(tr('feedback.dfa6b41cf20a'))
                await h.goals.publish(session_id)
            finally:
                self._scheduled.pop(session_id, None)
        self._scheduled[session_id] = asyncio.create_task(supervised())

    async def finished(self, record: Any) -> None:
        h = self.harness
        if h.runtime.closed or (record.error or {}).get('code') in {'run/steered', 'run/removed'}:
            return
        try:
            goal = h.goals.get(record.session_id)
        except SessionNotFoundError:
            return
        if goal is None:
            self._idle_rounds.pop(record.session_id, None)
            return
        if goal.phase != "active" or goal.activation != "armed":
            self._idle_rounds.pop(record.session_id, None)
            await h.goals.publish(record.session_id)
            return
        if record.goal_id is not None and record.goal_id != goal.id:
            self.schedule(record.session_id)
            return
        # 纯控制命令除恢复操作外，不应自行触发下一回合。
        parts = record.text.strip().split(maxsplit=1)
        rest = parts[1].strip() if len(parts) == 2 else ""
        if parts and parts[0] == "/goal" and (rest in {"", "status", "help", "edit", "pause", "resume", "clear", "delete"} or rest.split(maxsplit=1)[0] == "edit"):
            return
        if (record.result or {}).get('reason') == 'budget':
            h.goals.disarm(record.session_id, 'token-budget')
        elif (record.status == 'failed' and (record.error or {}).get('transient') is True
              and str((record.error or {}).get('code', '')).startswith('llm/')
              and not (record.error or {}).get('details', {}).get('compaction_terminal')):
            # 请求内重试已用尽，按目标继续恢复；新回合读取既有证据，
            # 不再次派发旧工具。抖动避免多个目标同时冲击恢复中的服务。
            delay = min(GOAL_RETRY_MAX_SECONDS,
                        GOAL_RETRY_BASE_SECONDS * 2 ** min(goal.retry_count, 4))
            delay *= random.uniform(.8, 1.0)
            delay = retry_delay((record.error or {}).get('details'), delay)
            h.goals.defer_retry(record.session_id, goal_id=goal.id, delay=delay,
                                error=str((record.error or {}).get('message', '')))
            self._idle_rounds.pop(record.session_id, None)
        elif record.status != "completed":
            h.goals.disarm(record.session_id, "interrupted" if record.status == "interrupted" else "run-failed")
        elif record.goal_id and not any(call.get('name') not in GOAL_BOOKKEEPING_TOOLS
                for call in (record.result or {}).get('tool_calls', ())):
            previous_id, count = self._idle_rounds.get(record.session_id, (goal.id, 0))
            count = count + 1 if previous_id == goal.id else 1
            self._idle_rounds[record.session_id] = (goal.id, count)
            if count >= 3:
                h.goals.disarm(record.session_id, "no-tool-progress")
        else:
            self._idle_rounds.pop(record.session_id, None)
        if record.status == 'completed':
            h.goals.clear_retry(record.session_id, goal_id=goal.id)
            self.wake(record.session_id)
        await h.goals.publish(record.session_id)
        self.schedule(record.session_id)

    def publish_soon(self, session_id: str) -> None:
        if self.closed or session_id in self._publishing:
            return
        async def publish():
            try:
                await self.harness.goals.publish(session_id)
            except SessionNotFoundError:
                pass
            finally:
                self._publishing.pop(session_id, None)
        self._publishing[session_id] = asyncio.create_task(publish())

    async def close(self) -> None:
        self.closed = True
        self._waiting.clear()
        self._idle_rounds.clear()
        self._retry_wakes.clear()
        for event in self._readiness_events:
            self.harness.bus.off(event, self._readiness_changed)
        tasks = list(self._scheduled.values()) + list(self._publishing.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.harness.goals.checkpoint_timing()

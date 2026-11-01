"""在现有任务树共享执行预算上实现持久化目标计量。"""
from __future__ import annotations

from harness.i18n import tr

import uuid

from ..core.errors import PersistenceError, ToolError
from .runtime import ExecutionBudget


class GoalReservation:
    def __init__(self, budget, reservation, request_id):
        self.budget, self.reservation, self.request_id = budget, reservation, request_id
        self.max_tokens = reservation.max_tokens

    def settle(self, tokens=0):
        if self.reservation.settled:
            return
        try:
            self.budget.harness.goals.settle_usage(self.budget.session_id, self.budget.goal_id, self.request_id, tokens)
        except OSError as exc:
            self.budget.harness.goals._live_reservations.discard(self.request_id)
            self.budget.brake()
            # 实际用量无法提交时，保留已持久化的预算预留。
            raise PersistenceError(tr('python.goal_budget.e78c84c8687f')) from exc
        finally:
            self.reservation.settle(tokens)
            self.budget.harness.goal_runtime.publish_soon(self.budget.session_id)


class GoalExecutionBudget(ExecutionBudget):
    def __init__(self, harness, session_id, goal_id, turn_limit):
        super().__init__(turn_limit)
        self.harness, self.session_id, self.goal_id = harness, session_id, goal_id
        self.turn_limit = turn_limit
        self.refresh_limit()

    def refresh_limit(self):
        goal = self.harness.goals.get(self.session_id)
        if goal is None or goal.id != self.goal_id:
            return  # 清除目标时，保留已经开始的回合所绑定的目标。
        # 内存中的预留已经计入持久化目标余额。
        available = None if goal.token_budget is None else max(0, goal.token_budget - goal.tokens_used - sum(goal.budget_reservations.values()))
        limits = [limit for limit in (self.turn_limit, self.spent + self.reserved + available if available is not None else None) if limit is not None]
        self.limit = min(limits) if limits else None

    def snapshot(self):
        """先刷新目标当前额度，仍复用同一个执行预算投影。"""
        self.refresh_limit()
        return super().snapshot()

    @property
    def exhausted(self):
        self.refresh_limit()
        return super().exhausted

    def brake(self):
        try:
            self.harness.goals.disarm(self.session_id, 'token-accounting-error', goal_id=self.goal_id)
        except OSError:
            pass  # 存储不可用时，disarm 的本地停止限制仍然有效。

    async def acquire(self, input_tokens, max_tokens):
        self.refresh_limit()
        reservation = await super().acquire(input_tokens, max_tokens)
        if reservation is None:
            self.harness.goals.disarm(self.session_id, 'token-budget', goal_id=self.goal_id)
            await self.harness.goals.publish(self.session_id)
            return None
        request_id = 'usage_' + uuid.uuid4().hex
        try:
            attached = self.harness.goals.reserve_usage(self.session_id, self.goal_id, request_id, reservation.allocated)
        except OSError as exc:
            reservation.settle(0)
            self.brake()
            raise PersistenceError(tr('python.goal_budget.c6a1aa318e19')) from exc
        except ToolError as exc:
            reservation.settle(0)
            if exc.code == 'GOAL_TOKEN_LIMIT':
                self.harness.goals.disarm(self.session_id, 'token-budget', goal_id=self.goal_id)
                await self.harness.goals.publish(self.session_id)
            raise
        except Exception:
            reservation.settle(0)
            raise
        held = GoalReservation(self, reservation, request_id) if attached else reservation
        try:
            await self.harness.goals.publish(self.session_id)
        except BaseException:
            # 请求尚未发送；发布期间取消时，必须先释放持久化预留和
            # 任务树预留，再继续传播取消信号。
            held.settle(0)
            raise
        return held


def resolve_execution_budget(harness, session):
    """按当前目标激活状态选择预算；调用方决定是否替换已有执行预算。"""
    goal = harness.goals.get(session.id) if harness.config.enable_goal else None
    if goal is not None and goal.phase == 'active' and goal.activation == 'armed':
        return GoalExecutionBudget(harness, session.id, goal.id, harness.config.token_budget)
    return ExecutionBudget(harness.config.token_budget)

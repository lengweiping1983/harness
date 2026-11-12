"""内置 Goal 工具。

goal 子系统由一个持久化状态机 + 三个模型可见工具（``get_goal`` /
``create_goal`` / ``update_goal``）组成。本文件把它做成一个进程内实现，
保留最关键的三条不变量：

1. **单例**：同一会话同时只有一个未完成目标；已有 ``active``/``paused``/
   ``blocked`` 目标时再建会报 ``GOAL_ALREADY_EXISTS``。
2. **CAS**：每次生命周期变更 ``revision`` 自增，模型必须回传 ``get_goal`` 拿到的
   精确 ``(id, revision)``；不匹配报 ``GOAL_STALE_REVISION``。
3. **控制边界**：模型只报告暂停、完成或阻塞；目标正文、执行上限和
   恢复操作由用户控制入口处理，不允许模型自行缩小目标或恢复。

状态以会话 header 的 goal metadata 持久化；activation 是进程本地开关，
重启恢复目标时默认 disarmed，必须由用户恢复，避免静默重放副作用。
"""

from __future__ import annotations

from harness.i18n import tr
from ...llm.types import SchemaResource

import json
import math
from collections.abc import Mapping
from copy import deepcopy
import time
import uuid
from dataclasses import asdict, dataclass, field, fields, replace
from functools import wraps
import threading
from typing import Any, Literal

from ...core.errors import PersistenceError, SessionNotFoundError, ToolError
from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolRunContext, ToolResult
from .goal_evidence import MAX_CRITERIA, MAX_EVIDENCE_PER_CHECK, MAX_CHECK_SUMMARY_CHARS, completion_guidance

GoalPhase = Literal["active", "paused", "blocked", "complete"]
GoalStatus = Literal["active", "paused", "blocked", "usage_limited", "budget_limited", "complete"]
GoalActivation = Literal["armed", "disarmed"]

#: 允许的轮次上限，避免模型填一个天文数字把会话永远钉在目标模式。
MAX_GOAL_ROUNDS_CEILING = 1_000_000

#: 默认轮次上限。
DEFAULT_MAX_GOAL_ROUNDS = 50

_UNSET = object()

UPDATE_ACTIONS: tuple[str, ...] = ("pause", "complete", "blocked")

_PHASE_LABEL = {
    "active": 'python.goal_tools.dc9591e56d50',
    "paused": 'python.goal_tools.eb0c326b60ae',
    "blocked": 'python.goal_tools.ece5ef215809',
    "complete": 'python.goal_tools.f28461bb49c8',
}


def _fail(code: str, message: str, **details: Any) -> ToolError:
    """构造带稳定错误码的 :class:`ToolError`。"""
    return ToolError(message, code=code, **details)


@dataclass(frozen=True, slots=True)
class GoalView:
    """一个目标的完整快照。"""

    id: str
    revision: int
    objective: str
    phase: GoalPhase
    max_goal_rounds: int | None
    #: 仅 ``blocked`` 时非空，形如 ``{"code": ..., "message": ...}``。
    blocked_reason: dict[str, str] | None = None
    #: 进程本地观测值，不持久化。
    activation: GoalActivation = "armed"
    rounds_started: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    stop_reason: str | None = None
    criteria: tuple[str, ...] = ()
    evidence_after: int = -1
    completion: tuple[dict[str, Any], ...] = ()
    token_budget: int | None = None
    tokens_used: int = 0
    budget_reservations: dict[str, int] = field(default_factory=dict)
    retry_at: float | None = None
    retry_count: int = 0
    retry_error: str | None = None
    elapsed_seconds: float = 0.0
    running_since: float | None = None

    @property
    def status(self) -> GoalStatus:
        """把生命周期和自动续接刹车原因归一成稳定的机器状态。"""
        if self.phase != "active":
            return self.phase
        if self.activation == "armed":
            return "active"
        if self.stop_reason in {"token-budget", "round-limit"}:
            return "budget_limited"
        if self.stop_reason in {"token-accounting-error", "usage-limited"}:
            return "usage_limited"
        return "paused"

    def to_value(self) -> dict[str, Any]:
        """物化成给模型看的稳定结构（对应 ``GoalToolValue``）。"""
        goal: dict[str, Any] = {
            "id": self.id,
            "revision": self.revision,
            "objective": self.objective,
            "phase": self.phase,
            "status": self.status,
            "roundsStarted": self.rounds_started,
            "maxGoalRounds": self.max_goal_rounds,
            "stopReason": self.stop_reason,
            "criteria": list(self.criteria or (self.objective,)),
            "completion": json.loads(json.dumps(self.completion)),
            "tokenBudget": self.token_budget,
            "tokensUsed": self.tokens_used,
            "tokensReserved": sum(self.budget_reservations.values()),
            "retryAt": self.retry_at,
            "retryCount": self.retry_count,
            "retryError": self.retry_error,
            "elapsedSeconds": self.elapsed_seconds + (max(0.0, time.time() - self.running_since) if self.running_since is not None else 0.0),
            "timingActive": self.running_since is not None,
        }
        if self.blocked_reason is not None:
            goal["blockedReason"] = dict(self.blocked_reason)
        return {"goal": goal, "activation": self.activation,
                "completionRequirements": {"maxChecks": MAX_CRITERIA,
                                           "maxEvidencePerCheck": MAX_EVIDENCE_PER_CHECK,
                                           "maxSummaryChars": MAX_CHECK_SUMMARY_CHARS}}

    def describe(self) -> str:
        """本地化的一句话描述，供 UI 与日志使用。"""
        phase = tr(_PHASE_LABEL[self.phase])
        text = tr('python.goal_tools.27a0b997804a', p0=f'{self.id}', p1=f'{self.revision}', p2=f'{phase}', p3=f'{self.objective}')
        if self.blocked_reason is not None:
            text += tr('python.goal_tools.9a3df87ea82a', p0=f"{self.blocked_reason.get('message', '')}")
        return text

    def context_value(self) -> dict[str, Any]:
        """只封装当前决策所需状态，完整计量与验收正文由 get_goal 读取。"""
        return {
            'id': self.id, 'revision': self.revision, 'objective': self.objective,
            'phase': self.phase, 'activation': self.activation,
            'status': self.status,
            'criteria': list(self.criteria or (self.objective,)),
            'roundsStarted': self.rounds_started, 'maxGoalRounds': self.max_goal_rounds,
            'tokenBudget': self.token_budget, 'tokensUsed': self.tokens_used,
            'tokensReserved': sum(self.budget_reservations.values()),
            'stopReason': self.stop_reason,
            **({'blockedReason': dict(self.blocked_reason)} if self.blocked_reason else {}),
        }


def _restore_goal(raw: Any) -> GoalView:
    """只读取完整的当前持久格式，不推算证据边界或补造旧字段。"""
    def invalid() -> None:
        raise _fail('GOAL_INVALID_STATE', tr('goal.storage.invalid'))

    def integer(value: Any, minimum: int = 0) -> bool:
        return type(value) is int and value >= minimum

    def number(value: Any) -> bool:
        if type(value) not in (int, float) or value < 0:
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False

    def text(value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip())

    expected = {item.name for item in fields(GoalView)} - {'activation', 'running_since'}
    if not isinstance(raw, Mapping) or set(raw) != expected:
        invalid()
    if not text(raw['id']) or not text(raw['objective']) or not isinstance(raw['phase'], str) or raw['phase'] not in _PHASE_LABEL:
        invalid()
    for key, minimum in [('revision', 1), ('rounds_started', 0), ('evidence_after', -1),
                         ('tokens_used', 0), ('retry_count', 0)]:
        if not integer(raw[key], minimum):
            invalid()
    for key, ceiling in [('max_goal_rounds', MAX_GOAL_ROUNDS_CEILING), ('token_budget', 1_000_000_000)]:
        if raw[key] is not None and (not integer(raw[key], 1) or raw[key] > ceiling):
            invalid()
    if any(not number(raw[key]) for key in ('created_at', 'updated_at', 'elapsed_seconds')):
        invalid()
    if raw['retry_at'] is not None and not number(raw['retry_at']):
        invalid()
    if any(raw[key] is not None and not isinstance(raw[key], str) for key in ('stop_reason', 'retry_error')):
        invalid()
    reason = raw['blocked_reason']
    if reason is not None and (not isinstance(reason, Mapping) or set(reason) != {'code', 'message'} or
                               not all(text(value) for value in reason.values())):
        invalid()
    checks = raw['criteria']
    if (not isinstance(checks, (list, tuple)) or not 1 <= len(checks) <= MAX_CRITERIA or
            any(not text(value) for value in checks) or len(set(checks)) != len(checks)):
        invalid()
    reservations = raw['budget_reservations']
    if not isinstance(reservations, Mapping) or any(not isinstance(key, str) or not integer(value)
                                                   for key, value in reservations.items()):
        invalid()
    if any(not key.strip() for key in reservations):
        invalid()
    if raw['token_budget'] is not None and raw['tokens_used'] + sum(reservations.values()) > raw['token_budget']:
        invalid()
    if raw['max_goal_rounds'] is not None and raw['rounds_started'] > raw['max_goal_rounds']:
        invalid()
    completion = raw['completion']
    if not isinstance(completion, (list, tuple)) or len(completion) > len(checks):
        invalid()
    seen = set()
    for check in completion:
        if not isinstance(check, Mapping) or set(check) != {'criterion', 'summary', 'evidence'}:
            invalid()
        index = check['criterion']
        if (not integer(index, 1) or index > len(checks) or index in seen or
                not text(check['summary']) or len(check['summary']) > MAX_CHECK_SUMMARY_CHARS):
            invalid()
        seen.add(index)
        evidence = check['evidence']
        if not isinstance(evidence, (list, tuple)) or not 1 <= len(evidence) <= MAX_EVIDENCE_PER_CHECK:
            invalid()
        for ref in evidence:
            if (not isinstance(ref, Mapping) or set(ref) != {'sourceSeq', 'callId', 'name', 'input', 'preview', 'truncated'} or
                    not integer(ref['sourceSeq']) or ref['sourceSeq'] <= raw['evidence_after'] or
                    not text(ref['callId']) or not text(ref['name']) or
                    not isinstance(ref['input'], str) or not isinstance(ref['preview'], str) or
                    type(ref['truncated']) is not bool):
                invalid()
    # 解冻嵌套元数据，避免把只读映射或持久容器暴露给运行时写入者。
    from ...core.jsonutil import to_jsonable
    data = to_jsonable(raw)
    data['criteria'] = tuple(data['criteria'])
    data['completion'] = tuple(data['completion'])
    goal = GoalView(**data, activation='disarmed')
    # 生命周期字段必须相互一致。否则损坏的 header 可能把没有验收
    # 证据的目标恢复成 complete，随后被界面或自动续接当成已完成。
    if goal.phase == 'complete':
        if (goal.blocked_reason is not None or goal.stop_reason is not None or
                goal.retry_at is not None or goal.retry_count != 0 or goal.retry_error is not None or
                len(goal.completion) != len(goal.criteria) or
                {item['criterion'] for item in goal.completion} != set(range(1, len(goal.criteria) + 1))):
            invalid()
    elif goal.completion:
        invalid()
    if goal.phase == 'blocked' and goal.blocked_reason is None:
        invalid()
    if goal.phase != 'blocked' and goal.blocked_reason is not None:
        invalid()
    return goal


def _locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


class GoalStore:
    """会话级目标状态。

    一个 session 至多保留一个目标；``complete`` 之后可以被同 session 的新
    ``create_goal`` 覆盖（只有 ``complete`` 才允许重建）。
    """

    def __init__(self, *, default_max_goal_rounds: int = DEFAULT_MAX_GOAL_ROUNDS, sessions=None, bus=None) -> None:
        if type(default_max_goal_rounds) is not int or not 1 <= default_max_goal_rounds <= MAX_GOAL_ROUNDS_CEILING:
            raise ValueError(tr('python.goal_tools.fcb203a96ba5', maximum=MAX_GOAL_ROUNDS_CEILING))
        self._lock = threading.RLock()
        self.sessions, self.bus = sessions, bus
        self.default_max_goal_rounds = default_max_goal_rounds
        #: 会话标识 session_id 到 GoalView 的映射
        self._goals: dict[str, GoalView] = {}
        self._versions: dict[str, int] = {}
        self._live_reservations: set[tuple[str, str, str]] = set()

    # -- 读取 ---------------------------------------------------------------
    @_locked
    def get(self, session_id: str) -> GoalView | None:
        if session_id not in self._goals and self.sessions is not None:
            metadata = self.sessions.get(session_id).header.metadata
            raw = metadata.get("goal")
            if raw is not None:
                version = metadata.get('goal_state_version')
                if type(version) is not int or version < 1:
                    raise _fail('GOAL_INVALID_STATE', tr('goal.storage.invalid'))
                goal = _restore_goal(raw)
                if goal.phase == 'complete':
                    # 完成回执引用的是会话事件，而不是 header 中可自行填写的
                    # 摘要。恢复时重新核对每一条引用，防止篡改或事件缺失后
                    # 仍向模型和界面报告已完成。
                    from .goal_evidence import result_reference
                    session = self.sessions.get(session_id)
                    try:
                        for check in goal.completion:
                            for saved in check['evidence']:
                                actual = result_reference(session, goal, saved['sourceSeq'])
                                if actual != saved:
                                    raise ValueError('completion evidence mismatch')
                    except Exception as exc:
                        raise _fail('GOAL_INVALID_STATE', tr('goal.storage.invalid')) from exc
                self._goals[session_id] = goal
        return deepcopy(self._goals.get(session_id))

    # -- 写入 ---------------------------------------------------------------
    @_locked
    def create(
        self,
        session_id: str,
        *,
        objective: str,
        max_goal_rounds: Any = _UNSET,
        success_criteria: list[str] | None = None,
        token_budget: int | None = None,
    ) -> GoalView:
        if not isinstance(objective, str) or not objective.strip():
            raise _fail("GOAL_INVALID_OBJECTIVE", tr('python.goal_tools.07cfd33c7c6f'))
        text = objective.strip()
        cap = self.default_max_goal_rounds if max_goal_rounds is _UNSET else self._check_rounds(max_goal_rounds)
        current = self.get(session_id)
        if current is not None and current.phase != "complete":
            raise _fail(
                "GOAL_ALREADY_EXISTS",
                tr('python.goal_tools.f9017ce9e74c', p0=f'{current.phase}', p1=f'{current.objective}'),
                goal_id=current.id,
                phase=current.phase,
            )
        from .goal_evidence import criteria
        checks = criteria(success_criteria, text)
        self._check_token_budget(token_budget)
        now = time.time()
        goal = GoalView(
            id=f"goal-{uuid.uuid4()}",
            revision=1,
            objective=text,
            phase="active",
            max_goal_rounds=cap,
            activation="armed",
            running_since=now,
            created_at=now,
            updated_at=now,
            criteria=checks,
            evidence_after=self.sessions.get(session_id).event_count - 1 if self.sessions else -1,
            token_budget=token_budget,
        )
        self._save(session_id, goal)
        return goal

    def _expect_current(self, session_id: str, goal_id: str, revision: int) -> GoalView:
        """CAS 校验：目标必须存在，且 ``(id, revision)`` 与当前一致。"""
        goal = self.get(session_id)
        if goal is None:
            raise _fail("GOAL_NOT_FOUND", tr('python.goal_tools.b12d74d335a7'))
        if goal.id != goal_id:
            raise _fail(
                "GOAL_NOT_FOUND",
                tr('python.goal_tools.027bf4b75b80', p0=f'{goal.id}', p1=f'{goal_id}'),
                goal_id=goal_id,
            )
        if goal.revision != revision:
            raise _fail(
                "GOAL_STALE_REVISION",
                tr('python.goal_tools.257b1b094ea5', p0=f'{goal.revision}', p1=f'{revision}'),
                current_revision=goal.revision,
                given_revision=revision,
            )
        return goal

    def _commit(self, goal: GoalView, **changes: Any) -> GoalView:
        """写入新快照并把 revision 自增（所有持久变更的唯一入口）。"""
        now = time.time()
        elapsed = goal.elapsed_seconds + (max(0.0, now - goal.running_since) if goal.running_since is not None else 0.0)
        active = changes.get("phase", goal.phase) == "active" and changes.get("activation", goal.activation) == "armed"
        new_goal = replace(goal, revision=goal.revision + 1, updated_at=now,
                           elapsed_seconds=elapsed, running_since=now if active else None, **changes)
        for session_id, existing in self._goals.items():
            if existing.id == goal.id:
                self._save(session_id, new_goal)
                return new_goal
        raise _fail("GOAL_NOT_FOUND", tr('python.goal_tools.84781f076dca', p0=f'{goal.id}'))

    @_locked
    def edit(
        self,
        session_id: str,
        *,
        goal_id: str,
        revision: int,
        objective: str | None = None,
        max_goal_rounds: Any = _UNSET,
        success_criteria: Any = _UNSET,
        token_budget: Any = _UNSET,
    ) -> GoalView:
        goal = self._expect_current(session_id, goal_id, revision)
        if goal.phase == "complete":
            raise _fail(
                "GOAL_INVALID_TRANSITION",
                tr('python.goal_tools.a0e90f8dc35a', p0=f'{goal.phase}'),
                phase=goal.phase,
            )
        changes: dict[str, Any] = {}
        if objective is not None:
            if not isinstance(objective, str):
                raise _fail("GOAL_INVALID_OBJECTIVE", tr('python.goal_tools.07cfd33c7c6f'))
            text = objective.strip()
            if not text:
                raise _fail("GOAL_INVALID_OBJECTIVE", tr('python.goal_tools.07cfd33c7c6f'))
            changes["objective"] = text
        if max_goal_rounds is not _UNSET:
            cap = self._check_rounds(max_goal_rounds)
            changes["max_goal_rounds"] = cap
        if token_budget is not _UNSET:
            self._check_token_budget(token_budget)
            changes['token_budget'] = token_budget
        if success_criteria is not _UNSET or objective is not None and text != goal.objective:
            from .goal_evidence import criteria
            updated_criteria = criteria(success_criteria, changes.get('objective', goal.objective)) if success_criteria is not _UNSET else goal.criteria or (goal.objective,)
            changes['criteria'] = updated_criteria
            if updated_criteria != (goal.criteria or (goal.objective,)) or changes.get('objective', goal.objective) != goal.objective:
                changes['evidence_after'] = self.sessions.get(session_id).event_count - 1 if self.sessions else -1
                changes['completion'] = ()
        if not changes:
            raise _fail(
                "GOAL_INVALID_EDIT",
                tr('python.goal_tools.98db53ace15f'),
            )
        return self._commit(goal, **changes)

    @_locked
    def pause(self, session_id: str, *, goal_id: str, revision: int) -> GoalView:
        goal = self._expect_current(session_id, goal_id, revision)
        if goal.phase != "active":
            raise _fail(
                "GOAL_INVALID_TRANSITION",
                tr('python.goal_tools.c9d1a50685ce', p0=f'{goal.phase}'),
                phase=goal.phase,
            )
        return self._commit(goal, phase="paused", activation="disarmed", retry_at=None, retry_count=0, retry_error=None)

    @_locked
    def resume(self, session_id: str, *, goal_id: str, revision: int) -> GoalView:
        goal = self._expect_current(session_id, goal_id, revision)
        if goal.phase not in ("active", "paused", "blocked"):
            raise _fail(
                "GOAL_INVALID_TRANSITION",
                tr('python.goal_tools.60527a48f6bc', p0=f'{goal.phase}'),
                phase=goal.phase,
            )
        if goal.phase == "active" and goal.activation == "armed":
            raise _fail(
                "GOAL_INVALID_TRANSITION",
                tr('python.goal_tools.eec67abbf09c'),
                phase=goal.phase,
            )
        if goal.max_goal_rounds is not None and goal.rounds_started >= goal.max_goal_rounds:
            raise _fail(
                "GOAL_INVALID_TRANSITION",
                tr('python.goal_tools.bfd8ef3edea8', p0=f'{goal.rounds_started}', p1=f'{goal.max_goal_rounds}'),
                max_goal_rounds=goal.max_goal_rounds,
            )
        if goal.token_budget is not None and goal.tokens_used + sum(goal.budget_reservations.values()) >= goal.token_budget:
            raise _fail("GOAL_TOKEN_LIMIT", tr('python.goal_tools.2a4839556456'))
        return self._commit(goal, phase="active", activation="armed", blocked_reason=None, stop_reason=None,
                            retry_at=None, retry_count=0, retry_error=None)

    @_locked
    def complete(self, session_id: str, *, goal_id: str, revision: int, checks: Any = None) -> GoalView:
        goal = self._expect_current(session_id, goal_id, revision)
        if goal.phase not in ("active", "paused", "blocked"):
            raise _fail(
                "GOAL_INVALID_TRANSITION",
                tr('python.goal_tools.005c6884f0e7', p0=f'{goal.phase}'),
                phase=goal.phase,
            )
        from .goal_evidence import completion_checks
        receipt = completion_checks(self.sessions.get(session_id) if self.sessions else None, goal, checks)
        return self._commit(goal, phase="complete", activation="disarmed", blocked_reason=None,
                            stop_reason=None, completion=receipt, retry_at=None, retry_count=0, retry_error=None)

    @_locked
    def block(self, session_id: str, *, goal_id: str, revision: int, reason: str) -> GoalView:
        goal = self._expect_current(session_id, goal_id, revision)
        if goal.phase != "active":
            raise _fail(
                "GOAL_INVALID_TRANSITION",
                tr('python.goal_tools.dc0923943dfa', p0=f'{goal.phase}'),
                phase=goal.phase,
            )
        if not isinstance(reason, str) or not reason.strip():
            raise _fail("GOAL_TOOL_INVALID_UPDATE", tr('python.goal_tools.1a419835edf0'))
        return self._commit(
            goal,
            phase="blocked",
            activation="disarmed",
            blocked_reason={"code": "model-reported", "message": reason.strip()},
        )

    # -- 内部 ---------------------------------------------------------------
    @staticmethod
    def _check_token_budget(value: Any) -> None:
        if value is not None and (type(value) is not int or not 1 <= value <= 1_000_000_000):
            raise _fail("GOAL_INVALID_TOKEN_BUDGET", tr('python.goal_tools.94416733081f'))

    @_locked
    def reserve_usage(self, session_id: str, goal_id: str, request_id: str, tokens: int) -> bool:
        goal = self.get(session_id)
        if goal is None or goal.id != goal_id:
            return False
        if request_id in goal.budget_reservations or type(tokens) is not int or tokens < 0:
            raise _fail("GOAL_INVALID_RESERVATION", tr('python.goal_tools.47c99ca9ae9f'))
        if goal.token_budget is not None and goal.tokens_used + sum(goal.budget_reservations.values()) + tokens > goal.token_budget:
            raise _fail("GOAL_TOKEN_LIMIT", tr('python.goal_tools.2bb28206b9c2'))
        reservations = {**goal.budget_reservations, request_id: tokens}
        # 用量计量推进界面版本，不改变模型生命周期的 CAS 修订号。
        self._save(session_id, replace(goal, budget_reservations=reservations, updated_at=time.time()))
        self._live_reservations.add((session_id, goal_id, request_id))
        return True

    @_locked
    def settle_usage(self, session_id: str, goal_id: str, request_id: str, tokens: int) -> None:
        goal = self.get(session_id)
        if goal is None or goal.id != goal_id or request_id not in goal.budget_reservations:
            self._live_reservations.discard((session_id, goal_id, request_id))
            return
        if type(tokens) is not int or tokens < 0:
            raise _fail("GOAL_INVALID_RESERVATION", tr('python.goal_tools.7193b010069b'))
        reservations = dict(goal.budget_reservations)
        reservations.pop(request_id)
        self._save(session_id, replace(goal, tokens_used=goal.tokens_used + tokens,
                   budget_reservations=reservations, updated_at=time.time()))
        self._live_reservations.discard((session_id, goal_id, request_id))

    def _check_rounds(self, value: int | None) -> int | None:
        """校验轮次；None 表示不设上限，正整数仍受输入范围约束。"""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise _fail("GOAL_INVALID_MAX_ROUNDS", tr('python.goal_tools.2ee807a80808'))
        if value < 1:
            raise _fail("GOAL_INVALID_MAX_ROUNDS", tr('python.goal_tools.94b6bf37ad4b'))
        if value > MAX_GOAL_ROUNDS_CEILING:
            raise _fail(
                "GOAL_INVALID_MAX_ROUNDS",
                tr('python.goal_tools.fa234d3b4ba7', p0=f'{MAX_GOAL_ROUNDS_CEILING}'),
            )
        return value

    @_locked
    def clear(self, session_id: str, *, goal_id: str | None = None, revision: int | None = None) -> None:
        """清掉会话的目标（测试与"放弃目标"场景使用）。"""
        if goal_id is not None:
            self._expect_current(session_id, goal_id, revision)
        goal = self.get(session_id)
        self._write(session_id, None)
        if goal is not None:
            self._live_reservations.difference_update((session_id, goal.id, request_id) for request_id in goal.budget_reservations)
        self._goals.pop(session_id, None)

    def _write(self, session_id: str, goal: GoalView | None) -> None:
        version = self._versions.get(session_id, 0)
        data = asdict(goal) if goal else None
        if data is not None:
            data.pop("activation")
            data.pop("running_since")
            data["elapsed_seconds"] = goal.to_value()["goal"]["elapsedSeconds"]
        if self.sessions is not None:
            version = self.sessions.get(session_id).header.metadata.get("goal_state_version", 0)
            self.sessions.update_header(session_id, metadata={"goal": data, "goal_state_version": version + 1})
        self._versions[session_id] = version + 1

    def _save(self, session_id: str, goal: GoalView) -> None:
        # 先提交持久化头信息，写入失败不能推进修订号。
        saved = deepcopy(goal)
        self._write(session_id, saved)
        self._goals[session_id] = saved

    @_locked
    def checkpoint_timing(self) -> None:
        """正常关闭时保存计时检查点，重启后不累计离线时段。"""
        for session_id, goal in self._goals.items():
            if goal.running_since is not None:
                self._write(session_id, goal)

    async def publish(self, session_id: str) -> None:
        if self.bus is not None:
            await self.bus.emit("goal/state", {"session_id": session_id, **self.snapshot(session_id)})

    @_locked
    def snapshot(self, session_id: str) -> dict[str, Any]:
        goal = self.get(session_id)
        version = (self.sessions.get(session_id).header.metadata.get("goal_state_version", 0)
                   if self.sessions is not None else self._versions.get(session_id, 0))
        value = goal.to_value() if goal else {"goal": None, "activation": "disarmed"}
        if goal is not None:
            value['goal']['tokensUncertain'] = sum(tokens for key, tokens in goal.budget_reservations.items() if (session_id, goal.id, key) not in self._live_reservations)
        return {**value, "stateVersion": version}

    @_locked
    def start_round(self, session_id: str, goal_id: str) -> GoalView:
        goal = self.get(session_id)
        if goal is None or goal.id != goal_id or goal.phase != "active" or goal.activation != "armed":
            raise _fail("GOAL_NOT_ARMED", tr('python.goal_tools.537db09980e1'))
        if goal.token_budget is not None and goal.tokens_used + sum(goal.budget_reservations.values()) >= goal.token_budget:
            raise _fail("GOAL_TOKEN_LIMIT", tr('python.goal_tools.998280abf35d'))
        if goal.max_goal_rounds is not None and goal.rounds_started >= goal.max_goal_rounds:
            raise _fail("GOAL_ROUND_LIMIT", tr('python.goal_tools.e90b660c0ff1'))
        return self._commit(goal, rounds_started=goal.rounds_started + 1)

    @_locked
    def defer_retry(self, session_id: str, *, goal_id: str, delay: float, error: str) -> GoalView:
        """在当前目标中持久化瞬时故障恢复时间，不重放旧工具。"""
        goal = self.get(session_id)
        if goal is None or goal.id != goal_id or goal.phase != 'active' or goal.activation != 'armed':
            raise _fail('GOAL_NOT_ARMED', tr('python.goal_tools.537db09980e1'))
        try:
            return self._commit(goal, retry_at=time.time() + delay, retry_count=goal.retry_count + 1,
                                retry_error=error[:1000], stop_reason=None)
        except (OSError, PersistenceError):
            # 重试计划没有落盘时不能继续执行，磁盘恢复后由用户恢复。
            self._goals[session_id] = replace(goal, activation='disarmed', stop_reason='dispatch-error',
                elapsed_seconds=goal.to_value()['goal']['elapsedSeconds'], running_since=None)
            raise

    @_locked
    def clear_retry(self, session_id: str, *, goal_id: str) -> None:
        """成功回合结束后复位退避，不改变已累计用量和轮次。"""
        goal = self.get(session_id)
        if goal is not None and goal.id == goal_id and (goal.retry_at is not None or goal.retry_count):
            try:
                self._commit(goal, retry_at=None, retry_count=0, retry_error=None)
            except (OSError, PersistenceError):
                self._goals[session_id] = replace(goal, activation='disarmed', stop_reason='dispatch-error',
                elapsed_seconds=goal.to_value()['goal']['elapsedSeconds'], running_since=None)
                raise

    @_locked
    def disarm(self, session_id: str, reason: str, *, goal_id: str | None = None) -> None:
        goal = self.get(session_id)
        if goal is not None and goal.phase == "active" and (goal_id is None or goal.id == goal_id):
            try:
                self._commit(goal, activation="disarmed", stop_reason=reason, retry_at=None)
            finally:
                # 激活状态是进程内的停止限制，磁盘错误不能重新启用。
                current = self._goals[session_id]
                self._goals[session_id] = replace(current, activation="disarmed",
                    elapsed_seconds=current.to_value()["goal"]["elapsedSeconds"], running_since=None)

    def context(self, session_id: str) -> str:
        try:
            goal = self.get(session_id) if session_id else None
        except SessionNotFoundError:
            # 临时子代理上下文不拥有会话级目标。
            return ""
        if goal is None:
            return ""
        # 目标独立于会话正文，压缩后仍从持久状态重建；时钟和诊断
        # 不参与决策封装，避免每次请求因计时变化重写上下文。
        state = goal.context_value()
        header = tr('prompt.goal.260') + json.dumps(state, ensure_ascii=False, separators=(',', ':'))
        if goal.phase != 'active' or goal.activation != 'armed':
            return header + tr('prompt.goal.inactive')
        return (header +
                tr('prompt.goal.259')
                + completion_guidance() + " "
                + tr('prompt.goal.258'))


def _validate_ref(args: dict[str, Any]) -> tuple[str, int]:
    """从工具参数里取出并校验 ``(goal_id, revision)``。"""
    goal_id = args.get("goal_id")
    revision = args.get("revision")
    if not isinstance(goal_id, str) or not goal_id or goal_id != goal_id.strip():
        raise _fail("GOAL_TOOL_INVALID_UPDATE", tr('python.goal_tools.f5e94496e6f6'))
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise _fail("GOAL_TOOL_INVALID_UPDATE", tr('python.goal_tools.ddd0b52dcc50'))
    return goal_id, revision


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def build_goal_tools(store: GoalStore) -> list[ToolDefinition]:
    """构造 ``get_goal`` / ``create_goal`` / ``update_goal``。"""

    def render(value: dict[str, Any]) -> str:
        # 单一机器可读表示保留完整计量、验收和证据分页，避免
        # 人类摘要遗漏字段或重复展开同一份状态。
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))

    def to_content(value: Any, _ctx: Any = None) -> list[TextBlock]:
        if isinstance(value, dict):
            return [TextBlock(text=render(value))]
        return [TextBlock(text=json.dumps(value, ensure_ascii=False))]

    async def get_goal(_args: Any, ctx: ToolRunContext) -> ToolResult:
        """读取当前会话的目标。"""
        bridge = ctx.services.get("team_goal")
        if bridge is not None:
            ctx = await bridge.context(ctx)
        goal = store.get(ctx.session_id)
        value = {"goal": None} if goal is None else goal.to_value()
        if goal is not None and store.sessions is not None:
            from .goal_evidence import candidates
            value.update(candidates(store.sessions.get(ctx.session_id), goal, _args.get('evidence_before')))
        return ToolResult(
            content=[TextBlock(text=render(value))],
            display=value,
            data=value,
        )

    async def create_goal(args: Any, ctx: ToolRunContext) -> ToolResult:
        """创建会话目标。"""
        if ctx.services.get("team_goal") is not None:
            raise _fail("GOAL_TEAM_READ_ONLY", tr('goal.team.bound'))
        raw = args.get("max_goal_rounds")
        cap: int | None
        if raw is None:
            cap = None
        elif isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise _fail("GOAL_INVALID_MAX_ROUNDS", tr('python.goal_tools.1fa9478d5ae5'))
        elif isinstance(raw, float) and not raw.is_integer():
            raise _fail("GOAL_INVALID_MAX_ROUNDS", tr('python.goal_tools.2ee807a80808'))
        else:
            cap = int(raw)
        goal = store.create(ctx.session_id, objective=args.get("objective"), success_criteria=args.get("success_criteria"),
                            max_goal_rounds=cap, token_budget=args.get('token_budget'))
        await store.publish(ctx.session_id)
        value = goal.to_value()
        return ToolResult(
            content=[TextBlock(text=tr('python.goal_tools.67948c42b405') + render(value))],
            display=value,
            data=value,
        )

    async def update_goal(args: Any, ctx: ToolRunContext) -> ToolResult:
        """按 CAS 更新目标。"""
        bridge = ctx.services.get("team_goal")
        if bridge is not None:
            root_writer = ctx.session_id == bridge.session.id and ctx.services.get('team_goal_writer') is True
            if not root_writer and args.get("action") not in {"complete", "blocked"}:
                raise _fail("GOAL_TEAM_READ_ONLY", tr('goal.team.controls'))
            ctx = await bridge.context(ctx, write=True, complete=args.get('action') == 'complete')
        action = args.get("action")
        if action not in UPDATE_ACTIONS:
            raise _fail(
                "GOAL_TOOL_INVALID_UPDATE",
                tr('python.goal_tools.80bd357ad116', p0=f'{list(UPDATE_ACTIONS)}', p1=f'{action!r}'),
            )
        goal_id, revision = _validate_ref(args)
        allowed = {'goal_id', 'revision', 'action', 'checks', 'blocked_reason'}
        if set(args) - allowed:
            raise _fail('GOAL_TOOL_INVALID_UPDATE', tr('goal.model.user_controls'))
        blocked_reason = args.get("blocked_reason")
        checks = args.get("checks")
        if checks is not None and action != "complete":
            raise _fail("GOAL_TOOL_INVALID_UPDATE", tr('python.goal_tools.fbc59c72e022'))
        if action == 'pause':
            if _has_text(blocked_reason):
                raise _fail('GOAL_TOOL_INVALID_UPDATE', tr('python.goal_tools.978e0eea36aa'))
            goal = store.pause(ctx.session_id, goal_id=goal_id, revision=revision)
            prefix = tr('python.goal_tools.bef024bdebd0')
        else:
            if action == "complete":
                if _has_text(blocked_reason):
                    raise _fail(
                        "GOAL_TOOL_INVALID_UPDATE",
                        tr('python.goal_tools.978e0eea36aa'),
                    )
                goal = store.complete(ctx.session_id, goal_id=goal_id, revision=revision, checks=checks)
                prefix = tr('python.goal_tools.e159a298e80a')
            else:
                if not isinstance(blocked_reason, str) or not blocked_reason.strip():
                    raise _fail(
                        "GOAL_TOOL_INVALID_UPDATE",
                        tr('python.goal_tools.4ea55a9ef35d'),
                    )
                goal = store.block(
                    ctx.session_id,
                    goal_id=goal_id,
                    revision=revision,
                    reason=blocked_reason,
                )
                prefix = tr('python.goal_tools.0da55b6c22fc')

        await store.publish(ctx.session_id)
        value = goal.to_value()
        return ToolResult(
            content=[TextBlock(text=prefix + render(value))],
            display=value,
            data=value,
        )

    return [
        ToolDefinition(
            schema={
                "description_key": 'python.goal_tools.37fd23f02c40',
                "parameter_keys": {
                    ('properties', 'evidence_before', 'description'): 'python.goal_tools.6a1b9d0b22c2',
                },
                "name": "get_goal",
                "description": (
                    tr('python.goal_tools.37fd23f02c40')
                ),
                "parameters": {"type": "object", "properties": {"evidence_before": {"type": "integer", "minimum": 0, "description": tr('python.goal_tools.6a1b9d0b22c2')}}, "additionalProperties": False},
            },
            execute=get_goal,
            effect="read",
            to_content=to_content,
            concurrency_safe=True,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.goal_tools.d3986be4aff1',
                "parameter_keys": {
                    ('properties', 'objective', 'description'): 'python.goal_tools.6ccf2c22759f',
                    ('properties', 'success_criteria', 'description'): 'python.goal_tools.0a725d3dd998',
                    ('properties', 'max_goal_rounds', 'description'): 'python.goal_tools.cc4c99c6a0d6',
                    ('properties', 'token_budget', 'description'): 'goal.model.token_budget',
                },
                "name": "create_goal",
                "description": (
                    tr('python.goal_tools.d3986be4aff1')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "objective": {
                            "type": "string",
                            "minLength": 1,
                            "description": tr('python.goal_tools.6ccf2c22759f'),
                        },
                        "success_criteria": {"type": "array", "minItems": 1, "maxItems": MAX_CRITERIA, "items": {"type": "string", "minLength": 1, "maxLength": 2000}, "description": tr('python.goal_tools.0a725d3dd998')},
                        "max_goal_rounds": {
                            "type": ["number", "null"],
                            "minimum": 1,
                            "maximum": MAX_GOAL_ROUNDS_CEILING,
                            "description": tr('python.goal_tools.cc4c99c6a0d6'),
                        },
                        "token_budget": {"type": "integer", "minimum": 1,
                            "description": tr('goal.model.token_budget')},
                    },
                    "required": ["objective"],
                    "additionalProperties": False,
                },
            },
            execute=create_goal,
            to_content=to_content,
            concurrency_safe=False,
        ),
        ToolDefinition(
            schema={
                "description_parts": (SchemaResource('python.goal_tools.47bf51087996'), SchemaResource('python.goal_evidence.0667121b256b', {'p0': f'{MAX_EVIDENCE_PER_CHECK}', 'p1': f'{MAX_CHECK_SUMMARY_CHARS}'}),),
                "parameter_keys": {
                    ('properties', 'goal_id', 'description'): 'python.goal_tools.63531734762e',
                    ('properties', 'revision', 'description'): 'python.goal_tools.48c086c80b0b',
                    ('properties', 'action', 'description'): 'python.goal_tools.0d44c715826f',
                    ('properties', 'checks', 'description'): 'python.goal_tools.eddf9551bdce',
                    ('properties', 'blocked_reason', 'description'): 'python.goal_tools.bce31d002826',
                },
                "name": "update_goal",
                "description": tr('python.goal_tools.47bf51087996') + completion_guidance(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "goal_id": {"type": "string", "minLength": 1, "description": tr('python.goal_tools.63531734762e')},
                        "revision": {
                            "type": "integer",
                            "minimum": 1,
                            "description": tr('python.goal_tools.48c086c80b0b'),
                        },
                        "action": {
                            "type": "string",
                            "enum": list(UPDATE_ACTIONS),
                            "description": (
                                tr('python.goal_tools.0d44c715826f')
                            ),
                        },
                        "checks": {"type": "array", "minItems": 1, "maxItems": MAX_CRITERIA, "items": {
                            "type": "object", "properties": {"criterion": {"type": "integer", "minimum": 1, "maximum": MAX_CRITERIA},
                            "summary": {"type": "string", "minLength": 1, "maxLength": MAX_CHECK_SUMMARY_CHARS}, "evidence_seqs": {"type": "array", "minItems": 1, "maxItems": MAX_EVIDENCE_PER_CHECK, "items": {"type": "integer", "minimum": 0}}},
                            "required": ["criterion", "summary", "evidence_seqs"], "additionalProperties": False}, "description": tr('python.goal_tools.eddf9551bdce')},
                        "blocked_reason": {
                            "type": "string",
                            "description": tr('python.goal_tools.bce31d002826'),
                        },
                    },
                    "required": ["goal_id", "revision", "action"],
                    "additionalProperties": False,
                },
            },
            execute=update_goal,
            to_content=to_content,
            concurrency_safe=False,
        ),
    ]


__all__ = [
    "DEFAULT_MAX_GOAL_ROUNDS",
    "MAX_GOAL_ROUNDS_CEILING",
    "UPDATE_ACTIONS",
    "GoalStore",
    "GoalView",
    "GoalStatus",
    "build_goal_tools",
]

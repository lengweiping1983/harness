"""全树子代理准入：名额、深度与让渡都在同一处判定。

本机 Codex 0.166.0 的 ``agents`` 配置段把并发、深度和单任务时限列为三个独立
硬约束（``max_concurrent_threads_per_session`` / ``max_depth`` /
``job_max_runtime_seconds``）。本模块实现同一组约束，并额外解决父子互锁：

* **名额按整棵代理树计**，后代不再绕过闸门，避免深层派生无界膨胀；
* **阻塞等待时让渡名额**，父代理等待子代理不会与闸门互相等待；
* **深度在派生时判定**，超限立即失败并给出可行动错误，而不是静默排队；
* 让渡与召回都是显式状态迁移；取消既不丢失名额，也不重复计数。

状态机::

    queued --(_admit)--> held --(lend)--> lent --(recall)--> queued|held
      |                    |                |
      +------(release)-----+----(release)---+--> released

``held`` 计入活跃名额；``lent`` 已在让渡时把名额交回闸门，因此不再计入；
``released`` 是幂等终态。
"""
from __future__ import annotations

from ..core.errors import HarnessError
from ..i18n import tr

import asyncio
import contextlib
from collections import deque
from dataclasses import dataclass, field
from typing import Any

#: 名额排队中，尚未开始工作
QUEUED = 'queued'
#: 持有名额，正在工作
HELD = 'held'
#: 已把名额让渡给闸门（自身阻塞在等待上）
LENT = 'lent'
#: 已释放，幂等终态
RELEASED = 'released'


class AgentDepthExceeded(HarnessError):
    """派生深度超过宿主上限；调用方必须先收敛已有分支。"""

    def __init__(self, message: str, *, depth: int, max_depth: int) -> None:
        super().__init__(message, code='agent/depth-exceeded')
        self.depth = depth
        self.max_depth = max_depth


@dataclass
class Permit:
    """一次运行的名额凭证；身份与深度在派生时固定，不随状态迁移改变。"""

    agent_id: str
    depth: int
    state: str = QUEUED
    event: asyncio.Event | None = field(default=None, repr=False, compare=False)

    @property
    def held(self) -> bool:
        """是否正在占用一个活跃名额。"""
        return self.state == HELD


class AdmissionGate:
    """整棵代理树共用的准入闸门。

    :param capacity: 同时活跃的运行上限；``None`` 表示不限制名额，
        此时仍登记深度与身份，保证快照和让渡语义一致。
    :param max_depth: 派生深度上限；``None`` 表示不限制。
    """

    def __init__(self, *, capacity: int | None = None, max_depth: int | None = None) -> None:
        if capacity is not None and (type(capacity) is not int or capacity < 1):
            raise ValueError('subagent/capacity-too-small')
        if max_depth is not None and (type(max_depth) is not int or max_depth < 1):
            raise ValueError('subagent/max-depth-too-small')
        self.capacity = capacity
        self.max_depth = max_depth
        self._active = 0
        self._queue: deque[Permit] = deque()
        self._permits: dict[str, Permit] = {}

    # ==================================================================
    # 判定
    # ==================================================================
    def check_depth(self, depth: int) -> None:
        """派生前判定深度，超限不留半成品运行记录。"""
        if type(depth) is not int or depth < 1:
            raise ValueError('subagent/invalid-depth')
        if self.max_depth is not None and depth > self.max_depth:
            raise AgentDepthExceeded(
                tr('subagent.depth_exceeded', depth=depth, max_depth=self.max_depth),
                depth=depth, max_depth=self.max_depth)

    async def acquire(self, agent_id: str, depth: int) -> Permit:
        """领取名额；容量不足时按先到先服务排队，取消不占用名额。

        领取过程被取消时凭证仍留在队列中：闸门稍后照常发放，
        由 :meth:`release_run` 统一收敛，因此不会丢失或重复计数名额。
        """
        self.check_depth(depth)
        permit = Permit(agent_id=agent_id, depth=depth, event=asyncio.Event())
        self._permits[agent_id] = permit
        self._queue.append(permit)
        self._admit()
        if permit.state != HELD:
            await permit.event.wait()
        return permit

    def lend(self, permit: Permit | None) -> bool:
        """阻塞等待前让出名额；返回是否真的让出，供调用方决定是否需要召回。"""
        if permit is None or permit.state != HELD:
            return False
        permit.state = LENT
        self._active -= 1
        self._admit()
        return True

    async def recall(self, permit: Permit | None) -> None:
        """等待结束后召回名额；容量已满时重新排队，取消不会丢失名额。"""
        if permit is None or permit.state == HELD:
            return
        if permit.state != LENT:
            raise ValueError('subagent/permit-not-lent')
        permit.state = QUEUED
        permit.event = asyncio.Event()
        self._queue.append(permit)
        self._admit()
        if permit.state == HELD:
            return
        await permit.event.wait()

    def release(self, permit: Permit | None) -> None:
        """归还名额；重复调用与未发放状态都是幂等无操作。"""
        if permit is None or permit.state == RELEASED:
            return
        state = permit.state
        permit.state = RELEASED
        permit.event = None
        self._permits.pop(permit.agent_id, None)
        with contextlib.suppress(ValueError):
            self._queue.remove(permit)
        if state == HELD:
            self._active -= 1
        # LENT 已在让渡时把名额交回闸门，这里不能再次增加。
        self._admit()

    def release_run(self, agent_id: str) -> None:
        """按运行身份归还名额；领取被取消后仍能收敛，重复调用无副作用。"""
        self.release(self._permits.get(agent_id))

    def permit_for(self, agent_id: str) -> Permit | None:
        """读取某次运行当前凭证，只用于观察与诊断。"""
        return self._permits.get(agent_id)

    # ==================================================================
    # 观察与内部迁移
    # ==================================================================
    def _admit(self) -> None:
        """按先到先服务发放名额；不限制容量时一次清空队列。"""
        while self._queue:
            if self.capacity is not None and self._active >= self.capacity:
                return
            permit = self._queue.popleft()
            if permit.state == RELEASED:
                continue
            permit.state = HELD
            self._active += 1
            if permit.event is not None:
                permit.event.set()

    def snapshot(self) -> dict[str, Any]:
        """当前准入事实；等待方据此判断是否需要先收敛分支。"""
        return {
            'capacity': self.capacity,
            'max_depth': self.max_depth,
            'active': self._active,
            'queued': sum(1 for permit in self._queue if permit.state == QUEUED),
            'lent': sum(1 for permit in self._permits.values() if permit.state == LENT),
            'deepest': max((permit.depth for permit in self._permits.values()), default=0),
        }


__all__ = ['AdmissionGate', 'AgentDepthExceeded', 'Permit', 'QUEUED', 'HELD', 'LENT', 'RELEASED']

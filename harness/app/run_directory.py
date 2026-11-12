"""统一查询实际运行及委派关系，使用共享存储和调度器的原活动对象。"""
from __future__ import annotations

from typing import Any

from ..core.errors import HarnessError
from ..i18n import tr
from ..core.runs import AgentRun


class RunDirectory:
    """跨队列与委派调度器查询，同一运行只有一份持久记录。"""

    def __init__(self, delegated: Any, controller: Any = None):
        self.delegated = delegated
        self.controller = controller

    def get(self, run_id: str) -> AgentRun | None:
        record = self.controller.records.get(run_id) if self.controller is not None else None
        return record if record is not None else self.delegated.get(run_id)

    def current(self, session_id: str) -> AgentRun | None:
        """只返回实际活动的拥有者，不把排队、已完成或旧会话运行当作当前运行。"""
        delegated = self.delegated.state_for_session(session_id)
        if delegated is not None and not delegated.finished:
            return delegated
        return self.controller.current(session_id) if self.controller is not None else None

    def context_owner(self, session_id: str) -> AgentRun | None:
        """从活动或落盘委派恢复成员归属，不把历史会话分叉冒充主从关系。"""
        owner = self.current(session_id) or self.delegated.state_for_session(session_id)
        if owner is not None or self.delegated.archive is None:
            return owner
        identifier = self.delegated.archive.latest_for_context(session_id)
        return self.delegated.get(identifier) if identifier else None

    def active(self, record: AgentRun) -> bool:
        return self.current(record.session_id) is record and not record.finished

    def ancestors(self, run_id: str) -> tuple[AgentRun, ...]:
        """按精确运行身份追溯委派，不按会话最新内容猜测；坏链明确拒绝。"""
        child = self.get(run_id)
        if child is None:
            raise KeyError(run_id)
        result = []
        seen = {child.id}
        while child.parent_run_id:
            parent = self.get(child.parent_run_id)
            if (parent is None or parent.id in seen or parent.session_id != child.parent_session_id
                    or (parent.root_session_id or parent.session_id) != child.root_session_id):
                raise HarnessError(tr('run.invalid_lineage'), code='run/invalid-lineage')
            seen.add(parent.id)
            result.append(parent)
            child = parent
        return tuple(result)

    def controls(self, session_id: str, record: AgentRun) -> bool:
        """取消权限沿真实委派链判断，会话可继续管理自己先前派出的后代。"""
        if record.parent_session_id == session_id:
            return True
        return any(parent.parent_session_id == session_id for parent in self.ancestors(record.id))

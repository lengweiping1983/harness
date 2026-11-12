"""为子代理循环事件绑定当次运行身份，避免复用会话混淆来源。"""
from __future__ import annotations
from typing import Any


class SubagentEventBus:
    """委托共享总线，同时为当前循环发出的负载追加可信运行身份。"""

    def __init__(self, bus: Any, agent_id: str, parent_session_id: str):
        self.bus = bus
        self.agent_id = agent_id
        self.parent_session_id = parent_session_id

    async def emit(self, event: str, *args: Any, **kwargs: Any) -> None:
        """处理单负载及会话标识加负载，不原位修改调用者的数据。"""
        if args and isinstance(args[-1], dict):
            args = (*args[:-1], {**args[-1], 'agent_id': self.agent_id,
                'parent_session_id': self.parent_session_id})
        await self.bus.emit(event, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        """订阅和统计仍属于原共享总线，不建立第二套事件分发。"""
        return getattr(self.bus, name)

"""把本轮团队的真实工具证据与主会话目标连接起来。"""
from __future__ import annotations

import asyncio
from dataclasses import replace

from ..i18n import tr
from ..core.errors import ToolError
from ..core.jsonutil import to_jsonable
from ..llm.types import ToolUseBlock
from ..tools.builtin.goal_evidence import BOOKKEEPING


class TeamGoal:
    def __init__(self, harness, session, goal):
        self.harness, self.session, self.goal_id = harness, session, goal.id
        self.turn = session.current_turn
        self.states = []
        self.copied = set()
        self.calls = []
        self.lock = asyncio.Lock()
        self.closed = False

    def register(self, state):
        self.states.append((state, len(state.session.events) if state.session else 0))
        state.text += '\n\n' + self.harness.goals.context(self.session.id)
        state.text += tr('goal.team.member_policy')
        state._team_goal = self
        state._team_goal_writer = False

    async def context(self, ctx, *, write=False, complete=False):
        goal = self.harness.goals.get(self.session.id)
        if self.closed or self.session.open_turn != self.turn or goal is None or goal.id != self.goal_id:
            raise ToolError(tr('goal.team.expired'), code='GOAL_TEAM_EXPIRED')
        if write and not ctx.services.get('team_goal_writer'):
            raise ToolError(tr('goal.team.read_only'), code='GOAL_TEAM_READ_ONLY')
        guard = getattr(self, 'completion_guard', None)
        if complete and guard is not None:
            feedback = await guard()
            if feedback:
                raise ToolError(feedback, code='GOAL_TEAM_UNVERIFIED')
        await self.sync()
        return replace(ctx, session_id=self.session.id)

    async def sync(self):
        """复制当前运行的成对工具事件，保留来源并使用父会话证据序号。"""
        async with self.lock:
            goal = self.harness.goals.get(self.session.id)
            if goal is None or goal.id != self.goal_id:
                return
            for state, start in self.states:
                child = state.session
                if child is None:
                    continue
                events = child.events[start:]
                for event in events:
                    key = (state.id, event.seq)
                    if event.type != 'tool/result' or event.data.get('name') in BOOKKEEPING or key in self.copied:
                        continue
                    call = next((row for row in reversed(events) if row.seq < event.seq
                        and row.type == 'tool/call' and row.data.get('status') == 'running'
                        and row.data.get('call_id') == event.data.get('call_id')
                        and row.data.get('turn') == event.data.get('turn')), None)
                    if call is None:
                        continue
                    call_id = f'team:{state.id}:{event.data["call_id"]}'
                    for source in (call, event):
                        data = to_jsonable(source.data)
                        data.update(call_id=call_id, turn=self.turn,
                            member_session_id=child.id, member_source_seq=source.seq, team_goal_id=self.goal_id)
                        for row in data.get('results', []):
                            row['tool_use_id'] = call_id
                        data['kind'] = 'team-goal/' + source.type
                        await self.session.append('note', data, ignorable=True)
                    self.copied.add(key)
                    self.calls.append(ToolUseBlock(id=call_id, name=call.data['name'], input=to_jsonable(call.data.get('input', {}))))

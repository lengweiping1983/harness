"""将父会话的持久引导送入本轮成员的执行边界，不重放到下一团队任务。"""
from __future__ import annotations
from typing import Any
from ..session.events import AppendOp


class TeamSteering:
    """成员各自确认消费；父回执仅在真实成员读取后标记应用。"""
    def __init__(self, harness: Any, session: Any, parent: Any, team: Any, *, acknowledge_parent: bool = True, manage_admission: bool = True):
        self.harness, self.session, self.parent = harness, session, parent
        self.turn = session.current_turn
        self.closed = False
        self.supported = not any(member.runner for member in team.members)
        self.acknowledge_parent = acknowledge_parent
        self.manage_admission = manage_admission

    def records(self):
        if self.closed or self.session.open_turn != self.turn:
            return []
        records = self.session.steering.records
        return [records[key] for key in self.session.steering.by_turn.get(self.turn, [])
                if records[key]['status'] in ('pending', 'applied')]

    def can_accept(self):
        return not self.closed

    def register(self, state: Any):
        state._team_steering_source = TeamSteeringSource(self, state)

    def close(self):
        self.closed = True
        if self.manage_admission:
            self.parent.accepting_steering = False
        if self.parent.team_steering is self:
            self.parent.team_steering = None


class TeamSteeringSource:
    """每个成员维护独立消费游标；新成员仍读取本轮已应用的补充。"""
    def __init__(self, controller: TeamSteering, state: Any):
        self.controller, self.state = controller, state
        self.delivered: set[str] = set()
        self.started = False
        self.admission_lock = controller.parent.steering_lock

    def fork(self, state: Any):
        return TeamSteeringSource(self.controller, state)

    def pending(self):
        return any(record['request_id'] not in self.delivered for record in self.controller.records())

    async def prepare(self, agent: Any, session: Any, turn: int):
        self.started = True
        for record in self.controller.records():
            key = record['request_id']
            if key not in self.delivered and key not in session.steering.records:
                await session.append('note', {'kind':'steering/accepted','request_id':key,
                    'turn':turn,'text':record['text'],
                    **({'attachments': record['attachments']} if record.get('attachments') else {}),'run_id':agent.run_id}, ignorable=True)

    async def applied(self, agent: Any, session: Any, turn: int):
        controller = self.controller
        async with controller.parent.steering_lock:
            for record in controller.records():
                key = record['request_id']
                consumed = session.steering.records.get(key)
                if not consumed or consumed['status'] != 'applied':
                    continue
                self.delivered.add(key)
                if not controller.acknowledge_parent and record['status'] == 'pending' and not record.get('team_delivered'):
                    # 送达是持久化的执行证据，不在未配对工具结果之前插入用户消息。
                    await controller.session.append('note', {'kind':'steering/team-delivered',
                        'request_id':key,'turn':controller.turn,'member_session_id':session.id,
                        'member_applied_seq':consumed['applied_seq']}, ignorable=True)
                    await controller.harness.bus.emit('steer/state', {'session_id':controller.session.id,
                        'receipt':controller.harness.steering_receipt(controller.session.id,key,summary=True)})
                if controller.acknowledge_parent and record['status'] == 'pending':
                    event = await controller.session.append('user/message', {'text':record['text'],
                        **({'attachments': record['attachments']} if record.get('attachments') else {}),
                        'source':'steer','request_id':key,'turn':controller.turn}, surface_op=AppendOp())
                    await controller.harness.bus.emit('user/message', {'session_id':controller.session.id,
                        **event.data,'source_seq':event.seq,'source_time':event.time})
                    await controller.harness.bus.emit('steer/state', {'session_id':controller.session.id,
                        'receipt':controller.harness.steering_receipt(controller.session.id,key,summary=True)})

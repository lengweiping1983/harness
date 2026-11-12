"""团队运行内的有界成员预览，不进入正式消息或模型上下文。"""
from __future__ import annotations
import asyncio
import time
from typing import Any

PREVIEW_LIMIT = 12000
PREVIEW_INTERVAL = 0.1


class MemberStreamBridge:
    """只转发本轮登记成员的可见文本，结束后撤销全部监听与定时任务。"""

    def __init__(self, bus: Any, parent: str, run_id: str):
        self.bus, self.parent, self.run_id = bus, parent, run_id
        self.bindings: dict[str, dict[str, Any]] = {}
        self.closed = False
        self.listeners = {'request/start': self.start, 'assistant/delta': self.delta, 'tool/output': self.external_output}
        for event, listener in self.listeners.items():
            bus.on(event, listener)

    def bind(self, state: Any, member: Any, stage: str) -> None:
        """身份由宿主登记；规划阶段只转发已提供的思考内容，不转发调度协议正文。"""
        if self.closed or len(self.bindings) >= 8 or stage not in ('member', 'reply', 'summary', 'planning'):
            return
        self.bindings[state.id] = {'state': state, 'member': member.name,
            'stage': stage, 'external': bool(getattr(state, 'runner', None)), 'output_seq': 0, 'request_id': None, 'position': (-1, -1), 'version': 0,
            'text': '', 'chars': 0, 'thinking': '', 'thinking_chars': 0, 'dirty': False, 'timer': None, 'last_emit': 0.0}

    def owned(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """运行、父聊天及当前子会话必须同时匹配。"""
        if not isinstance(payload, dict) or self.closed or payload.get('parent_session_id') != self.parent or not isinstance(payload.get('agent_id'), str):
            return None
        row = self.bindings.get(payload.get('agent_id'))
        return row if row and payload.get('session_id') == row['state'].session_id else None

    async def start(self, payload: dict[str, Any]) -> None:
        """按回合及步骤拒绝迟到请求，下一请求重新建立预览。"""
        row = self.owned(payload)
        position = (payload.get('turn'), payload.get('step'))
        request = payload.get('request_id')
        if not row or row['external'] or any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in position) or not isinstance(request, str) or not request or position <= row['position']:
            return
        row.update(request_id=request, position=position, text='', chars=0, thinking='', thinking_chars=0)
        await self.publish(row)

    async def delta(self, payload: dict[str, Any]) -> None:
        """分别预览模型提供的文本与思考，拒绝旧请求和规划协议正文。"""
        row = self.owned(payload)
        text = payload.get('delta')
        if not row or row['external'] or payload.get('request_id') != row['request_id'] or not row['request_id'] or payload.get('type') not in ('text', 'thinking') or not isinstance(text, str) or not text:
            return
        channel = 'thinking' if payload.get('type') == 'thinking' else 'text'
        if row['stage'] == 'planning' and channel == 'text':
            return
        row['dirty'] = True
        counter = 'thinking_chars' if channel == 'thinking' else 'chars'
        row[counter] += len(text)
        row[channel] = (row[channel] + text)[-PREVIEW_LIMIT:]
        if time.monotonic() - row['last_emit'] >= PREVIEW_INTERVAL:
            await self.publish(row)
        if row['dirty'] and row['timer'] is None:
            row['timer'] = asyncio.create_task(self.flush_later(row))

    async def external_output(self, payload: dict[str, Any]) -> None:
        """只把外部委派工具的 stdout 映射为文本，不暴露工具状态 stderr。"""
        row = self.owned(payload)
        if not row or not row['external'] or payload.get('name') != 'delegate_agent':
            return
        position = (payload.get('turn'), payload.get('step'))
        sequence, offset = payload.get('output_seq'), payload.get('stdout_offset')
        call, text = payload.get('call_id'), payload.get('stdout')
        if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in (*position, sequence, offset)) or sequence < 1 or not isinstance(call, str) or not call or not isinstance(text, str):
            return
        if position < row['position']:
            return
        if position > row['position']:
            row.update(position=position, request_id=call, output_seq=0, text='', chars=0, thinking='', thinking_chars=0)
        if row['request_id'] != call or sequence <= row['output_seq'] or offset < row['chars']:
            return
        row['output_seq'] = sequence
        if not text:
            return
        # 缺失区段后不拼接成假连续正文，总字符数保留真实偏移。
        row['text'] = ((row['text'] if offset == row['chars'] else '') + text)[-PREVIEW_LIMIT:]
        row['chars'] = offset + len(text)
        row['dirty'] = True
        if time.monotonic() - row['last_emit'] >= PREVIEW_INTERVAL:
            await self.publish(row)
        if row['dirty'] and row['timer'] is None:
            row['timer'] = asyncio.create_task(self.flush_later(row))

    async def flush_later(self, row: dict[str, Any]) -> None:
        """合并短间隔增量，最后一段仍会在有界延迟内可见。"""
        try:
            while not self.closed and self.bindings.get(row['state'].id) is row:
                await asyncio.sleep(PREVIEW_INTERVAL)
                if self.closed or self.bindings.get(row['state'].id) is not row:
                    break
                await self.publish(row)
                if not row['dirty']:
                    break
        finally:
            row['timer'] = None

    async def publish(self, row: dict[str, Any], *, cleared: bool = False) -> None:
        """发送完整有界快照和单调版本，消费者无需拼接丢失的增量。"""
        row['dirty'] = False
        row['version'] += 1
        row['last_emit'] = time.monotonic()
        await self.bus.emit('agent/member-stream', {'session_id': self.parent, 'run_id': self.run_id,
            'member': row['member'], 'agent_id': row['state'].id, 'stage': row['stage'],
            'request_id': row['request_id'], 'version': row['version'],
            'status': 'cleared' if cleared else 'streaming', 'text': '' if cleared else row['text'],
            'thinking': '' if cleared else row['thinking'],
            'thinking_truncated': row['thinking_chars'] > len(row['thinking']),
            'chars_total': row['chars'], 'truncated': row['chars'] > len(row['text'])})

    async def finish(self, agent_id: str) -> None:
        """移除身份后再清理定时任务，迟到事件不能重新建立预览。"""
        row = self.bindings.pop(agent_id, None)
        if not row:
            return
        timer = row['timer']
        if timer is not None:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
        if row['request_id']:
            await self.publish(row, cleared=True)

    async def close(self) -> None:
        """整轮结束时先撤销转发权限，随后收拢资源。"""
        if self.closed:
            return
        self.closed = True
        for event, listener in self.listeners.items():
            self.bus.off(event, listener)
        for identifier in list(self.bindings):
            await self.finish(identifier)

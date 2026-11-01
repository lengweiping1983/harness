"""Agent 消息的事件投影与投递：会话日志是唯一持久事实源。"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from typing import Any

from ..i18n import tr, resources
from ..core.language import current_language
from ..core.errors import SessionInvariantError
from .events import AppendOp

MAX_MESSAGE_BYTES = 65536
MAX_PENDING_MESSAGES = 128


def validate_message_text(text: Any) -> None:
    """消息和续接任务共用输入边界，拒绝非法编码或超过字节预算的正文。"""
    try:
        valid = isinstance(text, str) and bool(text.strip()) and len(text.encode('utf-8')) <= MAX_MESSAGE_BYTES
    except UnicodeError:
        valid = False
    if not valid:
        raise ValueError(tr('agent.message.invalid'))


def render_agent_message(record, language: str) -> str:
    """模型文本从原始消息派生，应用回执不再次保存正文。"""
    message = record.data
    frame = json.dumps({key: message[key] for key in
        ('message_id', 'sender_session_id', 'sender_agent_id', 'text')}, ensure_ascii=False)
    return resources(language)['agent.message.context'] + '\n' + frame


class AgentMailbox:
    """只索引已提交的消息与应用回执，不维护第二份收件箱文件。"""

    def __init__(self, session):
        self.session = session
        self.records: dict[str, Any] = {}
        self.applied: dict[str, int] = {}
        self.queued: dict[str, Any] = {}
        self.unapplied: dict[str, tuple[int, str]] = {}
        self.changed = asyncio.Event()
        self.lock = asyncio.Lock()

    def validate(self, event, *, replay: bool = False) -> None:
        """新写入先校验，损坏重放明确失败，原日志不做修复或迁移。"""
        data = event.data
        if event.type == 'developer/message' and data.get('source') == 'agent-message':
            fields = {'source', 'message_id', 'sender_session_id', 'turn', 'language'}
            sources = event.source_event_seqs or ()
            record = self.session.event_at(sources[0]) if len(sources) == 1 else None
            if (set(data) != fields or data.get('language') not in ('zh-CN', 'en')
                    or type(data.get('turn')) is not int or record is None or record.type != 'agent/message'
                    or record.data['message_id'] != data.get('message_id')
                    or record.data['sender_session_id'] != data.get('sender_session_id')):
                raise SessionInvariantError(tr('agent.message.invalid_record'))
            return
        if event.type != 'agent/message':
            return
        fields = {'message_id', 'sender_session_id', 'sender_agent_id', 'recipient_session_id', 'recipient_run_id', 'text', 'scope'}
        valid = isinstance(data, Mapping) and set(data) == fields
        valid = valid and isinstance(data['message_id'], str) and bool(re.fullmatch('msg_[a-f0-9]{64}', data['message_id']))
        valid = valid and all(isinstance(data[key], str) and data[key] and '\0' not in data[key]
                              for key in ('sender_session_id', 'recipient_session_id', 'text'))
        valid = valid and all(data[key] is None or isinstance(data[key], str) and bool(data[key])
                              for key in ('sender_agent_id', 'recipient_run_id', 'scope'))
        try:
            valid = valid and bool(data['text'].strip()) and len(data['text'].encode('utf-8')) <= MAX_MESSAGE_BYTES
        except UnicodeError:
            valid = False
        if not valid or not replay and data['recipient_session_id'] != self.session.id:
            raise SessionInvariantError(tr('agent.message.invalid_record'))
        previous = self.records.get(data['message_id'])
        if previous is not None and dict(previous.data) != dict(data):
            raise SessionInvariantError(tr('agent.message.conflict'))

    def absorb(self, event) -> None:
        """提交成功或重放时建立索引；未落盘的消息不会唤醒等待者。"""
        data = event.data
        if event.type == 'agent/message':
            self.validate(event, replay=True)
            # 分叉会复制历史，但旧收件人的待处理消息不能投递给新身份。
            if data['recipient_session_id'] != self.session.id:
                return
            identifier = data['message_id']
            self.records.setdefault(identifier, event)
            if identifier not in self.applied and identifier not in self.unapplied:
                self.queued.setdefault(identifier, event)
                self.changed.set()
        elif event.type == 'developer/message' and data.get('source') == 'agent-message':
            self.validate(event, replay=True)
            identifier = data.get('message_id')
            record = self.records.get(identifier) if isinstance(identifier, str) else None
            if record is not None and record.seq in (event.source_event_seqs or ()):
                self.applied.setdefault(identifier, event.seq)
                self.queued.pop(identifier, None)
                if not self.queued:
                    self.changed.clear()
        elif event.type == 'note' and data.get('kind') == 'agent/message-unapplied':
            identifier = data.get('message_id')
            record = self.records.get(identifier) if isinstance(identifier, str) else None
            if record is not None and record.seq in (event.source_event_seqs or ()):
                self.unapplied.setdefault(identifier, (event.seq, str(data.get('reason', ''))))
                self.queued.pop(identifier, None)
                if not self.queued:
                    self.changed.clear()

    def pending(self) -> tuple[Any, ...]:
        return tuple(self.queued.values())

    def receipt(self, identifier: str) -> dict[str, Any] | None:
        event = self.records.get(identifier)
        if event is None:
            return None
        return {'message_id': identifier, 'sender_session_id': event.data['sender_session_id'],
                'recipient_session_id': self.session.id, 'accepted_seq': event.seq,
                'status': 'applied' if identifier in self.applied else 'unapplied' if identifier in self.unapplied else 'queued',
                'unapplied_reason': self.unapplied.get(identifier, (None, None))[1],
                'applied_seq': self.applied.get(identifier),
                'persisted': self.session.commit_event is not None}

    async def accept(self, message: dict[str, Any]) -> dict[str, Any]:
        """同一调用重复投递只读回回执；更换内容明确冲突。"""
        text = message['text']
        validate_message_text(text)
        async with self.lock:
            identifier = message['message_id']
            previous = self.records.get(identifier)
            if previous is not None:
                if dict(previous.data) != message:
                    raise ValueError(tr('agent.message.conflict'))
                return self.receipt(identifier)
            if len(self.pending()) >= MAX_PENDING_MESSAGES:
                raise ValueError(tr('agent.message.full'))
            await self.session.append('agent/message', message)
            return self.receipt(identifier)

    async def apply(self, turn: int, *, run_id: str | None = None) -> int:
        """在模型请求前应用，每条消息与其来源在同一次表面提交中记录。"""
        count = 0
        async with self.lock:
            for event in self.pending():
                message = event.data
                if message['recipient_run_id'] is not None and message['recipient_run_id'] != run_id:
                    await self.session.append('note', {'kind': 'agent/message-unapplied',
                        'message_id': message['message_id'], 'reason': 'recipient-run-ended'},
                        source_event_seqs=[event.seq])
                    continue
                await self.session.append('developer/message', {
                    'source': 'agent-message', 'message_id': message['message_id'],
                    'sender_session_id': message['sender_session_id'], 'turn': turn,
                    'language': current_language.get(),
                }, surface_op=AppendOp(), source_event_seqs=[event.seq])
                count += 1
        return count

    async def wait_pending(self) -> None:
        """只唤醒等待中的 Agent，不自行启动回合或重放任务。"""
        while not self.pending():
            self.changed.clear()
            await self.changed.wait()

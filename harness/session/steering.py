"""已提交的引导回执，不将未应用的请求重放到新回合。"""
from __future__ import annotations

from harness.i18n import tr

from collections.abc import Mapping
from typing import Any

from .events import user_message_text


class SteeringLedger:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.by_turn: dict[int, list[str]] = {}

    def absorb(self, event: Any) -> None:
        data = event.data
        if not isinstance(data, Mapping):
            return
        request_id = data.get('request_id')
        if event.type == 'note' and data.get('kind') == 'steering/accepted':
            if not isinstance(request_id, str) or type(data.get('turn')) is not int or not isinstance(data.get('text'), str):
                return
            # 首个提交的请求具有权威性；重复请求不能
            # 替换内容或重置已经应用的回执。
            if request_id not in self.records:
                self.records[request_id] = {'request_id': request_id, 'turn': data['turn'],
                    'text': data['text'], 'status': 'unapplied' if data.get('unapplied_reason') else 'pending', 'accepted_seq': event.seq,
                    'accepted_at': event.time, 'applied_seq': None, 'reason': str(data.get('unapplied_reason', '')),
                    'team_delivered': False, 'delivery_seq': None,
                    **({'original_text': data['original_text']} if isinstance(data.get('original_text'), str) else {}),
                    **({'attachments': data['attachments']} if data.get('attachments') else {})}
                self.by_turn.setdefault(data['turn'], []).append(request_id)
        elif event.type == 'note' and data.get('kind') == 'steering/team-delivered':
            record = self.records.get(request_id) if isinstance(request_id, str) else None
            if (record is not None and type(data.get('turn')) is int and record['turn'] == data.get('turn') and record['status'] == 'pending'
                    and not record.get('team_delivered') and isinstance(data.get('member_session_id'), str)
                    and data['member_session_id'] and type(data.get('member_applied_seq')) is int
                    and data['member_applied_seq'] > 0):
                record.update(team_delivered=True, delivery_seq=event.seq)
        elif event.type == 'user/message' and data.get('source') == 'steer' and isinstance(request_id, str) and request_id in self.records:
            record = self.records[request_id]
            if data.get('turn') == record['turn'] and record['status'] == 'pending':
                record.update(status='applied', applied_seq=event.seq)
        elif event.type == 'turn/end' and type(data.get('turn')) is int:
            for request_id in self.by_turn.get(data.get('turn'), []):
                record = self.records[request_id]
                if record['status'] == 'pending':
                    reason = data.get('reason')
                    kind = reason.get('kind', '') if isinstance(reason, Mapping) else str(reason or '')
                    if not isinstance(kind, str): kind = ''
                    label = {'aborted': tr('python.steering.f006455e3baf'), 'error': tr('python.steering.9344597efd49'), 'interrupted': tr('python.steering.1f4691c29724'),
                        'max-tokens': tr('python.steering.f5f8c677a79f'), 'blocked': tr('python.steering.70785e51f882'), 'completed': tr('python.steering.324225eef1d7')}.get(kind, tr('python.steering.287eb2787dd3'))
                    record.update(status='unapplied', reason=label + tr('python.steering.09b63162133e'))

    def pending(self, turn: int) -> list[dict[str, Any]]:
        return [self.records[key] for key in self.by_turn.get(turn, []) if self.records[key]['status'] == 'pending']

    def receipt(self, request_id: str, *, active_turn: int | None, summary: bool = False) -> dict[str, Any] | None:
        record = self.records.get(request_id)
        if record is None:
            return None
        value = dict(record)
        if value['status'] == 'pending' and value['turn'] != active_turn:
            value.update(status='unapplied', reason=tr('python.steering.1f8ef0ca99b2'))
        text = user_message_text(value)
        value['text'] = text
        value.pop('original_text', None)
        value['text_truncated'] = summary and len(text) > 1600
        if summary:
            value['text'] = text[:1600]
        return value

    def recent(self, turn: int, *, active_turn: int | None, limit: int = 50) -> list[dict[str, Any]]:
        return [self.receipt(key, active_turn=active_turn, summary=True) for key in self.by_turn.get(turn, [])[-limit:]]

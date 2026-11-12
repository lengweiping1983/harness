"""原生模态窗口句柄只临时保存，响应时必须匹配当前标识。"""
from __future__ import annotations

from harness.i18n import tr

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ..core.errors import HarnessError
from .browser_diagnostics import diagnostic_url


@dataclass
class PendingDialog:
    session_id: str
    page: Any
    native: Any
    id: str
    page_id: str
    message: str
    kind: str
    url: str
    opened_at: float
    operation: dict | None = None
    responding: bool = False
    unconfirmed: bool = False

    def view(self):
        result = {'id': self.id, 'page_id': self.page_id, 'type': self.kind, 'message': self.message,
                  'url': self.url, 'opened_at': self.opened_at, 'response_unconfirmed': self.unconfirmed,
                  'page_content_trusted': False}
        message_copy = getattr(self.native, 'message_copy', None)
        if message_copy is not None:
            result['message_copy'] = message_copy
            result['message'] = tr(message_copy['key'], **message_copy['params'])
        if self.kind == 'prompt':
            result['default_value'] = getattr(self.native, 'default_value', '')[:2000]
        if self.operation:
            result['origin_action'] = {key: self.operation[key] for key in ('id', 'action', 'actor', 'status')}
        return result


class BrowserDialogs:
    def __init__(self):
        self.pending: dict[Any, PendingDialog] = {}
        self.events: dict[str, asyncio.Event] = {}

    def event(self, session_id):
        return self.events.setdefault(session_id, asyncio.Event())

    def opened(self, session_id, page, page_id, native, operation=None):
        entry = PendingDialog(session_id, page, native, uuid.uuid4().hex, page_id, native.message[:4000],
                              native.type, diagnostic_url(page.url), time.time(), operation)
        self.pending[page] = entry
        self.event(session_id).set()
        return entry

    def current(self, session_id, selected=None):
        preferred = self.pending.get(selected)
        if preferred and preferred.session_id == session_id:
            return preferred
        return next((entry for entry in self.pending.values() if entry.session_id == session_id), None)

    def forget(self, page):
        entry = self.pending.pop(page, None)
        if entry and not self.current(entry.session_id):
            self.event(entry.session_id).clear()

    def validate(self, session_id, args):
        if not isinstance(args.get('dialog_id'), str) or len(args['dialog_id']) > 80 or type(args.get('accept')) is not bool:
            raise HarnessError(tr('python.browser_dialogs.436d372580c4'), code='browser/dialog-input')
        entry = next((item for item in self.pending.values() if item.session_id == session_id and item.id == args['dialog_id']), None)
        if entry is None or args.get('page_id') not in {None, entry.page_id}:
            raise HarnessError(tr('python.browser_dialogs.437532dc6724'), code='browser/dialog-stale')
        text = args.get('prompt_text')
        if text is not None and (not isinstance(text, str) or len(text) > 2000 or entry.kind != 'prompt' or not args['accept']):
            raise HarnessError(tr('python.browser_dialogs.2fb503799a3f'), code='browser/dialog-input')
        if entry.kind == 'prompt' and args['accept'] and text is None:
            raise HarnessError(tr('python.browser_dialogs.4e4684111fc5'), code='browser/dialog-input')
        if entry.unconfirmed:
            raise HarnessError(tr('python.browser_dialogs.fe50f0c4283c'), code='browser/dialog-unconfirmed')
        if entry.responding:
            raise HarnessError(tr('python.browser_dialogs.e10eb017a3c4'), code='browser/dialog-busy')
        return entry

    async def respond(self, entry, args):
        entry.responding = True
        try:
            async with asyncio.timeout(5):
                if args['accept']:
                    if entry.kind == 'prompt':
                        await entry.native.accept(prompt_text=args['prompt_text'])
                    else:
                        await entry.native.accept()
                else:
                    if entry.kind == 'beforeunload' and entry.operation:
                        entry.operation['navigation_cancelled'] = True
                    await entry.native.dismiss()
        except BaseException as exc:
            entry.unconfirmed = True
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise HarnessError(tr('python.browser_dialogs.e1b91ff9a7f5'), code='browser/dialog-unconfirmed') from exc
        finally:
            entry.responding = False
        # 接受一个对话框可能在同一页面同步打开下一个对话框。
        if self.pending.get(entry.page) is entry:
            self.forget(entry.page)
        return {'accepted': args['accept'], 'type': entry.kind, 'response_confirmed': True}

"""连接桌面宿主的真实页面，复用现有浏览器观察和操作逻辑。"""
from __future__ import annotations

from harness.i18n import tr

import asyncio
import json
import os
import time
import urllib.request
import urllib.error
import urllib.parse

from .browser import ACTIONS, READ_ACTIONS, BrowserService
from ..core.errors import HarnessError


def desktop_cdp_endpoint(value):
    """桌面浏览器只连接本机宿主，拒绝远端及带凭据的端点。"""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {'http', 'https', 'ws', 'wss'} or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or parsed.username or parsed.password:
        raise HarnessError(tr('python.audit.cdp_local'), code='browser/desktop-endpoint')
    return value


class DesktopDialog:
    """桌面宿主持有弹窗，Python 仅转发同一标识的回应。"""

    def __init__(self, service, session_id, snapshot):
        self.service, self.session_id, self.snapshot = service, session_id, snapshot
        self.type, self.message = snapshot['type'], snapshot['message']
        self.message_copy = snapshot.get('message_copy')
        self.default_value = snapshot.get('default_value', '')

    async def accept(self, prompt_text=None):
        await self.service.request('dialog_response', self.session_id, id=self.snapshot['id'], accept=True, prompt_text=prompt_text)

    async def dismiss(self):
        await self.service.request('dialog_response', self.session_id, id=self.snapshot['id'], accept=False)


class DesktopContext:
    """只暴露当前聊天拥有的页面，避免关闭共享浏览器或其他聊天。"""

    def __init__(self, service, session_id, context):
        self.service, self.session_id, self.context = service, session_id, context
        self.owned = []

    @property
    def pages(self):
        return [page for page in self.owned if not page.is_closed()]

    async def new_page(self):
        result = await self.service.request('new_tab', self.session_id)
        page = await self.service.resolve(self, result['target_id'])
        tab = next((tab for tab in result['tabs'] if tab['id'] == result['active_tab']), None)
        await self.service._sync_viewport(self.session_id, tab.get('viewport') if tab else None, self)
        return page

    async def close(self):
        await self.service.request('close_session', self.session_id)


class DesktopBrowserService(BrowserService):
    desktop_managed = True

    def __init__(self, root, *, bus=None):
        super().__init__(root, headless=False, bus=bus)
        self.connection = None
        self._dialog_clients = {}
        self._dialog_monitor = None
        self._desktop_viewports = {}

    def _viewport(self, session_id):
        return self._desktop_viewports.get(session_id) or super()._viewport(session_id)

    async def _sync_viewport(self, session_id, viewport, context):
        if not isinstance(viewport, dict) or not all(type(viewport.get(key)) is int and viewport[key] > 0 for key in ('width', 'height')):
            return
        current = {key: viewport[key] for key in ('width', 'height')}
        previous = self._desktop_viewports.get(session_id)
        if previous != current:
            self._desktop_viewports[session_id] = current
            if previous is not None:
                self._revisions[session_id] = self._revisions.get(session_id, 0) + 1
                await self._discard_observations(context)

    def _watch_page(self, page, session_id=None):
        super()._watch_page(page, session_id)
        listeners = self._diagnostic_listeners[page]
        for event, listener in list(listeners):
            if event == 'dialog':
                page.remove_listener(event, listener)
                listeners.remove((event, listener))
        # 保留监听以阻止 Playwright 自动拒绝；真实回应由桌面宿主执行。
        listener = lambda _native: self._queue_dialog_sync(session_id)
        page.on('dialog', listener)
        listeners.append(('dialog', listener))

    def _queue_dialog_sync(self, session_id):
        if self._closing or not session_id:
            return
        async def sync():
            result = await self.request('state', session_id, measure=False)
            self._sync_dialogs(session_id, result)
        task = asyncio.create_task(sync())
        self._notifications.add(task)
        def finished(task):
            self._notifications.discard(task)
            if not task.cancelled():
                task.exception()
        task.add_done_callback(finished)

    def _sync_dialogs(self, session_id, result):
        wrapper = self.contexts.get(session_id)
        if not wrapper:
            return
        snapshots = {entry['page_id']: entry for entry in result.get('dialogs', [])}
        for page in wrapper.pages:
            snapshot = snapshots.get(self._id(page))
            current = self.dialogs.pending.get(page)
            if not snapshot:
                self.dialogs.forget(page)
            elif not current or current.id != snapshot['id']:
                operation = self._busy.get(session_id) or self._reply_origins.get(session_id)
                entry = self.dialogs.opened(session_id, page, self._id(page), DesktopDialog(self, session_id, snapshot), operation)
                entry.id = snapshot['id']
                if operation:
                    operation['status'] = 'waiting_dialog'
            elif current:
                current.unconfirmed = snapshot.get('response_unconfirmed', False)

    async def request(self, action, session_id, **args):
        self.validate_session(session_id)
        def send():
            request = urllib.request.Request(os.environ['HARNESS_DESKTOP_BRIDGE'],
                data=json.dumps({'action': action, 'session_id': session_id, **args}).encode(),
                headers={'Authorization': 'Bearer ' + os.environ['HARNESS_DESKTOP_TOKEN'], 'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=40) as response:
                return json.load(response)
        try:
            return await asyncio.to_thread(send)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.load(exc)
            except (ValueError, OSError):
                detail = {}
            finally:
                exc.close()
            if isinstance(detail, dict) and 'copy' in detail:
                copy = detail['copy']
                if not isinstance(copy, dict) or not isinstance(copy.get('key'), str) or not copy['key'] or not isinstance(copy.get('params'), dict) or not isinstance(detail.get('code'), str) or not detail['code']:
                    raise HarnessError(tr('browser.desktop.invalidFailure'), code='browser/desktop-response') from exc
                raise HarnessError(tr(copy['key'], **copy['params']), code=detail['code'], copy=copy) from exc
            raise HarnessError(tr('python.catalog.validation.desktopDisconnected'), code='browser/desktop-disconnected') from exc
        except Exception as exc:
            raise HarnessError(tr('python.catalog.validation.desktopDisconnected'), code='browser/desktop-disconnected') from exc

    async def _ready_state(self, session_id):
        """只读状态在页面初始化边界有界重查，不重放任何输入。"""
        deadline = time.monotonic() + 5
        while True:
            try:
                return await self.request('state', session_id)
            except HarnessError as exc:
                if exc.code != 'browser/desktop-page' or time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(.025)

    async def resolve(self, wrapper, target_id):
        for page in wrapper.pages:
            if self._ids.get(page) == target_id:
                self._active[wrapper.session_id] = page
                return page
        for _ in range(50):
            for page in wrapper.context.pages:
                client = await wrapper.context.new_cdp_session(page)
                try:
                    target = await client.send('Target.getTargetInfo')
                finally:
                    await client.detach()
                if target['targetInfo']['targetId'] == target_id:
                    if page not in wrapper.owned:
                        wrapper.owned.append(page)
                        self._watch_page(page, wrapper.session_id)
                    self._ids[page] = target_id
                    if page not in self._dialog_clients:
                        watcher = await wrapper.context.new_cdp_session(page)
                        watcher.on('Page.javascriptDialogClosed', lambda _event: self._queue_dialog_sync(wrapper.session_id))
                        await watcher.send('Page.enable')
                        self._dialog_clients[page] = watcher
                    self._active[wrapper.session_id] = page
                    return page
            await asyncio.sleep(.02)
        raise HarnessError(tr('python.catalog.validation.desktopNotReady'), code='browser/desktop-page')

    def _sync_owner(self, session_id, result):
        """同步原生直接接管，使正在按住的输入及时松开。"""
        if self._closing or session_id not in self.contexts or session_id in self._closing_sessions:
            return
        owner = result.get('owner')
        if owner not in {'agent', 'user'} or owner == self._owners.get(session_id, 'agent'):
            return
        self._owners[session_id] = owner
        self._revisions[session_id] = self._revisions.get(session_id, 0) + 1
        self._pixel_targets.pop(session_id, None)
        self._retire_observations(self.contexts.get(session_id))
        active = self._busy.get(session_id)
        if owner == 'user' and active and active['actor'] == 'agent':
            active['takeover_requested'] = True

    async def _monitor_dialogs(self):
        # 只在操作或弹窗等待期间同步小型宿主状态，不读取网页或传输图像。
        while not self._closing:
            for sid in list(self.contexts):
                if sid in self._busy or sid in self._operations or self.dialogs.current(sid):
                    try:
                        result = await self.request('state', sid, measure=False)
                        self._sync_owner(sid, result)
                        self._sync_dialogs(sid, result)
                    except HarnessError:
                        pass
            await asyncio.sleep(.1)

    async def page(self, session_id):
        self.validate_session(session_id)
        async with self._setup_lock:
            if self.connection is None:
                endpoint = desktop_cdp_endpoint(os.environ['HARNESS_DESKTOP_CDP'])
                from playwright.async_api import async_playwright
                self.engine = await async_playwright().start()
                self.connection = await self.engine.chromium.connect_over_cdp(endpoint, no_defaults=True)
                self._dialog_monitor = asyncio.create_task(self._monitor_dialogs())
                self._notifications.add(self._dialog_monitor)
            if session_id not in self.contexts:
                self.contexts[session_id] = DesktopContext(self, session_id, self.connection.contexts[0])
        wrapper = self.contexts[session_id]
        result = await self._ready_state(session_id)
        if not result['tabs'] and not wrapper.pages:
            await wrapper.new_page()
            result = await self._ready_state(session_id)
        if self._owners.get(session_id, 'agent') != result['owner']:
            self._revisions[session_id] = self._revisions.get(session_id, 0) + 1
            await self._discard_observations(wrapper)
        self._owners[session_id] = result['owner']
        prior = self._selected(session_id)
        for tab in result['tabs']:
            await self.resolve(wrapper, tab['target_id'])
        self._sync_dialogs(session_id, result)
        selected = result.get('active_target')
        selected_tab = next((tab for tab in result['tabs'] if tab['target_id'] == selected), None)
        viewport = selected_tab.get('viewport') if selected_tab else None
        await self._sync_viewport(session_id, viewport, wrapper)

        if selected:
            return await self.resolve(wrapper, selected)
        if prior in wrapper.pages:
            self._active[session_id] = prior
            return prior
        return self._selected(session_id) or await wrapper.new_page()

    async def control(self, session_id, owner, expected_revision):
        result = await super().control(session_id, owner, expected_revision)
        await self.request('control', session_id, owner=owner)
        return result

    def _remove_observations(self, context):
        removed = []
        if context:
            for page in list(self._observations):
                if page in context.owned:
                    removed.append(self._observations.pop(page))
        return removed

    async def execute(self, session_id, args, *, actor='agent'):
        self.validate_session(session_id)
        action = args.get('action', 'observe')
        if action not in ACTIONS:
            raise HarnessError(tr('python.browser.0667d4840b3a'), code='browser/invalid')
        if action in {'navigate', 'new_tab'} and args.get('url') is not None:
            self._url(args['url'])
        if action == 'navigate':
            self._url(args.get('url'))
        if args.get('action') == 'press':
            self._validate_press(args)
        if args.get('action') == 'resize':
            raise HarnessError(tr('python.catalog.validation.desktopSize'), code='browser/desktop-managed-layout')
        writing = action not in READ_ACTIONS
        if actor == 'agent' and (writing or self._selected(session_id) is None):
            # 首次隐藏页面可能尚无视口，必须先让工作区显现再准备原生输入。
            await self._emit(session_id, action=action, actor=actor, status='running', reveal=True)
        await self.page(session_id)
        if writing:
            await self.request('begin', session_id, actor=actor)
        try:
            result = await super().execute(session_id, args, actor=actor)
        finally:
            if writing:
                await self.request('end', session_id)
        page = self._selected(session_id)
        if page:
            await self.request('select_target', session_id, target_id=self._id(page))
        return result

    async def close(self):
        """断开 Agent 的连接，保留桌面宿主拥有的网页和登录资料。"""
        self._closing = True
        tasks = list(self._operations.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.frames.close()
        for context in list(self.contexts.values()):
            await self._discard_observations(context)
            for page in context.owned:
                self._forget_diagnostics(page)
        await self.titles.close()
        if self.engine:
            await self.engine.stop()
            self.engine = None
        self.connection = None
        self._dialog_clients.clear()
        self.contexts.clear()
        self._active.clear()
        self._ids.clear()
        self._desktop_viewports.clear()
        self._pixel_targets.clear()
        for task in list(self._notifications):
            task.cancel()
        await asyncio.gather(*self._notifications, return_exceptions=True)
        self._notifications.clear()

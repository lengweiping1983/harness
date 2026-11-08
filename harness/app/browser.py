"""会话拥有的 Chromium，由代理与右侧实时查看器共享。

仅按需捕获画面，不写入对话或模型日志。
只读查看器不获取操作锁，耗时操作期间仍可显示画面。
"""
from __future__ import annotations

from harness.i18n import tr

import asyncio
import hashlib
import math
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.errors import HarnessError
from .browser_observation import BrowserObservation, stale
from .browser_diagnostics import PageDiagnostics
from .browser_dialogs import BrowserDialogs
from .browser_frames import BrowserFrames
from .browser_navigation import BrowserNavigation
from .browser_titles import BrowserTitles

ACTIONS = ('navigate', 'observe', 'options', 'screenshot', 'click', 'fill', 'select', 'check', 'press', 'scroll',
           'diagnostics', 'dialog', 'resize', 'move', 'stop_loading', 'type', 'back', 'forward', 'reload', 'tabs', 'new_tab', 'select_tab', 'close_tab')
READ_ACTIONS = frozenset({'observe', 'options', 'screenshot', 'tabs', 'diagnostics'})
TOOL_ACTIONS = tuple(action for action in ACTIONS if action not in {'resize', 'move', 'stop_loading'})
VIEWPORT = {'width': 1280, 'height': 800}


class BrowserService:
    def __init__(self, root: Path, *, headless: bool = True, bus=None) -> None:
        self.root, self.headless, self.bus = root, headless, bus
        self.contexts: dict[str, Any] = {}
        self.engine: Any = None
        self._closing = False
        self._setup_lock = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}
        self._control_locks: dict[str, asyncio.Lock] = {}
        self._closing_sessions: set[str] = set()
        self._session_closures: dict[str, asyncio.Task] = {}
        self._observation_cleanup: dict[int, tuple[Any, Any, asyncio.Task]] = {}
        self._close_task: asyncio.Task | None = None
        self._closed_contexts: set[int] = set()
        self._frame_locks: dict[str, asyncio.Lock] = {}
        self._active: dict[str, Any] = {}
        self._ids: dict[Any, str] = {}
        self._observations: dict[Any, BrowserObservation] = {}
        self._diagnostics: dict[Any, PageDiagnostics] = {}
        self._diagnostic_listeners: dict[Any, list[tuple[str, Any]]] = {}
        self._owners: dict[str, str] = {}
        self._revisions: dict[str, int] = {}
        self._busy: dict[str, dict] = {}
        self._history: dict[str, deque] = {}
        self.dialogs = BrowserDialogs()
        self._operations: dict[str, asyncio.Task] = {}
        self._reply_origins: dict[str, dict] = {}
        self._notifications: set[asyncio.Task] = set()
        self.frames = BrowserFrames()
        self.navigation = BrowserNavigation()
        self.titles = BrowserTitles()
        self._viewports: dict[str, dict] = {}
        self._pixel_targets: dict[str, tuple] = {}

    @staticmethod
    def validate_session(session_id: str) -> None:
        if not session_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in session_id):
            raise HarnessError(tr('python.browser.dc259251f011'), code='browser/session')

    def _lock(self, session_id):
        self.validate_session(session_id)
        return self._locks.setdefault(session_id, asyncio.Lock())

    def _id(self, page):
        return self._ids.setdefault(page, uuid.uuid4().hex)

    def _selected(self, session_id):
        context = self.contexts.get(session_id)
        pages = [p for p in context.pages if not p.is_closed()] if context else []
        selected = self._active.get(session_id)
        if selected not in pages:
            selected = pages[-1] if pages else None
            if selected is not None:
                self._active[session_id] = selected
            else:
                self._active.pop(session_id, None)
        return selected

    def _viewport(self, session_id):
        page = self._selected(session_id)
        return getattr(page, 'viewport_size', None) or self._viewports.get(session_id, VIEWPORT)

    def _watch_page(self, page, session_id=None):
        if page in self._diagnostic_listeners:
            return
        diagnostics = self._diagnostics.setdefault(page, PageDiagnostics())
        def console(message):
            if message.type not in {'warning', 'error'}:
                return
            location = message.location or {}
            line = location.get('lineNumber')
            diagnostics.add('console', message.text, level=message.type, url=location.get('url', ''),
                            line=line + 1 if type(line) is int and line >= 0 else None)
        def response(value):
            if value.status >= 400:
                diagnostics.add('http', f'HTTP {value.status}', url=value.url, status=value.status,
                                resource_type=value.request.resource_type)
        def failed(request):
            self.navigation.failed(page, request)
            diagnostics.add('request', request.failure or tr('feedback.cfce761befa8'), url=request.url, resource_type=request.resource_type)
        def committed(frame):
            if self.navigation.committed(page, frame) and session_id and self._selected(session_id) is page:
                # 新文档（包括重新加载）会使旧像素坐标租约失效。
                self._revisions[session_id] = self._revisions.get(session_id, 0) + 1
        def dialog(native):
            sid = session_id or next((sid for sid, context in self.contexts.items() if context is page.context), None)
            if sid is None:
                return
            entry = self._busy.get(sid) or self._reply_origins.get(sid)
            self.dialogs.opened(sid, page, self._id(page), native, entry)
            if entry:
                entry['status'] = 'waiting_dialog'
            task = asyncio.create_task(self._emit(sid, action='dialog', actor=self._owners.get(sid, 'agent'), status='waiting_dialog'))
            self._notifications.add(task)
            def delivered(task):
                self._notifications.discard(task)
                if not task.cancelled():
                    task.exception()
            task.add_done_callback(delivered)
        listeners = [('dialog', dialog), ('console', console), ('pageerror', lambda error: diagnostics.add('javascript', str(error))),
                     ('response', response), ('requestfailed', failed),
                     ('request', lambda request: self.navigation.requested(page, request)), ('framenavigated', committed),
                     ('domcontentloaded', lambda: self.navigation.ready(page, 'interactive')),
                     ('load', lambda: self.navigation.ready(page, 'complete'))]
        for event, listener in listeners:
            page.on(event, listener)
        self._diagnostic_listeners[page] = listeners

    def _forget_diagnostics(self, page):
        self.titles.forget(page)
        for event, listener in self._diagnostic_listeners.pop(page, []):
            page.remove_listener(event, listener)
        self._diagnostics.pop(page, None)
        self.navigation.forget(page)
        self.dialogs.forget(page)

    async def diagnostics(self, session_id: str, tab_id: str | None = None, *, limit: int = 100, expected_revision: int | None = None):
        self.validate_session(session_id)
        page = self._selected(session_id)
        if tab_id is not None:
            context = self.contexts.get(session_id)
            page = next((p for p in context.pages if not p.is_closed() and self._id(p) == tab_id), None) if context else None
            if page is None:
                raise HarnessError(tr('python.browser.f17e5b7ab00d'), code='browser/tab-not-found')
        if page is None:
            return {'page_id': None, **PageDiagnostics().view(limit)}
        store = self._diagnostics.setdefault(page, PageDiagnostics())
        if expected_revision is not None:
            store.clear(expected_revision)
        return {'page_id': self._id(page), **store.view(limit)}

    async def page(self, session_id: str):
        """普通服务不启动独立浏览器，页面由桌面子类提供。"""
        self.validate_session(session_id)
        raise HarnessError(tr('browser.desktop.required'), code='browser/desktop-required')

    async def state(self, session_id: str, *, refresh_titles: bool = False) -> dict:
        """不启动 Chromium，装饰标题后台刷新，控制状态即时返回。"""
        self.validate_session(session_id)
        for closed in [p for p in self._ids if p.is_closed()]:
            await self.frames.forget(closed)
            self._ids.pop(closed, None)
            self._forget_diagnostics(closed)
            observation = self._observations.pop(closed, None)
            if observation:
                await observation.dispose()
        page = self._selected(session_id)
        context = self.contexts.get(session_id)
        def tab_view(p, title):
            if p.is_closed():
                return None
            return {'id': self._id(p), 'url': p.url, 'title': title or (tr('python.browser.9c5d341609cd') if p.url == 'about:blank' else p.url)}

        async def refreshed_tab(p):
            if p.is_closed():
                return None
            revision = self.navigation.documents.get(p, 0)
            title = await self.titles.read(p, revision, blocked=p in self.dialogs.pending)
            return tab_view(p, title)

        def cached_tab(p):
            if p.is_closed():
                return None
            revision = self.navigation.documents.get(p, 0)
            title = self.titles.snapshot(p, revision, blocked=p in self.dialogs.pending).text
            return tab_view(p, title)

        pages = list(context.pages) if context else []
        # 控制权交接和输入确认只读取缓存，不为装饰标题创建等待任务。
        views = await asyncio.gather(*(refreshed_tab(p) for p in pages)) if refresh_titles else [cached_tab(p) for p in pages]
        tabs = [tab for tab in views if tab]
        page = self._selected(session_id)
        dialog = self.dialogs.current(session_id, page)
        return {'dialog': dialog.view() if dialog else None, 'operation_pending': session_id in self._operations, 'started': context is not None, 'owner': self._owners.get(session_id, 'agent'),
                'viewer': {'fast_frames': True, 'resize': True, 'hover': True, 'navigation': True, 'takeover': True},
                'navigation': self.navigation.view(page),
                'control_revision': self._revisions.get(session_id, 0),
                'active_tab': self._id(page) if page else None, 'tabs': tabs,
                'viewport': dict(self._viewport(session_id)), 'activity': self._busy.get(session_id),
                'health': self._diagnostics[page].summary() if page in self._diagnostics else PageDiagnostics().summary(),
                'history': list(self._history.get(session_id, ())) }

    def _remove_observations(self, context):
        removed = []
        if context is None:
            return removed
        for page, observation in list(self._observations.items()):
            if page.context is context:
                self._observations.pop(page, None)
                removed.append(observation)
        return removed

    def _dispose_observation(self, context, observation):
        """一次释放由所属任务拥有，失败句柄保留到后续显式收尾。"""
        identity = id(observation)
        current = self._observation_cleanup.get(identity)
        if current is not None and not current[2].done():
            return current[2]
        task = asyncio.create_task(observation.dispose())
        self._observation_cleanup[identity] = (context, observation, task)
        def completed(done):
            if not done.cancelled() and done.exception() is None:
                if self._observation_cleanup.get(identity, (None, None, None))[2] is done:
                    self._observation_cleanup.pop(identity, None)
        task.add_done_callback(completed)
        return task

    async def _discard_observations(self, context):
        observations = {id(value): value for value in self._remove_observations(context)}
        observations.update({identity: value for identity, (owner, value, _) in self._observation_cleanup.items() if owner is context})
        errors = []
        for observation in observations.values():
            try:
                await join_cleanup(self._dispose_observation(context, observation))
            except BaseException as error:
                errors.append(error)
        raise_cleanup_errors(errors)

    def _retire_observations(self, context):
        # 同步撤销引用，不阻塞控制权接管；失败释放仍保留所属句柄。
        for observation in self._remove_observations(context):
            task = self._dispose_observation(context, observation)
            self._notifications.add(task)
            task.add_done_callback(self._notifications.discard)

    @staticmethod
    def progress_key(args, result):
        # 快照引用、操作标识和时间、租约及产物路径只是附带信息。
        # 这些信息不能使未变化的观察结果被误判为有效进展。
        observed = {key: result.get(key) for key in ('url', 'title', 'text', 'owner', 'active_tab', 'tabs', 'image_digest', 'scroll') if key in result}
        if 'records' in result:
            observed['diagnostics'] = [{key: value for key, value in record.items() if key not in {'id', 'timestamp'}} for record in result['records']]
        if 'targets' in result:
            observed['targets'] = [{key: value for key, value in target.items() if key != 'ref'} for target in result['targets']]
        if result.get('dialog'):
            observed['dialog'] = {key: value for key, value in result['dialog'].items() if key in {'type', 'message', 'url', 'response_unconfirmed'}}
        inputs = {key: value for key, value in args.items() if key not in {'ref', 'page_id', 'control_revision', 'dialog_id', 'screenshot_id'}}
        if args.get('action') == 'select' and isinstance(inputs.get('values'), list) and all(isinstance(value, str) for value in inputs['values']):
            inputs['values'] = sorted(inputs['values'])
        if 'operation_target' in result:
            inputs['target'] = result['operation_target']
        return ['browser', inputs, observed]

    async def _emit(self, session_id: str, **data):
        if self.bus:
            await self.bus.emit('browser/activity', {'session_id': session_id, **data})

    async def control(self, session_id: str, owner: str, expected_revision: int) -> dict:
        self.validate_session(session_id)
        if owner not in {'user', 'agent'} or type(expected_revision) is not int:
            raise HarnessError(tr('python.browser.152aea77bb2b'), code='browser/control-invalid')
        async with self._control_locks.setdefault(session_id, asyncio.Lock()):
            if self._closing or session_id in self._closing_sessions:
                raise HarnessError(tr('python.browser.b9183291b02c'), code='browser/closing')
            revision = self._revisions.get(session_id, 0)
            if revision != expected_revision:
                raise HarnessError(tr('python.browser.00391712619c'), code='browser/control-changed')
            if self._owners.get(session_id, 'agent') != owner:
                if owner == 'agent' and session_id in self._operations and not self.dialogs.current(session_id):
                    raise HarnessError(tr('python.browser.9cf49331606e'), code='browser/action-pending')
                context = self.contexts.get(session_id)
                self._owners[session_id] = owner
                self._revisions[session_id] = revision + 1
                self._retire_observations(context)
                active = self._busy.get(session_id)
                if owner == 'user' and active and active['actor'] == 'agent':
                    active['takeover_requested'] = True
                await self._emit(session_id, owner=owner, status='control')
            return await self.state(session_id)

    async def frame(self, session_id: str, page_id: str, *, expected_revision: int | None = None) -> bytes:
        self.validate_session(session_id)
        async with self._frame_locks.setdefault(session_id, asyncio.Lock()):
            page = self._selected(session_id)
            if self.dialogs.current(session_id):
                raise HarnessError(tr('python.browser.2aeb61e94843'), code='browser/dialog-pending')
            if page is None or self._id(page) != page_id:
                raise HarnessError(tr('python.browser.bfa676e81b5d'), code='browser/frame-changed')
            revision = self._revisions.get(session_id, 0)
            if expected_revision is not None and expected_revision != revision:
                raise HarnessError(tr('python.browser.36901662a1a3'), code='browser/frame-changed')
            viewport = dict(self._viewport(session_id))
            document_revision = self.navigation.documents.get(page, 0)
            try:
                try:
                    image = await self.frames.get(page, viewport, document_revision=document_revision)
                except Exception:
                    await self.frames.forget(page)
                    image = None
                if image is None:
                    image = await page.screenshot(type='jpeg', quality=70, timeout=2500)
            except Exception as exc:
                raise HarnessError(tr('python.browser.0c1e21914a4c'), code='browser/frame-unavailable') from exc
            if (self._selected(session_id) is not page or self._revisions.get(session_id, 0) != revision
                    or self._viewport(session_id) != viewport or self.navigation.documents.get(page, 0) != document_revision):
                raise HarnessError(tr('python.browser.9514d0f07090'), code='browser/frame-changed')
            return image

    @staticmethod
    def _url(value):
        if not isinstance(value, str) or len(value) > 8192:
            raise HarnessError(tr('python.browser.ebc3a7422451'), code='browser/url')
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise HarnessError(tr('python.browser.96d95b4132b2'), code='browser/url') from exc
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise HarnessError(tr('python.browser.d5676022e428'), code='browser/url')
        return value

    @staticmethod
    def _number(args, key, limit):
        value = args.get(key)
        if type(value) not in {int, float}:
            raise HarnessError(tr('python.browser.ad4ca5e91ba8'), code='browser/input')
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite or abs(value) > limit:
            raise HarnessError(tr('python.browser.ad4ca5e91ba8'), code='browser/input')
        return value

    async def execute(self, session_id: str, args: dict[str, Any], *, actor: str = 'agent') -> dict[str, Any]:
        if args.get('action') == 'stop_loading':
            return await self._stop_loading(session_id, args, actor)
        async with self._lock(session_id):
            action = args.get('action', 'observe')
            if action not in ACTIONS:
                raise HarnessError(tr('python.browser.0667d4840b3a'), code='browser/invalid')
            owner = self._owners.get(session_id, 'agent')
            if action not in READ_ACTIONS:
                if actor != owner:
                    raise HarnessError(tr('python.browser.e93d360284f9') if owner == 'user' else tr('python.browser.baa48c8371c8'), code='browser/not-owner')
                if actor == 'user':
                    if type(args.get('control_revision')) is not int or args['control_revision'] != self._revisions.get(session_id, 0):
                        raise HarnessError(tr('python.browser.5ccbf20376db'), code='browser/control-changed')
                    selected = self._selected(session_id)
                    if action != 'dialog' and selected and args.get('page_id') != self._id(selected):
                        raise HarnessError(tr('python.browser.9280520caa52'), code='browser/page-changed')
            if action == 'dialog':
                entry = self.dialogs.validate(session_id, args)
                if actor == 'user' and args.get('page_id') != entry.page_id:
                    raise HarnessError(tr('python.browser.e0896f1c96e1'), code='browser/page-changed')
                return await self._respond_dialog(session_id, args, entry, actor)
            if self.dialogs.current(session_id):
                return await self._modal_state(session_id)
            if session_id in self._operations:
                if action in READ_ACTIONS:
                    return {**await self.state(session_id), 'observation_status': 'action_pending', 'page_content_trusted': False}
                raise HarnessError(tr('python.browser.90982067344b'), code='browser/action-pending')
            # 启动 Chromium 前先校验；无效调用不能打开浏览器。
            if action in {'navigate', 'new_tab'} and args.get('url') is not None:
                self._url(args['url'])
            if action == 'navigate':
                self._url(args.get('url'))
            if action == 'resize':
                if actor != 'user':
                    raise HarnessError(tr('python.browser.fae81461efbd'), code='browser/user-only')
                if any(type(args.get(key)) is not int or not low <= args[key] <= high for key, low, high in [('width', 320, 1920), ('height', 240, 1200)]):
                    raise HarnessError(tr('python.browser.17b415718f92'), code='browser/viewport-invalid')
            if action == 'move':
                if actor != 'user':
                    raise HarnessError(tr('python.browser.f2dfbd466824'), code='browser/user-only')
                viewport = self._viewport(session_id)
                for key, dimension in [('x', 'width'), ('y', 'height')]:
                    if not 0 <= self._number(args, key, viewport[dimension]) < viewport[dimension]:
                        raise HarnessError(tr('python.browser.3dae4e8a6fd2'), code='browser/input')
            if args.get('ref') is not None:
                if action not in {'click', 'fill', 'select', 'check', 'options'} or args.get('selector'):
                    raise HarnessError(tr('python.browser.f758461452c5'), code='browser/target-invalid')
                selected = self._selected(session_id)
                observation = self._observations.get(selected)
                if observation is None:
                    raise stale()
                observation._index(args['ref'], self._id(selected))
            if action in {'select', 'check', 'options'}:
                if not args.get('ref'):
                    raise HarnessError(tr('python.browser.5683faaae1ae'), code='browser/target-invalid')
                if action == 'options' and (type(args.get('offset', 0)) is not int or not 0 <= args.get('offset', 0) <= 1000000 or not isinstance(args.get('query', ''), str) or len(args.get('query', '')) > 240):
                    raise HarnessError(tr('python.browser.968469c6a4b4'), code='browser/input')
                if action == 'check' and type(args.get('checked')) is not bool:
                    raise HarnessError(tr('python.browser.63836a5a66c4'), code='browser/input')
                if action == 'select':
                    values = args.get('values')
                    if (not isinstance(values, list) or len(values) > 20 or any(not isinstance(value, str) or len(value) > 1000 for value in values) or len(set(values)) != len(values)):
                        raise HarnessError(tr('python.browser.835ee469fea1'), code='browser/input')
            if action in {'click', 'fill'} and not args.get('selector') and not args.get('ref'):
                if action == 'fill':
                    raise HarnessError(tr('python.browser.834585eb7d1b'), code='browser/selector')
                viewport = self._viewport(session_id)
                x, y = self._number(args, 'x', viewport['width']), self._number(args, 'y', viewport['height'])
                if not 0 <= x < viewport['width'] or not 0 <= y < viewport['height']:
                    raise HarnessError(tr('python.browser.50614fcb1229'), code='browser/input')
            if action == 'screenshot' and type(args.get('full_page', True)) is not bool:
                raise HarnessError(tr('python.browser.50614fcb1229'), code='browser/input')
            if action == 'scroll':
                self._number(args, 'delta_x', 5000)
                self._number(args, 'delta_y', 5000)
            if action == 'press':
                self._validate_press(args)
            if action in {'fill', 'type'} and (not isinstance(args.get('text', ''), str) or len(args.get('text', '')) > 30000):
                raise HarnessError(tr('python.browser.d6a406dcccb1'), code='browser/input')
            entry = {'id': uuid.uuid4().hex, 'action': action, 'actor': actor, 'status': 'running', 'started_at': time.time()}
            self._busy[session_id] = entry
            if action != 'move':
                await self._emit(session_id, action=action, actor=actor, status='running')
            task = asyncio.create_task(self._operation(session_id, args, actor, entry))
            self._operations[session_id] = task
            # 模态窗口可能在工具调用结束后仍存在；需处理延迟任务的失败。
            task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
            try:
                return await self._await_operation(session_id, task)
            except asyncio.CancelledError as primary:
                try:
                    await self._cancel_operation(task)
                except asyncio.CancelledError:
                    raise primary
                except BaseException as cleanup:
                    raise_cleanup_errors([primary, cleanup])
                raise

    async def _cancel_operation(self, task):
        """操作只投递一次取消，重复取消观察者不能再打断其finally。"""
        if not task.done() and not task.cancelling():
            task.cancel()
        async def finish():
            try:
                await task
            except asyncio.CancelledError:
                pass
        await join_cleanup(asyncio.create_task(finish()))

    async def _modal_state(self, session_id):
        return {**await self.state(session_id), 'observation_status': 'dialog', 'page_content_trusted': False,
                'instruction': tr('python.browser.52684f6f1c20')}

    async def _await_operation(self, session_id, task):
        if self.dialogs.current(session_id):
            return await self._modal_state(session_id)
        waiter = asyncio.create_task(self.dialogs.event(session_id).wait())
        try:
            await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if self.dialogs.current(session_id):
                return await self._modal_state(session_id)
            return await asyncio.shield(task)
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    def _operation_receipt(self, session_id, result, entry):
        result = {**result, 'operation_pending': False}
        if entry.get('navigation_cancelled'):
            result['navigation_cancelled'] = True
        if entry.get('takeover_requested'):
            result.update(control_taken_over=True, owner=self._owners.get(session_id, 'agent'),
                          control_revision=self._revisions.get(session_id, 0),
                          instruction=(tr('python.browser.7a15f0b85e6e')
                            if entry.get('navigation_cancelled') else
                            tr('python.browser.9cfb35a1b205')))
        return result

    async def _operation(self, session_id, args, actor, entry):
        action = args.get('action', 'observe')
        try:
            async with asyncio.timeout(40):
                result = await self._execute(session_id, args, actor=actor)
            entry['status'] = 'waiting_dialog' if result.get('dialog') else 'cancelled' if entry.get('navigation_cancelled') else 'completed'
            return self._operation_receipt(session_id, result, entry)
        except asyncio.CancelledError:
            entry['status'] = 'cancelled'
            raise
        except HarnessError as exc:
            if self.dialogs.current(session_id):
                entry.update(status='unconfirmed', error=tr('python.browser.9f4ede2d007d'))
            else:
                entry.update(status='failed', error=exc.message)
            raise
        except Exception as exc:
            if entry.get('navigation_cancelled') and 'ERR_ABORTED' in str(exc):
                entry['status'] = 'cancelled'
                return self._operation_receipt(session_id, await self.state(session_id), entry)
            entry.update(status='unconfirmed' if self.dialogs.current(session_id) else 'failed', error=tr('python.browser.e46e509b0f4d'))
            raise HarnessError(entry['error'], code='browser/action-unconfirmed') from exc
        finally:
            entry['ended_at'] = time.time()
            if self._busy.get(session_id) is entry:
                self._busy.pop(session_id, None)
            if self._operations.get(session_id) is asyncio.current_task():
                self._operations.pop(session_id, None)
            if action != 'move' or entry['status'] != 'completed':
                self._history.setdefault(session_id, deque(maxlen=24)).appendleft(entry)
                await self._emit(session_id, action=action, actor=actor, status=entry['status'])

    async def _respond_dialog(self, session_id, args, dialog, actor):
        entry = {'id': uuid.uuid4().hex, 'action': 'dialog', 'actor': actor, 'status': 'running', 'started_at': time.time()}
        origin = dialog.operation
        if origin:
            self._reply_origins[session_id] = origin
        try:
            await self._emit(session_id, action='dialog', actor=actor, status='running')
            await self._discard_observations(self.contexts.get(session_id))
            response = await self.dialogs.respond(dialog, args)
            if origin and origin['status'] == 'waiting_dialog' and not self.dialogs.current(session_id):
                origin['status'] = 'running' if session_id in self._operations else 'completed'
            task = self._operations.get(session_id)
            if self.dialogs.current(session_id):
                result = await self._modal_state(session_id)
            elif task:
                try:
                    result = await self._await_operation(session_id, task)
                except HarnessError as error:
                    # 只读核对当前页面，同时保留原操作失败证据，不重放点击。
                    result = {**await self._execute(session_id, {'action': 'observe'}, actor=actor),
                              'origin_operation_error': error.to_dict()}
            else:
                result = await self._execute(session_id, {'action': 'observe'}, actor=actor)
            if not self.dialogs.current(session_id) and (result.get('dialog') or 'targets' not in result):
                result = {**result, **await self._execute(session_id, {'action': 'observe'}, actor=actor)}
            entry['status'] = 'completed'
            return {**result, 'dialog_response': response,
                    **({'navigation_cancelled': True} if dialog.kind == 'beforeunload' and not args['accept'] else {})}
        except asyncio.CancelledError:
            entry['status'] = 'cancelled'
            raise
        except Exception:
            entry['status'] = 'unconfirmed'
            raise
        finally:
            self._reply_origins.pop(session_id, None)
            entry['ended_at'] = time.time()
            self._history.setdefault(session_id, deque(maxlen=24)).appendleft(entry)
            await self._emit(session_id, action='dialog', actor=actor, status=entry['status'])

    async def _stop_loading(self, session_id, args, actor):
        """原生停止操作可中断持有操作锁的用户导航。"""
        self.validate_session(session_id)
        if actor != 'user':
            raise HarnessError(tr('python.browser.fe6e1f522a58'), code='browser/user-only')
        def check():
            if self._owners.get(session_id, 'agent') != 'user':
                raise HarnessError(tr('python.browser.baa48c8371c8'), code='browser/not-owner')
            if type(args.get('control_revision')) is not int or args['control_revision'] != self._revisions.get(session_id, 0):
                raise HarnessError(tr('python.browser.5ccbf20376db'), code='browser/control-changed')
            page = self._selected(session_id)
            if page is None or args.get('page_id') != self._id(page):
                raise HarnessError(tr('python.browser.9280520caa52'), code='browser/page-changed')
            return page
        page = check()
        if self.dialogs.current(session_id):
            return await self._modal_state(session_id)
        def navigation_operation():
            active = self._busy.get(session_id)
            if session_id in self._operations and (not active or active['action'] not in {'navigate', 'new_tab', 'back', 'forward', 'reload'}):
                raise HarnessError(tr('python.browser.0b304c9fe892'), code='browser/action-pending')
            return active
        active = navigation_operation()
        client = None
        entry = {'id': uuid.uuid4().hex, 'action': 'stop_loading', 'actor': actor, 'status': 'running', 'started_at': time.time()}
        try:
            async with asyncio.timeout(2):
                client = await page.context.new_cdp_session(page)
                if check() is not page:
                    raise HarnessError(tr('python.browser.9514d0f07090'), code='browser/page-changed')
                if self.dialogs.current(session_id):
                    entry['status'] = 'waiting_dialog'
                    return await self._modal_state(session_id)
                active = navigation_operation()
                if active:
                    active['navigation_cancelled'] = True
                await client.send('Page.stopLoading')
                self.navigation.phases[page] = 'stopped'
            entry['status'] = 'completed'
        except asyncio.CancelledError:
            if active:
                active.pop('navigation_cancelled', None)
            entry['status'] = 'unconfirmed'
            raise
        except HarnessError:
            entry['status'] = 'failed'
            raise
        except Exception as exc:
            if active:
                active.pop('navigation_cancelled', None)
            entry['status'] = 'unconfirmed'
            raise HarnessError(tr('python.browser.6cfd061d463b'), code='browser/stop-unconfirmed') from exc
        finally:
            primary = sys.exception()
            try:
                await join_cleanup(asyncio.create_task(self._finish_stop_loading(session_id, actor, entry, client)))
            except BaseException as cleanup:
                if primary is not None and not isinstance(cleanup, asyncio.CancelledError):
                    raise_cleanup_errors([primary, cleanup])
                raise
        return await self.state(session_id)

    async def _finish_stop_loading(self, session_id, actor, entry, client):
        """停止请求的连接释放和历史收尾共享一次所属等待。"""
        errors = []
        if client:
            try:
                await asyncio.wait_for(client.detach(), 1)
            except BaseException as error:
                errors.append(error)
                entry['status'] = 'unconfirmed'
        entry['ended_at'] = time.time()
        self._history.setdefault(session_id, deque(maxlen=24)).appendleft(entry)
        try:
            await self._emit(session_id, action='stop_loading', actor=actor, status=entry['status'])
        except BaseException as error:
            errors.append(error)
        raise_cleanup_errors(errors)

    @classmethod
    def _validate_press(cls, args):
        """无需创建页面即可拒绝无效按键与时长。"""
        if not isinstance(args.get('key'), str) or not 0 < len(args['key']) <= 80:
            raise HarnessError(tr('python.browser.6bdd4f359998'), code='browser/input')
        if 'duration_ms' in args:
            if type(args['duration_ms']) is not int or not 1 <= args['duration_ms'] <= 2000:
                raise HarnessError(tr('python.browser.50614fcb1229'), code='browser/input')
            cls._held_keys(args['key'])

    @staticmethod
    def _held_keys(key):
        """保留末尾加号键，只允许有限且不重复的组合修饰键。"""
        tokens, current = [], ''
        for char in key:
            if char == '+' and current:
                tokens.append(current)
                current = ''
            else:
                current += char
        tokens.append(current)
        modifiers = {'Shift', 'Control', 'Alt', 'Meta', 'ControlOrMeta', 'ShiftLeft', 'ShiftRight',
                     'ControlLeft', 'ControlRight', 'AltLeft', 'AltRight', 'MetaLeft', 'MetaRight'}
        if (not current or len(tokens) > 5 or len(set(tokens[:-1])) != len(tokens[:-1]) or
                any(token not in modifiers for token in tokens[:-1])):
            raise HarnessError(tr('python.browser.50614fcb1229'), code='browser/input')
        return tokens

    async def _held_press(self, session_id, page, args, actor):
        """按住期间检查接管；取消、失败和正常结束都尝试释放全部按键。"""
        pressed = []
        try:
            for key in self._held_keys(args['key']):
                if self._owners.get(session_id, 'agent') != actor or self._selected(session_id) is not page:
                    return
                pressed.append(key)
                await page.keyboard.down(key)
            deadline = time.monotonic() + args['duration_ms'] / 1000
            while (self._owners.get(session_id, 'agent') == actor and self._selected(session_id) is page):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(.025, remaining))
        finally:
            failures = []
            for key in reversed(pressed):
                try:
                    await asyncio.wait_for(page.keyboard.up(key), 2)
                except Exception as exc:
                    failures.append(exc)
            if failures:
                raise HarnessError(tr('python.browser.inputReleaseUnconfirmed'), code='browser/input-release-unconfirmed') from failures[0]

    async def _pixel_identity(self, session_id, page, token):
        """坐标使用 CSS 像素；页面自行滚动也会使旧截图失效。"""
        scroll = await page.evaluate('({x: scrollX, y: scrollY, width: innerWidth, height: innerHeight})')
        viewport = dict(self._viewport(session_id))
        if (self._selected(session_id) is not page or
                any(scroll[key] != viewport[key] for key in ('width', 'height'))):
            raise HarnessError(tr('python.browser.9514d0f07090'), code='browser/frame-changed')
        return (token, page, self._revisions.get(session_id, 0),
                viewport, self.navigation.documents.get(page, 0), scroll)

    async def _execute(self, session_id, args, *, actor):
        action = args.get('action', 'observe')
        operation_target = None
        if self.dialogs.current(session_id):
            return await self._modal_state(session_id)
        if action == 'diagnostics':
            return await self.diagnostics(session_id, args.get('tab_id'), limit=args.get('limit', 20))
        if action == 'options':
            page = self._selected(session_id)
            snapshot = self._observations[page]
            index = snapshot._index(args['ref'], self._id(page))
            targets = await snapshot.options(self._id(page), args['ref'], offset=args.get('offset', 0), query=args.get('query', ''))
            return {**await self.state(session_id), 'targets': targets, 'targets_truncated': False, 'page_content_trusted': False,
                    'page_id': self._id(page), 'options_view': True, 'operation_target': {'index': index, 'role': targets[0]['role'], 'name': targets[0]['name']}}
        previous_page = self._selected(session_id)
        page = await self.page(session_id)
        context = self.contexts[session_id]
        if action == 'click' and not args.get('ref') and not args.get('selector') and actor == 'agent':
            target = self._pixel_targets.get(session_id)
            current = await self._pixel_identity(session_id, page, args.get('screenshot_id'))
            if target is None or target != current:
                raise HarnessError(tr('python.browser.9514d0f07090'), code='browser/frame-changed')
        if action not in READ_ACTIONS:
            # 任意输入都会使截图坐标失效，失败后也不能重放旧点击。
            self._pixel_targets.pop(session_id, None)
        if action == 'new_tab':
            # 新启动的上下文已经包含首个空白页面。
            if previous_page is not None or len(context.pages) != 1 or page.url != 'about:blank':
                if len(context.pages) >= 12:
                    raise HarnessError(tr('python.browser.3cdb617e0ab9'), code='browser/tab-limit')
                page = await context.new_page()
            self._active[session_id] = page
            if session_id in self._viewports:
                await page.set_viewport_size(self._viewports[session_id])
            if args.get('url'):
                await page.goto(args['url'], wait_until='commit' if actor == 'user' else 'domcontentloaded', timeout=30000)
        elif action in {'select_tab', 'close_tab'}:
            target = next((p for p in context.pages if self._id(p) == args.get('tab_id')), None)
            if target is None:
                raise HarnessError(tr('python.browser.37b3b45ee6e2'), code='browser/tab-not-found')
            if action == 'close_tab':
                await self.frames.forget(target)
                await target.close()
                observation = self._observations.pop(target, None)
                if observation:
                    await observation.dispose()
                self._ids.pop(target, None)
                self._forget_diagnostics(target)
                page = self._selected(session_id)
            else:
                self._active[session_id] = target
                page = target
                await target.bring_to_front()
        elif action == 'navigate':
            await page.goto(args['url'], wait_until='commit' if actor == 'user' else 'domcontentloaded', timeout=30000)
        elif action == 'resize':
            viewport = {'width': args['width'], 'height': args['height']}
            await self._discard_observations(context)
            # 使所有旧坐标租约失效，包括操作部分失败的情况。
            self._viewports[session_id] = viewport
            self._revisions[session_id] = self._revisions.get(session_id, 0) + 1
            for target in context.pages:
                await self.frames.forget(target)
                await target.set_viewport_size(viewport)
        elif action in {'click', 'fill', 'select', 'check'} and args.get('ref'):
            observation = self._observations.get(page)
            if observation is None:
                raise stale()
            index = observation._index(args['ref'], self._id(page))
            operation_target = {'index': index, **{key: value for key, value in observation._fingerprint(observation.descriptors[index]).items() if key != 'options'}}
            if action == 'select':
                operation_target['choices'] = [{key: option[key] for key in ('value', 'label')} for option in observation.descriptors[index].get('options', []) if option['value'] in args['values']]
            if 'frame_path' in observation.descriptors[index]:
                operation_target['frame_path'] = observation.descriptors[index]['frame_path']
            await observation.act(self._id(page), args['ref'], action, args.get('text', ''), values=args.get('values'), checked=args.get('checked'))
        elif action == 'click':
            if args.get('selector'):
                await page.locator(str(args['selector'])).click(timeout=15000)
            else:
                await page.mouse.click(args['x'], args['y'])
        elif action == 'fill':
            await page.locator(str(args['selector'])).fill(args.get('text', ''), timeout=15000)
        elif action == 'type':
            await page.keyboard.insert_text(args.get('text', ''))
        elif action == 'move':
            await page.mouse.move(args['x'], args['y'])
        elif action == 'press':
            if 'duration_ms' in args:
                await self._held_press(session_id, page, args, actor)
            else:
                await page.keyboard.press(args['key'])
        elif action == 'scroll':
            await page.mouse.wheel(args['delta_x'], args['delta_y'])
        elif action in {'back', 'forward', 'reload'}:
            await getattr(page, {'back': 'go_back', 'forward': 'go_forward', 'reload': 'reload'}[action])(wait_until='commit' if actor == 'user' else 'domcontentloaded', timeout=30000)
        elif action == 'screenshot':
            folder = self.root / 'artifacts' / session_id
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / (uuid.uuid4().hex + '.png')
            self._pixel_targets.pop(session_id, None)
            full_page = args.get('full_page', True)
            target = await self._pixel_identity(session_id, page, path.stem)
            data = await page.screenshot(path=str(path), full_page=full_page,
                                         scale='device' if full_page else 'css', timeout=10000)
            current = await self._pixel_identity(session_id, page, path.stem)
            if current != target:
                raise HarnessError(tr('python.browser.9514d0f07090'), code='browser/frame-changed')
            if not full_page:
                self._pixel_targets[session_id] = target
            return {'url': page.url, 'path': str(path.resolve()), 'page_id': self._id(page),
                    'viewport': target[3], 'control_revision': target[2], 'full_page': full_page,
                    'screenshot_id': path.stem if not full_page else None,
                    'image_digest': hashlib.sha256(data).hexdigest()}
        if self.dialogs.current(session_id):
            return await self._modal_state(session_id)
        revision = self._revisions.get(session_id, 0)
        document = self.navigation.documents.get(page, 0)
        state = await self.state(session_id, refresh_titles=action == 'tabs')
        if operation_target is not None:
            state['operation_target'] = operation_target
        if (actor == 'user' and action not in READ_ACTIONS and action not in {'select', 'check'}) or action in {'tabs', 'select_tab', 'close_tab'} or page is None:
            return state
        old = self._observations.get(page)
        observation = await BrowserObservation.capture(page, self._id(page), scopes=old if action in {'select', 'check'} else None)
        try:
            title = await page.title()
            if old:
                await join_cleanup(self._dispose_observation(context, old))
        except BaseException as primary:
            try:
                await join_cleanup(self._dispose_observation(context, observation))
            except BaseException as cleanup:
                raise_cleanup_errors([primary, cleanup])
            raise
        if (self._closing or session_id in self._closing_sessions or self._selected(session_id) is not page
                or self._revisions.get(session_id, 0) != revision
                or self.navigation.documents.get(page, 0) != document):
            await join_cleanup(self._dispose_observation(context, observation))
            raise HarnessError(tr('python.browser.9514d0f07090'), code='browser/frame-changed')
        self._observations[page] = observation
        return {**state, 'targets': observation.targets(), 'targets_truncated': observation.truncated,
                'page_content_trusted': False, 'scroll': observation.scroll, 'page_id': self._id(page), 'url': page.url, 'title': title,
                'text': observation.text, 'observation_warnings': observation.warnings}

    async def close_session(self, session_id: str, *, expected_revision: int | None = None):
        self.validate_session(session_id)
        if expected_revision is not None and (self._owners.get(session_id, 'agent') != 'user' or expected_revision != self._revisions.get(session_id, 0)):
            raise HarnessError(tr('python.browser.27bed758182c'), code='browser/control-changed')
        task = self._session_closures.get(session_id)
        if task is None:
            task = asyncio.create_task(self._close_session(session_id, expected_revision))
            self._session_closures[session_id] = task
        try:
            await join_cleanup(task)
        finally:
            if task.done() and self._session_closures.get(session_id) is task:
                self._session_closures.pop(session_id, None)

    async def _close_session(self, session_id, expected_revision):
        async with self._lock(session_id):
            if expected_revision is not None and (self._owners.get(session_id, 'agent') != 'user' or expected_revision != self._revisions.get(session_id, 0)):
                raise HarnessError(tr('python.browser.27bed758182c'), code='browser/control-changed')
            self._closing_sessions.add(session_id)
            errors = []
            async def attempt(operation):
                try:
                    await operation()
                except BaseException as error:
                    errors.append(error)
            task = self._operations.get(session_id)
            if task:
                await attempt(lambda: self._cancel_operation(task))
            context = self.contexts.get(session_id)
            if context:
                pages = list(set(context.pages) | {p for p in self._diagnostics if p.context is context})
                if id(context) not in self._closed_contexts:
                    try:
                        await context.close()
                    except BaseException as error:
                        errors.append(error)
                    else:
                        self._closed_contexts.add(id(context))
                await attempt(lambda: self._discard_observations(context))
                for page in pages:
                    await attempt(lambda page=page: self.frames.forget(page))
                    observation = self._observations.get(page)
                    if observation:
                        await attempt(lambda observation=observation: join_cleanup(self._dispose_observation(context, observation)))
                    self._ids.pop(page, None)
                    self._forget_diagnostics(page)
                if not errors:
                    self.contexts.pop(session_id, None)
                    self._closed_contexts.discard(id(context))
            if errors:
                raise_cleanup_errors(errors)
            self._revisions[session_id] = self._revisions.get(session_id, 0) + 1
            for store in (self._active, self._owners, self._busy, self._history, self._viewports, self._pixel_targets):
                store.pop(session_id, None)
            self._closing_sessions.discard(session_id)
            await self._emit(session_id, status='closed')

    async def close(self) -> None:
        if self._close_task is None or self._close_task.done():
            self._close_task = asyncio.create_task(self._close_owned())
        await join_cleanup(self._close_task)

    async def _close_owned(self):
        self._closing = True
        errors = []
        async def attempt(operation):
            try:
                await operation()
            except BaseException as error:
                errors.append(error)
        await attempt(self.frames.close)
        async with self._setup_lock:
            pass  # 等待正在进行的启动，再枚举所属上下文。
        for session_id in list(self.contexts):
            await attempt(lambda session_id=session_id: self.close_session(session_id))
        if self.engine:
            try:
                await self.engine.stop()
            except BaseException as error:
                errors.append(error)
            else:
                self.engine = None
        await attempt(self.titles.close)
        for context, _, _ in list(self._observation_cleanup.values()):
            await attempt(lambda context=context: self._discard_observations(context))
        for task in list(self._notifications):
            if not task.done():
                await attempt(lambda task=task: join_cleanup(task))
        if errors:
            raise_cleanup_errors(errors)
        self._locks.clear()
        self._control_locks.clear()
        self._frame_locks.clear()
        self.dialogs.events.clear()
        self._notifications.clear()

"""按聊天工作区读取有界环境预览，不暴露配置正文。"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from harness.core.errors import HarnessError, SessionNotFoundError
from harness.i18n import tr
from ..errors import BadRequest, Conflict, NotFound
from ..responses import json_response
from ..router import Router


def build_router(ctx):
    router = Router()

    def service_for():
        service = ctx.harness.worktrees
        if service is None:
            raise BadRequest(tr('python.v1.4d7f49bfcc8f'), code='git/disabled')
        return service

    def session_for(session_id):
        try:
            return ctx.harness.get_session(session_id)
        except SessionNotFoundError as exc:
            raise NotFound(exc.message, code=exc.code) from exc

    def scope(service, session, session_id, worktree_id):
        # 仅在后台线程持有服务锁时查询 Git；客户端不能选择源或目标目录。
        row = service.get(worktree_id)
        cwd = Path(session.header.cwd or ctx.workspace_root()).resolve()
        target = service._target(row)
        if cwd != target and target not in cwd.parents:
            owner = row.get('owner_session_id')
            if owner and owner != session_id:
                raise NotFound(tr('ui.git.error.invalid'), code='git/not-found')
            root = Path(service.git(cwd, 'rev-parse', '--show-toplevel').strip()).resolve()
            if root != Path(row['repo']).resolve():
                raise NotFound(tr('ui.git.error.invalid'), code='git/not-found')
        return row

    def guarded(operation):
        try:
            return json_response(ctx.call(operation)).with_header('Cache-Control', 'no-store')
        except HarnessError as exc:
            error = NotFound if exc.code == 'git/not-found' else Conflict
            raise error(exc.message, code=exc.code, details=exc.details) from exc

    def writable(session, row, *, allow_waiting=False):
        # 此检查运行在聊天事件循环；显式用户操作仍须尊重只读与计划模式。
        harness = ctx.harness
        agent = harness._agent_for(session)
        if agent.task_read_only or agent.recovery_read_only or harness.plan.is_active(session) or harness.permissions.snapshot(session.id)['permission_policy'] == 'read_only':
            raise Conflict(tr('worktree.environment.read_only'), code='git/environment-read-only')
        target = Path(row['path']).resolve()
        def inside(chat):
            if not chat.header.cwd:
                return False
            path = Path(chat.header.cwd).resolve()
            return path.is_relative_to(target) or target.is_relative_to(path)
        pending = set(harness.runtime.active)
        if not allow_waiting:
            pending |= {record.session_id for record in harness.runtime.live_records() if record.status == 'queued'}
        if any(inside(harness.get_session(sid)) for sid in pending):
            raise Conflict(tr('worktree.environment.busy'), code='git/running')
        if any(not state.settled and state.session is not None and inside(state.session) for state in harness.subagents.list_states()):
            raise Conflict(tr('worktree.environment.busy'), code='git/running')
        if (row.get('owner_session_id'), row.get('team_run_id')) in harness._active_team_runs:
            raise Conflict(tr('agent.integration.run_active'), code='agent/run-active')

    def receipt_view(receipt):
        return {key: receipt[key] for key in ('receipt_id', 'worktree_id', 'fingerprint', 'status')} | {
            'copied_count': len(receipt['copied']), 'skipped_count': len(receipt['skipped']),
            'planned_count': len(receipt['planned']), 'reused': receipt.get('reused', False),
            'entries': [{'path': item['path'], 'action': 'copied'} for item in receipt['copied'][:100]] +
                       [{'path': item['path'], 'action': 'skip', 'reason': item['reason']} for item in receipt['skipped'][:max(0, 100 - len(receipt['copied']))]],
            'truncated': len(receipt['copied']) + len(receipt['skipped']) > 100}

    @router.get('/api/v1/sessions/{session_id}/worktrees/{worktree_id}/environment')
    def preview(request, session_id, worktree_id):
        # 页码只改变展示，完整选择仍参与版本计算；拒绝客户端目录覆盖。
        if set(request.query) - {'offset'}:
            raise BadRequest(tr('ui.git.error.invalid'), code='git/invalid')
        raw = request.get('offset', '0')
        if not raw.isascii() or not raw.isdecimal() or len(raw) > 6:
            raise BadRequest(tr('ui.git.error.invalid'), code='git/invalid')
        offset = int(raw)
        service = service_for()

        async def resolve():
            session = session_for(session_id)

            def read():
                with service._mutex:
                    scope(service, session, session_id, worktree_id)
                    plan = service.plan_environment(worktree_id)
                    entries = [{'path': item['path'], 'size': item['size'], 'action': 'copy'} for item in plan['files']]
                    entries.extend({'path': item['path'], 'reason': item['reason'], 'action': 'skip'} for item in plan['skipped'])
                    entries.sort(key=lambda item: (item['path'], item['action']))
                    end = len(entries) if len(entries) - offset - 100 < 5 else offset + 100
                    return {'worktree_id': worktree_id, 'configured': plan['configured'],
                            'fingerprint': plan['fingerprint'], 'copy_count': len(plan['files']),
                            'skip_count': len(plan['skipped']), 'total_bytes': plan['total_bytes'],
                            'entries': entries[offset:end], 'offset': offset,
                            'next_offset': end if end < len(entries) else None}
            return await asyncio.to_thread(read)

        return guarded(resolve())

    @router.get('/api/v1/sessions/{session_id}/worktrees/{worktree_id}/environment/receipt')
    def receipt(request, session_id, worktree_id):
        if request.query:
            raise BadRequest(tr('ui.git.error.invalid'), code='git/invalid')
        service = service_for()
        async def resolve():
            session = session_for(session_id)
            def read():
                with service._mutex:
                    row = scope(service, session, session_id, worktree_id)
                    summary = row.get('environment')
                    if not isinstance(summary, dict):
                        return {'receipt': None}
                    identity = summary.get('receipt_id')
                    if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity):
                        raise Conflict(tr('ui.git.error.invalid'), code='git/environment-invalid')
                    try:
                        value = json.loads((service.root / 'environment-receipts' / (identity + '.json')).read_text('utf-8'))
                        if value['receipt_id'] != identity or value['worktree_id'] != worktree_id or value['status'] not in {'preparing', 'ready', 'failed'}:
                            raise ValueError
                        result = receipt_view(value)
                    except FileNotFoundError as exc:
                        raise NotFound(tr('worktree.environment.uncertain'), code='git/receipt-missing') from exc
                    except (ValueError, KeyError, TypeError) as exc:
                        raise Conflict(tr('ui.git.error.invalid'), code='git/environment-invalid') from exc
                    return {'receipt': result}
            return await asyncio.to_thread(read)
        return guarded(resolve())

    @router.post('/api/v1/sessions/{session_id}/worktrees/{worktree_id}/environment')
    def copy(request, session_id, worktree_id):
        body = request.json()
        if request.query or set(body) != {'expected_fingerprint'} or not isinstance(body['expected_fingerprint'], str) or not re.fullmatch('[a-f0-9]{64}', body['expected_fingerprint']):
            raise BadRequest(tr('ui.git.error.invalid'), code='git/invalid')
        service = service_for()
        async def resolve():
            session = session_for(session_id)
            loop = asyncio.get_running_loop()
            preparations = ctx.harness.workspace_preparations
            def initial_scope():
                with service._mutex:
                    return scope(service, session, session_id, worktree_id)
            initial = await asyncio.to_thread(initial_scope)
            # 活动检查和租约登记位于同一事件循环，不给新任务启动留出 await 空隙。
            writable(session, initial)
            try:
                lease = preparations.claim(initial['path'])
            except HarnessError as exc:
                # 界面既有忙碌拒绝语义保证此时不会标记为未知写入。
                raise Conflict(exc.message, code='git/running') from exc
            async def check(row):
                writable(session, row, allow_waiting=True)
            def write():
                with service._mutex:
                    row = scope(service, session, session_id, worktree_id)
                    if Path(row['path']).resolve() != Path(initial['path']).resolve():
                        raise Conflict(tr('ui.git.error.fileChanged'), code='git/file-changed')
                    def verify():
                        future = asyncio.run_coroutine_threadsafe(check(row), loop)
                        try:
                            future.result(timeout=20)
                        except BaseException:
                            future.cancel()
                            raise
                    verify()
                    return receipt_view(service.copy_environment(worktree_id, expected_fingerprint=body['expected_fingerprint'], before_publish=verify))
            pending = asyncio.create_task(asyncio.to_thread(write))
            cancelled = False
            try:
                # 请求取消只放弃响应，不遗落仍在写文件的后台线程及准备租约。
                while not pending.done():
                    try:
                        await asyncio.shield(pending)
                    except asyncio.CancelledError:
                        cancelled = True
                    except Exception:
                        break
                try:
                    result = pending.result()
                except Exception as exc:
                    if cancelled:
                        raise asyncio.CancelledError from exc
                    raise
                if cancelled:
                    raise asyncio.CancelledError
                return {'receipt': result}
            finally:
                preparations.release(lease)
        return guarded(resolve())

    return router

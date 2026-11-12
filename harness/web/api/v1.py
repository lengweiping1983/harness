"""当前运行与扩展 API。"""
from __future__ import annotations

from functools import lru_cache

from ...app.collaboration_errors import collaboration_error_message
from ...core.language import current_language

from harness.i18n import tr

from typing import Any
from ..router import Router
from ..responses import bytes_response, json_response
from ..errors import ApiError, BadRequest, Conflict, NotFound, ServiceUnavailable
from ...app.queue_edit import queued_view
from ...core.errors import HarnessError, SessionNotFoundError


def build_router(ctx: Any) -> Router:
    router = Router()
    from .comparison import build_router as build_comparison_router
    router.include(build_comparison_router(ctx))
    from .git import build_router as build_git_router
    router.include(build_git_router(ctx))
    from .team_integrations import build_router as build_team_integration_router
    router.include(build_team_integration_router(ctx))
    from .worktree_environment import build_router as build_environment_router
    router.include(build_environment_router(ctx))
    from .memory import build_router as build_memory_router
    router.include(build_memory_router(ctx))
    from .personalities import build_router as build_personality_router
    router.include(build_personality_router(ctx))
    from .hooks import build_router as build_hook_router
    router.include(build_hook_router(ctx))
    from .turn_completion import build_router as build_completion_router
    router.include(build_completion_router(ctx))
    from .file_search import build_router as build_file_search_router
    router.include(build_file_search_router(ctx))
    from .terminals import build_router as build_terminal_router
    router.include(build_terminal_router(ctx))

    def submission_key(request, key):
        if request.subject is None:
            return key
        import re
        if not isinstance(key, str) or not re.fullmatch('[A-Za-z0-9_-]{16,128}', key):
            raise BadRequest(collaboration_error_message('run/idempotency-key-required'), code='run/idempotency-key-required')
        return 'human:' + request.subject + ':' + key

    def submitted_view(record, request):
        if request.subject is None:
            return record.to_dict()
        if record.requester is None or record.requester['subject'] != request.subject:
            raise NotFound(collaboration_error_message('run/not-found'), code='run/not-found')
        data = record.to_dict()
        return {key: data[key] for key in ('id', 'session_id', 'text', 'status', 'created_at',
            'request_id', 'started_at', 'finished_at', 'requester', 'error')}

    def text_field(body):
        if not isinstance(body, dict) or not isinstance(body.get("text"), str) or not body["text"].strip():
            raise BadRequest(tr('python.v1.31da74c2961b'), code="run/invalid")
        return body["text"]

    def queue_text_field(body):
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            raise BadRequest(tr('python.v1.31da74c2961b'), code="run/invalid")
        return body["text"]

    def owned_run(session_id, run_id):
        ctx.harness.get_session(session_id)
        record = ctx.harness.runtime.records.get(run_id)
        if record is None or record.session_id != session_id:
            raise NotFound(tr('python.v1.47c29ab69a98'), code='run/not-found')
        return record

    def edit_version(value):
        if not isinstance(value, str) or len(value) != 64:
            raise BadRequest(tr('python.v1.a7eb684dd99a'), code='run/invalid')
        return value

    def guarded(operation):
        try:
            return operation()
        except HarnessError as exc:
            if exc.code == 'access/denied':
                raise ApiError(exc.message, code=exc.code, status=403) from exc
            if exc.code in {'run/queue-changed', 'run/not-queued', 'run/queue-order-changed'}:
                raise Conflict(exc.message, code=exc.code, details=exc.details) from exc
            raise


    @lru_cache(maxsize=4)
    def schema_body(language: str, templates: tuple[str, ...]) -> bytes:
        # 缓存归属当前路由实例；语言与路由快照共同决定内容，只共享不可变字节。
        from ..protocol import openapi
        return json_response(openapi(list(templates))).body

    @router.get("/api/v1/openapi.json")
    def schema(request):
        body = schema_body(current_language.get(), tuple(ctx.router().templates()))
        return bytes_response(body, content_type="application/json; charset=utf-8",
                              headers={"Cache-Control": "no-store"}, accept_encoding=request.header('Accept-Encoding'))

    @router.get("/api/v1/sessions/{session_id}/permissions")
    def session_permissions(request, session_id):
        async def read():
            try:
                return {"permissions": ctx.harness.permissions.snapshot(session_id)}
            except SessionNotFoundError as exc:
                raise NotFound(tr('python.v1.19da31bd3666'), code="permissions/session-not-found") from exc
        return json_response(ctx.call(read()))

    @router.get('/api/v1/sessions/{session_id}/permissions/grants')
    def permission_grants(request, session_id):
        async def read():
            try:
                return ctx.harness.permissions.grants_view(session_id)
            except SessionNotFoundError as exc:
                raise NotFound(tr('python.v1.19da31bd3666'), code='permissions/session-not-found') from exc
        return json_response(ctx.call(read()))

    @router.get('/api/v1/sessions/{session_id}/permissions/grants/{grant_id}')
    def permission_grant(request, session_id, grant_id):
        async def read():
            try:
                row = ctx.harness.permissions.grant_detail(session_id, grant_id)
            except SessionNotFoundError as exc:
                raise NotFound(tr('python.v1.19da31bd3666'), code='permissions/session-not-found') from exc
            if row is None:
                raise NotFound(tr('python.v1.6fd3cc405b70'), code='permissions/grant-not-found')
            return {'grant': row}
        return json_response(ctx.call(read()))

    @router.delete('/api/v1/sessions/{session_id}/permissions/grants/{grant_id}')
    def revoke_permission_grant(request, session_id, grant_id):
        async def revoke():
            try:
                return await ctx.harness.permissions.revoke_grant(session_id, grant_id)
            except SessionNotFoundError as exc:
                raise NotFound(tr('python.v1.19da31bd3666'), code='permissions/session-not-found') from exc
            except LookupError as exc:
                raise NotFound(str(exc), code='permissions/grant-not-found') from exc
            except HarnessError as exc:
                if exc.code == 'permissions/inherited':
                    raise BadRequest(exc.message, code=exc.code) from exc
                raise
        return json_response(ctx.call(revoke()))

    @router.get("/api/v1/sessions/{session_id}/plans")
    def session_plans(request, session_id):
        from ...app.plans import summaries
        async def read():
            try:
                return {"plans": summaries(ctx.harness.get_session(session_id))}
            except SessionNotFoundError as exc:
                raise NotFound(tr('python.v1.19da31bd3666'), code="plans/session-not-found") from exc
        return json_response(ctx.call(read()))

    @router.get("/api/v1/sessions/{session_id}/plans/{plan_id}")
    def session_plan(request, session_id, plan_id):
        from ...app.plans import detail
        async def read():
            try:
                plan = detail(ctx.harness.get_session(session_id), plan_id)
            except SessionNotFoundError as exc:
                raise NotFound(tr('python.v1.19da31bd3666'), code="plans/session-not-found") from exc
            if plan is None:
                raise NotFound(tr('python.v1.9f59f71487ef'), code="plans/not-found")
            return {"plan": plan}
        return json_response(ctx.call(read()))

    @router.get('/api/v1/sessions/{session_id}/plans/{plan_id}/evidence/{source_seq}')
    def plan_evidence(request, session_id, plan_id, source_seq):
        from ...app.plans import evidence_page
        try:
            seq = int(source_seq)
            offset = int(request.get('offset') or '0')
            limit = int(request.get('limit') or '20000')
            if seq < 0 or offset < 0 or not 1 <= limit <= 20000:
                raise ValueError()
        except ValueError as exc:
            raise BadRequest(tr('python.v1.0931c5be8da3'), code='plans/evidence-invalid') from exc
        async def read():
            try:
                page = evidence_page(ctx.harness.get_session(session_id), plan_id, seq, offset=offset, limit=limit)
            except SessionNotFoundError as exc:
                raise NotFound(tr('python.v1.19da31bd3666'), code='plans/session-not-found') from exc
            except ValueError as exc:
                raise BadRequest(str(exc), code='plans/evidence-invalid') from exc
            if page is None:
                raise NotFound(tr('python.v1.f2806a6617f1'), code='plans/evidence-not-found')
            return {'evidence': page}
        return json_response(ctx.call(read()))

    @router.patch("/api/v1/sessions/{session_id}/permissions")
    def update_session_permissions(request, session_id):
        body = request.json()
        required = ('permission_policy', 'expected_version')
        missing = [field for field in required if field not in body]
        unexpected = sorted(set(body) - set(required))
        if missing or unexpected:
            raise BadRequest(
                tr('permissions.missing_field' if missing else 'permissions.unexpected_field',
                   field=(missing or unexpected)[0]),
                code="permissions/invalid",
                details={'missing': missing, 'unexpected': unexpected},
            )
        if not isinstance(body["expected_version"], str) or len(body["expected_version"]) != 64:
            raise BadRequest(tr('python.v1.d924a1c70677'), code="permissions/invalid-version",
                             details={'field': 'expected_version'})
        async def update():
            try:
                state = await ctx.harness.permissions.set_policy(session_id, body["permission_policy"], expected_version=body["expected_version"])
            except SessionNotFoundError as exc:
                raise NotFound(tr('python.v1.19da31bd3666'), code="permissions/session-not-found") from exc
            except HarnessError as exc:
                if exc.code == "permissions/changed":
                    raise Conflict(exc.message, code=exc.code, details=exc.details) from exc
                if exc.code in {"permissions/invalid-policy", "permissions/inherited"}:
                    raise BadRequest(exc.message, code=exc.code) from exc
                raise
            return {"permissions": state}
        return json_response(ctx.call(update()))

    @router.get("/api/v1/sessions/{session_id}/changes")
    def changes(request, session_id):
        import asyncio
        from ...app.review import ReviewService
        path = request.get("path")
        async def read():
            session = ctx.harness.get_session(session_id)
            cwd = session.header.cwd or ctx.workspace_root()
            service = ReviewService()
            return await asyncio.to_thread(service.patch, cwd, path) if path else await asyncio.to_thread(service.scan, cwd)
        return json_response(ctx.call(read()))

    @router.get("/api/v1/sessions/{session_id}/conversation")
    def conversation(request, session_id):
        from ..views import session_detail
        try:
            limit = int(request.get('limit') or '100')
        except ValueError as exc:
            raise BadRequest(tr('python.v1.4e11272b3e64'), code='conversation/invalid-limit') from exc
        async def read():
            result = session_detail(ctx.harness, session_id, view='conversation-window', limit=limit, cursor=request.get('cursor'))
            if request.subject is not None:
                from ..shared_projection import detail
                return detail(result)
            return result
        return json_response(ctx.call(read()), accept_encoding=request.header('Accept-Encoding'))

    def read_turn_changes(request, session_id, turn, source_seq=None, net=False):
        import asyncio
        from ...app.turn_review import turn_changes
        from ...app.turn_net_changes import turn_net_changes
        values = [turn] + ([source_seq] if source_seq is not None else [])
        if any(not value.isascii() or not value.isdecimal() or len(value) > 16 for value in values):
            raise BadRequest(tr('python.v1.67ad307354eb'), code='changes/invalid')
        try:
            offset = int(request.get('offset') or '0')
            through = int(request.get('through')) if request.get('through') is not None else None
        except ValueError as exc:
            raise BadRequest(tr('python.v1.ad4b9385db1b'), code='changes/invalid') from exc
        async def read():
            session = ctx.harness.get_session(session_id)
            return await asyncio.to_thread(turn_net_changes if net else turn_changes, session, int(turn), int(source_seq) if source_seq is not None else None, offset=offset, through=through)
        try:
            return json_response(ctx.call(read()), accept_encoding=request.header('Accept-Encoding'))
        except SessionNotFoundError as exc:
            raise NotFound(tr('python.v1.19da31bd3666'), code=exc.code) from exc
        except HarnessError as exc:
            if exc.code in {'changes/turn-not-found', 'changes/operation-not-found'}:
                raise NotFound(exc.message, code=exc.code) from exc
            if exc.code == 'changes/ambiguous-turn':
                raise Conflict(exc.message, code=exc.code) from exc
            if exc.code == 'changes/invalid':
                raise BadRequest(exc.message, code=exc.code) from exc
            raise

    @router.get('/api/v1/sessions/{session_id}/turns/{turn}/changes')
    def review_turn(request, session_id, turn):
        return read_turn_changes(request, session_id, turn)

    @router.get('/api/v1/sessions/{session_id}/turns/{turn}/changes/{source_seq}')
    def review_operation(request, session_id, turn, source_seq):
        return read_turn_changes(request, session_id, turn, source_seq)

    @router.get('/api/v1/sessions/{session_id}/turns/{turn}/net-changes')
    def review_net_turn(request, session_id, turn):
        return read_turn_changes(request, session_id, turn, net=True)

    @router.get('/api/v1/sessions/{session_id}/turns/{turn}/net-changes/{source_seq}')
    def review_net_file(request, session_id, turn, source_seq):
        return read_turn_changes(request, session_id, turn, source_seq, net=True)

    def restore_identity(turn, source_seq, through):
        if any(not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 16 for value in (turn, source_seq)):
            raise BadRequest(tr('python.v1.319ec3d88f10'), code='restore/invalid')
        if type(through) is not int or not 0 <= through <= 2**53 - 1:
            raise BadRequest(tr('python.v1.39b2ddb4b8e5'), code='restore/invalid')
        return int(turn), int(source_seq), through

    def restore_response(operation):
        try:
            return json_response(ctx.call(operation()))
        except SessionNotFoundError as exc:
            raise NotFound(tr('python.v1.19da31bd3666'), code=exc.code) from exc
        except HarnessError as exc:
            if exc.code in {'restore/not-found', 'changes/turn-not-found'}:
                raise NotFound(exc.message, code=exc.code) from exc
            if exc.code in {'restore/invalid', 'changes/invalid'}:
                raise BadRequest(exc.message, code=exc.code) from exc
            if exc.code in {'restore/changed', 'restore/request-conflict', 'restore/stale-snapshot', 'restore/corrupt', 'changes/ambiguous-turn'}:
                raise Conflict(exc.message, code=exc.code) from exc
            raise

    @router.get('/api/v1/sessions/{session_id}/turns/{turn}/net-changes/{source_seq}/restore')
    def restore_preview(request, session_id, turn, source_seq):
        try:
            through = int(request.get('through'))
        except (TypeError, ValueError) as exc:
            raise BadRequest(tr('python.v1.39b2ddb4b8e5'), code='restore/invalid') from exc
        turn, source_seq, through = restore_identity(turn, source_seq, through)
        async def read():
            return ctx.harness.file_restores.preview(session_id, turn, source_seq, through)
        return restore_response(read)

    @router.post('/api/v1/sessions/{session_id}/turns/{turn}/net-changes/{source_seq}/restore')
    def restore_file(request, session_id, turn, source_seq):
        body = request.json()
        if not isinstance(body, dict) or set(body) != {'through', 'expected_version', 'request_id'}:
            raise BadRequest(tr('python.v1.e2fcd83df4a7'), code='restore/invalid')
        turn, source_seq, through = restore_identity(turn, source_seq, body['through'])
        async def apply():
            return await ctx.harness.file_restores.apply(session_id, turn, source_seq, through, body['expected_version'], body['request_id'])
        return restore_response(apply)

    @router.get('/api/v1/sessions/{session_id}/turns/{turn}/net-changes/{source_seq}/restores/{request_id}')
    def restore_receipt(request, session_id, turn, source_seq, request_id):
        turn, source_seq, _ = restore_identity(turn, source_seq, 0)
        async def read():
            return ctx.harness.file_restores.receipt(session_id, turn, source_seq, request_id)
        return restore_response(read)

    @router.get("/api/v1/sessions/{session_id}/submissions/{key}")
    def submission(request, session_id, key):
        async def read():
            record = ctx.harness.runtime.submission(session_id, submission_key(request, key))
            return {"run": submitted_view(record, request) if record else None}
        return json_response(ctx.call(read()))

    @router.get("/api/v1/sessions/{session_id}/runs")
    def list_runs(request, session_id):
        ctx.harness.get_session(session_id)
        try:
            limit = int(request.get("limit") or "30")
        except ValueError as exc:
            raise BadRequest(tr('python.v1.b8840111a4ec'), code="run/invalid") from exc
        if not 1 <= limit <= 200:
            raise BadRequest(tr('python.v1.07cae6382d9e'), code="run/invalid")
        async def read():
            include = request.get('include_queued') or '1'
            summary = request.get('summary') or '0'
            if include not in {'0', '1'} or summary not in {'0', '1'}:
                raise BadRequest(tr('python.v1.7ad9cf66f5e5'), code='run/invalid')
            if request.get('before'):
                owned_run(session_id, request.get('before'))
            return ctx.harness.runtime.history(session_id, limit=limit, before=request.get('before') or None,
                                               include_queued=include == '1', summary=summary == '1')
        return json_response(ctx.call(read()))

    @router.get('/api/v1/sessions/{session_id}/turns/{turn}/run')
    def turn_execution(request, session_id, turn):
        from ...app.recovery import execution_for_turn
        if not turn.isascii() or not turn.isdecimal() or len(turn) > 16:
            raise BadRequest(tr('python.v1.a77c5b2a365d'), code='run/invalid')
        async def read():
            return queued_view(execution_for_turn(ctx.harness, session_id, int(turn)))
        try:
            return json_response(ctx.call(read()))
        except SessionNotFoundError as exc:
            raise NotFound(tr('python.v1.19da31bd3666'), code=exc.code) from exc
        except HarnessError as exc:
            if exc.code == 'run/turn-not-found':
                raise NotFound(exc.message, code=exc.code) from exc
            if exc.code == 'run/ambiguous-turn':
                raise Conflict(exc.message, code=exc.code) from exc
            raise

    @router.get("/api/v1/sessions/{session_id}/runs/{run_id}")
    def scoped_run(request, session_id, run_id):
        async def read():
            record = owned_run(session_id, run_id)
            return queued_view(record) if request.subject is None else {**submitted_view(record, request), 'edit_version': queued_view(record)['edit_version']}
        return json_response(ctx.call(read()))

    @router.patch('/api/v1/sessions/{session_id}/runs/{run_id}')
    def scoped_edit_run(request, session_id, run_id):
        body = request.json()
        text = queue_text_field(body)
        version = edit_version(body.get('expected_version'))
        if request.subject is not None and set(body) != {'text', 'expected_version'}:
            raise BadRequest(collaboration_error_message('run/invalid'), code='run/invalid')
        async def edit():
            owned_run(session_id, run_id)
            record = guarded(lambda: ctx.harness.runtime.edit(run_id, text, expected_version=version, subject=request.subject))
            return queued_view(record) if request.subject is None else {**submitted_view(record, request), 'edit_version': queued_view(record)['edit_version']}
        return json_response(ctx.call(edit()))

    @router.delete('/api/v1/sessions/{session_id}/runs/{run_id}')
    def scoped_remove_run(request, session_id, run_id):
        version = edit_version(request.get('expected_version'))
        async def remove():
            owned_run(session_id, run_id)
            guarded(lambda: ctx.harness.runtime.remove(run_id, expected_version=version, subject=request.subject))
            return {'removed': run_id}
        return json_response(ctx.call(remove()))

    @router.post("/api/v1/sessions/{session_id}/runs")
    def submit(request, session_id):
        body = request.json()
        async def create():
            if request.subject is not None and set(body) != {'text'}:
                raise BadRequest(collaboration_error_message('run/invalid'), code='run/invalid')
            record = ctx.harness.runtime.submit(session_id, text_field(body),
                key=submission_key(request, request.header('Idempotency-Key') or None),
                request_id=request.header('X-Request-ID') or None, recipients=body.get('recipients'),
                recipient_team=body.get('recipient_team'), selection_revision=body.get('selection_revision'),
                recipient_identities=body.get('recipient_identities'), subject=request.subject)
            return submitted_view(record, request)
        return json_response(ctx.call(create()), status=202)

    @router.get("/api/v1/runs/{run_id}")
    def get_run(request, run_id):
        async def read():
            return ctx.harness.runtime.get(run_id).to_dict()
        return json_response(ctx.call(read()))

    @router.patch("/api/v1/runs/{run_id}")
    def edit_run(request, run_id):
        body = request.json()
        text = queue_text_field(body)
        version = edit_version(body.get('expected_version')) if 'expected_version' in body else None
        async def edit():
            return queued_view(guarded(lambda: ctx.harness.runtime.edit(run_id, text, expected_version=version)))
        return json_response(ctx.call(edit()))

    @router.delete("/api/v1/runs/{run_id}")
    def remove_run(request, run_id):
        version = edit_version(request.get('expected_version')) if request.get('expected_version') is not None else None
        async def remove():
            guarded(lambda: ctx.harness.runtime.remove(run_id, expected_version=version, subject=request.subject))
            return {"removed": run_id}
        return json_response(ctx.call(remove()))

    @router.get("/api/v1/sessions/{session_id}/queue")
    def queue(request, session_id):
        async def read():
            ctx.harness.get_session(session_id)
            rows = ctx.harness.runtime.queue(session_id)
            if request.subject is not None:
                rows = [{**{key: value for key, value in row.items() if key in {'id', 'edit_version', 'requester'}},
                         'text': (row.get('prompt_input') or {}).get('text', row['text'])} for row in rows]
            return {'runs': rows}
        return json_response(ctx.call(read()))

    @router.get('/api/v1/sessions/{session_id}/queue-control')
    def queue_control(request, session_id):
        async def read():
            return ctx.harness.runtime.queue_control(session_id)
        try:
            return json_response(ctx.call(read()))
        except SessionNotFoundError as exc:
            raise NotFound(exc.message, code=exc.code) from exc

    @router.patch('/api/v1/sessions/{session_id}/queue-control')
    def update_queue_control(request, session_id):
        body = request.json()
        if not isinstance(body, dict) or type(body.get('paused')) is not bool:
            raise BadRequest(tr('python.v1.bdc6d8e1e74a'), code='run/invalid')
        try:
            return json_response(ctx.call(ctx.harness.runtime.set_queue_paused(session_id, body['paused'])))
        except SessionNotFoundError as exc:
            raise NotFound(exc.message, code=exc.code) from exc

    @router.post('/api/v1/sessions/{session_id}/queue-order')
    def reorder_queue(request, session_id):
        body = request.json()
        if not isinstance(body, dict) or not isinstance(body.get('kind'), str) or body.get('kind') != 'runs':
            raise BadRequest(tr('queue.order.invalid'), code='run/invalid')
        async def move():
            ctx.harness.get_session(session_id)
            guarded(lambda: ctx.harness.runtime.reorder_queue(session_id, body.get('expected'), body.get('order')))
            return {'reordered': True}
        return json_response(ctx.call(move()))

    @router.post("/api/v1/sessions/{session_id}/steer")
    def steer(request, session_id):
        body = request.json()
        text = text_field(body)
        return json_response(ctx.call(ctx.harness.steer(session_id, text, expected_turn=body.get('expected_turn'),
            request_id=request.header('Idempotency-Key') or body.get('request_id'))))

    @router.post('/api/v1/sessions/{session_id}/runs/{run_id}/steer')
    def steer_queued_run(request, session_id, run_id):
        body = request.json()
        if not isinstance(body, dict):
            raise BadRequest(tr('python.v1.a7bd8f758780'), code='run/invalid')
        version = edit_version(body.get('expected_version'))
        async def move():
            owned_run(session_id, run_id)
            try:
                return await ctx.harness.runtime.steer_queued(session_id, run_id,
                    expected_turn=body.get('expected_turn'), expected_version=version)
            except HarnessError as exc:
                if exc.code in {'run/queue-changed', 'run/not-queued', 'run/stale-turn'}:
                    raise Conflict(exc.message, code=exc.code, details=exc.details) from exc
                raise
        return json_response(ctx.call(move()))

    @router.get('/api/v1/sessions/{session_id}/steering/{request_id}')
    def steering_receipt(request, session_id, request_id):
        async def read():
            return {'receipt': ctx.harness.steering_receipt(session_id, request_id)}
        return json_response(ctx.call(read()))

    @router.post("/api/v1/sessions/{session_id}/stop")
    def stop(request, session_id):
        body = request.json()
        return json_response({"requested": ctx.call(ctx.harness.cancel(session_id, expected_turn=body.get('expected_turn')))})

    def owned_subagent(session_id, agent_id):
        ctx.harness.get_session(session_id)
        state = ctx.harness.subagents.get(agent_id)
        if state is None or (state.parent_session_id != session_id and state.root_session_id != session_id):
            raise NotFound(tr('python.v1.a99fd28bf21f'), code='subagent/not-found')
        return state

    @router.get('/api/v1/sessions/{session_id}/subagents')
    def subagent_history(request, session_id):
        try:
            limit = int(request.get('limit') or '50')
        except ValueError as exc:
            raise BadRequest(tr('python.v1.b8840111a4ec'), code='subagent/invalid') from exc
        if not 1 <= limit <= 100:
            raise BadRequest(tr('python.v1.5e570c4f9b35'), code='subagent/invalid')
        async def read():
            ctx.harness.get_session(session_id)
            try:
                return ctx.harness.subagents.history_page(session_id, before=request.get('before'), limit=limit, include_descendants=True)
            except LookupError as exc:
                raise NotFound(tr('python.v1.eeae2302d205'), code='subagent/not-found') from exc
        return json_response(ctx.call(read()))

    @router.get('/api/v1/sessions/{session_id}/subagents/{agent_id}')
    def subagent_detail(request, session_id, agent_id):
        async def read():
            state = owned_subagent(session_id, agent_id)
            return {'agent': {**state.to_dict(), 'prompt': state.text}}
        return json_response(ctx.call(read()))

    @router.post('/api/v1/sessions/{session_id}/subagents/{agent_id}/stop')
    def stop_subagent(request, session_id, agent_id):
        async def stop_owned():
            owned_subagent(session_id, agent_id)
            state = await ctx.harness.subagents.interrupt(agent_id, reason=tr('python.v1.52d770c7135f'))
            result = {'agent': {**state.to_dict(), 'prompt': state.text}}
            # 日志写入失败不能阻止取消。
            try:
                await ctx.harness.get_session(session_id).append('note', {
                    'kind': 'subagent/control', 'action': 'stop', 'source': 'user',
                    'agent_id': agent_id, 'status': state.status,
                }, ignorable=True)
            except Exception:
                result['audit_error'] = tr('ui.audit.agentStopIncomplete')
            return result
        return json_response(ctx.call(stop_owned()))

    @router.get('/api/v1/sessions/{session_id}/jobs')
    def job_history(request, session_id):
        before = request.get('before')
        try:
            limit = int(request.get('limit') or '50')
        except ValueError as exc:
            raise BadRequest(tr('python.v1.b8840111a4ec'), code='job/invalid') from exc
        if not 1 <= limit <= 100:
            raise BadRequest(tr('python.v1.efebacc0cde9'), code='job/invalid')
        async def read():
            ctx.harness.get_session(session_id)
            try:
                return await ctx.harness.jobs.history_page(session_id, before=before, limit=limit)
            except LookupError as exc:
                raise NotFound(tr('python.v1.0178f467df02'), code='job/not-found') from exc
            except Exception as exc:
                raise ServiceUnavailable(tr('python.v1.6036b943d037'), code='job/history-unavailable') from exc
        return json_response(ctx.call(read()))

    @router.get("/api/v1/sessions/{session_id}/jobs/{job_id}/output")
    def job_output(request, session_id, job_id):
        try:
            limit = int(request.get("limit") or "32768")
        except ValueError as exc:
            raise BadRequest(tr('python.v1.b8840111a4ec'), code="job/invalid") from exc
        if not 1 <= limit <= 32768:
            raise BadRequest(tr('python.v1.367eb150f91f'), code="job/invalid")
        async def read():
            ctx.harness.get_session(session_id)
            try:
                return await ctx.harness.jobs.output_snapshot(job_id, session_id, limit=limit)
            except LookupError as exc:
                raise NotFound(tr('python.v1.6974695774e4'), code="job/not-found") from exc
            except Exception as exc:
                raise ServiceUnavailable(tr('python.v1.8f19a4af3c40'), code='job/output-unavailable') from exc
        return json_response(ctx.call(read()))

    @router.post("/api/v1/sessions/{session_id}/jobs/{job_id}/stop")
    def stop_job(request, session_id, job_id):
        async def stop_owned():
            session = ctx.harness.get_session(session_id)
            try:
                outcome, job = await ctx.harness.jobs.kill(job_id, session_id, tr('python.v1.52d770c7135f'))
            except LookupError as exc:
                raise NotFound(tr('python.v1.6974695774e4'), code="job/not-found") from exc
            result = {"outcome": outcome, "job": job}
            # 清理优先于审计落盘；磁盘失败不能阻止用户停止所属进程。
            try:
                await session.append("note", {"kind": "job/control", "action": "stop", "source": "user", **result}, ignorable=True)
            except Exception:
                result["audit_error"] = tr('ui.audit.jobStopIncomplete')
            return result
        return json_response(ctx.call(stop_owned()))

    @router.get("/api/v1/sessions/{session_id}/terminals/{terminal_id}/output")
    def terminal_output(request, session_id, terminal_id):
        try:
            count = int(request.get('count') or '500')
        except ValueError as exc:
            raise BadRequest(tr('python.v1.9e7592c6180b'), code='terminal/invalid') from exc
        if not 1 <= count <= 2000:
            raise BadRequest(tr('python.v1.02876341e159'), code='terminal/invalid')
        async def read():
            ctx.harness.get_session(session_id)
            try:
                terminal = ctx.harness.terminals.expect_owned(session_id, terminal_id)
            except LookupError as exc:
                raise NotFound(tr('python.v1.cda5311aecd8'), code='terminal/not-found') from exc
            return {**terminal.peek(count), 'sessionId': terminal_id, 'session': terminal.status(), 'terminal': terminal.snapshot()}
        return json_response(ctx.call(read()))

    @router.post("/api/v1/sessions/{session_id}/terminals/{terminal_id}/stop")
    def stop_terminal(request, session_id, terminal_id):
        async def stop_owned():
            session = ctx.harness.get_session(session_id)
            try:
                result = await ctx.harness.terminals.close(session_id, terminal_id)
                terminal = ctx.harness.terminals.expect_owned(session_id, terminal_id)
            except LookupError as exc:
                raise NotFound(tr('python.v1.cda5311aecd8'), code='terminal/not-found') from exc
            result['terminal'] = terminal.snapshot()
            try:
                await session.append('note', {'kind': 'terminal/control', 'action': 'stop', 'source': 'user', **result}, ignorable=True)
            except Exception:
                result['audit_error'] = tr('ui.audit.terminalStopIncomplete')
            return result
        return json_response(ctx.call(stop_owned()))

    @router.post("/api/v1/sessions/{session_id}/continue")
    def continue_run(request, session_id):
        body = request.json()
        if not isinstance(body, dict):
            raise BadRequest(tr('python.v1.7f2649f84893'), code='run/invalid')
        async def create():
            original_id = body.get('run_id')
            if original_id is not None and (not isinstance(original_id, str) or not original_id.strip()):
                raise BadRequest(tr('python.v1.2c7d89a884f4'), code='run/invalid')
            original = owned_run(session_id, original_id) if original_id is not None else None
            recovery = None
            mode = body.get('mode', 'continue')
            if not isinstance(mode, str) or mode not in {'inspect', 'continue'}:
                raise BadRequest(tr('python.v1.5b073d46a321'), code='run/invalid')
            if 'recovery_version' in body or mode == 'inspect':
                from ...app.recovery import recovery_preview
                if original is None or not isinstance(body.get('recovery_version'), str):
                    raise BadRequest(tr('python.v1.633fdafcc410'), code='run/invalid')
                note = body.get('verification_note', '')
                if not isinstance(note, str) or len(note) > 4000:
                    raise BadRequest(tr('python.v1.d623b1acafb2'), code='run/invalid')
                recovery = {'mode': mode, 'source_version': body['recovery_version'], 'verification_note': note.strip()}
                key = request.header('Idempotency-Key')
                known = ctx.harness.runtime.submission(session_id, key) if key else None
                if known is None:
                    preview = recovery_preview(ctx.harness, session_id, original_id)
                    if body['recovery_version'] != preview['version']:
                        raise BadRequest(tr('python.v1.14ca706b1ac5'), code='run/recovery-changed')
                    if mode == 'continue' and preview['verification_required'] and not note.strip():
                        raise BadRequest(tr('python.v1.4cd7a241596b'), code='run/verification-required')
            default = tr('python.v1.fc3c78952508')
            if original is not None and recovery is None:
                default += tr('python.v1.855e96e1ce64') + original.id + tr('python.v1.34642f2f373b') + original.text
            if mode == 'inspect':
                default = tr('python.v1.75649d4f503b')
            text = text_field(body) if body.get('text') not in (None, '') else default
            if recovery is not None:
                recovery['instruction_text'] = text
                if known is not None:
                    if known.retry_of != original_id or known.recovery != recovery:
                        raise Conflict(tr('python.runtime.414842c23b85'), code='run/idempotency-conflict')
                    return known.to_dict()
                # 审核草稿属于指令；服务端保留完整的可信原始任务，
                # 包括预览中被截断的约束。
                text += tr('python.v1.855e96e1ce64') + original.id + tr('python.v1.34642f2f373b') + original.text
                if recovery['verification_note']:
                    text += tr('python.v1.236954da55ad') + recovery['verification_note']
            return ctx.harness.runtime.submit(session_id, text, key=request.header("Idempotency-Key") or None,
                                              request_id=request.header("X-Request-ID") or None,
                                              retry_of=original_id, recovery=recovery).to_dict()
        return json_response(ctx.call(create()), status=202)

    @router.get('/api/v1/sessions/{session_id}/runs/{run_id}/recovery')
    def recovery(request, session_id, run_id):
        owned_run(session_id, run_id)
        from ...app.recovery import recovery_preview
        try:
            offset = int(request.get('offset') or '0')
            limit = int(request.get('limit') or '50')
        except ValueError as exc:
            raise BadRequest(tr('python.v1.45b71dd649a1'), code='run/invalid') from exc
        async def read():
            return recovery_preview(ctx.harness, session_id, run_id, offset=offset, limit=limit)
        return json_response(ctx.call(read()))

    @router.get("/api/v1/sessions/{session_id}/events")
    def events(request, session_id):
        after = request.get_int("after", -1)
        limit = request.get_int("limit", 200)
        rows = ctx.harness.sessions.read_events(session_id, after=after, limit=limit)
        return json_response({"events": rows, "next_after": rows[-1]["seq"] if rows else after})

    @router.post("/api/v1/sessions/{session_id}/fork")
    def fork(request, session_id):
        body = request.json()
        async def create():
            child = ctx.harness.sessions.fork(session_id, through_seq=body.get("through_seq"), title=body.get("title"),
                                               before_message_seq=body.get("before_message_seq"))
            result = {"session": child.header.to_dict()}
            if body.get("before_message_seq") is not None:
                result["agent_mount"] = ctx.harness.agent_mount_state(child)
            return result
        return json_response(ctx.call(create()), status=201)

    @router.post("/api/v1/sessions/{session_id}/archive")
    def archive(request, session_id):
        archived = bool(request.json().get("archived", True))
        async def update():
            if session_id in ctx.harness.runtime.active or ctx.harness.runtime.queue(session_id):
                raise BadRequest(tr('python.v1.622ba6569279'), code="session/running")
            return {"session": ctx.harness.sessions.update_header(session_id, metadata={"archived": archived})}
        return json_response(ctx.call(update()))

    @router.get("/api/v1/approvals")
    def approvals(request):
        return json_response({"questions": ctx.harness.questions.pending(request.get("session_id"))})

    @router.post("/api/v1/approvals/{question_id}")
    def answer(request, question_id):
        from ...interaction.questions import QuestionError
        try:
            result = ctx.call(ctx.harness.questions.answer_and_notify(question_id, request.json().get("answers")))
        except QuestionError as exc:
            raise Conflict(str(exc), code='question/conflict') from exc
        return json_response(result)

    @router.get("/api/v1/capabilities")
    def capabilities(request):
        config = ctx.harness.config
        return json_response({"version": 1, "permission_policy": config.permission_policy,
                              "browser": config.enable_browser, "automations": config.enable_automations,
                              "worktrees": config.enable_worktrees, "interfaces": router.templates()})

    def automation_service():
        if ctx.harness.automations is None:
            raise BadRequest(tr('python.v1.0bfc9544f1c8'), code="automation/disabled")
        return ctx.harness.automations

    # 服务端构造时启动调度，不能依赖用户打开设置页面。
    if getattr(getattr(ctx, 'harness', None), 'automations', None) is not None:
        async def start_scheduler():
            ctx.harness.automations.start()
        ctx.call(start_scheduler())

    @router.get("/api/v1/automations")
    def list_automations(request):
        service = automation_service()
        async def start():
            service.start()
            return {"automations": service.list(), "runs": service.history(), "scheduler": service.health()}
        return json_response(ctx.call(start()))

    @router.post("/api/v1/automations")
    def save_automation(request):
        return json_response(automation_service().save(request.json()), status=201)

    @router.delete("/api/v1/automations/{automation_id}")
    def delete_automation(request, automation_id):
        automation_service().remove(automation_id)
        return json_response({"removed": automation_id})

    @router.post("/api/v1/automations/preview")
    def preview_automation(request):
        from ...app.schedules import validate_schedule, next_due
        import time
        body = request.json()
        schedule, timezone = validate_schedule(body)
        after, occurrences = time.time(), []
        for _ in range(1 if schedule["kind"] == "once" else 5):
            after = next_due(schedule, timezone, after)
            occurrences.append(after)
        return json_response({"occurrences": occurrences, "timezone": timezone})

    @router.post("/api/v1/automations/{automation_id}/run")
    def run_automation(request, automation_id):
        return json_response(ctx.call(automation_service().run_now(automation_id)), status=202)

    @router.post("/api/v1/automations/{automation_id}/stop")
    def stop_automation(request, automation_id):
        ctx.call(automation_service().stop(automation_id))
        return json_response({"stopped": automation_id})

    @router.post("/api/v1/automation-runs/{run_id}/read")
    def read_automation_run(request, run_id):
        automation_service().mark_read(run_id)
        return json_response({"read": run_id})

    def worktree_service():
        if ctx.harness.worktrees is None:
            raise BadRequest(tr('python.v1.4d7f49bfcc8f'), code="git/disabled")
        return ctx.harness.worktrees

    @router.get("/api/v1/worktrees")
    def list_worktrees(request):
        return json_response({"worktrees": worktree_service().list()})

    @router.get("/api/v1/sessions/{session_id}/git")
    def repository_status(request, session_id):
        async def read():
            session = ctx.harness.get_session(session_id)
            return session.header.cwd or str(ctx.workspace_root())
        return json_response(worktree_service().repository(ctx.call(read())))

    @router.post("/api/v1/worktrees")
    def create_worktree(request):
        body = request.json()
        repo = str(body.get("repo", ctx.workspace_root()))
        from pathlib import Path
        candidate = Path(repo).resolve()
        allowed = [Path(ctx.workspace_root()).resolve(), *[Path(item["path"]).resolve() for item in ctx.workspaces().list()]]
        allowed.extend(Path(item["path"]).resolve() for item in worktree_service().list() if item["status"] == "active")
        if not any(candidate.is_relative_to(root) for root in allowed):
            raise BadRequest(tr('python.v1.12f6490d4fb8'), code="git/unregistered")
        return json_response(worktree_service().create(repo, ref=str(body.get("ref", "HEAD"))), status=201)

    @router.get("/api/v1/worktrees/{worktree_id}/diff")
    def diff_worktree(request, worktree_id):
        return json_response(worktree_service().diff(worktree_id))

    @router.get('/api/v1/worktrees/{worktree_id}/review')
    def review_worktree(request, worktree_id):
        return json_response(worktree_service().review(worktree_id, path=request.get('path')))

    @router.post("/api/v1/worktrees/{worktree_id}/archive")
    def archive_worktree(request, worktree_id):
        service = worktree_service()
        target = service.get(worktree_id)["path"]
        async def archive_idle():
            from pathlib import Path
            ctx.harness.workspace_preparations.assert_available(target)
            pending = set(ctx.harness.runtime.active) | {r.session_id for r in ctx.harness.runtime.live_records() if r.status == "queued"}
            target_path = Path(target).resolve()
            if any(ctx.harness.get_session(sid).header.cwd and Path(ctx.harness.get_session(sid).header.cwd).resolve().is_relative_to(target_path) for sid in pending):
                raise BadRequest(tr('python.v1.0b24759b4cde'), code="git/running")
            # 与运行提交共享事件循环，检查与归档之间不启动新的任务。
            return service.archive(worktree_id)
        return json_response(ctx.call(archive_idle()))

    @router.post("/api/v1/worktrees/{worktree_id}/restore")
    def restore_worktree(request, worktree_id):
        return json_response(worktree_service().restore(worktree_id))

    def effective_settings():
        from ...llm.capabilities import minimum
        from ...llm.request_limits import context_margin
        config = ctx.harness.config
        adapter = ctx.harness.adapter
        model, url = config.model, config.base_url
        manual = {'context_window': config.model_context_window, 'max_output_tokens': config.model_max_output_tokens}
        account = getattr(ctx.harness, 'chatgpt', None)
        if account is not None and account.logged_in and account.models:
            info = account.resolve()
            model, url = info['id'], 'https://chatgpt.com/backend-api/codex'
            manual = {'context_window': info.get('context_window'), 'max_output_tokens': info.get('max_output')}
        elif adapter.route_pool:
            snapshot = adapter.route_pool.snapshot()
            selected = next(m for m in snapshot['models'] if m['id'] == snapshot['current']['model_id'])
            model, url, manual = selected['model'], selected['base_url'], selected
        caps = adapter.capabilities.peek(url, model, manual)
        window = minimum(config.context_window, caps.get('context_window'))
        output = minimum(caps.get('max_output_tokens'),
            window - context_margin(window) if window else None) if caps.get('max_output_tokens') else None
        from ...core.language import resolve_language
        return {**caps, **resolve_language(config.language).to_dict(), 'model': model, 'context_window': window, 'max_tokens': output,
            'summary_max_tokens': minimum(output, config.summary_max_tokens) if output else None}

    @router.get("/api/v1/settings")
    def settings(request):
        from ..views import skill_catalog
        return json_response({"config": ctx.harness.config.to_dict(), "effective": effective_settings(), "skills": skill_catalog(ctx.harness), "tools": ctx.harness.tools.names()})

    @router.patch("/api/v1/settings")
    def update_settings(request):
        body = request.json()
        from dataclasses import replace
        from ...session.store import _atomic_write
        import json
        allowed = {"tool_schema_mode", "tool_result_max_chars", "context_auto_manage", "context_working_budget", "permission_policy", "context_window", "output_reserve", "max_steps", "token_budget", "summary_max_tokens", "model_context_window", "model_max_output_tokens", "network_timeout"}
        if not isinstance(body, dict) or set(body) - allowed:
            raise BadRequest(tr('python.v1.5fefdc38f00c'), code="settings/invalid")
        nullable_integers = {"tool_result_max_chars", "context_window", "max_steps", "token_budget", "summary_max_tokens", "model_context_window", "model_max_output_tokens"}
        for key, value in body.items():
            if key == 'tool_schema_mode':
                if value not in ('auto', 'all'):
                    raise BadRequest(tr('python.web.validation.toolSchemaMode'), code='settings/invalid')
                continue
            if key == 'context_auto_manage':
                if type(value) is not bool:
                    raise BadRequest(tr('context.settings.boolean'), code='settings/invalid')
                continue
            if key != "permission_policy" and not (key in nullable_integers and value is None) and (type(value) is not int or value < (0 if key == "output_reserve" else 1)):
                message_key = ('python.settings.nonnegativeInteger' if key == 'output_reserve' else
                               'python.settings.nullablePositiveInteger' if key in nullable_integers else
                               'python.settings.positiveInteger')
                raise BadRequest(tr(message_key, field=key), code="settings/invalid")
        async def update():
            config = ctx.harness.config
            try:
                candidate = replace(config, **body)
            except (TypeError, ValueError) as exc:
                raise BadRequest(str(exc), code="settings/invalid") from exc
            if ctx.harness.runtime.active and set(body) - {'tool_schema_mode', 'tool_result_max_chars', 'context_auto_manage', 'context_working_budget', 'summary_max_tokens', 'output_reserve'}:
                raise BadRequest(tr('python.v1.61accde14899'), code="settings/busy")
            permissions_changed = candidate.permission_policy != config.permission_policy
            values = {key: getattr(candidate, key) for key in allowed | {"model", "base_url", "reasoning_effort"}}
            _atomic_write(config.home / "runtime-settings.json", json.dumps({"version": 1, "settings": values}, ensure_ascii=False))
            for key, value in values.items():
                setattr(config, key, value)
            ctx.harness.adapter.network_timeout = config.network_timeout
            ctx.harness.adapter.context_window = config.context_window
            from ...llm.types import ModelInfo
            ctx.harness.adapter.model_info = ModelInfo('openrouter', config.model, config.model_context_window, config.model_max_output_tokens)
            if permissions_changed:
                await ctx.harness.permissions.publish(invalidate=True)
            return {"config": config.to_dict(), "effective": effective_settings()}
        return json_response(ctx.call(update()))
    from . import browser, connection
    router.include(connection.build_router(ctx))
    router.include(browser.build_router(ctx))
    return router

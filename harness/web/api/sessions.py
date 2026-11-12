"""会话相关接口。

覆盖：列表 / 新建 / 详情 / 删除 / 事件增量拉取 / 导出为 Markdown。

**增量拉取**（``GET /api/v1/sessions/{id}/live-events?since=N``）是 WebSocket 的
降级通道：当代理禁用了 Upgrade、或网络抖动导致 WS 反复重连时，前端仍能
按 ``seq`` 补齐缺失事件，而不必整段重载会话。
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
from pathlib import Path
from typing import Any

from ..errors import BadRequest, NotFound, ServiceUnavailable
from ...core.errors import HarnessError
from ..models import REASONING_EFFORTS, model_catalog
from ..responses import json_response, text_response
from ..router import Router
from ..query import LIVE_EVENTS_QUERY, SESSION_LIST_QUERY, SESSION_ACTIVITY_QUERY, read_query
from ..input_schemas import SESSION_PATCH, TITLE_MAX_LENGTH
from .. import shared_projection
from ..views import session_detail, session_listing, session_header_view


def build_router(ctx: Any) -> Router:
    """注册会话路由。``ctx`` 是 :class:`~harness.web.context.WebContext`。"""
    router = Router()

    @router.get("/api/v1/sessions")
    def list_sessions(request: Any) -> Any:
        limit = read_query(request, SESSION_LIST_QUERY).get('limit')
        rows = session_listing(ctx.harness, visible_session_ids=request.visible_session_ids)
        if limit is not None:
            rows = rows[:limit]
        return json_response(
            {"sessions": [(shared_projection.header(row) if request.subject is not None else row) for row in rows]},
            accept_encoding=request.header("Accept-Encoding"),
        )

    @router.get("/api/v1/sessions/activity")
    def session_activity(request: Any) -> Any:
        """活动状态只依赖实际任务；共享读取先按会话账本筛选。"""
        query = read_query(request, SESSION_ACTIVITY_QUERY)
        async def snapshot():
            visible = request.visible_session_ids
            return {"running": [identifier for identifier, task in ctx.harness.runtime.active.items()
                                if not task.done() and (visible is None or identifier in visible)],
                    "catalog_revision": ctx.harness.sessions.catalog_revision if visible is None
                        else ctx.shared_access.catalog_revision(visible),
                    **ctx.hub.activity_completions(query.get('server_id'), query.get('after'), visible)}
        return json_response(ctx.call(snapshot()))

    @router.post("/api/v1/sessions")
    def create_session(request: Any) -> Any:
        payload = request.json()
        unknown = sorted(set(payload) - {'cwd', 'title', 'model', 'agent_selection', 'project'})
        if unknown:
            raise BadRequest(tr('ui.session.unknownField', field=unknown[0][:200]),
                             code='session/invalid-field', details={'field': unknown[0][:200],
                                 'copy': {'key': 'ui.session.unknownField', 'params': {'field': unknown[0][:200]}}})
        # 先拒绝结构错误，避免字符串化对象后创建无法使用的持久对话。
        for field in ('cwd', 'title', 'model'):
            if payload.get(field) is not None and not isinstance(payload[field], str):
                raise BadRequest(tr('python.sessions.invalid_field', field=field),
                                 code='session/invalid-field', details={'field': field})
        model = str(payload.get('model') or '').strip()
        if model:
            known_models = {row['id'] for row in model_catalog(ctx.harness.config)['models']}
            if model not in known_models:
                raise BadRequest(tr('python.sessions.28af3eb8ea53', p0=f'{model}'), code='model/unknown')
        if isinstance(payload.get('title'), str) and len(payload['title']) > TITLE_MAX_LENGTH:
            raise BadRequest(tr('ui.session.titleTooLong', limit=TITLE_MAX_LENGTH), code='session/invalid-field',
                             details={'field': 'title', 'maxLength': TITLE_MAX_LENGTH,
                                      'copy': {'key': 'ui.session.titleTooLong', 'params': {'limit': TITLE_MAX_LENGTH}}})
        cwd = payload.get('cwd')
        if cwd:
            # 显式目录先解析并确认存在，再创建任何持久对话记录。
            try:
                directory = Path(cwd).expanduser().resolve(strict=True)
                if not directory.is_dir():
                    raise ValueError(cwd)
            except (OSError, RuntimeError, ValueError) as exc:
                raise BadRequest(tr('python.shell_tools.9065985f4c79', p0=cwd),
                                 code='session/invalid-cwd', details={'field': 'cwd'}) from exc
            cwd = str(directory)
        selection = payload.get('agent_selection')
        if selection is not None and (not isinstance(selection, str) or not selection.strip()):
            raise BadRequest(tr('python.plugins.81af4a868780'), code='agent/invalid-selection')
        project = payload.get('project')
        project_metadata = {}
        if 'project' in payload:
            allowed = {'workspace_id', 'mode', 'ref', 'expected_head', 'expected_branch'}
            if (not isinstance(project, dict) or set(project) - allowed
                    or not isinstance(project.get('workspace_id'), str) or not project['workspace_id']
                    or project.get('mode') not in ('local', 'worktree')
                    or 'ref' in project and (not isinstance(project['ref'], str) or not project['ref'] or project['ref'].startswith('-') or '\0' in project['ref'])
                    or any(key in project and project[key] is not None and not isinstance(project[key], str)
                           for key in ('expected_head', 'expected_branch'))):
                raise BadRequest(tr('ui.newConversation.invalidSelection'), code='session/workspace-invalid',
                                 details={'copy': {'key': 'ui.newConversation.invalidSelection'}})
            workspace = ctx.workspaces().get(project['workspace_id'])
            if workspace is None or cwd is not None and Path(cwd) != Path(workspace['path']).resolve():
                raise BadRequest(tr('ui.newConversation.chooseProject'), code='session/workspace-invalid',
                                 details={'copy': {'key': 'ui.newConversation.chooseProject'}})
            service = ctx.harness.worktrees
            try:
                if service is None:
                    if project['mode'] != 'local' or 'ref' in project:
                        raise BadRequest(tr('python.v1.4d7f49bfcc8f'), code='git/disabled')
                    directory = Path(workspace['path']).resolve(strict=True)
                    if not directory.is_dir():
                        raise ValueError(workspace['path'])
                    cwd = str(directory)
                else:
                    cwd, project_metadata = service.prepare_conversation(workspace['path'], project)
            except (OSError, ValueError, RuntimeError) as exc:
                raise BadRequest(tr('python.shell_tools.9065985f4c79', p0=workspace['path']), code='session/invalid-cwd') from exc
            except HarnessError as exc:
                error = ServiceUnavailable if exc.code == 'session/workspace-uncertain' else BadRequest
                raise error(exc.message, code=exc.code, details=exc.details) from exc
            project_metadata['workspace_id'] = workspace['id']
        request_id = request.header('X-Request-ID')
        metadata = {**project_metadata, **({} if payload.get('title') else {'auto_title': 'awaiting'})}
        if request_id:
            metadata.update(creation_request_id=request_id, creation_completed=False)
        try:
            session = ctx.harness.create_session(cwd=cwd, title=payload.get("title"), metadata=metadata)
        except Exception as exc:
            if project is not None:
                raise ServiceUnavailable(tr('ui.newConversation.preparationUnconfirmed'),
                                         code='session/workspace-uncertain', details={**project_metadata, 'copy': {'key': 'ui.newConversation.preparationUnconfirmed'}}) from exc
            raise
        # 新建时就把模型定死，省掉"建完会话再切一次模型"这一步。
        model = str(payload.get("model") or "").strip()
        if model:
            ctx.harness.sessions.update_header(session.id, metadata={"model": model})
            session = ctx.harness.get_session(session.id)
        mount = None
        if selection is not None:
            try:
                mount = ctx.call(ctx.harness.set_agent_selection(session, selection))
            except Exception as exc:
                ctx.harness.delete_session(session.id)
                if project is not None:
                    raise ServiceUnavailable(tr('ui.newConversation.preparationUnconfirmed'),
                                             code='session/workspace-uncertain', details={**project_metadata, 'copy': {'key': 'ui.newConversation.preparationUnconfirmed'}}) from exc
                raise
        if request_id:
            # 只有模型及Agent初始化完成，读回才能确认创建已完成。
            ctx.harness.sessions.update_header(session.id, metadata={'creation_completed': True})
        return json_response(
            {"session": session_header_view(session.header), **({'agent_mount': mount} if mount is not None else {})},
            accept_encoding=request.header("Accept-Encoding"),
        )

    def integer(request, key, default=None, *, maximum=2**53 - 1):
        value = request.get(key)
        if value is None:
            return default
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise BadRequest(tr('python.sessions.22ebc6a27f38', p0=f'{key}'), code='search/invalid-position') from exc
        if result < 0 or result > maximum:
            raise BadRequest(tr('python.sessions.22ebc6a27f38', p0=f'{key}'), code='search/invalid-position')
        return result

    @router.get('/api/v1/sessions/{session_id}/context')
    def audit_context(request, session_id):
        from ..context_audit import context_audit, resolve_context
        async def read():
            session = await resolve_context(ctx.harness, session_id, request.get('agent_id'))
            through = integer(request, 'through')
            # 在所属事件循环冻结投影，在循环外汇总大历史，
            # 避免检查阻塞正在进行的生成。
            snapshot = (session.events, session.build_messages() if through is None else None, len(session.surface))
            request_sequence = session.request_traces.latest_sequence(session.id, include_children=True) if through is None else None
            live = [ctx.harness.subagents.summary(state.id) for state in ctx.harness.subagents.list_states()
                    if state.parent_session_id == session.id] if through is None else []
            def project():
                from ..execution_trace import agents_for
                count = len({row['agent_id'] for row in agents_for(ctx.harness.subagents, session.id, live)
                             if row['parent_session_id'] == session.id}) if through is None else None
                return context_audit(session, through=through, before=integer(request, 'before'),
                                     limit=integer(request, 'limit', 20, maximum=20000), snapshot=snapshot, subagent_count=count,
                                     request_sequence=request_sequence)
            return await asyncio.to_thread(project)
        return json_response(ctx.call(read()), accept_encoding=request.header('Accept-Encoding'))

    @router.post('/api/v1/sessions/{session_id}/context/compact')
    def compact_context(request, session_id):
        body = request.json()
        if (not isinstance(body, dict) or 'request_id' not in body or set(body) - {'request_id', 'restart', 'mode'}
                or ('restart' in body and type(body['restart']) is not bool)
                or not isinstance(body.get('mode', 'compact'), str) or body.get('mode', 'compact') not in {'compact', 'project'}
                or body.get('mode') == 'project' and body.get('restart')):
            raise BadRequest(tr('context.control.invalid'), code='run/invalid')
        record = ctx.call(ctx.harness.runtime.submit_compaction(session_id, body['request_id'],
            restart=body.get('restart', False), mode=body.get('mode', 'compact')))
        return json_response(record.to_dict(), status=202)

    @router.get('/api/v1/sessions/{session_id}/context/compact')
    def context_control(request, session_id):
        async def read():
            record = ctx.harness.runtime.context_control(session_id, request.get('request_id'))
            return record.to_dict() if record is not None else None
        return json_response(ctx.call(read()))

    @router.get('/api/v1/sessions/{session_id}/context/skills')
    def audit_skills(request, session_id):
        from ..context_audit import resolve_context
        from ..skill_audit import skill_audit
        async def read():
            session = await resolve_context(ctx.harness, session_id, request.get('agent_id'))
            return await asyncio.to_thread(skill_audit, session.events,
                through=integer(request, 'through'), before=integer(request, 'before'),
                limit=integer(request, 'limit', 20, maximum=20000))
        return json_response(ctx.call(read()), accept_encoding=request.header('Accept-Encoding'))

    @router.get('/api/v1/sessions/{session_id}/context/skills/{result_seq}')
    def skill_original(request, session_id, result_seq):
        from ..context_audit import resolve_context
        from ..skill_audit import read_skill
        try:
            seq = int(result_seq)
        except ValueError as exc:
            raise BadRequest(tr('python.sessions.13434f7174dc')) from exc
        async def read():
            session = await resolve_context(ctx.harness, session_id, request.get('agent_id'))
            return await asyncio.to_thread(read_skill, session.events, seq,
                through=integer(request, 'through', session.event_count - 1), offset=integer(request, 'offset', 0))
        return json_response(ctx.call(read()), accept_encoding=request.header('Accept-Encoding'))

    @router.get('/api/v1/sessions/{session_id}/context/compactions/{start_seq}')
    def audit_compaction(request, session_id, start_seq):
        from ..context_audit import read_compaction, resolve_context
        try:
            start_seq = int(start_seq)
        except ValueError as exc:
            raise BadRequest(tr('python.sessions.ffd1d9b92e60')) from exc
        async def read():
            session = await resolve_context(ctx.harness, session_id, request.get('agent_id'))
            return await asyncio.to_thread(read_compaction, session, start_seq,
                through=integer(request, 'through', session.event_count - 1), part=request.get('part', 'summary'),
                offset=integer(request, 'offset', 0))
        return json_response(ctx.call(read()), accept_encoding=request.header('Accept-Encoding'))

    @router.get('/api/v1/sessions/{session_id}/trace')
    def execution_trace(request, session_id):
        from ..execution_trace import read
        return json_response(ctx.call(read(ctx.harness, session_id,
            agent_session=request.get('agent_session'), after=request.get('after'),
            limit=integer(request, 'limit', 50, maximum=20000), entry=request.get('entry'),
            part=request.get('part', 'input'), offset=integer(request, 'offset', 0))),
            accept_encoding=request.header('Accept-Encoding'))

    @router.get('/api/v1/sessions/{session_id}/requests')
    def requests(request, session_id):
        async def read():
            ctx.harness.get_session(session_id)
            archive = ctx.harness.sessions.request_traces
            return await asyncio.to_thread(archive.page, session_id,
                include_children=request.get_bool('children', True), before=integer(request, 'before'),
                limit=integer(request, 'limit', 30, maximum=20000), agent_session_id=request.get('agent_session'))
        return json_response(ctx.call(read()), accept_encoding=request.header('Accept-Encoding'))

    @router.get('/api/v1/sessions/{session_id}/requests/{trace_id}')
    def request_trace(request, session_id, trace_id):
        from ..request_traces import read_trace
        async def read():
            ctx.harness.get_session(session_id)
            return await asyncio.to_thread(read_trace, ctx.harness.sessions.request_traces, session_id, trace_id,
                part=request.get('part'), index=integer(request, 'index', 0), offset=integer(request, 'offset', 0),
                sessions=ctx.harness.sessions)
        return json_response(ctx.call(read()), accept_encoding=request.header('Accept-Encoding'))

    @router.get('/api/v1/sessions/{session_id}/requests/{trace_id}/status')
    def request_status(request, session_id, trace_id):
        async def read():
            ctx.harness.get_session(session_id)
            row = await asyncio.to_thread(ctx.harness.sessions.request_traces.metadata, session_id, trace_id)
            if row is None:
                raise NotFound(tr('python.sessions.16ee3070e2fa'))
            return row
        return json_response(ctx.call(read()), accept_encoding=request.header('Accept-Encoding'))

    @router.get('/api/v1/sessions/{session_id}/requests/{trace_id}/export')
    def export_trace(request, session_id, trace_id):
        from ..responses import content_disposition
        async def read():
            ctx.harness.get_session(session_id)
            record = await asyncio.to_thread(ctx.harness.sessions.request_traces.read, session_id, trace_id)
            if record is None:
                raise NotFound(tr('python.sessions.16ee3070e2fa'))
            return record[1]
        return text_response(ctx.call(read()), content_type='application/json; charset=utf-8',
            headers={'Content-Disposition': content_disposition('request-' + trace_id + '.json'), 'Cache-Control': 'no-store'},
            accept_encoding=request.header('Accept-Encoding'))

    @router.get('/api/v1/sessions/{session_id}/search')
    def search(request, session_id):
        from ..conversation_search import search_dialogue
        async def read():
            return await asyncio.to_thread(search_dialogue, ctx.harness.get_session(session_id), request.get('q', ''),
                                  limit=integer(request, 'limit', 50, maximum=20000), through=integer(request, 'through'),
                                  before=integer(request, 'before'))
        return json_response(ctx.call(read()), accept_encoding=request.header('Accept-Encoding'))

    @router.get('/api/v1/sessions/{session_id}/messages/{source_seq}')
    def message(request, session_id, source_seq):
        from ..conversation_search import read_dialogue
        try:
            sequence = int(source_seq)
        except ValueError as exc:
            raise BadRequest(tr('python.sessions.2c204c36499c'), code='search/invalid-position') from exc
        async def read():
            return await asyncio.to_thread(read_dialogue, ctx.harness.get_session(session_id), sequence, start=integer(request, 'start', 0))
        return json_response(ctx.call(read()), accept_encoding=request.header('Accept-Encoding'))

    @router.get('/api/v1/sessions/{session_id}/tool-results/{source_seq}')
    def tool_result(request, session_id, source_seq):
        from ..context_audit import resolve_context
        from ..tool_results import read_result
        try:
            seq = int(source_seq)
        except ValueError as exc:
            raise BadRequest(tr('python.web.validation.toolResultSequence'), code='tool-result/invalid-range') from exc
        async def read():
            session = await resolve_context(ctx.harness, session_id, request.get('agent_id'))
            return await asyncio.to_thread(read_result, session, seq, index=integer(request, 'index', 0),
                offset=integer(request, 'offset', 0), limit=integer(request, 'limit', 8192, maximum=20000),
                expected_hash=request.get('sha256'), query=request.get('q'))
        return json_response(ctx.call(read()), accept_encoding=request.header('Accept-Encoding'))

    @router.patch("/api/v1/sessions/{session_id}")
    def update_session(request: Any, session_id: str) -> Any:
        """改会话级设置：标题、模型、思考强度。

        模型存在 ``header.metadata`` 里而不是全局 config —— 会话是并发单位，
        两个项目同时跑、模型不同时，全局开关必然打架。
        """
        payload = request.json()
        unknown = sorted(set(payload) - set(SESSION_PATCH['properties']))
        if unknown:
            raise BadRequest(tr('ui.session.unknownField', field=unknown[0][:200]),
                             code='session/invalid-field', details={'field': unknown[0][:200],
                                 'copy': {'key': 'ui.session.unknownField', 'params': {'field': unknown[0][:200]}}})
        for field in ('title', 'model', 'reasoning_effort'):
            if field in payload and payload[field] is not None and not isinstance(payload[field], str):
                raise BadRequest(tr('python.sessions.invalid_field', field=field),
                                 code='session/invalid-field', details={'field': field})
        if isinstance(payload.get('title'), str) and len(payload['title']) > TITLE_MAX_LENGTH:
            raise BadRequest(tr('ui.session.titleTooLong', limit=TITLE_MAX_LENGTH), code='session/invalid-field',
                             details={'field': 'title', 'maxLength': TITLE_MAX_LENGTH,
                                      'copy': {'key': 'ui.session.titleTooLong', 'params': {'limit': TITLE_MAX_LENGTH}}})
        ctx.harness.get_session(session_id)  # 校验存在性

        metadata: dict[str, Any] = {}
        if "reasoning_effort_inherit" in payload:
            inherit = payload["reasoning_effort_inherit"]
            if not isinstance(inherit, bool):
                raise BadRequest(tr('python.sessions.1d88dbeca7c2'), code="model/invalid-inheritance")
            if inherit:
                if "reasoning_effort" in payload:
                    raise BadRequest(tr('python.sessions.eb0498c99b84'), code="model/conflicting-effort")
                # None 继承基础配置，空字符串明确省略供应商参数。
                # 这两种选择必须保持区别。
                metadata["reasoning_effort"] = None
        if "model" in payload:
            model = payload.get("model")
            if model is None or not str(model).strip():
                metadata["model"] = ""
            else:
                known = {spec["id"] for spec in model_catalog(ctx.harness.config)["models"]}
                candidate = str(model).strip()
                if candidate not in known:
                    raise BadRequest(tr('python.sessions.28af3eb8ea53', p0=f'{candidate}'), code="model/unknown")
                metadata["model"] = candidate
        if "reasoning_effort" in payload:
            effort = payload.get("reasoning_effort")
            if effort in (None, ""):
                metadata["reasoning_effort"] = ""
            elif effort not in REASONING_EFFORTS and not any(
                    effort in entry.get('reasoning_efforts', [])
                    for entry in model_catalog(ctx.harness.config)['models'] if entry['id'] == (metadata.get('model') or ctx.harness.config.model)):
                raise BadRequest(tr('python.sessions.3b5ce6b54aaf', p0=f'{effort}'), code="model/unknown-effort")
            else:
                metadata["reasoning_effort"] = effort

        changes: dict[str, Any] = {}
        title = payload.get("title")
        if isinstance(title, str) and title.strip():
            changes["title"] = title.strip()
        if metadata:
            changes["metadata"] = metadata
        if not changes:
            raise BadRequest(tr('python.sessions.53857d5bce25'), code="session/empty-patch")

        header = ctx.harness.sessions.update_header(session_id, **changes)
        # 会话可能已有活跃 AgentLoop，改完必须让它下次请求就用新配置。
        ctx.harness.refresh_session_config(session_id)
        return json_response({"session": session_header_view(header)})

    @router.get("/api/v1/sessions/{session_id}")
    def get_session(request: Any, session_id: str) -> Any:
        async def read():
            from ..context_audit import resolve_context
            session = await resolve_context(ctx.harness, session_id, request.get('agent_id'))
            result = session_detail(ctx.harness, session.id, view=request.get('view', 'full'), session=session if request.get('agent_id') else None)
            return shared_projection.detail(result) if request.subject is not None else result
        return json_response(ctx.call(read()), accept_encoding=request.header("Accept-Encoding"))

    @router.get("/api/v1/sessions/{session_id}/goal")
    def get_goal(request, session_id):
        ctx.harness.get_session(session_id)
        return json_response(ctx.harness.goals.snapshot(session_id))

    @router.get("/api/v1/sessions/{session_id}/goal/{goal_id}/evidence/{source_seq}")
    def goal_evidence(request, session_id, goal_id, source_seq):
        from ...tools.builtin.goal_evidence import evidence_page
        from ...core.errors import ToolError
        async def read():
            goal = ctx.harness.goals.get(session_id)
            if goal is None or goal.id != goal_id:
                raise NotFound(tr('python.sessions.60b7cd2dc48e'), code="goal/evidence-not-found")
            try:
                return evidence_page(ctx.harness.get_session(session_id), goal, int(source_seq),
                                     integer(request, 'offset', 0), integer(request, 'limit', 20000, maximum=20000))
            except ValueError as exc:
                raise BadRequest(tr('python.sessions.7a99397c7b45'), code="goal/evidence-invalid") from exc
            except ToolError as exc:
                if exc.code == 'GOAL_EVIDENCE_NOT_FOUND':
                    raise NotFound(str(exc), code='goal/evidence-not-found') from exc
                raise BadRequest(str(exc), code='goal/evidence-invalid') from exc
        return json_response({'evidence': ctx.call(read())})

    @router.post("/api/v1/sessions/{session_id}/goal")
    def control_goal(request, session_id):
        try:
            return json_response(ctx.call(ctx.harness.goal_runtime.control(session_id, request.json())))
        except HarnessError as exc:
            if exc.code != 'goal/control-incomplete':
                raise
            raise ServiceUnavailable(exc.message, code=exc.code, details=exc.details) from exc

    @router.delete("/api/v1/sessions/{session_id}")
    def delete_session(request: Any, session_id: str) -> Any:
        async def delete():
            await ctx.harness.delete_session_async(session_id)
            # 会话没了，在事件循环中清理补发缓冲与观察者连接。
            ctx.hub.forget(session_id)
        ctx.call(delete())
        return json_response({"deleted": session_id})

    @router.get("/api/v1/sessions/{session_id}/live-events")
    def session_events(request: Any, session_id: str) -> Any:
        """按 ``seq`` 增量拉取事件（WebSocket 不可用时的降级通道）。"""
        since = read_query(request, LIVE_EVENTS_QUERY)['since']
        ctx.harness.get_session(session_id)  # 校验存在性
        events = ctx.hub.replay(session_id, since)
        return json_response(
            {
                "session_id": session_id,
                "since": since,
                "last_seq": ctx.hub.last_seq(session_id),
                "events": [row for item in events if (row := shared_projection.frame(item)) is not None] if request.subject is not None else events,
            },
            accept_encoding=request.header("Accept-Encoding"),
        )

    @router.get("/api/v1/sessions/{session_id}/export")
    def export_session(request: Any, session_id: str) -> Any:
        """导出为 Markdown —— "复制/保存会话"按钮的服务端实现。"""
        detail = session_detail(ctx.harness, session_id)
        markdown = to_markdown(shared_projection.detail(detail) if request.subject is not None else detail)
        return text_response(
            markdown,
            content_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{session_id}.md"'},
            accept_encoding=request.header("Accept-Encoding"),
        )

    return router


def to_markdown(detail: dict[str, Any]) -> str:
    """把会话视图模型渲染成一份可读的 Markdown。"""
    header = detail.get("header") or {}
    lines: list[str] = [f"# {header.get('title') or header.get('id') or tr('python.sessions.a63280253f17')}", ""]
    meta = header.get("cwd")
    if meta:
        lines.append(tr('python.sessions.cad7707c1ef5', p0=f'{meta}'))
    lines.append("")
    for message in detail.get("messages") or []:
        role = message.get("role")
        text = message.get("text") or ""
        if role == "user":
            lines += [tr('python.sessions.8b80d4c7be28'), "", text, ""]
        elif role == "assistant":
            thinking = message.get("thinking")
            if thinking:
                lines += [tr('python.sessions.b543f86c22bd'), "", thinking, "", "</details>", ""]
            lines += [tr('python.sessions.fa0de61e2703'), "", text, ""]
        elif role == "tool":
            for result in message.get("results") or []:
                name = result.get("name", "tool")
                body = result.get("display") or result.get("output") or result.get("content") or ""
                if isinstance(body, (dict, list)):
                    import json

                    body = json.dumps(body, ensure_ascii=False, indent=2)
                lines += [f"<details><summary>🔧 {name}</summary>", "", "```", str(body), "```", "", "</details>", ""]
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["build_router", "to_markdown"]

"""从聊天的持久化团队快照整合冻结交付。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from harness.core.errors import HarnessError, SessionNotFoundError
from harness.i18n import tr
from ..errors import BadRequest, Conflict, NotFound
from ..responses import json_response
from ..router import Router
from ..team_history import latest_team_events, team_event_view


def build_router(ctx):
    router = Router()

    @router.post('/api/v1/sessions/{session_id}/team-runs/{run_id}/integration')
    def integrate(request, session_id, run_id):
        body = request.json()
        if set(body) != {'expected_seq'} or type(body['expected_seq']) is not int:
            raise BadRequest(tr('ui.git.error.invalid'), code='git/invalid')
        service = ctx.harness.worktrees
        if service is None:
            raise BadRequest(tr('python.v1.4d7f49bfcc8f'), code='git/disabled')

        async def resolve():
            try:
                session = ctx.harness.get_session(session_id)
            except SessionNotFoundError as exc:
                raise NotFound(exc.message, code=exc.code) from exc
            events = [event for event in latest_team_events(session.events) if event.data['run_id'] == run_id]
            if not events:
                raise NotFound(tr('ui.git.error.invalid'), code='agent/run-not-found')
            event = events[0]
            active = ctx.harness._active_team_runs
            data = team_event_view(event, session_id=session_id, active_runs=active)['data']
            if event.seq != body['expected_seq']:
                raise Conflict(tr('agent.integration.run_changed'), code='agent/run-changed')
            if (session_id, run_id) in active or data.get('phase') not in {'done', 'failed', 'interrupted'}:
                raise Conflict(tr('agent.integration.run_active'), code='agent/run-active')
            worktrees = data.get('worktrees')
            members = data.get('members')
            if not isinstance(worktrees, list) or not 1 <= len(worktrees) <= 8 or not isinstance(members, list):
                raise Conflict(tr('agent.integration.incomplete'), code='agent/delivery-incomplete')
            states = {member.get('name'): member for member in members if isinstance(member, dict) and isinstance(member.get('name'), str)}
            ids, names, deliveries = [], [], []
            for row in worktrees:
                if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not isinstance(row.get('team_member'), str):
                    raise Conflict(tr('agent.integration.incomplete'), code='agent/delivery-incomplete')
                state = states.get(row['team_member'], {})
                delivery = state.get('delivery')
                if state.get('status') != 'done' or not isinstance(delivery, dict) or any(not isinstance(delivery.get(key), str) or not delivery[key] for key in ('tree', 'ref', 'base_commit')):
                    raise Conflict(tr('agent.integration.incomplete'), code='agent/delivery-incomplete')
                ids.append(row['id']); names.append(row['team_member']); deliveries.append(state['delivery'])
            if len(set(ids)) != len(ids) or len(set(names)) != len(names):
                raise Conflict(tr('agent.integration.incomplete'), code='agent/delivery-incomplete')

            def create():
                # 同一进程内串行核验与创建，避免重复点击绕过候选复用检查。
                with service._mutex:
                    latest = next((item for item in latest_team_events(session.events) if item.data['run_id'] == run_id), None)
                    if latest is None or latest.seq != event.seq or (session_id, run_id) in ctx.harness._active_team_runs:
                        raise Conflict(tr('agent.integration.run_changed'), code='agent/run-changed')
                    repository = Path(service.git(Path(session.header.cwd or ctx.workspace_root()), 'rev-parse', '--show-toplevel').strip()).resolve()
                    for wid, name, delivery in zip(ids, names, deliveries):
                        row = service.get(wid)
                        if row.get('owner_session_id') != session_id or row.get('team_run_id') != run_id or row.get('team_member') != name or Path(row['repo']).resolve() != repository or row.get('delivery') != delivery or row.get('base_commit') != delivery['base_commit']:
                            raise Conflict(tr('agent.integration.delivery_changed'), code='agent/delivery-changed')
                        if not delivery['ref'].startswith('refs/harness/deliveries/' + wid + '/') or service.git(repository, 'rev-parse', '--verify', delivery['ref'] + '^{tree}').strip() != delivery['tree']:
                            raise Conflict(tr('agent.integration.delivery_changed'), code='agent/delivery-changed')
                        if delivery.get('commit') and service.git(repository, 'rev-parse', '--verify', delivery['ref'] + '^{commit}').strip() != delivery['commit']:
                            raise Conflict(tr('agent.integration.delivery_changed'), code='agent/delivery-changed')
                    expected_sources = [{'id': wid, 'tree': delivery['tree'], 'ref': delivery['ref']} for wid, delivery in zip(ids, deliveries)]
                    for candidate in service.list():
                        if candidate.get('integration') is not True or candidate.get('status') != 'active' or candidate.get('integration_status') != 'ready' or candidate.get('owner_session_id') != session_id or candidate.get('team_run_id') != run_id or candidate.get('integration_sources') != ids:
                            continue
                        inputs = candidate.get('delivery_inputs')
                        if not isinstance(inputs, dict) or not isinstance(inputs.get('sources'), list):
                            continue
                        sources = inputs['sources']
                        actual = [{key: source.get(key) for key in ('id', 'tree', 'ref')} for source in sources if isinstance(source, dict)]
                        if actual == expected_sources:
                            return {'worktree': candidate, 'reused': True}
                    return {'worktree': service.create_team_integration(ids), 'reused': False}
            return await asyncio.to_thread(create)

        try:
            return json_response(ctx.call(resolve()))
        except HarnessError as exc:
            raise Conflict(exc.message, code=exc.code, details=exc.details) from exc

    return router

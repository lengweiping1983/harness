"""唯一 Superagent 服务：来源、授权、调度、执行、交付和恢复共用生命周期。"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.errors import HarnessError
from ..i18n import tr
from ..core.language import current_language
from .superagent_common import _error, _json as strict_loads
from .superagent_coordinator_store import _hash, _json, _now
from ..session.store import _atomic_write


class SuperagentService:
    """CLI 和嵌入宿主共用同一协调服务，没有编排模式选择。"""

    @classmethod
    async def open(cls, config_path: Path, *, worker_transport=None, coordinator_transport=None):
        from .superagent_bootstrap import open_service
        return await open_service(cls, config_path, worker_transport=worker_transport,
                                  coordinator_transport=coordinator_transport)

    @classmethod
    async def open_coordinator(
        cls, state_dir: Path, *, instance_id: str, identity: object,
        executor_adapter: object, assessor: object | None = None,
        source_kinds: tuple = (), secret_resolver=None, local_identity_verifier=None,
        watchdog_limits: dict | None = None, effect_adapters: Mapping | None = None,
        interval: float = 0.5, once_timeout: float = 600,
        max_threads_per_tick: int = 16, planner: object | None = None,
        system_profiles: Mapping[str, str] | None = None,
        provider_clients: Mapping | None = None, _runtime_stack=None,
    ):
        """嵌入宿主注入原生端口，与生产 CLI 共用同一服务。"""
        if (
            isinstance(interval, bool) or not isinstance(interval, (int, float))
            or not 0.01 <= interval <= 60
            or isinstance(once_timeout, bool) or not isinstance(once_timeout, (int, float))
            or not 1 <= once_timeout <= 86400
            or type(max_threads_per_tick) is not int or not 1 <= max_threads_per_tick <= 100
            or not isinstance(source_kinds, (tuple, list))
            or effect_adapters is not None and not isinstance(effect_adapters, Mapping)
            or provider_clients is not None and not isinstance(provider_clients, Mapping)
        ):
            raise ValueError("unified SuperagentService runtime options are invalid")
        from .superagent_coordinator_host import CoordinatorHost

        host = CoordinatorHost.open(
            state_dir, instance_id=instance_id, identity=identity,
            secret_resolver=secret_resolver,
            local_identity_verifier=local_identity_verifier,
            source_kinds=tuple(source_kinds), executor_adapter=executor_adapter,
            watchdog_limits=watchdog_limits, planner=planner,
            system_profiles=system_profiles, _runtime_stack=_runtime_stack,
        )
        try:
            service = cls()
            service._coordinator_host = host
            service._coordinator_assessor = assessor
            from .superagent_delivery_runtime import CoordinatorDeliveryRuntime
            service._coordinator_delivery = CoordinatorDeliveryRuntime(
                host, provider_clients=provider_clients, closing=lambda: service.closed,
            )
            adapters = dict(effect_adapters or {})
            if "outbox.send" in adapters:
                raise ValueError(tr("superagent.invalid", field="outbox.send"))
            adapters["outbox.send"] = service._coordinator_delivery
            service._coordinator_effect_recovery = host.effect_recovery_service(
                adapters,
            )
            service._coordinator_thread_cursor = 0
            service._coordinator_max_threads_per_tick = max_threads_per_tick
            service._coordinator_last_status = None
            service.spec = {"interval": float(interval), "once_timeout": float(once_timeout)}
            service.root = host.state_dir
            service.listeners = []
            service._owned_harnesses = []
            service._production_config = None
            service._source_config_marker = None
            service._source_refresh_lock = asyncio.Lock()
            service.closed = False
            service._close_task = None
            service._tick_task = None
            service._serve_error = None
            service.owner = asyncio.get_running_loop()
            return service
        except BaseException:
            await host.aclose()
            raise

    @property
    def coordinator(self):
        if self.closed:
            raise _error("closed")
        return self._coordinator_host

    @property
    def coordinator_status(self):
        return self._coordinator_last_status

    def receive_source(self, source_id, *, headers, raw, **kwargs):
        """外部监听器只向统一来源入口提交，不创建独立收件账本。"""
        self.coordinator
        return self._coordinator_delivery.receive(source_id, headers=headers, raw=raw, **kwargs)

    def source_ingress(self, source_id):
        """为通用 WebhookServer 提供已绑定来源的接收适配。"""
        self.coordinator
        return self._coordinator_delivery.ingress(source_id)

    async def tick(self):
        if self.closed:
            raise _error("closed")
        if self.owner is not asyncio.get_running_loop():
            future = asyncio.run_coroutine_threadsafe(self.tick(), self.owner)
            return await join_cleanup(asyncio.wrap_future(future))
        if self._tick_task is None or self._tick_task.done():
            language = self._production_config['language'] if self._production_config else current_language.get()
            with current_language.set(language):
                self._tick_task = asyncio.create_task(self._tick_coordinator())
        return await join_cleanup(self._tick_task)

    async def _apply_local_requests(self):
        host = self._coordinator_host
        with host._store.transaction() as db:
            rows = db.execute("SELECT * FROM coordinator_requests WHERE state='pending' ORDER BY created_at,request_id LIMIT 17").fetchall()
        errors = []
        for row in rows[:16]:
            if _hash(row['payload_json']) != row['payload_hash']:
                raise _error('corrupt')
            payload = strict_loads(row['payload_json'])
            try:
                result = await host.execution.deliver_instruction(
                    payload['mission_id'], payload['job_id'], payload['assignment_id'], row['request_id'],
                    payload['trigger_id'], payload['text'], expected_run_id=payload['expected_run_id'],
                    expected_turn_id=payload['expected_turn_id'])
                state = 'accepted'
            except Exception as exc:
                original = host.execution.instruction_receipt(row['request_id'])
                if original is not None:
                    result, state = original, 'accepted'
                elif isinstance(exc, (HarnessError, PermissionError, LookupError, ValueError)):
                    result, state = {'code': getattr(exc, 'code', type(exc).__name__), 'detail': str(exc)}, 'rejected'
                else:
                    errors.append({'request_id': row['request_id'], 'code': type(exc).__name__})
                    continue
            with host._store.transaction(write=True) as db:
                db.execute("UPDATE coordinator_requests SET state=?,receipt_json=?,updated_at=? WHERE request_id=? AND state='pending'", (state, _json(result), _now(), row['request_id']))
        return {'backlog': len(rows) > 16, 'errors': errors}

    def _publish_status(self, status):
        if self._production_config is None:
            return
        runtime = {**status, 'instance_id': self._production_config['instance_id'], 'observed_at': _now()}
        active_runs = {}
        e = self._coordinator_host._execution_service
        for assignment in e._read_all("SELECT a.assignment_id FROM assignments a WHERE a.state IN ('accepted','running') ORDER BY assignment_id LIMIT 128"):
            current = e._assignment(assignment['assignment_id'])
            worker = e._worker_row(current['worker_id'])
            try:
                evidence = e._executor.inspect(worker, current)
                observed = {key: evidence.get(key) for key in ('run_id', 'status', 'settled', 'turn', 'unconfirmed_tools', 'unsettled_background')}
            except (HarnessError, PermissionError, ValueError):
                observed = {'run_id': current['run_id'], 'turn': None, 'status': 'unknown', 'settled': False}
            active_runs[current['assignment_id']] = observed
        runtime['active_runs'] = active_runs
        _atomic_write(self.root / 'runtime-status.json', _json(runtime))

    async def _tick_coordinator(self):
        host = self._coordinator_host
        executor=host._execution_service._executor
        remote_lease_errors=[]
        if callable(getattr(executor,'refresh_leases',None)):
            remote_lease_errors=await executor.refresh_leases()
        if self._production_config is not None:
            marker = tuple((source.source_instance_id,source.config_revision) for source in host.sources.list_instances())
            if marker != self._source_config_marker:
                await self.refresh_sources()
                self._source_config_marker = marker
        for listener in self.listeners:
            if listener['server'].errors:
                raise listener['server'].errors[0]
        local_requests = await self._apply_local_requests()
        self._coordinator_delivery.prepare_channels()
        controls = await host.execution.reconcile_controls(limit=100)
        instructions = await host.execution.reconcile_instructions(limit=100)
        threads = host.sources.list_threads(limit=1000)
        selected = []
        if threads:
            start = self._coordinator_thread_cursor % len(threads)
            selected = (threads[start:] + threads[:start])[:self._coordinator_max_threads_per_tick]
            self._coordinator_thread_cursor = (start + len(selected)) % len(threads)

        assessments = []
        routes = []
        route_errors = []
        for thread in selected:
            if self.closed:
                break
            result = await host.authorization.assess_next_async(
                thread["thread_id"], self._coordinator_assessor,
                actor={"kind": "host"}, assessor_actor={"kind": "assessor"},
            )
            assessments.append(result)

        if self.closed:
            return self._coordinator_last_status or {'runtime_state':'stopping'}

        # 已接受的 TicketInput 在暂停、撤权或进程重启后仍保留 waiting；
        # 每轮从账本重读并复用 route_trigger 的原身份幂等路径。
        route_candidates = host.execution.pending_routes(limit=101)
        route_backlog = len(route_candidates) > 100
        for item in route_candidates[:100]:
            try:
                receipt = await host.execution.route_trigger(
                    item["ticket_id"], item["trigger_id"],
                )
            except Exception as exc:
                route_errors.append({
                    "ticket_id": item["ticket_id"],
                    "trigger_id": item["trigger_id"],
                    "code": getattr(exc, "code", type(exc).__name__),
                    "detail": str(exc),
                })
            else:
                if receipt.get("state") == "attention_required":
                    route_errors.append({"ticket_id": item["ticket_id"], "trigger_id": item["trigger_id"],
                                         "code": "superagent/attention-required"})
                routes.append({
                    "ticket_id": item["ticket_id"],
                    "trigger_id": item["trigger_id"],
                    "receipt": receipt,
                })
        remaining_routes = host.execution.pending_routes(limit=101)
        pending_route_count = len(remaining_routes)
        route_backlog = route_backlog or pending_route_count > 100

        dag_scheduling = await host.execution.schedule_dag_missions(limit=100)
        reconciliation = await host.execution.reconcile_active(limit=100)
        active_states = {"submitting", "accepted", "running", "capturing", "stopping"}
        active_assignments = [
            item for item in reconciliation["assignments"]
            if item.get("state") in active_states
        ]
        unknown_assignments = [
            item for item in reconciliation["assignments"]
            if item.get("state") in {"unknown", "unavailable"}
        ]

        mission_states = host.execution.active_mission_states(limit=101)
        mission_backlog = len(mission_states) > 100
        mission_states = mission_states[:100]
        active_missions = [item["mission_id"] for item in mission_states]
        pending_missions = sum(1 for item in mission_states if item["state"] != "unknown")
        unknown_missions = sum(1 for item in mission_states if item["state"] == "unknown")
        watchdog_observations = []
        for mission_id in active_missions:
            activity = host.execution.watchdog_activity(mission_id)
            watchdog_observations.append(host.watchdog.observe(mission_id, activity=activity))
        watchdog_status = host.watchdog.status(limit=100, mission_ids=active_missions)
        watchdog_attention = (
            watchdog_status["states"]["stalled"] + watchdog_status["states"]["blocked"]
        )

        pending_triggers = blocked_triggers = 0
        unavailable_threads = {thread['thread_id'] for thread, result in zip(selected, assessments) if result.get('state') in {'unavailable', 'blocked'}}
        for thread in threads:
            triggers = host.sources.list_triggers(thread_id=thread["thread_id"], limit=1000)
            count = sum(1 for trigger in triggers if (trigger.get("disposition") or {}).get("state") == "evaluation.queued")
            pending_triggers += count
            if thread['thread_id'] in unavailable_threads:
                blocked_triggers += count

        control_pending = sum(
            1 for item in controls["missions"] if item.get("stop_state") == "pending"
        )
        control_unknown = sum(
            1 for item in controls["missions"] if item.get("stop_state") == "unknown"
        )
        control_backlog = controls["processed"] >= 100
        unavailable_assessments = sum(
            1 for item in assessments if item.get("state") == "unavailable"
        )
        blocked_assessments = sum(
            1 for item in assessments if item.get("state") == "blocked"
        )
        effect_report = None
        with host._operation():
            publication_backlog = self._coordinator_delivery.prepare_candidates()
            sending = await self._coordinator_delivery.tick()
        if self._coordinator_effect_recovery is not None:
            effect_report = asdict(await self._coordinator_effect_recovery.recover_async(limit=100))
        uncertain_effects = 0 if effect_report is None else (
            effect_report["left_unknown"] + effect_report["unavailable"] + effect_report["errors"]
        )
        status = {
            "runtime_state": "running",
            "local_requests": local_requests,
            "outbox": sending,
            "instructions": instructions,
            "remote_lease_errors": remote_lease_errors,
            "open_attentions": host._execution_service._read_one("SELECT count(*) AS n FROM attentions WHERE state='open'")["n"],
            "blocked_missions": host._execution_service._read_one(
                "SELECT count(*) AS n FROM missions m JOIN tickets t ON t.ticket_id=m.ticket_id "
                "WHERE t.state='open' AND m.state IN ('blocked','rejected','suppressed') "
                "AND NOT EXISTS(SELECT 1 FROM missions newer WHERE newer.ticket_id=m.ticket_id AND newer.sequence>m.sequence)"
            )["n"],
            "publication_backlog": publication_backlog,
            "threads_considered": len(selected),
            "evaluations": len(assessments),
            "accepted": sum(1 for item in assessments if item.get("kind") in {"accept", "reopen"}),
            "routes": routes,
            "route_errors": route_errors,
            "controls": controls,
            "control_pending": control_pending,
            "control_unknown": control_unknown,
            "control_backlog": control_backlog,
            "reconciliation": reconciliation,
            "dag_scheduling": dag_scheduling,
            "dag_scheduler_backlog": dag_scheduling.get("backlog", False),
            "dag_scheduler_errors": len(dag_scheduling.get("errors", [])),
            "active_assignments": len(active_assignments),
            "unknown_assignments": len(unknown_assignments),
            "pending_triggers": pending_triggers,
            "blocked_triggers": blocked_triggers,
            "pending_routes": pending_route_count,
            "route_backlog": route_backlog,
            "reconcile_backlog": reconciliation["processed"] >= 100,
            "active_missions": len(active_missions),
            "mission_backlog": mission_backlog,
            "pending_missions": pending_missions,
            "unknown_missions": unknown_missions,
            "watchdog": watchdog_status,
            "watchdog_attention": watchdog_attention,
            "watchdog_observations": len(watchdog_observations),
            "effect_recovery_enabled": self._coordinator_effect_recovery is not None,
            "effect_recovery": effect_report,
            "uncertain_effects": uncertain_effects,
            "unavailable_assessments": unavailable_assessments,
            "blocked_assessments": blocked_assessments,
        }
        self._coordinator_last_status = status
        self._publish_status(status)
        return status

    async def _serve_coordinator(self, stop: asyncio.Event, *, once: bool = False):
        started = time.monotonic()
        while not stop.is_set() and not self.closed:
            try:
                status = await self.tick()
            except Exception as exc:
                self._serve_error = exc
                raise
            if self.closed:
                return 0
            if once and (status["route_errors"] or status['local_requests']['errors']):
                return 2
            pending = bool(
                status["pending_triggers"] > status['blocked_triggers'] or status["pending_routes"]
                or status["route_backlog"] or status["active_assignments"]
                or status["pending_missions"] or status["mission_backlog"]
                or status["dag_scheduler_backlog"]
                or status["control_pending"] or status["control_backlog"]
                or status["reconcile_backlog"]
                or status["publication_backlog"] or status["outbox"]["backlog"]
                or status["outbox"]["pending"] > status["outbox"]["unavailable"]
                or status["instructions"]["backlog"] or status['local_requests']['backlog']
            )
            uncertain = bool(
                status["control_unknown"] or status["unknown_assignments"]
                or status["unknown_missions"] or status["watchdog_attention"]
                or status["uncertain_effects"] or status["unavailable_assessments"]
                or status["blocked_assessments"] or status["route_errors"]
                or status["dag_scheduler_errors"] or status.get("remote_lease_errors")
                or status["outbox"]["unavailable"] or status["outbox"]["unknown"]
                or status["open_attentions"] or status["blocked_missions"] or status["instructions"]["errors"]
            )
            if once and not pending:
                return 2 if uncertain else 0
            if once and time.monotonic() - started >= self.spec["once_timeout"]:
                self._serve_error = _error("once-timeout")
                raise self._serve_error
            try:
                await asyncio.wait_for(stop.wait(), self.spec["interval"])
            except TimeoutError:
                pass
        return 0

    async def _aclose_coordinator(self):
        if self.owner is not asyncio.get_running_loop():
            future = asyncio.run_coroutine_threadsafe(self._aclose_coordinator(), self.owner)
            await join_cleanup(asyncio.wrap_future(future))
            return
        if self._close_task is None:
            self.closed = True
            self._coordinator_host._execution_service.stop_admission()
            for listener in self.listeners:
                listener['server'].stop()
            self._close_task = asyncio.create_task(self._close_coordinator())
        await join_cleanup(self._close_task)

    async def _close_coordinator(self):
        errors = []
        async with self._source_refresh_lock:
            pass
        for listener in reversed(self.listeners):
            try:
                await listener['server'].aclose()
            except BaseException as exc:
                errors.append(exc)
        if self._tick_task is not None:
            try:
                await self._tick_task
            except BaseException as exc:
                errors.append(exc)
        for harness in reversed(self._owned_harnesses):
            try:
                await harness.close()
            except BaseException as exc:
                errors.append(exc)
        def final_status(host_errors):
            all_errors = [*errors, *host_errors]
            previous = self._coordinator_last_status or {}
            failure = all_errors[0] if all_errors else self._serve_error
            self._coordinator_last_status = {**previous,
                'runtime_state':'failed' if failure is not None else 'stopped',
                **({'last_error_code':getattr(failure, 'code', 'runtime/failure')} if failure is not None else {})}
            self._publish_status(self._coordinator_last_status)
        self._coordinator_host._before_release = final_status
        try:
            await self._coordinator_host.aclose()
        except BaseException as exc:
            errors.append(exc)
            self._coordinator_last_status = {**(self._coordinator_last_status or {}),
                'runtime_state':'failed', 'last_error_code':getattr(exc, 'code', 'runtime/failure')}
        raise_cleanup_errors(errors)


    async def serve(self, stop: asyncio.Event, *, once=False):
        return await self._serve_coordinator(stop, once=once)

    async def refresh_sources(self):
        """显式来源编辑后同步当前凭据和所属监听器，不重授回复权或重放输入。"""
        async with self._source_refresh_lock:
            return await self._refresh_sources()

    async def _refresh_sources(self):
        """来源监听器的创建、撤销与关闭串行化，避免并发重复绑定。"""
        if self.closed or self._production_config is None:
            raise _error('closed')
        from .superagent_bootstrap import provider_configuration
        from .superagent_http import WebhookServer
        spec = self._production_config
        clients,_ = provider_configuration(spec,self._coordinator_host._store)
        self._coordinator_delivery.clients = clients
        for listener in tuple(self.listeners):
            if not self.coordinator.sources.get_instance(listener['source_id']).enabled:
                await listener['server'].aclose()
                self.listeners.remove(listener)
        for source_id,source in spec['sources'].items():
            if source['kind']=='local' or not self.coordinator.sources.get_instance(source_id).enabled:
                continue
            if any(listener['source_id']==source_id for listener in self.listeners):
                continue
            server = WebhookServer(self.source_ingress(source_id),source['transport']['webhook_port'],spec['language'],
                path='/'+source['kind']+'/'+source_id,prefix=source['kind'],success_status=202 if source['kind']=='github' else 200)
            self.listeners.append({'source_id':source_id,'kind':source['kind'],'server':server})
            await server.start()
            if self.closed:
                await server.aclose()
                raise _error('closed')

    async def aclose(self):
        await self._aclose_coordinator()

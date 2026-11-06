"""下一代 Superagent 的宿主组合边界；不启动旧 Router 或 Harness Worker。"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Condition
from typing import Protocol

from ..core.cleanup import raise_cleanup_errors
from ..core.errors import HarnessError
from ..i18n import tr
from ..session.write_lock import event_write_lock
from .superagent_coordinator_service import CoordinatorApplicationService
from .superagent_coordinator_store import CoordinatorStore
from .superagent_effect_recovery import EffectLookupAdapter, EffectRecoveryService
from .superagent_outbox import OutboxEffectService
from .superagent_artifacts import CoordinatorArtifacts
from .superagent_authorization import CoordinatorAuthorizationEngine
from .superagent_shadow import ShadowComparisonService
from .superagent_source_registry import LocalIdentityVerifier, SecretResolver, SourceKind, SourceRegistry
from .superagent_input_delivery import CoordinatorAssignmentInputDelivery
from .superagent_mission_instruction import CoordinatorMissionInstruction
from .superagent_watchdog import CoordinatorWatchdog


_LEGACY_STATE_FILES = (
    "service.sqlite3", "inbox.sqlite3", "missions.sqlite3",
    "control.sqlite3", "watchdog.sqlite3", "status.json",
)
_LEGACY_STATE_DIRS = (
    "incoming", "receipts", "drafts", "guidance", "router-home", "worker-home",
)


def _state_dir_conflict() -> HarnessError:
    return HarnessError(tr("superagent.conflict"), code="superagent/state-dir-conflict")


def _legacy_state_present(root: Path) -> bool:
    if any((root / name).exists() or (root / name).is_symlink()
           for name in (*_LEGACY_STATE_FILES, *_LEGACY_STATE_DIRS)):
        return True
    legacy_database_markers = _LEGACY_STATE_FILES[:5]
    if any(
        (ancestor / name).exists() or (ancestor / name).is_symlink()
        for ancestor in root.parents
        for name in legacy_database_markers
    ):
        return True
    return any(root.glob("*.router.sqlite3")) or any(root.glob("*.deliveries.sqlite3"))


def _paths_overlap(left: Path, right: Path) -> bool:
    return (
        left == right
        or left.is_relative_to(right)
        or right.is_relative_to(left)
    )


class _HostAggregateRepository:
    """只向宿主开放聚合创建和读取，不暴露原始状态写入方法。"""

    __slots__ = ("__repository",)

    def __init__(self, repository):
        self.__repository = repository

    def get(self, identity: str) -> dict | None:
        return self.__repository.get(identity)

    def create(self, values: dict, **kwargs) -> dict:
        if not isinstance(values, dict):
            raise TypeError("aggregate values must be a dictionary")
        if "state" in values:
            raise PermissionError("new aggregates must use their domain initial state")
        return self.__repository.create(values, **kwargs)


class _HostPlanRepository:
    """只开放不可变 PlanRevision 的创建和读取。"""

    __slots__ = ("__repository",)

    def __init__(self, repository):
        self.__repository = repository

    def get(self, *args, **kwargs):
        return self.__repository.get(*args, **kwargs)

    def create(self, *args, **kwargs):
        return self.__repository.create(*args, **kwargs)


class _HostDependencyRepository:
    """只开放不可变 PlanRevision 依赖的读取。"""

    __slots__ = ("__repository",)

    def __init__(self, repository):
        self.__repository = repository

    def for_job(self, *args, **kwargs):
        return self.__repository.for_job(*args, **kwargs)

    def for_plan(self, *args, **kwargs):
        return self.__repository.for_plan(*args, **kwargs)


class _CoordinatorHostUnitOfWork:
    """受限的宿主工作单元视图，不暴露 SQLite 连接或内部应用服务。"""

    __slots__ = ("tickets", "missions", "jobs", "assignments", "workers", "attentions", "outbox", "plans", "dependencies")

    def __init__(self, uow):
        self.tickets = _HostAggregateRepository(uow.tickets)
        self.missions = _HostAggregateRepository(uow.missions)
        self.jobs = _HostAggregateRepository(uow.jobs)
        self.assignments = _HostAggregateRepository(uow.assignments)
        self.workers = _HostAggregateRepository(uow.workers)
        self.attentions = _HostAggregateRepository(uow.attentions)
        self.outbox = _HostAggregateRepository(uow.outbox)
        self.plans = _HostPlanRepository(uow.plans)
        self.dependencies = _HostDependencyRepository(uow.dependencies)


@dataclass(frozen=True)
class VerifiedTransitionEvidence:
    """由可信宿主回执核验器签发；绑定聚合版本，不接受模型报告直接充当 facts。"""

    aggregate_type: str
    aggregate_id: str
    target_state: str
    expected_revision: int
    facts: Mapping[str, bool]
    changes: Mapping[str, object] = field(default_factory=dict)
    audit_payload: object | None = None
    effect: Mapping[str, object] | None = None


class HostEvidenceVerifier(Protocol):
    """将原始 Harness / Provider 回执核验为绑定聚合版本的宿主事实。"""

    def verify_transition(
        self, *, aggregate_type: str, aggregate_id: str,
        target_state: str, expected_revision: int, receipt: object,
    ) -> VerifiedTransitionEvidence:
        ...


class _HostOutboxService:
    """在每次调用时重新核对宿主租约，避免缓存句柄越过关闭边界。"""

    __slots__ = ("__host", "__service")

    def __init__(self, host, service):
        self.__host = host
        self.__service = service

    def claim(self, *, owner: str, limit: int = 100, lease_until: str) -> list[dict]:
        with self.__host._operation():
            return self.__service.claim(owner=owner, limit=limit, lease_until=lease_until)

    def settle(self, **kwargs) -> bool:
        with self.__host._operation():
            return self.__service.settle(**kwargs)

    def reconcile(self, **kwargs) -> bool:
        with self.__host._operation():
            return self.__service.reconcile(**kwargs)

    def withdraw(self, message_id: str, *, expected_revision: int, actor: object) -> bool:
        with self.__host._operation():
            return self.__service.withdraw(message_id, expected_revision=expected_revision, actor=actor)


class _HostEffectRecoveryService:
    """回查同步与异步执行期间都占有宿主租约。"""

    __slots__ = ("__host", "__service")

    def __init__(self, host, service):
        self.__host = host
        self.__service = service

    def recover(self, *, limit: int = 100):
        with self.__host._operation():
            return self.__service.recover(limit=limit)

    async def recover_async(self, *, limit: int = 100):
        with self.__host._operation():
            return await self.__service.recover_async(limit=limit)


class _HostSourceRegistry:
    """每次来源注册、配置或验收都持有宿主活动租约。"""

    __slots__ = ("__host", "__service")

    def __init__(self, host, service):
        self.__host = host
        self.__service = service

    def kinds(self):
        with self.__host._operation():
            return self.__service.kinds()

    def register(self, kind: SourceKind):
        with self.__host._operation():
            unregister = self.__service.register(kind)

        def guarded_unregister():
            with self.__host._operation():
                return unregister()

        return guarded_unregister

    def create_instance(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.create_instance(*args, **kwargs)

    def update_instance(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.update_instance(*args, **kwargs)

    def get_instance(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.get_instance(*args, **kwargs)

    def list_instances(self):
        with self.__host._operation():
            return self.__service.list_instances()

    def get_trigger(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.get_trigger(*args, **kwargs)

    def list_triggers(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.list_triggers(*args, **kwargs)

    def get_thread(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.get_thread(*args, **kwargs)

    def list_threads(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.list_threads(*args, **kwargs)

    def get_channel(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.get_channel(*args, **kwargs)

    def list_channels(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.list_channels(*args, **kwargs)

    def get_cursor(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.get_cursor(*args, **kwargs)

    def advance_cursor(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.advance_cursor(*args, **kwargs)

    def receive(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.receive(*args, **kwargs)

    def receive_local(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.receive_local(*args, **kwargs)


class _HostAuthorizationEngine:
    """授权写操作和评估读取都不能越过宿主关闭边界。"""

    __slots__ = ("__host", "__service")

    def __init__(self, host, service):
        self.__host = host
        self.__service = service

    def publish_policy(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.publish_policy(*args, **kwargs)

    def current_policy(self):
        with self.__host._operation():
            return self.__service.current_policy()

    def begin_evaluation(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.begin_evaluation(*args, **kwargs)

    def complete_evaluation(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.complete_evaluation(*args, **kwargs)

    def assess_next(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.assess_next(*args, **kwargs)

    async def assess_next_async(self, *args, **kwargs):
        with self.__host._operation():
            return await self.__service.assess_next_async(*args, **kwargs)

    def requeue_unavailable(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.requeue_unavailable(*args, **kwargs)

    def subscribe(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.subscribe(*args, **kwargs)

    def unsubscribe(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.unsubscribe(*args, **kwargs)

    def handback(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.handback(*args, **kwargs)

    def revalidate_mandate(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.revalidate_mandate(*args, **kwargs)

    def policy_revision(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.policy_revision(*args, **kwargs)

    def list_policy_revisions(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.list_policy_revisions(*args, **kwargs)

    def evaluation(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.evaluation(*args, **kwargs)

    def list_evaluations(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.list_evaluations(*args, **kwargs)

    def decision(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.decision(*args, **kwargs)

    def list_decisions(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.list_decisions(*args, **kwargs)

    def mandate(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.mandate(*args, **kwargs)

    def list_mandates(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.list_mandates(*args, **kwargs)

    def ticket(self, *args, **kwargs):
        with self.__host._operation():
            return self.__service.ticket(*args, **kwargs)


class _HostShadowComparison:
    """Shadow 只读扫描期间保留宿主实例租约。"""

    __slots__ = ("__host", "__service")

    def __init__(self, host, service):
        self.__host = host
        self.__service = service

    def compare(self, **kwargs):
        with self.__host._operation():
            return self.__service.compare(**kwargs)


class _HostExecutionService:
    __slots__ = ("__host", "__service", "__input_delivery", "__mission_instruction")
    def __init__(self, host, service, input_delivery, mission_instruction):
        self.__host, self.__service = host, service
        self.__input_delivery, self.__mission_instruction = input_delivery, mission_instruction
    async def route_trigger(self, ticket_id, trigger_id):
        with self.__host._operation(): return await self.__service.route_trigger(ticket_id, trigger_id)
    async def reconcile(self, assignment_id):
        with self.__host._operation(): return await self.__service.reconcile(assignment_id)
    async def reconcile_ticket(self, ticket_id):
        with self.__host._operation(): return await self.__service.reconcile_ticket(ticket_id)
    async def schedule_dag_missions(self, *, limit=100):
        with self.__host._operation():
            return await self.__service.schedule_dag_missions(limit=limit)
    def pending_routes(self, *, limit=100):
        with self.__host._operation():
            return self.__service.pending_routes(limit=limit)
    async def reconcile_active(self, *, limit=100):
        with self.__host._operation():
            return await self.__service.reconcile_active(limit=limit)
    def active_mission_states(self, *, limit=100):
        with self.__host._operation():
            return self.__service.active_mission_states(limit=limit)
    def active_missions(self, *, limit=100):
        with self.__host._operation():
            return self.__service.active_missions(limit=limit)
    def watchdog_activity(self, mission_id):
        with self.__host._operation():
            return self.__service.watchdog_activity(mission_id)
    async def deliver_input_to_assignment(self, input_id, assignment_id):
        with self.__host._operation():
            return await self.__input_delivery.deliver(input_id, assignment_id)
    async def reconcile_input_delivery(self, input_id, assignment_id):
        with self.__host._operation():
            return await self.__input_delivery.reconcile(input_id, assignment_id)
    async def deliver_instruction(self, mission_id, job_id, assignment_id, request_id, trigger_id, text):
        with self.__host._operation():
            return await self.__mission_instruction.deliver(
                mission_id, job_id, assignment_id, request_id, trigger_id, text,
            )
    async def reconcile_instruction(self, request_id):
        with self.__host._operation():
            return await self.__mission_instruction.reconcile(request_id)
    def instruction_receipt(self, request_id):
        with self.__host._operation():
            return self.__mission_instruction.receipt(request_id)
    async def stop(self, assignment_id, *, reason):
        with self.__host._operation(): return await self.__service.stop(assignment_id, reason=reason)
    def report(self, assignment_id):
        with self.__host._operation(): return self.__service.report(assignment_id)
    def list_workers(self, ticket_id):
        with self.__host._operation(): return self.__service.list_workers(ticket_id)
    def list_attentions(self, ticket_id, *, state=None):
        with self.__host._operation(): return self.__service.list_attentions(ticket_id, state=state)
    def resolve_attention(self, attention_id, answer_trigger_id):
        with self.__host._operation(): return self.__service.resolve_attention(attention_id, answer_trigger_id)
    def outbox_body(self, message_id):
        with self.__host._operation(): return self.__service.outbox_body(message_id)
    def control_mission(self, mission_id, action, *, request_id, actor):
        with self.__host._operation():
            return self.__service.control_mission(mission_id, action, request_id=request_id, actor=actor)
    def control_receipt(self, request_id):
        with self.__host._operation(): return self.__service.control_receipt(request_id)
    def mission_control(self, mission_id):
        with self.__host._operation(): return self.__service.mission_control(mission_id)
    def control_status(self, mission_id):
        with self.__host._operation(): return self.__service.control_status(mission_id)
    async def reconcile_controls(self, *, limit=100):
        with self.__host._operation():
            return await self.__service.reconcile_controls(limit=limit)


class _HostArtifactsService:
    __slots__ = ("__host", "__service")

    def __init__(self, host, service):
        self.__host, self.__service = host, service

    def get(self, assignment_id):
        with self.__host._operation():
            return self.__service.get(assignment_id)

    async def capture(self, assignment, worker):
        with self.__host._operation():
            return await self.__service.capture(assignment, worker)

    async def deliver(self, assignment, worker, artifacts):
        with self.__host._operation():
            return await self.__service.deliver(assignment, worker, artifacts)



class _HostWatchdogService:
    """每次观察和读取都持有协调宿主活动租约。"""

    __slots__ = ("__host", "__service")

    def __init__(self, host, service):
        self.__host = host
        self.__service = service

    def observe(self, mission_id, *, activity=None, now=None):
        with self.__host._operation():
            return self.__service.observe(mission_id, activity=activity, now=now)

    def get(self, mission_id):
        with self.__host._operation():
            return self.__service.get(mission_id)

    def status(self, *, limit=32, mission_ids=None):
        with self.__host._operation():
            return self.__service.status(limit=limit, mission_ids=mission_ids)


class CoordinatorHost:
    """下一代 Superagent 的宿主组合边界。

    组合 Source Registry、Authorization Engine 与可选的显式 Shadow 审计；
    默认不依赖旧账本、不创建旧版 Harness / Router / Worker，也不注册生产路由。
    """

    def __init__(
        self, state_dir: Path, store: CoordinatorStore, stack: ExitStack,
        *, instance_id: str, legacy_state_dir: Path | None = None, evidence_verifier: HostEvidenceVerifier | None = None,
        secret_resolver: SecretResolver | None = None,
        local_identity_verifier: LocalIdentityVerifier | None = None,
        source_kinds: tuple[SourceKind, ...] = (), executor_adapter: object | None = None,
        watchdog_limits: dict | None = None, planner: object | None = None,
    ):
        if executor_adapter is not None and getattr(executor_adapter, "instance_id", None) != instance_id:
            raise ValueError("ExecutorAdapter instance_id must match CoordinatorHost")
        self.state_dir = state_dir
        self._legacy_state_dir = legacy_state_dir
        self._store = store
        self._stack = stack
        self._evidence_verifier = evidence_verifier
        self._lifecycle_lock = Condition()
        self._active_operations = 0
        self._closing = False
        self._closed = False
        self._close_task = None
        self._outbox = _HostOutboxService(self, OutboxEffectService(store))
        self._watchdog_service = CoordinatorWatchdog(store, limits=watchdog_limits)
        self._watchdog = _HostWatchdogService(self, self._watchdog_service)
        self._authorization_service = CoordinatorAuthorizationEngine(store)
        self._source_service = SourceRegistry(
            store, ingest=self._authorization_service.receive_normalized,
            on_config_changed=self._authorization_service.revalidate_source_mandates_in_transaction,
            on_kind_changed=self._authorization_service.revalidate_source_kind_mandates_in_transaction,
            initial_kinds=source_kinds,
            secret_resolver=secret_resolver, local_identity_verifier=local_identity_verifier,
        )
        self._shadow_service = (
            ShadowComparisonService(store, legacy_state_dir)
            if legacy_state_dir is not None else None
        )
        self._sources = _HostSourceRegistry(self, self._source_service)
        self._authorization = _HostAuthorizationEngine(self, self._authorization_service)
        self._shadow = (
            _HostShadowComparison(self, self._shadow_service)
            if self._shadow_service is not None else None
        )
        self._execution_service = None
        self._input_delivery_service = None
        self._mission_instruction_service = None
        self._artifacts_service = None
        self._execution = None
        self._artifacts = None
        if executor_adapter is not None:
            from .superagent_execution import CoordinatorExecutionService
            self._execution_service = CoordinatorExecutionService(
                store, self._authorization_service, self._source_service, executor_adapter,
                instance_id=instance_id, planner=planner,
            )
            self._input_delivery_service = CoordinatorAssignmentInputDelivery(
                store, self._authorization_service, self._source_service, executor_adapter,
                admit_mission=self._execution_service._admit_mission,
            )
            self._mission_instruction_service = CoordinatorMissionInstruction(
                store, self._authorization_service, self._source_service, executor_adapter,
                admit_mission=self._execution_service._admit_mission,
            )
            self._artifacts_service = CoordinatorArtifacts(
                store, executor_adapter, self._execution_service._admit_mission,
            )
            self._execution_service._dag_scheduler.set_artifacts(self._artifacts_service)
            self._execution = _HostExecutionService(
                self, self._execution_service, self._input_delivery_service,
                self._mission_instruction_service,
            )
            self._artifacts = _HostArtifactsService(self, self._artifacts_service)

    @classmethod
    def open(
        cls, state_dir: str | Path, *, instance_id: str, identity: object,
        legacy_state_dir: str | Path | None = None,
        evidence_verifier: HostEvidenceVerifier | None = None,
        secret_resolver: SecretResolver | None = None,
        local_identity_verifier: LocalIdentityVerifier | None = None,
        source_kinds: tuple[SourceKind, ...] = (), executor_adapter: object | None = None,
        watchdog_limits: dict | None = None, planner: object | None = None,
    ) -> "CoordinatorHost":
        requested = Path(state_dir).expanduser()
        is_junction = getattr(requested, "is_junction", None)
        if requested.is_symlink() or (callable(is_junction) and is_junction()):
            raise _state_dir_conflict()
        root = requested.resolve()
        if root.exists() and not root.is_dir():
            raise ValueError("Coordinator state_dir must be a directory")
        legacy = Path(legacy_state_dir).expanduser().resolve() if legacy_state_dir is not None else None
        if legacy is not None and _paths_overlap(root, legacy):
            raise _state_dir_conflict()
        # 先于创建锁文件拒绝已存在的 v1 目录，保证失败探测不改变旧账本目录。
        if _legacy_state_present(root):
            raise _state_dir_conflict()

        root.mkdir(parents=True, exist_ok=True)
        stack = ExitStack()
        try:
            # 与 v1 SuperagentService 共用实例锁，禁止同一目录双宿主并发启动。
            try:
                stack.enter_context(event_write_lock(root / "superagent-runtime"))
            except OSError as exc:
                raise HarnessError(
                    tr("superagent.runtime-busy"), code="superagent/runtime-busy",
                ) from exc
            if _legacy_state_present(root):
                raise _state_dir_conflict()
            store = CoordinatorStore(
                root / "superagent.sqlite3", instance_id=instance_id, identity=identity,
            )
            return cls(
                root, store, stack, instance_id=instance_id, legacy_state_dir=legacy, evidence_verifier=evidence_verifier,
                secret_resolver=secret_resolver, local_identity_verifier=local_identity_verifier,
                source_kinds=source_kinds, executor_adapter=executor_adapter,
                watchdog_limits=watchdog_limits, planner=planner,
            )
        except BaseException:
            if executor_adapter is not None:
                close = getattr(executor_adapter, "close", None)
                if callable(close):
                    close()
            stack.close()
            raise

    @property
    def database_path(self) -> Path:
        return self._store.path

    @property
    def outbox(self) -> _HostOutboxService:
        self._ensure_open()
        return self._outbox

    @property
    def sources(self) -> _HostSourceRegistry:
        self._ensure_open()
        return self._sources

    @property
    def authorization(self) -> _HostAuthorizationEngine:
        self._ensure_open()
        return self._authorization

    @property
    def shadow(self) -> _HostShadowComparison:
        self._ensure_open()
        if self._shadow is None:
            raise HarnessError(tr("superagent.conflict"), code="superagent/shadow-unavailable")
        return self._shadow

    @property
    def execution(self):
        self._ensure_open()
        if self._execution is None:
            raise HarnessError(tr("superagent.conflict"), code="superagent/executor-unavailable")
        return self._execution

    @property
    def artifacts(self) -> _HostArtifactsService:
        self._ensure_open()
        if self._artifacts is None:
            raise HarnessError(tr("superagent.conflict"), code="superagent/artifact-unavailable")
        return self._artifacts

    @property
    def watchdog(self) -> _HostWatchdogService:
        self._ensure_open()
        return self._watchdog

    @contextmanager
    def unit_of_work(self, *, actor: object, correlation_id: str | None = None):
        """只暴露聚合创建/读取；状态变化必须经过绑定回执和 Revision 的 transition。"""
        with self._operation():
            with self._store.unit_of_work(actor=actor, correlation_id=correlation_id) as uow:
                yield _CoordinatorHostUnitOfWork(uow)

    def transition(
        self, aggregate_type: str, identity: str, *, expected_revision: int,
        target_state: str, receipt: object, actor: object,
        correlation_id: str | None = None,
    ) -> dict | None:
        """持有宿主租约完成回执核验、CAS、审计和 Effect 入队。"""
        with self._operation():
            return self._transition(
                aggregate_type, identity, expected_revision=expected_revision,
                target_state=target_state, receipt=receipt, actor=actor,
                correlation_id=correlation_id,
            )

    def _transition(
        self, aggregate_type: str, identity: str, *, expected_revision: int,
        target_state: str, receipt: object, actor: object,
        correlation_id: str | None = None,
    ) -> dict | None:
        """只接受宿主核验器签发、绑定当前聚合 Revision 的 facts。"""
        self._ensure_open()
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        verifier = self._evidence_verifier
        if verifier is None:
            raise HarnessError(
                tr("superagent.conflict"), code="superagent/host-evidence-unavailable",
            )
        verified = verifier.verify_transition(
            aggregate_type=aggregate_type, aggregate_id=identity,
            target_state=target_state, expected_revision=expected_revision, receipt=receipt,
        )
        if not isinstance(verified, VerifiedTransitionEvidence):
            raise PermissionError("host evidence verifier must return VerifiedTransitionEvidence")
        if (
            verified.aggregate_type != aggregate_type
            or verified.aggregate_id != identity
            or verified.target_state != target_state
            or type(verified.expected_revision) is not int
            or verified.expected_revision != expected_revision
        ):
            raise PermissionError("verified host evidence identity or revision does not match the requested transition")
        if (
            not isinstance(verified.facts, Mapping)
            or any(not isinstance(key, str) or type(value) is not bool
                   for key, value in verified.facts.items())
            or not isinstance(verified.changes, Mapping)
            or verified.effect is not None and not isinstance(verified.effect, Mapping)
        ):
            raise TypeError("verified host evidence contains invalid facts, changes or effect")
        with self._store.unit_of_work(actor=actor, correlation_id=correlation_id) as uow:
            service = CoordinatorApplicationService(uow)
            return service.transition(
                aggregate_type, identity, expected_revision=expected_revision,
                target_state=target_state, facts=dict(verified.facts),
                changes=dict(verified.changes), audit_payload=verified.audit_payload,
                effect=None if verified.effect is None else dict(verified.effect),
            )

    def effect_recovery_service(
        self, adapters: Mapping[str, EffectLookupAdapter],
    ) -> _HostEffectRecoveryService:
        self._ensure_open()
        return _HostEffectRecoveryService(self, EffectRecoveryService(self._store, adapters))

    @contextmanager
    def _operation(self):
        """在宿主操作期间保留实例租约；关闭不能越过在途事务或回查。"""
        with self._lifecycle_lock:
            self._ensure_open()
            self._active_operations += 1
        try:
            yield
        finally:
            with self._lifecycle_lock:
                self._active_operations -= 1
                if self._active_operations == 0:
                    self._lifecycle_lock.notify_all()

    def _ensure_open(self) -> None:
        if self._closed or self._closing:
            raise HarnessError(tr("superagent.conflict"), code="superagent/closed")

    def _wait_for_idle(self) -> None:
        with self._lifecycle_lock:
            while self._active_operations:
                self._lifecycle_lock.wait()

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            if self._closing or self._active_operations:
                raise HarnessError(
                    tr("superagent.runtime-busy"), code="superagent/runtime-busy",
                )
            self._closing = True
        errors = []
        if self._execution_service is not None:
            try:
                self._execution_service.close()
            except BaseException as exc:
                errors.append(exc)
        try:
            self._stack.close()
        except BaseException as exc:
            errors.append(exc)
        finally:
            with self._lifecycle_lock:
                self._closed = True
                self._closing = False
                self._lifecycle_lock.notify_all()
        raise_cleanup_errors(errors)

    async def aclose(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lifecycle_lock:
            task = self._close_task
            if task is None:
                if self._closed:
                    return
                if self._closing:
                    raise HarnessError(
                        tr("superagent.runtime-busy"), code="superagent/runtime-busy",
                    )
                self._closing = True
                task = loop.create_task(self._aclose_impl(), name="superagent-host-close")
                self._close_task = task
            elif task.get_loop() is not loop:
                raise HarnessError(
                    tr("superagent.runtime-busy"), code="superagent/runtime-busy",
                )
        await asyncio.shield(task)

    async def _aclose_impl(self) -> None:
        errors = []
        try:
            await asyncio.to_thread(self._wait_for_idle)
            if self._execution_service is not None:
                try:
                    await self._execution_service.aclose()
                except BaseException as exc:
                    errors.append(exc)
            try:
                self._stack.close()
            except BaseException as exc:
                errors.append(exc)
        finally:
            with self._lifecycle_lock:
                self._closed = True
                self._closing = False
                self._lifecycle_lock.notify_all()
        raise_cleanup_errors(errors)

    def __enter__(self) -> "CoordinatorHost":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.close()
        return False

    async def __aenter__(self) -> "CoordinatorHost":
        self._ensure_open()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        await self.aclose()
        return False

"""下一代 Superagent 的宿主组合边界；不启动旧 Router 或 Harness Worker。"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Protocol

from ..core.errors import HarnessError
from ..i18n import tr
from ..session.write_lock import event_write_lock
from .superagent_coordinator_service import CoordinatorApplicationService
from .superagent_coordinator_store import CoordinatorStore
from .superagent_effect_recovery import EffectLookupAdapter, EffectRecoveryService
from .superagent_outbox import OutboxEffectService


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

    __slots__ = ("tickets", "missions", "jobs", "assignments", "outbox", "plans", "dependencies")

    def __init__(self, uow):
        self.tickets = _HostAggregateRepository(uow.tickets)
        self.missions = _HostAggregateRepository(uow.missions)
        self.jobs = _HostAggregateRepository(uow.jobs)
        self.assignments = _HostAggregateRepository(uow.assignments)
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


class CoordinatorHost:
    """Phase 1 Runtime Composition Root；只管理新账本和宿主证据入口。

    不创建 Harness、Session、Worker、Router、Source 或 Channel；
    也不注册 CLI / HTTP 路由。真实 Executor / Provider 核验器由后续阶段注入。
    """

    def __init__(
        self, state_dir: Path, store: CoordinatorStore, stack: ExitStack,
        *, evidence_verifier: HostEvidenceVerifier | None = None,
    ):
        self.state_dir = state_dir
        self._store = store
        self._stack = stack
        self._evidence_verifier = evidence_verifier
        self._lifecycle_lock = Lock()
        self._active_operations = 0
        self._outbox = _HostOutboxService(self, OutboxEffectService(store))
        self._closed = False

    @classmethod
    def open(
        cls, state_dir: str | Path, *, instance_id: str, identity: object,
        legacy_state_dir: str | Path,
        evidence_verifier: HostEvidenceVerifier | None = None,
    ) -> "CoordinatorHost":
        requested = Path(state_dir).expanduser()
        is_junction = getattr(requested, "is_junction", None)
        if requested.is_symlink() or (callable(is_junction) and is_junction()):
            raise _state_dir_conflict()
        root = requested.resolve()
        if root.exists() and not root.is_dir():
            raise ValueError("Coordinator state_dir must be a directory")
        legacy = Path(legacy_state_dir).expanduser().resolve()
        if _paths_overlap(root, legacy):
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
            return cls(root, store, stack, evidence_verifier=evidence_verifier)
        except BaseException:
            stack.close()
            raise

    @property
    def database_path(self) -> Path:
        return self._store.path

    @property
    def outbox(self) -> _HostOutboxService:
        self._ensure_open()
        return self._outbox

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

    def _ensure_open(self) -> None:
        if self._closed:
            raise HarnessError(tr("superagent.conflict"), code="superagent/closed")

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            if self._active_operations:
                raise HarnessError(
                    tr("superagent.runtime-busy"), code="superagent/runtime-busy",
                )
            self._closed = True
            self._stack.close()

    def __enter__(self) -> "CoordinatorHost":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.close()
        return False

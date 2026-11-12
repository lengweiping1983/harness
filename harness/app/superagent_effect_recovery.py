"""安全的 Effect 结果回查编排；只查询外部结果，不发送或重放副作用。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
import json
from typing import Awaitable, Literal, Mapping, Protocol

from .superagent_coordinator_store import CoordinatorStore, _json
from .superagent_outbox import OutboxEffectService


@dataclass(frozen=True)
class EffectLookupResult:
    """Provider 对原幂等键的回查结论。"""

    status: Literal["applied", "not_applied", "permanent_rejection", "indeterminate"]
    receipt: object | None = None
    retry_safe: bool = False
    detail: str | None = None


class EffectLookupAdapter(Protocol):
    """只读 Provider Adapter；禁止在 lookup 中执行发送操作。"""

    def lookup(
        self, *, effect: Mapping[str, object],
    ) -> EffectLookupResult | Awaitable[EffectLookupResult]:
        ...


@dataclass(frozen=True)
class EffectRecoveryReport:
    """looked_up 统计 Adapter 调用尝试；unavailable 统计缺失 Adapter。"""

    marked_unknown: int
    looked_up: int
    completed: int
    retry_wait: int
    failed: int
    left_unknown: int
    unavailable: int
    errors: int
    stale: int


class _AsyncAdapterRequired(RuntimeError):
    """同步入口遇到异步 Adapter 时，提示调用方改用 recover_async。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EffectRecoveryService:
    """把过期租约标记为 unknown，并通过注入的只读 Adapter 回查原操作。"""

    def __init__(self, store: CoordinatorStore, adapters: Mapping[str, EffectLookupAdapter]):
        self._store = store
        self._adapters = dict(adapters)
        self._cursor = ("", "")

    def _record_unknown_lookup_event(
        self, effect_id: str, generation: int, *, event_type: str, payload: dict,
    ) -> bool:
        """仅在原 Effect 仍为同一代 unknown 时记录回查结果。"""
        with self._store.transaction(write=True) as db:
            current = db.execute(
                "SELECT * FROM effects WHERE effect_id=? AND state='unknown' AND generation=?",
                (effect_id, generation),
            ).fetchone()
            if current is None:
                return False
            event_payload = {"generation": generation, **payload}
            previous = db.execute(
                "SELECT event_type,payload_json FROM audit_events WHERE event_id=?",
                (current["last_event_id"],),
            ).fetchone()
            if (
                previous is not None
                and previous["event_type"] == event_type
                and previous["payload_json"] == _json(event_payload)
            ):
                return True
            CoordinatorStore._append_effect_event(
                db, current, event_type=event_type,
                payload=event_payload,
                actor={"kind": "system", "component": "durable-effect-recovery"},
            )
            return True

    def _start(self, limit: int):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._store.transaction(write=True) as db:
            expired = self._store.mark_expired_effects_unknown(db, limit=limit)
            rows = db.execute(
                "SELECT * FROM effects WHERE state='unknown' "
                "ORDER BY (created_at,effect_id) <= (?,?),created_at,effect_id LIMIT ?",
                (*self._cursor, limit),
            ).fetchall()
            if rows:
                self._cursor = (rows[-1]["created_at"], rows[-1]["effect_id"])
        return expired, rows

    @staticmethod
    def _decode_effect(row) -> dict:
        """严格读取持久化的原始请求和回执，不修复损坏数据。"""
        effect = dict(row)
        try:
            effect["payload"] = json.loads(effect.pop("payload_json"))
            raw_receipt = effect.pop("receipt_json")
            effect["receipt"] = None if raw_receipt is None else json.loads(raw_receipt)
        except (TypeError, ValueError) as exc:
            raise ValueError("persisted Effect payload or receipt is corrupt") from exc
        if not isinstance(effect["payload"], dict):
            raise ValueError("persisted Effect payload must be an object")
        return effect

    def _apply_result(self, effect: dict, result: object) -> str:
        """仅在 Provider 证据充分且 generation 未变化时提交结算。"""
        if not isinstance(result, EffectLookupResult):
            raise TypeError("lookup adapter must return EffectLookupResult")

        if result.status == "applied":
            if result.receipt is None:
                raise ValueError("applied lookup must include a provider receipt")
            state, error, due_at = "completed", None, None
        elif result.status == "not_applied":
            if result.retry_safe is not True:
                return self._record_observation(
                    effect, "effect.lookup_indeterminate",
                    {"status": "not_applied_retry_not_safe"},
                )
            if result.receipt is None:
                raise ValueError("retry-safe lookup must include a provider receipt")
            state = "retry_wait"
            error = result.detail or "provider confirmed the operation was not applied; retry is safe"
            due_at = _now()
        elif result.status == "permanent_rejection":
            if result.receipt is None:
                raise ValueError("permanent rejection lookup must include a provider receipt")
            state = "failed"
            error = result.detail or "provider confirmed permanent rejection"
            due_at = None
        elif result.status == "indeterminate":
            return self._record_observation(
                effect, "effect.lookup_indeterminate", {"status": "indeterminate"},
            )
        else:
            raise ValueError("lookup adapter returned an unsupported status")

        if effect["kind"] == "outbox.send":
            changed = OutboxEffectService(self._store).reconcile(
                effect_id=effect["effect_id"], generation=effect["generation"],
                state=state, receipt=result.receipt, error=error, due_at=due_at,
            )
        else:
            with self._store.transaction(write=True) as db:
                changed = self._store.reconcile_effect(
                    db, effect_id=effect["effect_id"], generation=effect["generation"],
                    state=state, receipt=result.receipt, error=error, due_at=due_at,
                )
        return state if changed else "stale"

    async def lookup_original(self, effect):
        """显式人工核实只读取指定原操作，不扫描其他目标或提交写入。"""
        adapter = self._adapters.get(effect['kind'])
        if adapter is None:
            return EffectLookupResult('indeterminate',detail='lookup_unavailable')
        result = adapter.lookup(effect=effect)
        return await result if inspect.isawaitable(result) else result

    def _record_observation(self, effect: dict, event_type: str, payload: dict) -> str:
        return "left_unknown" if self._record_unknown_lookup_event(
            effect["effect_id"], effect["generation"],
            event_type=event_type, payload=payload,
        ) else "stale"

    def _record_error(self, effect: dict, error: Exception) -> str:
        return self._record_observation(
            effect, "effect.lookup_error", {"error_type": type(error).__name__},
        )

    @staticmethod
    def _counters() -> dict[str, int]:
        return {
            "looked_up": 0, "completed": 0, "retry_wait": 0, "failed": 0,
            "left_unknown": 0, "unavailable": 0, "errors": 0, "stale": 0,
        }

    @staticmethod
    def _report(expired, counters: dict[str, int]) -> EffectRecoveryReport:
        return EffectRecoveryReport(
            marked_unknown=len(expired), looked_up=counters["looked_up"],
            completed=counters["completed"], retry_wait=counters["retry_wait"],
            failed=counters["failed"], left_unknown=counters["left_unknown"],
            unavailable=counters["unavailable"], errors=counters["errors"],
            stale=counters["stale"],
        )

    def _record_missing_adapter(self, effect: dict, counters: dict[str, int]) -> None:
        counters["unavailable"] += 1
        outcome = self._record_observation(
            effect, "effect.lookup_unavailable", {"reason": "adapter_missing"},
        )
        counters[outcome] += 1

    def recover(self, *, limit: int = 100) -> EffectRecoveryReport:
        """同步回查入口；异步 Provider Adapter 必须使用 recover_async。"""
        expired, rows = self._start(limit)
        counters = self._counters()
        for row in rows:
            effect = dict(row)
            adapter = self._adapters.get(effect["kind"])
            if adapter is None:
                self._record_missing_adapter(effect, counters)
                continue
            try:
                effect = self._decode_effect(row)
                counters["looked_up"] += 1
                result = adapter.lookup(effect=effect)
                if inspect.isawaitable(result):
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()
                    raise _AsyncAdapterRequired(
                        "asynchronous Provider Adapter requires recover_async()"
                    )
                outcome = self._apply_result(effect, result)
                counters[outcome] += 1
            except _AsyncAdapterRequired:
                raise
            except Exception as exc:
                counters["errors"] += 1
                outcome = self._record_error(effect, exc)
                counters[outcome] += 1
        return self._report(expired, counters)

    async def recover_async(self, *, limit: int = 100) -> EffectRecoveryReport:
        """异步回查入口；等待 Provider 只读 HTTP 请求，不阻塞宿主事件循环。"""
        expired, rows = self._start(limit)
        counters = self._counters()
        for row in rows:
            effect = dict(row)
            adapter = self._adapters.get(effect["kind"])
            if adapter is None:
                self._record_missing_adapter(effect, counters)
                continue
            try:
                effect = self._decode_effect(row)
                counters["looked_up"] += 1
                result = adapter.lookup(effect=effect)
                if inspect.isawaitable(result):
                    result = await result
                outcome = self._apply_result(effect, result)
                counters[outcome] += 1
            except Exception as exc:
                counters["errors"] += 1
                outcome = self._record_error(effect, exc)
                counters[outcome] += 1
        return self._report(expired, counters)

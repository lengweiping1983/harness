"""下一代 Superagent 宿主组合与旧账本隔离回归。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import sqlite3
from threading import Event, Thread
import tempfile
from contextlib import closing
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.app.superagent_coordinator_host import (
    CoordinatorHost, VerifiedTransitionEvidence,
)
from harness.app.superagent_coordinator_service import CoordinatorApplicationService
from harness.app.superagent_effect_recovery import EffectLookupResult
from harness.core.errors import HarnessError


@dataclass(frozen=True)
class _HarnessReceipt:
    reference: str


class _Verifier:
    def __init__(self, revision_offset=0):
        self.revision_offset = revision_offset

    def verify_transition(
        self, *, aggregate_type, aggregate_id, target_state, expected_revision, receipt,
    ):
        if not isinstance(receipt, _HarnessReceipt):
            raise PermissionError("model output is not a host receipt")
        return VerifiedTransitionEvidence(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            target_state=target_state,
            expected_revision=expected_revision + self.revision_offset,
            facts={"authorized": True, "human_stop": True},
            changes={
                "outcome": "stopped", "ended_by": "human",
                "ended_at": "2026-11-04T16:00:00Z",
            },
            audit_payload={"host_receipt_ref": receipt.reference},
        )


class CoordinatorHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def open_host(self, path=None, **kwargs):
        return CoordinatorHost.open(
            path or self.root / "v2-state",
            instance_id="v2-instance", identity={"profile": "coordinator"},
            **kwargs,
        )

    def test_host_owns_a_dedicated_database_and_shared_instance_lock(self):
        host = self.open_host()
        self.addCleanup(host.close)
        self.assertEqual(host.database_path, (self.root / "v2-state" / "superagent.sqlite3").resolve())
        self.assertTrue(host.database_path.is_file())
        self.assertTrue((host.state_dir / "superagent-runtime.lock").exists())
        names = {path.name for path in host.state_dir.iterdir()}
        self.assertFalse(names.intersection({
            "service.sqlite3", "inbox.sqlite3", "missions.sqlite3",
            "control.sqlite3", "watchdog.sqlite3", "incoming", "receipts",
            "drafts", "guidance", "router-home", "worker-home",
        }))
        with self.assertRaises(HarnessError) as caught:
            self.open_host()
        self.assertEqual(caught.exception.code, "superagent/runtime-busy")
        host.close()
        reopened = self.open_host()
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.database_path, host.database_path)

    def test_host_does_not_require_legacy_state_or_assemble_shadow_by_default(self):
        host = self.open_host()
        self.addCleanup(host.close)
        self.assertFalse((self.root / "legacy-v1").exists())
        self.assertFalse(hasattr(host, "shadow"))

    def test_host_rejects_a_legacy_v1_state_directory_without_modifying_it(self):
        legacy = self.root / "legacy-v1"
        legacy.mkdir()
        database = legacy / "service.sqlite3"
        database.write_bytes(b"legacy-service-database")
        (legacy / "incoming").mkdir()
        before = sorted(path.name for path in legacy.iterdir())
        with self.assertRaises(HarnessError) as caught:
            self.open_host(legacy)
        self.assertEqual(caught.exception.code, "superagent/state-dir-conflict")
        self.assertEqual(database.read_bytes(), b"legacy-service-database")
        self.assertEqual(sorted(path.name for path in legacy.iterdir()), before)
        self.assertFalse((legacy / "superagent.sqlite3").exists())
        nested = legacy / "nested-v2"
        with self.assertRaises(HarnessError) as nested_caught:
            self.open_host(nested)
        self.assertEqual(nested_caught.exception.code, "superagent/state-dir-conflict")
        self.assertFalse(nested.exists())

    def test_host_rejects_overlapping_explicit_v1_and_v2_state_directories(self):
        legacy = self.root / "legacy-v1"
        target = legacy / "nested-v2"
        with self.assertRaises(TypeError):
            self.open_host(target, legacy_state_dir=legacy)
        self.assertFalse(target.exists())

    def test_host_rejects_v1_state_nested_inside_v2_directory(self):
        target = self.root / "v2-state"
        legacy = target / "legacy-v1"
        with self.assertRaises(TypeError):
            self.open_host(target, legacy_state_dir=legacy)
        self.assertFalse(target.exists())

    def test_host_composition_does_not_construct_model_runtime_components(self):
        with patch('harness.app.superagent_bootstrap.Harness', side_effect=AssertionError('宿主组合不得构造模型')):
            host = self.open_host()
        self.addCleanup(host.close)
        self.assertTrue(host.database_path.is_file())
        self.assertFalse(hasattr(host, 'shadow'))

    def test_close_refuses_to_release_lease_while_unit_of_work_is_active(self):
        host = self.open_host()
        with host.unit_of_work(actor={"kind": "human"}) as uow:
            with self.assertRaises(HarnessError) as caught:
                host.close()
            self.assertEqual(caught.exception.code, "superagent/runtime-busy")
            with self.assertRaises(HarnessError) as second:
                self.open_host()
            self.assertEqual(second.exception.code, "superagent/runtime-busy")
            ticket = uow.tickets.create({
                "ticket_id": "ticket-active-host-close",
                "mandate_id": "mandate-active-host-close",
                "origin_trigger_id": "trigger-active-host-close",
                "title": "Lease remains held",
            })
            self.assertEqual(ticket["state"], "open")
        host.close()
        reopened = self.open_host()
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.database_path, host.database_path)

    def _create_expired_effect(self, host, *, kind, key):
        with host._store.transaction(write=True) as db:
            effect_id = host._store.enqueue_effect(
                db, kind=kind, aggregate_id="ticket-host-lifecycle",
                idempotency_key=key, payload={"operation": "read-original-result"},
            )
            claimed = host._store.claim_effects(
                db, owner="crashed-worker", limit=1,
                lease_until="2999-01-01T00:00:00Z",
            )
            self.assertEqual(len(claimed), 1)
            db.execute(
                "UPDATE effects SET lease_until='2000-01-01T00:00:00.000Z' WHERE effect_id=?",
                (effect_id,),
            )
        return effect_id

    def test_close_refuses_while_transition_receipt_verification_is_active(self):
        """Transition 回执核验在途时不得释放宿主锁。"""
        entered, release = Event(), Event()

        class BlockingVerifier(_Verifier):
            def verify_transition(inner_self, **kwargs):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("transition verifier was not released")
                return super().verify_transition(**kwargs)

        host = self.open_host(evidence_verifier=BlockingVerifier())
        with host.unit_of_work(actor={"kind": "human"}) as uow:
            ticket = uow.tickets.create({
                "ticket_id": "ticket-transition-lease",
                "mandate_id": "mandate-transition-lease",
                "origin_trigger_id": "trigger-transition-lease",
                "title": "Transition lease",
            })
        result = []

        def transition():
            try:
                result.append(host.transition(
                    "ticket", ticket["ticket_id"], expected_revision=1,
                    target_state="ended", receipt=_HarnessReceipt("evidence://transition"),
                    actor={"kind": "human"},
                ))
            except BaseException as exc:
                result.append(exc)

        worker = Thread(target=transition)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(HarnessError) as caught:
                host.close()
            self.assertEqual(caught.exception.code, "superagent/runtime-busy")
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(result), 1)
        self.assertNotIsInstance(result[0], BaseException)
        host.close()

    def test_close_refuses_while_outbox_operation_is_active(self):
        """Outbox 数据库操作在途时不得释放宿主锁。"""
        host = self.open_host()
        outbox = host.outbox
        entered, release = Event(), Event()
        result = []

        def blocking_claim(service, *, owner, limit=100, lease_until):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Outbox claim was not released")
            return []

        def claim():
            try:
                result.append(outbox.claim(
                    owner="lifecycle-worker", lease_until="2999-01-01T00:00:00Z",
                ))
            except BaseException as exc:
                result.append(exc)

        with patch("harness.app.superagent_outbox.OutboxEffectService.claim", new=blocking_claim):
            worker = Thread(target=claim)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(HarnessError) as caught:
                    host.close()
                self.assertEqual(caught.exception.code, "superagent/runtime-busy")
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [[]])
        host.close()

    def test_close_refuses_while_sync_provider_recovery_is_active(self):
        """同步 Provider 回查在途时不得释放宿主锁。"""
        host = self.open_host()
        self._create_expired_effect(host, kind="provider.sync", key="sync-lookup")
        entered, release = Event(), Event()
        result = []

        class BlockingAdapter:
            def lookup(inner_self, *, effect):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("synchronous lookup was not released")
                return EffectLookupResult(status="indeterminate")

        recovery = host.effect_recovery_service({"provider.sync": BlockingAdapter()})

        def recover():
            try:
                result.append(recovery.recover())
            except BaseException as exc:
                result.append(exc)

        worker = Thread(target=recover)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(HarnessError) as caught:
                host.close()
            self.assertEqual(caught.exception.code, "superagent/runtime-busy")
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].left_unknown, 1)
        host.close()

    def test_close_refuses_while_async_provider_recovery_is_active(self):
        """异步 Provider 回查等待期间不得释放宿主锁。"""
        host = self.open_host()
        self._create_expired_effect(host, kind="provider.async", key="async-lookup")
        entered, release = asyncio.Event(), asyncio.Event()

        class BlockingAdapter:
            async def lookup(inner_self, *, effect):
                entered.set()
                await release.wait()
                return EffectLookupResult(status="indeterminate")

        recovery = host.effect_recovery_service({"provider.async": BlockingAdapter()})

        async def exercise():
            task = asyncio.create_task(recovery.recover_async())
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                with self.assertRaises(HarnessError) as caught:
                    host.close()
                self.assertEqual(caught.exception.code, "superagent/runtime-busy")
            finally:
                release.set()
            report = await asyncio.wait_for(task, timeout=5)
            self.assertEqual(report.left_unknown, 1)

        asyncio.run(exercise())
        host.close()

    def test_cached_outbox_and_recovery_handles_reject_after_host_close(self):
        host = self.open_host()
        outbox = host.outbox
        recovery = host.effect_recovery_service({})
        host.close()
        with self.assertRaises(HarnessError) as outbox_error:
            outbox.claim(owner="late-worker", lease_until="2099-01-01T00:00:00Z")
        self.assertEqual(outbox_error.exception.code, "superagent/closed")
        with self.assertRaises(HarnessError) as recovery_error:
            recovery.recover()
        self.assertEqual(recovery_error.exception.code, "superagent/closed")
        with self.assertRaises(HarnessError) as async_error:
            asyncio.run(recovery.recover_async())
        self.assertEqual(async_error.exception.code, "superagent/closed")

    def test_host_unit_of_work_is_a_restricted_creation_and_read_view(self):
        host = self.open_host()
        self.addCleanup(host.close)
        with host.unit_of_work(actor={"kind": "human"}) as uow:
            self.assertFalse(hasattr(uow, "db"))
            self.assertFalse(hasattr(uow, "store"))
            self.assertFalse(hasattr(uow, "actor"))
            self.assertFalse(hasattr(uow.tickets, "update"))
            with self.assertRaises(AttributeError):
                CoordinatorApplicationService(uow)
            with self.assertRaises(PermissionError):
                uow.tickets.create({
                    "ticket_id": "ticket-forged-terminal",
                    "mandate_id": "mandate-forged-terminal",
                    "origin_trigger_id": "trigger-forged-terminal",
                    "title": "Must begin open",
                    "state": "ended",
                })
            ticket = uow.tickets.create({
                "ticket_id": "ticket-host-view",
                "mandate_id": "mandate-host-view",
                "origin_trigger_id": "trigger-host-view",
                "title": "Must begin open",
            })
            self.assertEqual(ticket["state"], "open")
            self.assertEqual(uow.tickets.get(ticket["ticket_id"])["state"], "open")

    def test_host_transition_requires_verifier_issued_receipt_not_model_report(self):
        host = self.open_host(evidence_verifier=_Verifier())
        self.addCleanup(host.close)
        with host.unit_of_work(actor={"kind": "human"}) as uow:
            ticket = uow.tickets.create({
                "ticket_id": "ticket-host-evidence", "mandate_id": "mandate-host-evidence",
                "origin_trigger_id": "trigger-host-evidence", "title": "Host receipt only",
            })
        with self.assertRaises(PermissionError):
            host.transition(
                "ticket", ticket["ticket_id"], expected_revision=1,
                target_state="ended", receipt={"human_stop": True},
                actor={"kind": "human"},
            )
        self.assertEqual(host._store.revision("ticket", ticket["ticket_id"]), 1)
        ended = host.transition(
            "ticket", ticket["ticket_id"], expected_revision=1,
            target_state="ended", receipt=_HarnessReceipt("evidence://harness/stop"),
            actor={"kind": "human"}, correlation_id="corr-host-evidence",
        )
        self.assertEqual(ended["state"], "ended")
        with closing(sqlite3.connect(host.database_path)) as db:
            payload = db.execute(
                "SELECT payload_json FROM audit_events WHERE aggregate_id=? ORDER BY seq DESC LIMIT 1",
                (ticket["ticket_id"],),
            ).fetchone()[0]
        self.assertIn("evidence://harness/stop", payload)

    def test_host_rejects_evidence_bound_to_a_different_aggregate_revision(self):
        host = self.open_host(evidence_verifier=_Verifier(revision_offset=-1))
        self.addCleanup(host.close)
        with host.unit_of_work(actor={"kind": "human"}) as uow:
            ticket = uow.tickets.create({
                "ticket_id": "ticket-stale-evidence", "mandate_id": "mandate-stale-evidence",
                "origin_trigger_id": "trigger-stale-evidence", "title": "Stale revision",
            })
        with self.assertRaises(PermissionError):
            host.transition(
                "ticket", ticket["ticket_id"], expected_revision=1,
                target_state="ended", receipt=_HarnessReceipt("evidence://harness/stale-stop"),
                actor={"kind": "human"},
            )
        self.assertEqual(host._store.revision("ticket", ticket["ticket_id"]), 1)

    def test_host_refuses_transitions_until_a_real_host_verifier_is_injected(self):
        host = self.open_host()
        self.addCleanup(host.close)
        with host.unit_of_work(actor={"kind": "human"}) as uow:
            ticket = uow.tickets.create({
                "ticket_id": "ticket-no-verifier", "mandate_id": "mandate-no-verifier",
                "origin_trigger_id": "trigger-no-verifier", "title": "No verifier",
            })
        with self.assertRaises(HarnessError) as caught:
            host.transition(
                "ticket", ticket["ticket_id"], expected_revision=1,
                target_state="ended", receipt=object(), actor={"kind": "human"},
            )
        self.assertEqual(caught.exception.code, "superagent/host-evidence-unavailable")
        self.assertEqual(host._store.revision("ticket", ticket["ticket_id"]), 1)

    def test_closed_host_rejects_further_access(self):
        host = self.open_host()
        host.close()
        host.close()
        with self.assertRaises(HarnessError) as caught:
            with host.unit_of_work(actor={"kind": "human"}):
                pass
        self.assertEqual(caught.exception.code, "superagent/closed")

    def test_aclose_waits_for_active_operations_and_releases_the_instance_lease(self):
        host = self.open_host()
        entered = Event()
        release = Event()

        def hold_operation():
            with host._operation():
                entered.set()
                release.wait(3)

        worker = Thread(target=hold_operation)
        worker.start()
        self.assertTrue(entered.wait(1))

        async def close_host():
            task = asyncio.create_task(host.aclose())
            await asyncio.sleep(0)
            self.assertTrue(host._closing)
            self.assertFalse(task.done())
            with self.assertRaises(HarnessError) as closed:
                _ = host.outbox
            self.assertEqual(closed.exception.code, "superagent/closed")
            with self.assertRaises(HarnessError) as busy:
                self.open_host()
            self.assertEqual(busy.exception.code, "superagent/runtime-busy")
            release.set()
            await task
            await host.aclose()

        try:
            asyncio.run(close_host())
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        reopened = self.open_host()
        self.addCleanup(reopened.close)

    def test_coordinator_watchdog_persists_monotonic_escalation_and_progress(self):
        host = self.open_host(watchdog_limits={
            "stale_after": 1.0, "max_stale_ticks": 2, "action": "block",
        })
        self.addCleanup(host.close)
        with host.unit_of_work(actor={"kind": "test"}) as uow:
            ticket = uow.tickets.create({
                "ticket_id": "ticket-watchdog", "mandate_id": "mandate-watchdog",
                "origin_trigger_id": "trigger-watchdog", "title": "Watchdog test",
            })
            mission = uow.missions.create({
                "mission_id": "mission-watchdog", "ticket_id": ticket["ticket_id"],
                "sequence": 1, "repo": "D:/repo", "base_commit": "a" * 40,
            })

        first = host.watchdog.observe(
            mission["mission_id"], activity={"run_status": "running", "event_seq": 1},
            now="2026-11-06T00:00:00Z",
        )
        self.assertEqual((first["state"], first["aggregate_revision"]), ("healthy", 1))
        stalled = host.watchdog.observe(
            mission["mission_id"], activity={"run_status": "running", "event_seq": 1},
            now="2026-11-06T00:00:01Z",
        )
        self.assertEqual((stalled["state"], stalled["stagnant_ticks"]), ("stalled", 1))
        blocked = host.watchdog.observe(
            mission["mission_id"], activity={"run_status": "running", "event_seq": 1},
            now="2026-11-06T00:00:02Z",
        )
        self.assertEqual((blocked["state"], blocked["stagnant_ticks"]), ("blocked", 2))

        # 时钟回拨不降低 observed_at，也不制造新的 Revision。
        repeated = host.watchdog.observe(
            mission["mission_id"], activity={"run_status": "running", "event_seq": 1},
            now="2026-11-05T23:59:00Z",
        )
        self.assertEqual(repeated, blocked)
        before = host.watchdog.get(mission["mission_id"])
        status = host.watchdog.status()
        after = host.watchdog.get(mission["mission_id"])
        self.assertEqual(before, after)
        self.assertEqual(status["states"]["blocked"], 1)

        progressed = host.watchdog.observe(
            mission["mission_id"], activity={"run_status": "completed", "event_seq": 2},
            now="2026-11-06T00:00:03Z",
        )
        self.assertEqual(progressed["state"], "healthy")
        self.assertEqual(progressed["aggregate_revision"], 4)
        self.assertEqual(progressed["last_progress_at"], "2026-11-06T00:00:03.000Z")
        self.assertFalse((host.state_dir / "watchdog.sqlite3").exists())

        with host._store.transaction() as db:
            row = db.execute(
                "SELECT state,aggregate_revision FROM watchdog_snapshots WHERE mission_id=?",
                (mission["mission_id"],),
            ).fetchone()
        self.assertEqual((row["state"], row["aggregate_revision"]), ("healthy", 4))

    def test_coordinator_watchdog_attention_policy_never_auto_blocks(self):
        host = self.open_host()
        self.addCleanup(host.close)
        with host.unit_of_work(actor={"kind": "test"}) as uow:
            ticket = uow.tickets.create({
                "ticket_id": "ticket-watchdog-attention", "mandate_id": "mandate-watchdog-attention",
                "origin_trigger_id": "trigger-watchdog-attention", "title": "Watchdog attention",
            })
            mission = uow.missions.create({
                "mission_id": "mission-watchdog-attention", "ticket_id": ticket["ticket_id"],
                "sequence": 1, "repo": "D:/repo", "base_commit": "b" * 40,
            })
        host.watchdog.observe(
            mission["mission_id"], activity={"run_status": "running"},
            now="2026-11-06T00:00:00Z",
        )
        stale = host.watchdog.observe(
            mission["mission_id"], activity={"run_status": "running"},
            now="2026-11-06T02:00:00Z",
        )
        self.assertEqual(stale["state"], "stalled")
        self.assertGreaterEqual(stale["stagnant_ticks"], 3)

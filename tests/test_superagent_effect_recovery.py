"""Effect Recovery Service 的安全回查与不重放测试。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.app.superagent_coordinator_store import CoordinatorStore
from harness.app.superagent_effect_recovery import (
    EffectLookupResult,
    EffectRecoveryService,
)


class FakeLookupAdapter:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.seen = []

    def lookup(self, *, effect):
        self.seen.append(dict(effect))
        if self.error is not None:
            raise self.error
        return self.result


class EffectRecoveryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "superagent.sqlite3"
        self.store = CoordinatorStore(
            self.path, instance_id="instance-a", identity={"profile": "recovery-test"},
        )

    def _unknown_effect(self, key="effect-key", kind="provider.send"):
        with self.store.transaction(write=True) as db:
            effect_id = self.store.enqueue_effect(
                db, kind=kind, aggregate_id="ticket-1",
                idempotency_key=key, payload={"body_ref": "artifact://body/1"},
            )
            claimed = self.store.claim_effects(
                db, owner="crashed-worker", limit=1, lease_until="2999-01-01T00:00:00Z",
            )
            self.assertEqual(len(claimed), 1)
            generation = claimed[0]["generation"]
            db.execute(
                "UPDATE effects SET lease_until='2000-01-01T00:00:00.000Z' WHERE effect_id=?",
                (effect_id,),
            )
        return effect_id, generation

    def test_applied_provider_lookup_completes_without_resending(self):
        effect_id, _ = self._unknown_effect()
        adapter = FakeLookupAdapter(EffectLookupResult(
            status="applied", receipt={"provider_id": "remote-1"},
        ))
        report = EffectRecoveryService(self.store, {"provider.send": adapter}).recover()
        self.assertEqual((report.marked_unknown, report.looked_up, report.completed), (1, 1, 1))
        self.assertEqual(adapter.seen[0]["payload"], {"body_ref": "artifact://body/1"})
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT state,receipt_json FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            self.assertEqual((row["state"], row["receipt_json"]), ("completed", '{"provider_id":"remote-1"}'))

    def test_unavailable_unknown_does_not_starve_later_effect_in_bounded_ticks(self):
        first, _ = self._unknown_effect("missing", kind="unavailable.send")
        adapter = FakeLookupAdapter(EffectLookupResult("applied", receipt={"provider_id": "found"}))
        recovery = EffectRecoveryService(self.store, {"provider.send": adapter})
        self.assertEqual(recovery.recover(limit=1).unavailable, 1)
        second, _ = self._unknown_effect("available")
        for _ in range(2):
            recovery.recover(limit=1)
        self.assertEqual([item["effect_id"] for item in adapter.seen], [second])
        with self.store.transaction() as db:
            states = dict(db.execute("SELECT effect_id,state FROM effects").fetchall())
        self.assertEqual(states, {first: "unknown", second: "completed"})

    def test_retry_requires_explicit_provider_proof_and_retry_safe_flag(self):
        effect_id, _ = self._unknown_effect()
        adapter = FakeLookupAdapter(EffectLookupResult(
            status="not_applied", receipt={"lookup": "not_found", "window_valid": True},
            retry_safe=True, detail="provider confirmed absence",
        ))
        report = EffectRecoveryService(self.store, {"provider.send": adapter}).recover()
        self.assertEqual(report.retry_wait, 1)
        with self.store.transaction(write=True) as db:
            claimed = self.store.claim_effects(
                db, owner="retry-worker", limit=1, lease_until="2999-01-02T00:00:00Z",
            )
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0]["effect_id"], effect_id)
            self.assertEqual(claimed[0]["generation"], 2)

    def test_not_applied_without_retry_safe_proof_stays_unknown(self):
        effect_id, _ = self._unknown_effect()
        adapter = FakeLookupAdapter(EffectLookupResult(
            status="not_applied", receipt={"lookup": "not_found"}, retry_safe=False,
        ))
        report = EffectRecoveryService(self.store, {"provider.send": adapter}).recover()
        self.assertEqual(report.left_unknown, 1)
        with self.store.transaction() as db:
            audit = db.execute(
                "SELECT event_type,causation_id FROM audit_events "
                "WHERE aggregate_type='effect' AND aggregate_id=? ORDER BY seq DESC LIMIT 1",
                (effect_id,),
            ).fetchone()
            effect = db.execute(
                "SELECT last_event_id FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            self.assertEqual(audit["event_type"], "effect.lookup_indeterminate")
            self.assertEqual(effect["last_event_id"], db.execute(
                "SELECT event_id FROM audit_events WHERE aggregate_type='effect' "
                "AND aggregate_id=? ORDER BY seq DESC LIMIT 1", (effect_id,)
            ).fetchone()[0])
        with self.store.transaction(write=True) as db:
            self.assertEqual(self.store.claim_effects(
                db, owner="unsafe-retry", limit=1, lease_until="2999-01-02T00:00:00Z",
            ), [])
            row = db.execute("SELECT state FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
            self.assertEqual(row["state"], "unknown")

    def test_indeterminate_missing_adapter_and_adapter_error_stay_unknown(self):
        effect_ids = [
            self._unknown_effect("key-0", kind="provider.send")[0],
            self._unknown_effect("key-1", kind="provider.send")[0],
            self._unknown_effect("key-2", kind="provider.error")[0],
        ]
        indeterminate = FakeLookupAdapter(EffectLookupResult(status="indeterminate"))
        broken = FakeLookupAdapter(error=RuntimeError("provider unavailable"))
        report = EffectRecoveryService(
            self.store, {"provider.send": indeterminate, "provider.error": broken},
        ).recover()
        self.assertEqual(report.unavailable, 0)
        self.assertEqual(report.looked_up, 3)
        self.assertEqual(report.errors, 1)
        self.assertEqual(report.left_unknown, 3)
        with self.store.transaction() as db:
            events = {
                row["aggregate_id"]: row["event_type"]
                for row in db.execute(
                    "SELECT aggregate_id,event_type FROM audit_events "
                    "WHERE aggregate_type='effect' AND event_type IN "
                    "('effect.lookup_indeterminate','effect.lookup_error')"
                )
            }
            states = {
                row["effect_id"]: row["state"]
                for row in db.execute(
                    "SELECT effect_id,state FROM effects WHERE effect_id IN (?,?,?)", effect_ids
                )
            }
            for effect_id in effect_ids:
                chain = db.execute(
                    "SELECT event_id,event_type,causation_id FROM audit_events "
                    "WHERE aggregate_type='effect' AND aggregate_id=? ORDER BY seq",
                    (effect_id,),
                ).fetchall()
                cursor = db.execute(
                    "SELECT last_event_id FROM effects WHERE effect_id=?", (effect_id,)
                ).fetchone()["last_event_id"]
                self.assertEqual(chain[-1]["causation_id"], chain[-2]["event_id"])
                self.assertEqual(cursor, chain[-1]["event_id"])
        self.assertEqual(set(states.values()), {"unknown"})
        self.assertEqual(events[effect_ids[0]], "effect.lookup_indeterminate")
        self.assertEqual(events[effect_ids[1]], "effect.lookup_indeterminate")
        self.assertEqual(events[effect_ids[2]], "effect.lookup_error")

    def test_missing_adapter_leaves_unknown_without_lookup(self):
        effect_id, _ = self._unknown_effect()
        report = EffectRecoveryService(self.store, {}).recover()
        self.assertEqual((report.marked_unknown, report.unavailable, report.left_unknown, report.looked_up), (1, 1, 1, 0))
        with self.store.transaction() as db:
            event = db.execute(
                "SELECT event_id,event_type,causation_id FROM audit_events WHERE aggregate_type='effect' "
                "AND aggregate_id=? ORDER BY seq DESC LIMIT 1",
                (effect_id,),
            ).fetchone()
            previous = db.execute(
                "SELECT event_id FROM audit_events WHERE aggregate_type='effect' "
                "AND aggregate_id=? ORDER BY seq DESC LIMIT 1 OFFSET 1",
                (effect_id,),
            ).fetchone()
            cursor = db.execute(
                "SELECT last_event_id FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()["last_event_id"]
            self.assertEqual(event["event_type"], "effect.lookup_unavailable")
            self.assertEqual(event["causation_id"], previous["event_id"])
            self.assertEqual(cursor, event["event_id"])
            self.assertEqual(
                db.execute("SELECT state FROM effects WHERE effect_id=?", (effect_id,)).fetchone()[0],
                "unknown",
            )
            first_event_id = event["event_id"]
            first_event_count = db.execute(
                "SELECT COUNT(*) FROM audit_events WHERE aggregate_type='effect' AND aggregate_id=?",
                (effect_id,),
            ).fetchone()[0]

        repeated = EffectRecoveryService(self.store, {}).recover()
        self.assertEqual((repeated.unavailable, repeated.left_unknown), (1, 1))
        with self.store.transaction() as db:
            latest = db.execute(
                "SELECT event_id,event_type FROM audit_events WHERE aggregate_type='effect' "
                "AND aggregate_id=? ORDER BY seq DESC LIMIT 1",
                (effect_id,),
            ).fetchone()
            event_count = db.execute(
                "SELECT COUNT(*) FROM audit_events WHERE aggregate_type='effect' AND aggregate_id=?",
                (effect_id,),
            ).fetchone()[0]
        self.assertEqual(latest["event_id"], first_event_id)
        self.assertEqual(latest["event_type"], "effect.lookup_unavailable")
        self.assertEqual(event_count, first_event_count)

    def test_stale_lookup_result_cannot_overwrite_concurrent_reconciliation(self):
        effect_id, generation = self._unknown_effect("concurrent-reconcile")

        class RacingAdapter:
            def lookup(inner_self, *, effect):
                with self.store.transaction(write=True) as db:
                    self.store.reconcile_effect(
                        db, effect_id=effect["effect_id"], generation=effect["generation"],
                        state="completed", receipt={"provider_id": "winner"},
                    )
                return EffectLookupResult(
                    status="not_applied", receipt={"lookup": "not_found"},
                    retry_safe=True,
                )

        report = EffectRecoveryService(self.store, {"provider.send": RacingAdapter()}).recover()
        self.assertEqual(report.stale, 1)
        self.assertEqual(report.left_unknown, 0)
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT state,receipt_json,generation FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            self.assertEqual(tuple(row), ("completed", '{"provider_id":"winner"}', generation))

    def test_recovery_limit_is_bounded(self):
        with self.assertRaises(ValueError):
            EffectRecoveryService(self.store, {}).recover(limit=0)
        with self.assertRaises(ValueError):
            EffectRecoveryService(self.store, {}).recover(limit=101)


if __name__ == "__main__":
    unittest.main()

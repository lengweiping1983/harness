"""Outbox 与 Durable Effect 事务关联和业务回执一致性回归。"""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
import tempfile
from threading import Barrier
import unittest
from pathlib import Path

from harness.app.superagent_coordinator_store import CoordinatorStore
from harness.app.superagent_effect_recovery import EffectLookupResult, EffectRecoveryService
from harness.app.superagent_outbox import OutboxEffectService


class _Lookup:
    def __init__(self, result):
        self.result = result
        self.seen = []

    def lookup(self, *, effect):
        self.seen.append(dict(effect))
        return self.result


class OutboxEffectServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CoordinatorStore(
            Path(self.temp.name) / "superagent.sqlite3",
            instance_id="outbox-test",
            identity={"profile": "outbox"},
        )
        self.outbox = OutboxEffectService(self.store)

    def _create_message(self, suffix="1"):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            ticket = uow.tickets.create({
                "ticket_id": f"ticket-{suffix}",
                "mandate_id": f"mandate-{suffix}",
                "origin_trigger_id": f"trigger-{suffix}",
                "title": "Notify requester",
            })
            message = uow.outbox.create({
                "message_id": f"message-{suffix}",
                "ticket_id": ticket["ticket_id"],
                "channel_id": "channel-1",
                "kind": "result",
                "body_ref": f"artifact://body/{suffix}",
                "body_hash": "a" * 64,
                "idempotency_key": f"outbox-key-{suffix}",
            })
            effect_id = uow.db.execute(
                "SELECT effect_id FROM outbox_effect_links WHERE message_id=?",
                (message["message_id"],),
            ).fetchone()[0]
        return message["message_id"], effect_id

    def test_message_and_effect_creation_roll_back_as_one_unit(self):
        with self.assertRaises(ValueError):
            with self.store.unit_of_work(actor={"kind": "human"}) as uow:
                ticket = uow.tickets.create({
                    "ticket_id": "ticket-rollback",
                    "mandate_id": "mandate-rollback",
                    "origin_trigger_id": "trigger-rollback",
                    "title": "Rollback atomic create",
                })
                uow.outbox.create({
                    "message_id": "message-rollback",
                    "ticket_id": ticket["ticket_id"],
                    "channel_id": "channel-1",
                    "kind": "result",
                    "body_ref": "artifact://body/rollback",
                    "body_hash": "a" * 64,
                    "idempotency_key": "outbox-rollback",
                }, effect={"kind": "manual.duplicate"})
        with self.store.transaction() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM outbox_messages WHERE message_id='message-rollback'"
            ).fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM effects WHERE idempotency_key='outbox-rollback'"
            ).fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM tickets WHERE ticket_id='ticket-rollback'"
            ).fetchone()[0], 0)

    def test_withdraw_cancels_only_an_unclaimed_message_and_effect(self):
        message_id, effect_id = self._create_message()
        self.assertTrue(self.outbox.withdraw(
            message_id, expected_revision=1, actor={"kind": "human", "ref": "actor-1"},
        ))
        with self.store.transaction() as db:
            self.assertEqual(db.execute(
                "SELECT state FROM outbox_messages WHERE message_id=?", (message_id,)
            ).fetchone()[0], "withdrawn")
            self.assertEqual(db.execute(
                "SELECT state FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()[0], "cancelled")
        self.assertEqual(self.outbox.claim(
            owner="worker", limit=10, lease_until="2999-01-01T00:00:00Z",
        ), [])

    def test_claimed_message_cannot_be_withdrawn(self):
        message_id, _ = self._create_message()
        self.outbox.claim(owner="worker", limit=1, lease_until="2999-01-01T00:00:00Z")
        with self.assertRaises(ValueError):
            self.outbox.withdraw(
                message_id, expected_revision=2, actor={"kind": "human"},
            )

    def test_expired_effect_and_outbox_become_unknown_in_one_transaction(self):
        message_id, effect_id = self._create_message()
        self.outbox.claim(owner="crashed-worker", limit=1, lease_until="2999-01-01T00:00:00Z")
        with self.store.transaction(write=True) as db:
            db.execute(
                "UPDATE effects SET lease_until='2000-01-01T00:00:00.000Z' WHERE effect_id=?",
                (effect_id,),
            )
            expired = self.store.mark_expired_effects_unknown(db)
            self.assertEqual(len(expired), 1)
            self.assertEqual(expired[0]["state"], "unknown")
            self.assertEqual(db.execute(
                "SELECT state FROM outbox_messages WHERE message_id=?", (message_id,)
            ).fetchone()[0], "unknown")
            effect_events = db.execute(
                "SELECT event_id,event_type,causation_id FROM audit_events "
                "WHERE aggregate_type='effect' AND aggregate_id=? ORDER BY seq",
                (effect_id,),
            ).fetchall()
            message_events = db.execute(
                "SELECT event_id,event_type,causation_id FROM audit_events "
                "WHERE aggregate_type='outbox_message' AND aggregate_id=? ORDER BY seq",
                (message_id,),
            ).fetchall()
            effect_unknown = next(row for row in effect_events if row["event_type"] == "effect.unknown")
            sending = next(row for row in message_events if row["event_type"] == "outbox.sending")
            unknown = next(row for row in message_events if row["event_type"] == "outbox.unknown")
            self.assertEqual(effect_unknown["causation_id"], sending["event_id"])
            self.assertEqual(unknown["causation_id"], effect_unknown["event_id"])

    def test_recovery_provider_receipt_updates_effect_and_outbox_together(self):
        message_id, effect_id = self._create_message()
        self.outbox.claim(owner="crashed-worker", limit=1, lease_until="2999-01-01T00:00:00Z")
        with self.store.transaction(write=True) as db:
            db.execute(
                "UPDATE effects SET lease_until='2000-01-01T00:00:00.000Z' WHERE effect_id=?",
                (effect_id,),
            )
            self.store.mark_expired_effects_unknown(db)
        adapter = _Lookup(EffectLookupResult(
            status="applied",
            receipt={
                "idempotency_key": "outbox-key-1",
                "provider_id": "provider-message-1",
                "sent_at": "2026-11-04T16:00:00Z",
            },
        ))
        report = EffectRecoveryService(self.store, {"outbox.send": adapter}).recover()
        self.assertEqual((report.looked_up, report.completed, report.errors), (1, 1, 0))
        with self.store.transaction() as db:
            effect = db.execute(
                "SELECT state FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            message = db.execute(
                "SELECT state,provider_id,provider_key FROM outbox_messages WHERE message_id=?",
                (message_id,),
            ).fetchone()
        self.assertEqual(effect["state"], "completed")
        self.assertEqual(
            tuple(message), ("sent", "provider-message-1", "outbox-key-1"),
        )
        self.assertEqual(adapter.seen[0]["payload"]["message_id"], message_id)

    def test_permanent_provider_rejection_reconciles_to_dropped(self):
        message_id, effect_id = self._create_message("rejected")
        self.outbox.claim(owner="worker", limit=1, lease_until="2999-01-01T00:00:00Z")
        self.assertTrue(self.outbox.settle(
            effect_id=effect_id, owner="worker", generation=1,
            state="unknown", error="provider response lost",
        ))
        adapter = _Lookup(EffectLookupResult(
            status="permanent_rejection",
            receipt={
                "idempotency_key": "outbox-key-rejected",
                "permanent_rejection": True,
                "provider_id": "provider-rejected",
            },
            detail="provider rejected the original message",
        ))
        report = EffectRecoveryService(self.store, {"outbox.send": adapter}).recover()
        self.assertEqual((report.completed, report.failed, report.errors, report.left_unknown), (0, 1, 0, 0))
        with self.store.transaction() as db:
            self.assertEqual(db.execute(
                "SELECT state FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()[0], "failed")
            self.assertEqual(db.execute(
                "SELECT state FROM outbox_messages WHERE message_id=?", (message_id,)
            ).fetchone()[0], "dropped")

    def test_claim_and_settlement_audit_events_form_one_causal_chain(self):
        message_id, effect_id = self._create_message("audit")
        with self.store.transaction() as db:
            effect = db.execute(
                "SELECT correlation_id,last_event_id FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            correlation_id = effect["correlation_id"]
        self.outbox.claim(owner="worker-audit", limit=1, lease_until="2999-01-01T00:00:00Z")
        self.assertTrue(self.outbox.settle(
            effect_id=effect_id, owner="worker-audit", generation=1, state="completed",
            receipt={
                "idempotency_key": "outbox-key-audit",
                "provider_id": "provider-audit",
                "sent_at": "2026-11-04T16:00:00Z",
            },
        ))
        with self.store.transaction() as db:
            events = db.execute(
                "SELECT event_id,event_type,causation_id,correlation_id FROM audit_events "
                "WHERE correlation_id=? ORDER BY seq",
                (correlation_id,),
            ).fetchall()
            effect = db.execute(
                "SELECT last_event_id FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
        relevant = [row for row in events if row["event_type"] in {
            "outbox_message.created", "effect.enqueued", "effect.leased",
            "outbox.sending", "effect.completed", "outbox.sent",
        }]
        self.assertEqual([row["event_type"] for row in relevant], [
            "outbox_message.created", "effect.enqueued", "effect.leased",
            "outbox.sending", "effect.completed", "outbox.sent",
        ])
        for previous, current in zip(relevant, relevant[1:]):
            self.assertEqual(current["causation_id"], previous["event_id"])
            self.assertEqual(current["correlation_id"], correlation_id)
        self.assertEqual(effect["last_event_id"], relevant[-1]["event_id"])

    def test_settlement_audit_failure_rolls_back_effect_and_business_projection(self):
        message_id, effect_id = self._create_message("audit-rollback")
        self.outbox.claim(owner="worker-crash", limit=1, lease_until="2999-01-01T00:00:00Z")
        with self.store.transaction(write=True) as db:
            db.execute(
                "CREATE TRIGGER fail_outbox_sent_audit BEFORE INSERT ON audit_events "
                "WHEN NEW.event_type='outbox.sent' BEGIN SELECT RAISE(ABORT,'injected audit failure'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.outbox.settle(
                effect_id=effect_id, owner="worker-crash", generation=1, state="completed",
                receipt={
                    "idempotency_key": "outbox-key-audit-rollback",
                    "provider_id": "provider-audit-rollback",
                    "sent_at": "2026-11-04T16:00:00Z",
                },
            )
        with self.store.transaction() as db:
            effect = db.execute(
                "SELECT state,lease_owner,generation FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            message = db.execute(
                "SELECT state FROM outbox_messages WHERE message_id=?", (message_id,)
            ).fetchone()
        self.assertEqual(tuple(effect), ("leased", "worker-crash", 1))
        self.assertEqual(message["state"], "sending")

    def test_concurrent_claims_never_lease_one_outbox_effect_twice(self):
        self._create_message("concurrent")
        barrier = Barrier(2)

        def claim(owner):
            barrier.wait()
            return self.outbox.claim(
                owner=owner, limit=1, lease_until="2999-01-01T00:00:00Z"
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ("worker-a", "worker-b")))
        claimed = [effect for batch in results for effect in batch]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["generation"], 1)

    def test_withdraw_racing_claim_is_serialized_without_split_state(self):
        message_id, effect_id = self._create_message("withdraw-race")
        barrier = Barrier(2)

        def claim():
            barrier.wait()
            return self.outbox.claim(
                owner="race-worker", limit=1, lease_until="2999-01-01T00:00:00Z",
            )

        def withdraw():
            barrier.wait()
            return self.outbox.withdraw(
                message_id, expected_revision=1, actor={"kind": "human", "ref": "race-actor"},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            claim_future = pool.submit(claim)
            withdraw_future = pool.submit(withdraw)
            claimed = claim_future.result()
            withdrawn = withdraw_future.result()

        with self.store.transaction() as db:
            effect = db.execute(
                "SELECT state,lease_owner,generation FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            message = db.execute(
                "SELECT state,aggregate_revision FROM outbox_messages WHERE message_id=?",
                (message_id,),
            ).fetchone()
        if withdrawn:
            self.assertEqual(claimed, [])
            self.assertEqual(tuple(effect), ("cancelled", None, 0))
            self.assertEqual(tuple(message), ("withdrawn", 2))
        else:
            self.assertEqual(len(claimed), 1)
            self.assertEqual(tuple(effect), ("leased", "race-worker", 1))
            self.assertEqual(tuple(message), ("sending", 2))

    def test_malformed_provider_receipts_never_advance_outbox_or_replay(self):
        message_id, effect_id = self._create_message("malformed-receipt")
        self.outbox.claim(
            owner="receipt-worker", limit=1, lease_until="2999-01-01T00:00:00Z",
        )
        with self.assertRaises(ValueError):
            self.outbox.settle(
                effect_id=effect_id, owner="receipt-worker", generation=1,
                state="completed",
                receipt={"idempotency_key": "outbox-key-malformed-receipt", "provider_id": "provider-1"},
            )
        with self.store.transaction() as db:
            effect = db.execute(
                "SELECT state,lease_owner,generation FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            message = db.execute(
                "SELECT state,provider_id,sent_at FROM outbox_messages WHERE message_id=?",
                (message_id,),
            ).fetchone()
        self.assertEqual(tuple(effect), ("leased", "receipt-worker", 1))
        self.assertEqual(tuple(message), ("sending", None, None))

        with self.store.transaction(write=True) as db:
            db.execute(
                "UPDATE effects SET lease_until='2000-01-01T00:00:00.000Z' WHERE effect_id=?",
                (effect_id,),
            )
            self.store.mark_expired_effects_unknown(db)
        adapter = _Lookup(EffectLookupResult(
            status="applied",
            receipt={
                "idempotency_key": "outbox-key-malformed-receipt",
                "provider_id": "provider-1",
                "sent_at": "not-a-provider-timestamp",
            },
        ))
        report = EffectRecoveryService(self.store, {"outbox.send": adapter}).recover()
        self.assertEqual((report.looked_up, report.completed, report.errors, report.left_unknown), (1, 0, 1, 1))
        self.assertEqual(len(adapter.seen), 1)
        with self.store.transaction() as db:
            effect = db.execute(
                "SELECT state,receipt_json,last_event_id FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            message = db.execute(
                "SELECT state,provider_id,sent_at FROM outbox_messages WHERE message_id=?",
                (message_id,),
            ).fetchone()
            lookup_event = db.execute(
                "SELECT event_id,event_type,causation_id FROM audit_events "
                "WHERE aggregate_type='effect' AND aggregate_id=? ORDER BY seq DESC LIMIT 1",
                (effect_id,),
            ).fetchone()
            sent_events = db.execute(
                "SELECT COUNT(*) FROM audit_events WHERE aggregate_type='outbox_message' "
                "AND aggregate_id=? AND event_type='outbox.sent'",
                (message_id,),
            ).fetchone()[0]
        self.assertEqual(effect["state"], "unknown")
        self.assertIsNone(effect["receipt_json"])
        self.assertEqual(tuple(message), ("unknown", None, None))
        self.assertEqual(lookup_event["event_type"], "effect.lookup_error")
        self.assertEqual(effect["last_event_id"], lookup_event["event_id"])
        self.assertEqual(sent_events, 0)

    def test_retry_claim_increments_generation_and_rejects_stale_or_wrong_receipts(self):
        message_id, effect_id = self._create_message("retry")
        first = self.outbox.claim(
            owner="worker-first", limit=1, lease_until="2999-01-01T00:00:00Z"
        )[0]
        self.assertEqual(first["generation"], 1)
        with self.assertRaises(ValueError):
            self.outbox.settle(
                effect_id=effect_id, owner="worker-first", generation=1, state="completed",
                receipt={
                    "idempotency_key": "different-key",
                    "provider_id": "provider-wrong",
                    "sent_at": "2026-11-04T16:00:00Z",
                },
            )
        self.assertTrue(self.outbox.settle(
            effect_id=effect_id, owner="worker-first", generation=1, state="retry_wait",
            receipt={"idempotency_key": "outbox-key-retry", "retry_safe": True},
            error="provider confirmed safe retry",
            due_at="2000-01-01T00:00:00Z",
        ))
        second = self.outbox.claim(
            owner="worker-second", limit=1, lease_until="2999-01-01T00:00:00Z"
        )[0]
        self.assertEqual((second["generation"], second["attempts"]), (2, 2))
        self.assertFalse(self.outbox.settle(
            effect_id=effect_id, owner="worker-first", generation=1, state="completed",
            receipt={
                "idempotency_key": "outbox-key-retry",
                "provider_id": "provider-stale",
                "sent_at": "2026-11-04T16:01:00Z",
            },
        ))
        with self.store.transaction() as db:
            current = db.execute(
                "SELECT state,lease_owner,generation,attempts FROM effects WHERE effect_id=?",
                (effect_id,),
            ).fetchone()
            sending = db.execute(
                "SELECT state,provider_key,attempts FROM outbox_messages WHERE message_id=?",
                (message_id,),
            ).fetchone()
        self.assertEqual(tuple(current), ("leased", "worker-second", 2, 2))
        self.assertEqual(tuple(sending), ("sending", "outbox-key-retry", 2))
        self.assertTrue(self.outbox.settle(
            effect_id=effect_id, owner="worker-second", generation=2, state="completed",
            receipt={
                "idempotency_key": "outbox-key-retry",
                "provider_id": "provider-final",
                "sent_at": "2026-11-04T16:02:00Z",
            },
        ))
        with self.store.transaction() as db:
            final_effect = db.execute(
                "SELECT state,generation,attempts FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            final_message = db.execute(
                "SELECT state,provider_key,provider_id FROM outbox_messages WHERE message_id=?",
                (message_id,),
            ).fetchone()
        self.assertEqual(tuple(final_effect), ("completed", 2, 2))
        self.assertEqual(tuple(final_message), ("sent", "outbox-key-retry", "provider-final"))

    def test_generic_effect_settlement_cannot_bypass_outbox_projection(self):
        _, effect_id = self._create_message()
        self.outbox.claim(owner="worker", limit=1, lease_until="2999-01-01T00:00:00Z")
        with self.store.transaction(write=True) as db:
            with self.assertRaises(ValueError):
                self.store.settle_effect(
                    db, effect_id=effect_id, owner="worker", generation=1,
                    state="unknown", error="must use the business coordinator",
                )


if __name__ == "__main__":
    unittest.main()

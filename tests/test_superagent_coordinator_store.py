"""下一代 Superagent Coordinator Store 的事务、幂等和恢复回归。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness.app.superagent_coordinator_store import CoordinatorStore, MIGRATIONS, _APPLICATION_ID
from harness.app.superagent_outbox import OutboxEffectService
from harness.core.errors import HarnessError

class CoordinatorStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state" / "superagent.sqlite3"
        self.store = CoordinatorStore(self.path, instance_id="instance-a", identity={"profile": "local"})

    def test_initializes_schema_and_binds_single_instance(self):
        self.assertTrue(self.path.exists())
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA application_id").fetchone()[0], _APPLICATION_ID)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 8)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 8)
            self.assertEqual(db.execute("SELECT instance_id FROM instances").fetchone()[0], "instance-a")

    def test_reopen_is_idempotent_and_identity_is_bound(self):
        CoordinatorStore(self.path, instance_id="instance-a", identity={"profile": "local"})
        with self.assertRaises(HarnessError):
            CoordinatorStore(self.path, instance_id="instance-b", identity={"profile": "local"})
        with self.assertRaises(HarnessError):
            CoordinatorStore(self.path, instance_id="instance-a", identity={"profile": "other"})

    def test_refuses_nonempty_unrecognized_database_without_modifying_it(self):
        path = Path(self.temp.name) / "foreign.sqlite3"
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE user_data(value TEXT NOT NULL)")
            db.execute("INSERT INTO user_data(value) VALUES('keep')")
            db.commit()
        with self.assertRaises(HarnessError):
            CoordinatorStore(path, instance_id="instance-a", identity={})
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("SELECT value FROM user_data").fetchone()[0], "keep")
            self.assertIsNone(db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone())

    def test_refuses_memory_database(self):
        with self.assertRaises(ValueError):
            CoordinatorStore(":memory:", instance_id="instance-a", identity={})

    def test_corrupt_migration_checksum_is_rejected(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE schema_migrations SET checksum='bad' WHERE version=1")
            db.commit()
        with self.assertRaises(HarnessError):
            CoordinatorStore(self.path, instance_id="instance-a", identity={"profile": "local"})

    def _create_legacy_schema_with_effect(self, path: Path, version: int):
        identity = {"profile": "upgrade"}
        identity_json = '{"profile":"upgrade"}'
        identity_hash = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()
        instance_id = f"instance-v{version}"
        effect_id = f"legacy-effect-v{version}"
        with closing(sqlite3.connect(path)) as db:
            db.isolation_level = None
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at TEXT NOT NULL)"
            )
            for migration_version, script in MIGRATIONS[:version]:
                pending = ""
                for line in script.splitlines():
                    pending += line + "\n"
                    if sqlite3.complete_statement(pending):
                        db.execute(pending)
                        pending = ""
                self.assertEqual(pending.strip(), "")
                db.execute(
                    "INSERT INTO schema_migrations(version,checksum,applied_at) VALUES(?,?,?)",
                    (migration_version, hashlib.sha256(script.encode("utf-8")).hexdigest(),
                     "2026-01-01T00:00:00.000Z"),
                )
            db.execute(
                "INSERT INTO instances(instance_id,identity_json,identity_hash,created_at) VALUES(?,?,?,?)",
                (instance_id, identity_json, identity_hash, "2026-01-01T00:00:00.000Z"),
            )
            db.execute(
                "INSERT INTO effects(effect_id,kind,aggregate_id,idempotency_key,payload_json,state,due_at,created_at) "
                "VALUES(?,?,?,?,?,'pending',?,?)",
                (effect_id, "legacy.dispatch", "mission:legacy", f"legacy-key-{version}",
                 '{"legacy":true}', "2000-01-01T00:00:00.000Z", "2026-01-01T00:00:00.000Z"),
            )
            db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            db.execute(f"PRAGMA user_version={version}")
            db.commit()
        return instance_id, identity, effect_id

    def test_every_prior_schema_version_upgrades_to_v8_without_losing_effects(self):
        for version in range(1, 8):
            with self.subTest(from_version=version):
                path = Path(self.temp.name) / f"schema-v{version}.sqlite3"
                instance_id, identity, effect_id = self._create_legacy_schema_with_effect(path, version)
                upgraded = CoordinatorStore(path, instance_id=instance_id, identity=identity)
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 8)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 8)
                    columns = {row[1] for row in db.execute("PRAGMA table_info(effects)")}
                    self.assertTrue({"correlation_id", "last_event_id"} <= columns)
                    legacy = db.execute(
                        "SELECT kind,aggregate_id,idempotency_key,payload_json,state,correlation_id,last_event_id "
                        "FROM effects WHERE effect_id=?",
                        (effect_id,),
                    ).fetchone()
                    self.assertEqual(
                        tuple(legacy),
                        ("legacy.dispatch", "mission:legacy", f"legacy-key-{version}",
                         '{"legacy":true}', "pending", None, None),
                    )

                # 旧 Effect 不伪造历史 correlation / causation；升级后仍可安全结算。
                with upgraded.transaction(write=True) as db:
                    claimed = upgraded.claim_effects(
                        db, owner=f"legacy-worker-{version}", limit=1,
                        lease_until="2999-01-01T00:00:00Z",
                    )
                    self.assertEqual([row["effect_id"] for row in claimed], [effect_id])
                with upgraded.transaction(write=True) as db:
                    self.assertTrue(upgraded.settle_effect(
                        db, effect_id=effect_id, owner=f"legacy-worker-{version}",
                        generation=1, state="completed", receipt={"legacy": "confirmed"},
                    ))
                with upgraded.transaction() as db:
                    events = db.execute(
                        "SELECT event_id,event_type,correlation_id,causation_id FROM audit_events "
                        "WHERE aggregate_type='effect' AND aggregate_id=? ORDER BY seq",
                        (effect_id,),
                    ).fetchall()
                    final = db.execute(
                        "SELECT state,last_event_id FROM effects WHERE effect_id=?", (effect_id,)
                    ).fetchone()
                self.assertEqual([row["event_type"] for row in events], [
                    "effect.leased", "effect.completed",
                ])
                self.assertIsNone(events[0]["correlation_id"])
                self.assertEqual(events[1]["causation_id"], events[0]["event_id"])
                self.assertIsNone(events[1]["correlation_id"])
                self.assertEqual(final["state"], "completed")
                self.assertEqual(final["last_event_id"], events[-1]["event_id"])

    def test_migration7_unique_index_failure_rolls_back_entire_upgrade(self):
        path = Path(self.temp.name) / "schema-v6-duplicate-assignments.sqlite3"
        identity_json = '{"profile":"upgrade-rollback"}'
        identity_hash = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()
        with closing(sqlite3.connect(path)) as db:
            db.isolation_level = None
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at TEXT NOT NULL)"
            )
            for migration_version, script in MIGRATIONS[:6]:
                pending = ""
                for line in script.splitlines():
                    pending += line + "\n"
                    if sqlite3.complete_statement(pending):
                        db.execute(pending)
                        pending = ""
                self.assertEqual(pending.strip(), "")
                db.execute(
                    "INSERT INTO schema_migrations(version,checksum,applied_at) VALUES(?,?,?)",
                    (migration_version, hashlib.sha256(script.encode("utf-8")).hexdigest(),
                     "2026-01-01T00:00:00.000Z"),
                )
            db.execute(
                "INSERT INTO instances(instance_id,identity_json,identity_hash,created_at) VALUES(?,?,?,?)",
                ("instance-v6-rollback", identity_json, identity_hash, "2026-01-01T00:00:00.000Z"),
            )
            db.execute(
                "INSERT INTO tickets(ticket_id,mandate_id,origin_trigger_id,title,state,created_at,updated_at,aggregate_revision) "
                "VALUES('ticket-rollback','mandate-rollback','trigger-rollback','Rollback','open',?,?,1)",
                ("2026-01-01T00:00:00.000Z", "2026-01-01T00:00:00.000Z"),
            )
            db.execute(
                "INSERT INTO missions(mission_id,ticket_id,sequence,state,repo,base_commit,budgets_json,"
                "created_at,updated_at,aggregate_revision) "
                "VALUES('mission-rollback','ticket-rollback',1,'planning','D:/repo',?, '{}',?,?,1)",
                ("a" * 40, "2026-01-01T00:00:00.000Z", "2026-01-01T00:00:00.000Z"),
            )
            db.execute(
                "INSERT INTO jobs(job_id,mission_id,plan_revision,node_id,role,state,spec_hash,aggregate_revision) "
                "VALUES('job-rollback','mission-rollback',0,'__system__plan','plan','pending',?,1)",
                ("b" * 64,),
            )
            for attempt in (1, 2):
                db.execute(
                    "INSERT INTO assignments(assignment_id,job_id,attempt_no,submission_key,state,report_json,aggregate_revision) "
                    "VALUES(?,?,?,?, 'running','{}',1)",
                    (f"assignment-rollback-{attempt}", "job-rollback", attempt,
                     f"submission-rollback-{attempt}"),
                )
            db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            db.execute("PRAGMA user_version=6")
            db.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            CoordinatorStore(
                path, instance_id="instance-v6-rollback",
                identity={"profile": "upgrade-rollback"},
            )
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 6)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 6)
            indexes = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )}
            self.assertNotIn("missions_one_active_per_ticket", indexes)
            self.assertNotIn("assignments_one_active_per_job", indexes)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 2)

    def test_future_schema_version_is_rejected(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO schema_migrations VALUES(9,'future','now')")
            db.execute("PRAGMA user_version=9")
            db.commit()
        with self.assertRaises(HarnessError):
            CoordinatorStore(self.path, instance_id="instance-a", identity={"profile": "local"})

    def test_missing_migration_history_is_not_reconstructed_over_existing_data(self):
        with self.store.transaction(write=True) as db:
            self.store.append_audit(
                db, aggregate_type="ticket", aggregate_id="t-keep",
                event_type="ticket.created", payload={}, actor={"kind": "test"},
            )
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DELETE FROM schema_migrations")
            db.execute("PRAGMA user_version=0")
            db.commit()
        with self.assertRaises(HarnessError):
            CoordinatorStore(self.path, instance_id="instance-a", identity={"profile": "local"})
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], 1)

    def test_missing_instance_identity_with_existing_data_is_rejected(self):
        with self.store.transaction(write=True) as db:
            self.store.append_audit(
                db, aggregate_type="ticket", aggregate_id="t-keep",
                event_type="ticket.created", payload={}, actor={"kind": "test"},
            )
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DELETE FROM instances")
            db.commit()
        with self.assertRaises(HarnessError):
            CoordinatorStore(self.path, instance_id="instance-a", identity={"profile": "local"})

    def test_revision_cas_only_advances_expected_revision(self):
        with self.store.transaction(write=True) as db:
            self.assertTrue(self.store.compare_and_swap(db, "ticket", "t-1", 0))
        self.assertEqual(self.store.revision("ticket", "t-1"), 1)
        with self.store.transaction(write=True) as db:
            self.assertFalse(self.store.compare_and_swap(db, "ticket", "t-1", 0))
            self.assertTrue(self.store.compare_and_swap(db, "ticket", "t-1", 1))
        self.assertEqual(self.store.revision("ticket", "t-1"), 2)

    def test_cas_rejects_boolean_revision_and_requires_transaction(self):
        with self.assertRaises(ValueError):
            with self.store.transaction(write=True) as db:
                self.store.compare_and_swap(db, "ticket", "t-1", True)
        with closing(self.store._connect()) as db:
            with self.assertRaises(RuntimeError):
                self.store.compare_and_swap(db, "ticket", "t-1", 0)

    def test_concurrent_cas_allows_only_one_writer(self):
        def update():
            with self.store.transaction(write=True) as db:
                return self.store.compare_and_swap(db, "ticket", "t-race", 0)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: update(), range(2)))

        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.store.revision("ticket", "t-race"), 1)

    def test_cas_rollback_does_not_leave_revision(self):
        with self.assertRaises(RuntimeError):
            with self.store.transaction(write=True) as db:
                self.assertTrue(self.store.compare_and_swap(db, "mission", "m-1", 0))
                raise RuntimeError("rollback")
        self.assertIsNone(self.store.revision("mission", "m-1"))

    def test_audit_event_is_append_only_and_uses_canonical_payload(self):
        with self.store.transaction(write=True) as db:
            event_id = self.store.append_audit(
                db, aggregate_type="ticket", aggregate_id="t-1", event_type="ticket.created",
                payload={"z": 1, "a": "中文"}, actor={"kind": "human"},
            )
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM audit_events WHERE event_id=?", (event_id,)).fetchone()
        self.assertEqual(row["payload_json"], '{"a":"中文","z":1}')
        with self.store.transaction(write=True) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE audit_events SET event_type='rewritten' WHERE event_id=?", (event_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM audit_events WHERE event_id=?", (event_id,))

    def test_audit_rejects_invalid_schema_version_and_empty_identity(self):
        with self.store.transaction(write=True) as db:
            with self.assertRaises(ValueError):
                self.store.append_audit(
                    db, aggregate_type="ticket", aggregate_id="t-1", event_type="x",
                    payload={}, actor={}, schema_version=True,

                )
            with self.assertRaises(ValueError):
                self.store.append_audit(
                    db, aggregate_type=" ", aggregate_id="t-1", event_type="x",
                    payload={}, actor={},
                )

    def test_effect_enqueue_is_idempotent_and_conflicts_on_changed_payload(self):
        with self.store.transaction(write=True) as db:
            first = self.store.enqueue_effect(
                db, kind="dispatch", aggregate_id="m-1", idempotency_key="submit-1",
                payload={"x": 1}, due_at="2026-11-04T08:00:00-08:00",
            )
            second = self.store.enqueue_effect(
                db, kind="dispatch", aggregate_id="m-1", idempotency_key="submit-1",
                payload={"x": 1}, due_at="2026-11-04T16:00:00Z",
            )
            self.assertEqual(first, second)
            with self.assertRaises(HarnessError):
                self.store.enqueue_effect(
                    db, kind="dispatch", aggregate_id="m-1", idempotency_key="submit-1",
                    payload={"x": 2},
                )
        with self.store.transaction() as db:
            row = db.execute("SELECT due_at FROM effects WHERE effect_id=?", (first,)).fetchone()
        self.assertEqual(row["due_at"], "2026-11-04T16:00:00.000Z")

    def test_durable_effect_identity_and_payload_are_immutable(self):
        with self.store.transaction(write=True) as db:
            effect_id = self.store.enqueue_effect(
                db, kind="dispatch", aggregate_id="mission-immutable",
                idempotency_key="immutable-key", payload={"body_hash": "a" * 64},
            )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction(write=True) as db:
                db.execute(
                    "UPDATE effects SET payload_json='{}' WHERE effect_id=?", (effect_id,)
                )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction(write=True) as db:
                db.execute("DELETE FROM effects WHERE effect_id=?", (effect_id,))

    def test_effect_rejects_invalid_time_and_non_json_payload(self):
        with self.store.transaction(write=True) as db:
            with self.assertRaises(ValueError):
                self.store.enqueue_effect(
                    db, kind="dispatch", aggregate_id="m-1", idempotency_key="bad-time",
                    payload={}, due_at="tomorrow",
                )
            with self.assertRaises(HarnessError):
                self.store.enqueue_effect(
                    db, kind="dispatch", aggregate_id="m-1", idempotency_key="bad-json",
                    payload={"value": float("nan")},
                )

    def test_claim_and_settle_uses_generation_fencing_and_receipt(self):
        with self.store.transaction(write=True) as db:
            effect_id = self.store.enqueue_effect(
                db, kind="notify", aggregate_id="t-1", idempotency_key="msg-1", payload={"body": "hello"},
            )
        with self.store.transaction(write=True) as db:
            claimed = self.store.claim_effects(
                db, owner="worker-a", limit=10, lease_until="2999-01-01T00:00:00Z",
            )

            generation = claimed[0]["generation"]
        with self.store.transaction(write=True) as db:
            self.assertFalse(self.store.settle_effect(
                db, effect_id=effect_id, owner="worker-b", generation=generation,
                state="completed", receipt={"provider": "ok"},
            ))
            self.assertTrue(self.store.settle_effect(
                db, effect_id=effect_id, owner="worker-a", generation=generation,
                state="completed", receipt={"provider": "ok"},
            ))
        with self.store.transaction() as db:
            row = db.execute("SELECT state,receipt_json FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
        self.assertEqual((row["state"], row["receipt_json"]), ("completed", '{"provider":"ok"}'))

    def test_unknown_effect_requires_reconciliation_before_retry(self):
        with self.store.transaction(write=True) as db:
            effect_id = self.store.enqueue_effect(
                db, kind="dispatch", aggregate_id="m-1", idempotency_key="submit-unknown",
                payload={"run": "original"},
            )
            claimed = self.store.claim_effects(
                db, owner="worker-a", limit=1, lease_until="2999-01-01T00:00:00Z",
            )
            generation = claimed[0]["generation"]
            self.assertTrue(self.store.settle_effect(
                db, effect_id=effect_id, owner="worker-a", generation=generation,
                state="unknown", error="acknowledgement lost",
            ))
        with self.store.transaction(write=True) as db:
            self.assertFalse(self.store.settle_effect(
                db, effect_id=effect_id, owner="worker-a", generation=generation,
                state="completed", receipt={"late": "worker-receipt"},
            ))
            self.assertFalse(self.store.reconcile_effect(
                db, effect_id=effect_id, generation=generation + 1, state="retry_wait",
                receipt={"confirmed_not_started": True}, error="provider confirmed no send",
                due_at="2999-01-01T00:00:00Z",
            ))
            self.assertTrue(self.store.reconcile_effect(
                db, effect_id=effect_id, generation=generation, state="retry_wait",
                receipt={"confirmed_not_started": True}, error="provider confirmed no send",
                due_at="2999-01-01T00:00:00Z",
            ))
        with self.store.transaction(write=True) as db:
            self.assertEqual(self.store.claim_effects(
                db, owner="worker-b", limit=1, lease_until="2999-01-02T00:00:00Z",
            ), [])
            row = db.execute("SELECT state,generation FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
            self.assertEqual((row["state"], row["generation"]), ("retry_wait", generation))

    def test_claim_rollback_does_not_persist_lease(self):
        with self.store.transaction(write=True) as db:
            effect_id = self.store.enqueue_effect(
                db, kind="notify", aggregate_id="t-1", idempotency_key="claim-rollback", payload={},
            )
        with self.assertRaises(RuntimeError):
            with self.store.transaction(write=True) as db:
                self.store.claim_effects(
                    db, owner="worker-a", limit=1, lease_until="2999-01-01T00:00:00Z",
                )
                raise RuntimeError("crash before commit")
        with self.store.transaction() as db:
            row = db.execute("SELECT state,attempts,generation FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
        self.assertEqual(tuple(row), ("pending", 0, 0))

    def test_concurrent_claims_do_not_lease_the_same_effect_twice(self):
        with self.store.transaction(write=True) as db:

            self.store.enqueue_effect(

                db, kind="notify", aggregate_id="t-1", idempotency_key="claim-race", payload={},
            )
        def claim(owner):
            with self.store.transaction(write=True) as db:
                return self.store.claim_effects(
                    db, owner=owner, limit=1, lease_until="2999-01-01T00:00:00Z",
                )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ("worker-a", "worker-b")))
        self.assertEqual(sum(len(result) for result in results), 1)

    def test_cancel_only_applies_before_claim(self):
        with self.store.transaction(write=True) as db:
            pending_id = self.store.enqueue_effect(
                db, kind="dispatch", aggregate_id="m-1", idempotency_key="cancel-pending", payload={},
            )
            self.assertTrue(self.store.cancel_effect(db, effect_id=pending_id))
            leased_id = self.store.enqueue_effect(
                db, kind="dispatch", aggregate_id="m-1", idempotency_key="cancel-leased", payload={},
            )
            self.store.claim_effects(db, owner="worker-a", limit=1, lease_until="2999-01-01T00:00:00Z")
            self.assertFalse(self.store.cancel_effect(db, effect_id=leased_id))

    def test_receipt_write_failure_keeps_effect_for_reconciliation(self):
        with self.store.transaction(write=True) as db:
            effect_id = self.store.enqueue_effect(
                db, kind="dispatch", aggregate_id="m-1", idempotency_key="receipt-write-failure",
                payload={"run": "original"},
            )
            claimed = self.store.claim_effects(
                db, owner="worker-a", limit=1, lease_until="2999-01-01T00:00:00Z",
            )
            generation = claimed[0]["generation"]
        with self.assertRaises(HarnessError):
            with self.store.transaction(write=True) as db:
                self.store.settle_effect(
                    db, effect_id=effect_id, owner="worker-a", generation=generation,
                    state="completed", receipt={"invalid": float("nan")},
                )
        with self.store.transaction(write=True) as db:
            row = db.execute(
                "SELECT state,receipt_json,lease_owner FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            self.assertEqual(tuple(row), ("leased", None, "worker-a"))
            db.execute(
                "UPDATE effects SET lease_until='2000-01-01T00:00:00.000Z' WHERE effect_id=?",
                (effect_id,),
            )
            unknown = self.store.mark_expired_effects_unknown(db)
            self.assertEqual(len(unknown), 1)
            self.assertTrue(self.store.reconcile_effect(
                db, effect_id=effect_id, generation=generation, state="completed",
                receipt={"provider": "confirmed", "remote_id": "remote-2"},
            ))

    def test_expired_lease_moves_to_unknown_and_requires_reconciliation(self):
        with self.store.transaction(write=True) as db:
            effect_id = self.store.enqueue_effect(
                db, kind="dispatch", aggregate_id="m-1", idempotency_key="expired-reconcile",
                payload={"run": "same"},
            )
            claimed = self.store.claim_effects(
                db, owner="worker-a", limit=1, lease_until="2999-01-01T00:00:00Z",
            )
            generation = claimed[0]["generation"]
            db.execute(
                "UPDATE effects SET lease_until='2000-01-01T00:00:00.000Z' WHERE effect_id=?",
                (effect_id,),
            )
        with self.store.transaction(write=True) as db:
            unknown = self.store.mark_expired_effects_unknown(db, limit=10)
            self.assertEqual(len(unknown), 1)
            self.assertEqual(unknown[0]["state"], "unknown")
            self.assertEqual(unknown[0]["generation"], generation)
            self.assertIsNone(unknown[0]["lease_owner"])
            self.assertEqual(self.store.mark_expired_effects_unknown(db), [])
            self.assertEqual(self.store.claim_effects(
                db, owner="worker-b", limit=1, lease_until="2999-01-02T00:00:00Z",
            ), [])
            self.assertTrue(self.store.reconcile_effect(
                db, effect_id=effect_id, generation=generation, state="retry_wait",
                receipt={"provider_lookup": "not_found", "retry_safe": True},
                error="provider confirmed no operation", due_at="2000-01-01T00:00:00Z",
            ))
        with self.store.transaction(write=True) as db:
            claimed_again = self.store.claim_effects(
                db, owner="worker-b", limit=1, lease_until="2999-01-03T00:00:00Z",
            )
            self.assertEqual(len(claimed_again), 1)
            self.assertEqual(claimed_again[0]["generation"], generation + 1)

    def test_process_crash_before_claim_commit_rolls_back_to_pending(self):
        with self.store.transaction(write=True) as db:
            effect_id = self.store.enqueue_effect(
                db, kind="dispatch", aggregate_id="m-1", idempotency_key="crash-before-commit",
                payload={"run": "original"},
            )
        code = (
            "import os,sys\n"
            "from harness.app.superagent_coordinator_store import CoordinatorStore\n"
            "store=CoordinatorStore(sys.argv[1],instance_id='instance-a',identity={'profile':'local'})\n"
            "with store.transaction(write=True) as db:\n"
            "    claimed=store.claim_effects(db,owner='crashed-worker',limit=1,lease_until='2999-01-01T00:00:00Z')\n"
            "    assert len(claimed)==1\n"
            "    os._exit(73)\n"
        )
        result = subprocess.run([sys.executable, "-c", code, str(self.path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 73, result.stderr)
        reopened = CoordinatorStore(self.path, instance_id="instance-a", identity={"profile": "local"})
        with reopened.transaction(write=True) as db:
            row = db.execute(
                "SELECT state,attempts,generation FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            self.assertEqual(tuple(row), ("pending", 0, 0))
            claimed = reopened.claim_effects(
                db, owner="recovered-worker", limit=1, lease_until="2999-01-02T00:00:00Z",
            )
            self.assertEqual(len(claimed), 1)

    def test_process_crash_after_claim_commit_requires_unknown_reconciliation(self):
        with self.store.transaction(write=True) as db:
            effect_id = self.store.enqueue_effect(
                db, kind="dispatch", aggregate_id="m-1", idempotency_key="crash-after-commit",
                payload={"run": "original"},
            )
        code = (
            "import os,sys\n"
            "from harness.app.superagent_coordinator_store import CoordinatorStore\n"
            "store=CoordinatorStore(sys.argv[1],instance_id='instance-a',identity={'profile':'local'})\n"
            "with store.transaction(write=True) as db:\n"
            "    claimed=store.claim_effects(db,owner='crashed-worker',limit=1,lease_until='2999-01-01T00:00:00Z')\n"
            "    assert len(claimed)==1\n"
            "os._exit(73)\n"
        )
        result = subprocess.run([sys.executable, "-c", code, str(self.path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 73, result.stderr)
        reopened = CoordinatorStore(self.path, instance_id="instance-a", identity={"profile": "local"})
        with reopened.transaction(write=True) as db:
            db.execute(
                "UPDATE effects SET lease_until='2000-01-01T00:00:00.000Z' WHERE effect_id=?",
                (effect_id,),
            )
            self.assertEqual(reopened.claim_effects(
                db, owner="other-worker", limit=1, lease_until="2999-01-02T00:00:00Z",

            ), [])
            unknown = reopened.mark_expired_effects_unknown(db)
            self.assertEqual(len(unknown), 1)
            self.assertEqual(unknown[0]["state"], "unknown")
            self.assertTrue(reopened.reconcile_effect(
                db, effect_id=effect_id, generation=unknown[0]["generation"],
                state="completed", receipt={"provider": "found", "remote_id": "remote-1"},
            ))
        with reopened.transaction() as db:
            row = db.execute(
                "SELECT state,receipt_json FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            self.assertEqual(row["state"], "completed")
            self.assertEqual(row["receipt_json"], '{"provider":"found","remote_id":"remote-1"}')

    def test_expired_lease_is_not_implicitly_reclaimed(self):
        with self.store.transaction(write=True) as db:
            effect_id = self.store.enqueue_effect(
                db, kind="dispatch", aggregate_id="m-1", idempotency_key="submit-expired", payload={},
            )
            claimed = self.store.claim_effects(
                db, owner="worker-a", limit=1, lease_until="2999-01-01T00:00:00Z",
            )

            self.assertEqual(len(claimed), 1)

            db.execute(
                "UPDATE effects SET lease_until='2000-01-01T00:00:00.000Z' WHERE effect_id=?",
                (effect_id,),
            )
        with self.store.transaction(write=True) as db:
            self.assertEqual(self.store.claim_effects(

                db, owner="worker-b", limit=1, lease_until="2999-01-01T00:00:00Z",

            ), [])
            row = db.execute("SELECT state,lease_owner FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
            self.assertEqual((row["state"], row["lease_owner"]), ("leased", "worker-a"))

    def test_normalized_aggregate_schema_and_constraints(self):
        with closing(sqlite3.connect(self.path)) as db:
            names = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            self.assertTrue({
                "tickets", "missions", "mission_plans", "jobs", "job_dependencies", "assignments"
            } <= names)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 8)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 8)

    def test_unit_of_work_commits_aggregate_audit_and_effect_together(self):
        with self.store.unit_of_work(actor={"kind": "human"}, correlation_id="corr-1") as uow:
            ticket = uow.tickets.create({
                "ticket_id": "ticket-1", "mandate_id": "mandate-1",
                "origin_trigger_id": "trigger-1", "title": "Build feature",
            }, effect={
                "kind": "ticket.accepted", "aggregate_id": "ticket:ticket-1",
                "idempotency_key": "ticket-1-accepted", "payload": {"ticket_id": "ticket-1"},
            })
            mission = uow.missions.create({
                "mission_id": "mission-1", "ticket_id": ticket["ticket_id"],
                "sequence": 1, "repo": "D:/repo", "base_commit": "a" * 40,
            })
            job = uow.jobs.create({
                "job_id": "job-1", "mission_id": mission["mission_id"],
                "plan_revision": 0, "node_id": "__system__plan",
                "role": "plan", "spec_hash": "b" * 64,
            })
            assignment = uow.assignments.create({
                "assignment_id": "assignment-1", "job_id": job["job_id"],
                "attempt_no": 1, "submission_key": "submit-1",
            })
        self.assertEqual(ticket["aggregate_revision"], 1)
        self.assertEqual(mission["aggregate_revision"], 1)
        self.assertEqual(job["aggregate_revision"], 1)
        self.assertEqual(assignment["aggregate_revision"], 1)
        with self.store.transaction() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM audit_events WHERE aggregate_id IN "
                "('ticket-1','mission-1','job-1','assignment-1')"
            ).fetchone()[0], 4)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM effects WHERE idempotency_key='ticket-1-accepted'"
            ).fetchone()[0], 1)
            effect = db.execute(
                "SELECT effect_id,correlation_id,last_event_id FROM effects WHERE idempotency_key='ticket-1-accepted'"
            ).fetchone()
            created = db.execute(
                "SELECT event_id FROM audit_events WHERE aggregate_type='ticket' AND aggregate_id='ticket-1'"
                " AND event_type='ticket.created'"
            ).fetchone()[0]
            enqueued = db.execute(
                "SELECT event_id,causation_id,correlation_id FROM audit_events "
                "WHERE aggregate_type='effect' AND aggregate_id=? AND event_type='effect.enqueued'",
                (effect["effect_id"],),
            ).fetchone()
            self.assertEqual(enqueued["causation_id"], created)
            self.assertEqual(enqueued["correlation_id"], "corr-1")
            self.assertEqual(effect["correlation_id"], "corr-1")
            self.assertEqual(effect["last_event_id"], enqueued["event_id"])

    def test_generic_effect_lifecycle_emits_causal_audit_chain(self):
        with self.store.unit_of_work(actor={"kind": "system"}, correlation_id="corr-generic") as uow:
            uow.tickets.create({
                "ticket_id": "ticket-generic-effect", "mandate_id": "mandate-generic-effect",
                "origin_trigger_id": "trigger-generic-effect", "title": "Generic Effect audit",
            }, effect={
                "kind": "ticket.notify", "aggregate_id": "ticket:ticket-generic-effect",
                "idempotency_key": "generic-effect-key", "payload": {"ticket_id": "ticket-generic-effect"},
            })
            effect_id = uow.db.execute(
                "SELECT effect_id FROM effects WHERE idempotency_key='generic-effect-key'"
            ).fetchone()[0]
        with self.store.transaction(write=True) as db:
            claimed = self.store.claim_effects(
                db, owner="generic-worker", limit=1, lease_until="2999-01-01T00:00:00Z",
            )
            self.assertEqual([row["effect_id"] for row in claimed], [effect_id])
        with self.store.transaction(write=True) as db:
            self.assertTrue(self.store.settle_effect(
                db, effect_id=effect_id, owner="generic-worker", generation=1,
                state="completed", receipt={"confirmed": True},
            ))
        with self.store.transaction() as db:
            effect = db.execute(
                "SELECT correlation_id,last_event_id FROM effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            events = db.execute(
                "SELECT event_id,event_type,causation_id,correlation_id FROM audit_events "
                "WHERE aggregate_type='effect' AND aggregate_id=? ORDER BY seq",
                (effect_id,),
            ).fetchall()
        self.assertEqual([row["event_type"] for row in events], [
            "effect.enqueued", "effect.leased", "effect.completed",
        ])
        for previous, current in zip(events, events[1:]):
            self.assertEqual(current["causation_id"], previous["event_id"])
            self.assertEqual(current["correlation_id"], "corr-generic")
        self.assertEqual(effect["correlation_id"], "corr-generic")
        self.assertEqual(effect["last_event_id"], events[-1]["event_id"])

    def test_audit_causation_must_reference_an_event_in_the_same_correlation(self):
        with self.store.transaction(write=True) as db:
            parent = self.store.append_audit(
                db, aggregate_type="ticket", aggregate_id="ticket-cause",
                event_type="ticket.created", payload={}, actor={"kind": "test"},
                correlation_id="corr-parent",
            )
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.append_audit(
                    db, aggregate_type="effect", aggregate_id="effect-cause",
                    event_type="effect.enqueued", payload={}, actor={"kind": "test"},
                    correlation_id="corr-other", causation_id=parent,
                )

    def test_audit_events_are_immutable_and_effect_cursors_are_correlation_bound(self):
        with self.store.transaction(write=True) as db:
            parent = self.store.append_audit(
                db, aggregate_type="ticket", aggregate_id="ticket-immutable",
                event_type="ticket.created", payload={}, actor={"kind": "test"},
                correlation_id="corr-immutable",
            )
            effect_id = self.store.enqueue_effect(
                db, kind="immutable.check", aggregate_id="ticket:ticket-immutable",
                idempotency_key="immutable-check", payload={},
                correlation_id="corr-immutable", causation_id=parent,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE audit_events SET event_type='ticket.changed' WHERE event_id=?",
                    (parent,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM audit_events WHERE event_id=?", (parent,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE effects SET correlation_id='corr-other' WHERE effect_id=?",
                    (effect_id,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE effects SET last_event_id='missing-event' WHERE effect_id=?",
                    (effect_id,),
                )

    def test_unit_of_work_rolls_back_record_audit_revision_and_effect_on_failure(self):
        with self.assertRaises(ValueError):
            with self.store.unit_of_work(actor={"kind": "system"}) as uow:
                uow.tickets.create({
                    "ticket_id": "ticket-rollback", "mandate_id": "mandate-rollback",
                    "origin_trigger_id": "trigger-rollback", "title": "Rollback",
                }, effect={"kind": "notify", "aggregate_id": "ticket:rollback",
                           "idempotency_key": "rollback-1"})
        self.assertIsNone(self.store.revision("ticket", "ticket-rollback"))
        with self.store.transaction() as db:
            self.assertIsNone(db.execute(
                "SELECT 1 FROM tickets WHERE ticket_id='ticket-rollback'"
            ).fetchone())
            self.assertIsNone(db.execute(
                "SELECT 1 FROM audit_events WHERE aggregate_id='ticket-rollback'"
            ).fetchone())
            self.assertIsNone(db.execute(
                "SELECT 1 FROM effects WHERE idempotency_key='rollback-1'"
            ).fetchone())

    def test_repository_cas_rejects_stale_write_without_partial_audit(self):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            uow.tickets.create({
                "ticket_id": "ticket-cas", "mandate_id": "mandate-cas",
                "origin_trigger_id": "trigger-cas", "title": "Initial",
            })
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            updated = uow.tickets.update(
                "ticket-cas", expected_revision=1, changes={"title": "Updated"},
                event_type="ticket.renamed",
            )
            self.assertEqual(updated["aggregate_revision"], 2)
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            stale = uow.tickets.update(
                "ticket-cas", expected_revision=1, changes={"title": "Stale"},
                event_type="ticket.renamed",
            )
            self.assertIsNone(stale)
        self.assertEqual(self.store.revision("ticket", "ticket-cas"), 2)
        with self.store.transaction() as db:
            self.assertEqual(db.execute(

                "SELECT title FROM tickets WHERE ticket_id='ticket-cas'"
            ).fetchone()[0], "Updated")

            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM audit_events WHERE aggregate_id='ticket-cas'"
            ).fetchone()[0], 2)

    def test_repository_state_changes_must_pass_through_application_service(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            ticket = uow.tickets.create({
                "ticket_id": "ticket-state-guard", "mandate_id": "mandate-state-guard",
                "origin_trigger_id": "trigger-state-guard", "title": "State guard",
            })
            mission = uow.missions.create({
                "mission_id": "mission-state-guard", "ticket_id": ticket["ticket_id"],
                "sequence": 1, "repo": "D:/repo", "base_commit": "a" * 40,
            })
            job = uow.jobs.create({
                "job_id": "job-state-guard", "mission_id": mission["mission_id"],
                "plan_revision": 0, "node_id": "__system__plan", "role": "plan",
                "spec_hash": "b" * 64,
            })
            assignment = uow.assignments.create({
                "assignment_id": "assignment-state-guard", "job_id": job["job_id"],
                "attempt_no": 1, "submission_key": "submission-state-guard",
            })
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            cases = (
                (uow.tickets, ticket["ticket_id"], "ended"),
                (uow.missions, mission["mission_id"], "unknown"),
                (uow.jobs, job["job_id"], "unknown"),
                (uow.assignments, assignment["assignment_id"], "unknown"),
            )
            for repository, identity, target in cases:
                with self.subTest(aggregate=identity):
                    with self.assertRaises(PermissionError):
                        repository.update(
                            identity, expected_revision=1,
                            changes={"state": target}, event_type="bypass.attempt",
                        )
        self.assertEqual(self.store.revision("ticket", ticket["ticket_id"]), 1)
        self.assertEqual(self.store.revision("mission", mission["mission_id"]), 1)
        self.assertEqual(self.store.revision("job", job["job_id"]), 1)
        self.assertEqual(self.store.revision("assignment", assignment["assignment_id"]), 1)

    def test_assignment_run_identity_can_be_set_once_but_never_replaced(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            uow.tickets.create({
                "ticket_id": "ticket-run", "mandate_id": "mandate-run",
                "origin_trigger_id": "trigger-run", "title": "Run identity",
            })
            uow.missions.create({

                "mission_id": "mission-run", "ticket_id": "ticket-run",

                "sequence": 1, "repo": "D:/repo", "base_commit": "c" * 40,
            })

            uow.jobs.create({
                "job_id": "job-run", "mission_id": "mission-run", "plan_revision": 0,

                "node_id": "__system__plan", "role": "plan", "spec_hash": "d" * 64,

            })
            uow.assignments.create({
                "assignment_id": "assignment-run", "job_id": "job-run",
                "attempt_no": 1, "submission_key": "submit-run",
            })
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            updated = uow.assignments.update(
                "assignment-run", expected_revision=1, changes={"run_id": "run-1"},
                event_type="assignment.run_bound",
            )
            self.assertEqual(updated["run_id"], "run-1")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.unit_of_work(actor={"kind": "system"}) as uow:
                uow.assignments.update(
                    "assignment-run", expected_revision=2, changes={"run_id": "run-2"},
                    event_type="assignment.run_replaced",
                )
        with self.store.transaction() as db:
            self.assertEqual(db.execute(
                "SELECT run_id,aggregate_revision FROM assignments WHERE assignment_id='assignment-run'"
            ).fetchone()[:], ("run-1", 2))

    def test_assignment_and_mission_uniqueness_are_enforced(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            uow.tickets.create({
                "ticket_id": "ticket-unique", "mandate_id": "mandate-unique",
                "origin_trigger_id": "trigger-unique", "title": "Unique",
            })

            uow.missions.create({

                "mission_id": "mission-unique", "ticket_id": "ticket-unique",

                "sequence": 1, "repo": "D:/repo", "base_commit": "e" * 40,

            })

            uow.jobs.create({
                "job_id": "job-unique", "mission_id": "mission-unique",
                "plan_revision": 0, "node_id": "__system__plan", "role": "plan",
                "spec_hash": "f" * 64,
            })
            uow.assignments.create({
                "assignment_id": "assignment-unique", "job_id": "job-unique",
                "attempt_no": 1, "submission_key": "submit-unique",
            })
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.unit_of_work(actor={"kind": "system"}) as uow:
                uow.assignments.create({
                    "assignment_id": "assignment-duplicate", "job_id": "job-unique",
                    "attempt_no": 1, "submission_key": "submit-duplicate",
                })

    def test_active_mission_and_assignment_uniqueness_are_database_enforced(self):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            uow.tickets.create({
                "ticket_id": "ticket-active", "mandate_id": "mandate-active",
                "origin_trigger_id": "trigger-active", "title": "Active ownership",
            })
            uow.missions.create({
                "mission_id": "mission-active-1", "ticket_id": "ticket-active",
                "sequence": 1, "repo": "D:/repo", "base_commit": "a" * 40,
            })
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.unit_of_work(actor={"kind": "system"}) as uow:
                uow.missions.create({
                    "mission_id": "mission-active-2", "ticket_id": "ticket-active",
                    "sequence": 2, "repo": "D:/repo", "base_commit": "b" * 40,
                })
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            uow.missions._transition(
                "mission-active-1", expected_revision=1,
                changes={"state": "completed"}, event_type="mission.completed",
            )
            mission = uow.missions.create({
                "mission_id": "mission-active-2", "ticket_id": "ticket-active",
                "sequence": 2, "repo": "D:/repo", "base_commit": "b" * 40,
            })
            job = uow.jobs.create({
                "job_id": "job-active", "mission_id": mission["mission_id"],
                "plan_revision": 0, "node_id": "__system__plan", "role": "plan",
                "spec_hash": "c" * 64,
            })
            uow.assignments.create({
                "assignment_id": "assignment-active-1", "job_id": job["job_id"],
                "attempt_no": 1, "submission_key": "submission-active-1",
            })
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.unit_of_work(actor={"kind": "system"}) as uow:
                uow.assignments.create({
                    "assignment_id": "assignment-active-2", "job_id": "job-active",
                    "attempt_no": 2, "submission_key": "submission-active-2",
                })
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            uow.assignments._transition(
                "assignment-active-1", expected_revision=1,
                changes={"state": "failed"}, event_type="assignment.failed",
            )
            retry = uow.assignments.create({
                "assignment_id": "assignment-active-2", "job_id": "job-active",
                "attempt_no": 2, "submission_key": "submission-active-2",
            })
            self.assertEqual(retry["attempt_no"], 2)

    def test_outbox_effect_and_message_move_atomically_through_unknown_and_retry(self):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            uow.tickets.create({
                "ticket_id": "ticket-outbox", "mandate_id": "mandate-outbox",
                "origin_trigger_id": "trigger-outbox", "title": "Notify requester",
            })
            message = uow.outbox.create({
                "message_id": "message-1", "ticket_id": "ticket-outbox",
                "channel_id": "channel-1", "kind": "progress",
                "body_ref": "artifact://body/1", "body_hash": "a" * 64,
                "idempotency_key": "outbox-message-1",
            })
            self.assertEqual(message["state"], "pending")
            effect = uow.db.execute(
                "SELECT effect_id FROM outbox_effect_links WHERE message_id='message-1'"
            ).fetchone()
            self.assertIsNotNone(effect)
            self.assertEqual(uow.db.execute(
                "SELECT state FROM effects WHERE effect_id=?", (effect["effect_id"],)
            ).fetchone()[0], "pending")

        outbox = OutboxEffectService(self.store)
        with self.store.transaction(write=True) as db:
            self.assertEqual(
                self.store.claim_effects(
                    db, owner="generic-worker", limit=10, lease_until="2999-01-01T00:00:00Z",
                ),
                [],
            )
        claimed = outbox.claim(owner="worker-a", limit=1, lease_until="2999-01-01T00:00:00Z")
        self.assertEqual(claimed[0]["state"], "leased")
        with self.store.transaction() as db:
            message = db.execute(
                "SELECT state,attempts,aggregate_revision FROM outbox_messages WHERE message_id='message-1'"
            ).fetchone()
            self.assertEqual(tuple(message), ("sending", 1, 2))
            self.assertEqual(db.execute(
                "SELECT state FROM effects WHERE effect_id=?", (effect["effect_id"],)
            ).fetchone()[0], "leased")

        self.assertTrue(outbox.settle(
            effect_id=effect["effect_id"], owner="worker-a", generation=1,
            state="unknown", error="provider acknowledgement lost",
        ))
        with self.store.transaction() as db:
            self.assertEqual(db.execute(
                "SELECT state FROM outbox_messages WHERE message_id='message-1'"
            ).fetchone()[0], "unknown")

        self.assertTrue(outbox.reconcile(
            effect_id=effect["effect_id"], generation=1, state="retry_wait",
            receipt={"idempotency_key": "outbox-message-1", "retry_safe": True},
            error="provider confirmed no send", due_at="2000-01-01T00:00:00Z",
        ))
        retried = outbox.claim(owner="worker-b", limit=1, lease_until="2999-01-02T00:00:00Z")
        self.assertEqual((retried[0]["generation"], retried[0]["attempts"]), (2, 2))
        self.assertTrue(outbox.settle(
            effect_id=effect["effect_id"], owner="worker-b", generation=2,
            state="completed",
            receipt={
                "idempotency_key": "outbox-message-1", "provider_id": "provider-a",
                "sent_at": "2026-11-04T16:00:00Z",
            },
        ))
        with self.store.transaction() as db:
            message = db.execute(
                "SELECT state,provider_id,provider_key,aggregate_revision FROM outbox_messages "
                "WHERE message_id='message-1'"
            ).fetchone()
            self.assertEqual(tuple(message), ("sent", "provider-a", "outbox-message-1", 6))
            self.assertEqual(db.execute(
                "SELECT state FROM effects WHERE effect_id=?", (effect["effect_id"],)
            ).fetchone()[0], "completed")

        with self.assertRaises(ValueError):
            with self.store.unit_of_work(actor={"kind": "system"}) as uow:
                uow.outbox.update(
                    "message-1", expected_revision=5, changes={"state": "retry_wait"},
                    event_type="outbox.illegal_direct_transition",
                )

    def test_outbox_sent_requires_provider_receipt_and_is_terminal(self):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            uow.tickets.create({
                "ticket_id": "ticket-sent", "mandate_id": "mandate-sent",
                "origin_trigger_id": "trigger-sent", "title": "Send result",
            })
            uow.outbox.create({
                "message_id": "message-sent", "ticket_id": "ticket-sent",
                "channel_id": "channel-sent", "kind": "result",
                "body_ref": "artifact://body/sent", "body_hash": "b" * 64,
                "idempotency_key": "outbox-sent",
            })
            effect_id = uow.db.execute(
                "SELECT effect_id FROM outbox_effect_links WHERE message_id='message-sent'"
            ).fetchone()[0]
        outbox = OutboxEffectService(self.store)
        outbox.claim(owner="worker-sent", limit=1, lease_until="2999-01-01T00:00:00Z")
        with self.assertRaises(ValueError):
            outbox.settle(
                effect_id=effect_id, owner="worker-sent", generation=1,
                state="completed", receipt=None,
            )
        sent = outbox.settle(
            effect_id=effect_id, owner="worker-sent", generation=1,
            state="completed",
            receipt={
                "idempotency_key": "outbox-sent", "provider_id": "provider-a",
                "sent_at": "2026-11-04T16:00:00Z",
            },
        )
        self.assertTrue(sent)
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT state,provider_id,provider_key,aggregate_revision FROM outbox_messages "
                "WHERE message_id='message-sent'"
            ).fetchone()
            self.assertEqual(tuple(row), ("sent", "provider-a", "outbox-sent", 3))
        with self.assertRaises(ValueError):
            with self.store.unit_of_work(actor={"kind": "system"}) as uow:
                uow.outbox.update(
                    "message-sent", expected_revision=3, changes={"state": "retry_wait"},
                    event_type="outbox.retry_after_sent",
                )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.unit_of_work(actor={"kind": "system"}) as uow:
                uow.outbox.update(
                    "message-sent", expected_revision=3, changes={"provider_key": "key-b"},
                    event_type="outbox.rekeyed",
                )

    def test_outbox_provider_receipt_must_match_original_idempotency_key(self):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            uow.tickets.create({
                "ticket_id": "ticket-provider", "mandate_id": "mandate-provider",
                "origin_trigger_id": "trigger-provider", "title": "Provider identity",
            })
            uow.outbox.create({
                "message_id": "message-provider", "ticket_id": "ticket-provider",
                "channel_id": "channel", "kind": "result",
                "body_ref": "artifact://body/provider", "body_hash": "c" * 64,
                "idempotency_key": "original-provider-key",
            })
            effect_id = uow.db.execute(
                "SELECT effect_id FROM outbox_effect_links WHERE message_id='message-provider'"
            ).fetchone()[0]
        outbox = OutboxEffectService(self.store)
        outbox.claim(owner="provider-worker", limit=1, lease_until="2999-01-01T00:00:00Z")
        with self.assertRaises(ValueError):
            outbox.settle(
                effect_id=effect_id, owner="provider-worker", generation=1,
                state="completed",
                receipt={
                    "idempotency_key": "different-key", "provider_id": "provider",
                    "sent_at": "2026-11-04T16:00:00Z",
                },
            )
        self.assertTrue(outbox.settle(
            effect_id=effect_id, owner="provider-worker", generation=1,
            state="completed",
            receipt={
                "idempotency_key": "original-provider-key", "provider_id": "provider",
                "sent_at": "2026-11-04T16:00:00Z",
            },
        ))

    def _create_mission(self, uow, suffix="plan"):
        uow.tickets.create({
            "ticket_id": f"ticket-{suffix}", "mandate_id": f"mandate-{suffix}",
            "origin_trigger_id": f"trigger-{suffix}", "title": "Plan repository",
        })
        return uow.missions.create({
            "mission_id": f"mission-{suffix}", "ticket_id": f"ticket-{suffix}",
            "sequence": 1, "repo": "D:/repo", "base_commit": "a" * 40,
        })

    def test_plan_revision_repository_persists_immutable_snapshot_and_dag(self):
        with self.store.unit_of_work(actor={"kind": "human"}, correlation_id="plan-correlation") as uow:
            mission = self._create_mission(uow)
            plan = uow.plans.create(

                mission["mission_id"], expected_mission_revision=1,

                plan={"nodes": [

                    {"node_id": "design", "role": "work", "profile_id": "planner",

                     "instruction_ref": "artifact://instructions/design", "dependencies": []},
                    {"node_id": "implement", "role": "work", "profile_id": "coder",
                     "dependencies": [{"node_id": "design", "requires_delivery": True}]},
                    {"node_id": "verify", "role": "work", "profile_id": "tester",
                     "dependencies": [{"node_id": "implement", "requires_delivery": False}]},
                ]},
                acceptance={"criteria": ["tests pass", "evidence recorded"]},
                created_by_kind="human", created_by_ref="actor:user-1",
            )
            self.assertEqual(plan["plan_revision"], 1)
            self.assertEqual(len(plan["jobs"]), 3)
            self.assertEqual(len(plan["dependencies"]), 2)
            with self.assertRaises(sqlite3.IntegrityError):
                uow.db.execute(
                    "INSERT INTO jobs(job_id,mission_id,plan_revision,node_id,role,state,spec_hash,aggregate_revision) "
                    "VALUES('late-job',?,1,'late-node','work','pending',?,1)",
                    (mission["mission_id"], "e" * 64),
                )
            self.assertEqual(
                [(edge["node_id"], edge["depends_on_node_id"], edge["requires_delivery"])
                 for edge in plan["dependencies"]],
                [("implement", "design", 1), ("verify", "implement", 0)],
            )
            self.assertEqual(plan["plan_hash"], __import__("hashlib").sha256(
                __import__("json").dumps(
                    plan["plan_json"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest())
            self.assertEqual(uow.missions.get(mission["mission_id"])["plan_revision"], 1)
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            self.assertEqual(len(uow.plans.list_for_mission("mission-plan")), 1)
            with self.assertRaises(sqlite3.IntegrityError):
                uow.db.execute(
                    "UPDATE mission_plans SET plan_json='{}' WHERE mission_id='mission-plan' AND plan_revision=1"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                uow.db.execute(
                    "DELETE FROM job_dependencies WHERE mission_id='mission-plan' AND plan_revision=1"
                )

    def test_job_planned_spec_is_immutable_after_plan_creation(self):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            mission = self._create_mission(uow, "job-spec")
            plan = uow.plans.create(
                mission["mission_id"], expected_mission_revision=1,
                plan={"nodes": [{"node_id": "work", "role": "work", "profile_id": "coder"}]},
                acceptance={"criteria": ["done"]},
                created_by_kind="human", created_by_ref="actor:user-1",
            )
            job_id = plan["jobs"][0]["job_id"]
            with self.assertRaises(ValueError):
                uow.jobs.update(
                    job_id, expected_revision=1, changes={"profile_id": "different"},
                    event_type="job.profile_rewritten",
                )
            with self.assertRaises(sqlite3.IntegrityError):
                uow.db.execute("UPDATE jobs SET spec_hash=? WHERE job_id=?", ("f" * 64, job_id))

    def test_plan_read_detects_job_spec_materialization_mismatch(self):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            mission = self._create_mission(uow, "job-spec-tamper")
            plan = uow.plans.create(
                mission["mission_id"], expected_mission_revision=1,
                plan={"nodes": [{"node_id": "work", "role": "work", "profile_id": "coder"}]},
                acceptance={"criteria": ["done"]},
                created_by_kind="human", created_by_ref="actor:user-1",
            )
            job_id = plan["jobs"][0]["job_id"]
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            uow.db.execute("DROP TRIGGER jobs_plan_spec_immutable")
            uow.db.execute("UPDATE jobs SET spec_hash=? WHERE job_id=?", ("f" * 64, job_id))
            with self.assertRaises(HarnessError):
                uow.plans.get(mission["mission_id"], 1)

    def test_plan_revision_read_detects_snapshot_hash_mismatch(self):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            mission = self._create_mission(uow, "tamper")
            uow.plans.create(
                mission["mission_id"], expected_mission_revision=1,
                plan={"nodes": [{"node_id": "work", "role": "work", "profile_id": "coder"}]},
                acceptance={"criteria": ["done"]},
                created_by_kind="human", created_by_ref="actor:user-1",
            )
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            uow.db.execute("DROP TRIGGER mission_plans_immutable_update")
            uow.db.execute(
                "UPDATE mission_plans SET plan_json='{}' WHERE mission_id=? AND plan_revision=1",
                (mission["mission_id"],),
            )
            with self.assertRaises(HarnessError):
                uow.plans.get(mission["mission_id"], 1)

    def test_plan_revision_rejects_cycle_and_rolls_back_everything(self):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            mission = self._create_mission(uow, "cycle")
        with self.assertRaises(ValueError):
            with self.store.unit_of_work(actor={"kind": "human"}) as uow:
                uow.plans.create(
                    mission["mission_id"], expected_mission_revision=1,
                    plan={"nodes": [
                        {"node_id": "a", "role": "work", "profile_id": "coder",
                         "dependencies": [{"node_id": "b"}]},
                        {"node_id": "b", "role": "work", "profile_id": "coder",
                         "dependencies": [{"node_id": "a"}]},
                    ]},
                    acceptance={"criteria": ["done"]},
                    created_by_kind="human", created_by_ref="actor:user-1",
                )
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            self.assertEqual(uow.missions.get(mission["mission_id"])["plan_revision"], 0)
            self.assertEqual(uow.plans.list_for_mission(mission["mission_id"]), [])
            self.assertEqual(uow.db.execute(
                "SELECT COUNT(*) FROM jobs WHERE mission_id=?", (mission["mission_id"],)
            ).fetchone()[0], 0)

    def test_plan_revision_rejects_foreign_dependency_and_reserved_node(self):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            mission = self._create_mission(uow, "invalid")
        invalid_plans = [
            {"nodes": [{"node_id": "node-a", "role": "work", "profile_id": "coder",
                        "dependencies": [{"node_id": "missing"}]}]},
            {"nodes": [{"node_id": "__system__work", "role": "work", "profile_id": "coder"}]},
            {"nodes": [{"node_id": "node-a", "role": "plan", "profile_id": "planner"}]},
        ]
        for index, plan in enumerate(invalid_plans):
            with self.assertRaises(ValueError):
                with self.store.unit_of_work(actor={"kind": "human"}) as uow:
                    uow.plans.create(
                        mission["mission_id"], expected_mission_revision=1,
                        plan=plan, acceptance={"criteria": ["done"]},
                        created_by_kind="human", created_by_ref=f"actor:user-{index}",
                    )

    def test_plan_revision_requires_same_mission_planner_assignment(self):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            mission = self._create_mission(uow, "planner")
            uow.jobs.create({
                "job_id": "planner-job", "mission_id": mission["mission_id"],
                "plan_revision": 0, "node_id": "__system__plan", "role": "plan",
                "spec_hash": "d" * 64,

            })
            uow.assignments.create({
                "assignment_id": "planner-assignment", "job_id": "planner-job",
                "attempt_no": 1, "submission_key": "planner-submit",
            })
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            with self.assertRaises(ValueError):
                uow.plans.create(
                    mission["mission_id"], expected_mission_revision=1,
                    plan={"nodes": [{"node_id": "work", "role": "work", "profile_id": "coder"}]},
                    acceptance={"criteria": ["done"]},
                    created_by_kind="assignment", created_by_ref="not-this-assignment",
                )
            plan = uow.plans.create(
                mission["mission_id"], expected_mission_revision=1,
                plan={"nodes": [{"node_id": "work", "role": "work", "profile_id": "coder"}]},
                acceptance={"criteria": ["done"]},
                created_by_kind="assignment", created_by_ref="planner-assignment",
            )
            self.assertEqual(plan["created_by_ref"], "planner-assignment")

    def test_mission_plan_pointer_cannot_be_changed_without_new_revision(self):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            mission = self._create_mission(uow, "pointer")
            with self.assertRaises(sqlite3.IntegrityError):
                uow.db.execute(
                    "UPDATE missions SET plan_revision=9 WHERE mission_id=?",
                    (mission["mission_id"],),
                )
        with self.assertRaises(ValueError):
            with self.store.unit_of_work(actor={"kind": "human"}) as uow:
                uow.missions.update(
                    mission["mission_id"], expected_revision=1,
                    changes={"plan_revision": 9}, event_type="mission.plan_skipped",
                )

    def test_database_rejects_dependency_cycle_even_without_repository(self):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            mission = self._create_mission(uow, "trigger-cycle")
            plan = uow.plans.create(
                mission["mission_id"], expected_mission_revision=1,
                plan={"nodes": [
                    {"node_id": "a", "role": "work", "profile_id": "coder"},
                    {"node_id": "b", "role": "work", "profile_id": "coder",
                     "dependencies": [{"node_id": "a"}]},
                ]},
                acceptance={"criteria": ["done"]},
                created_by_kind="human", created_by_ref="actor:user-1",
            )
            ids = {job["node_id"]: job["job_id"] for job in plan["jobs"]}
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.unit_of_work(actor={"kind": "system"}) as uow:
                uow.db.execute(
                    "INSERT INTO job_dependencies(job_id,depends_on_job_id,mission_id,plan_revision,ordinal,requires_delivery) "
                    "VALUES(?,?,?,?,?,?)",
                    (ids["a"], ids["b"], "mission-trigger-cycle", 1, 0, 0),
                )

"""统一协调模型的作用域、计划封存和投递身份回归。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harness.app.superagent_coordinator_store import CoordinatorStore, _hash, _json, _now
from harness.core.errors import HarnessError
from harness.app.superagent_authorization import CoordinatorAuthorizationEngine
from harness.app.superagent_source_registry import SourceRegistry
from harness.app.superagent_coordination import CoordinatorInputDelivery


class UnifiedModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CoordinatorStore(Path(self.temp.name) / "superagent.sqlite3",
                                      instance_id="unified", identity={})

    def plan(self, uow, suffix="one", *, system=True):
        ticket = uow.tickets.create({"ticket_id": "ticket-" + suffix,
            "mandate_id": "mandate-" + suffix, "origin_trigger_id": "trigger-" + suffix,
            "title": "统一交付"})
        mission = uow.missions.create({"mission_id": "mission-" + suffix,
            "ticket_id": ticket["ticket_id"], "sequence": 1, "repo": "/repo", "base_commit": "a" * 40})
        return uow.plans.create(mission["mission_id"], expected_mission_revision=1,
            plan={"nodes": [{"node_id": name, "role": "work", "profile_id": "builder",
                             "dependencies": []} for name in ("first", "second")]},
            acceptance={"kind": "artifact_review", "criteria": [{"id": "files", "kind": "inspection"}]},
            created_by_kind="host", created_by_ref="test",
            system_profiles={"integrate": "builder", "review": "reviewer"} if system else None)

    def worker(self, uow, job, stage=1):
        identity = f"worker:{job['job_id']}:{stage}"
        return uow.workers.create({"worker_id": identity, "ticket_id": "ticket-one",
            "mission_id": job["mission_id"], "job_id": job["job_id"], "stage": stage,
            "profile_id": job["profile_id"], "repo": "/repo", "base_commit": "a" * 40,
            "runtime_instance_id": "unified", "identity_hash": _hash(identity)})

    def test_host_materializes_system_roles_and_seals_the_complete_plan(self):
        with self.store.unit_of_work(actor={}) as uow:
            plan = self.plan(uow)
            self.assertEqual({j["role"] for j in plan["jobs"]}, {"work", "integrate", "review"})
            self.assertEqual(len(plan["jobs"]), 4)
            edges = {(d["node_id"], d["depends_on_node_id"]) for d in plan["dependencies"]}
            self.assertEqual(edges, {("__system__integrate", "first"),
                ("__system__integrate", "second"), ("__system__review", "__system__integrate")})
            self.assertTrue(all(d["requires_delivery"] for d in plan["dependencies"]))
            with self.assertRaises(sqlite3.IntegrityError):
                uow.db.execute("UPDATE jobs SET profile_id='other' WHERE job_id=?", (plan["jobs"][0]["job_id"],))
        with self.store.unit_of_work(actor={}) as uow:
            self.assertEqual(uow.plans.get("mission-one", 1)["plan_hash"], plan["plan_hash"])

    def test_same_profile_has_independent_node_and_continuation_resources(self):
        with self.store.unit_of_work(actor={}) as uow:
            jobs = [j for j in self.plan(uow)["jobs"] if j["role"] == "work"]
            workers = [self.worker(uow, jobs[0]), self.worker(uow, jobs[1]), self.worker(uow, jobs[0], 2)]
            self.assertEqual(len({w["worker_id"] for w in workers}), 3)
            with self.assertRaises(sqlite3.IntegrityError):
                uow.db.execute("UPDATE workers SET job_id=? WHERE worker_id=?",
                               (jobs[1]["job_id"], workers[0]["worker_id"]))

    def test_worker_rejects_foreign_mission_and_wrong_profile_before_resource_creation(self):
        with self.store.unit_of_work(actor={}) as uow:
            job = self.plan(uow)["jobs"][0]
            self.plan(uow, "two")
        for changes in ({"mission_id": "mission-two"}, {"profile_id": "foreign"}, {"base_commit": "b" * 40}):
            with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError):
                with self.store.unit_of_work(actor={}) as uow:
                    self.worker(uow, {**job, **changes}) if "base_commit" not in changes else uow.workers.create({
                        "worker_id": "bad", "ticket_id": "ticket-one", "mission_id": job["mission_id"],
                        "job_id": job["job_id"], "stage": 1, "profile_id": job["profile_id"],
                        "repo": "/repo", "base_commit": changes["base_commit"], "runtime_instance_id": "unified",
                        "identity_hash": "a" * 64})

    def test_planner_cannot_propose_system_roles(self):
        with self.assertRaises(ValueError):
            with self.store.unit_of_work(actor={}) as uow:
                plan = self.plan(uow, system=False)
                uow.plans.create("mission-one", expected_mission_revision=2,
                    plan={"nodes": [{"node_id": "review", "role": "review", "profile_id": "reviewer"}]},
                    acceptance={}, created_by_kind="host", created_by_ref="test")
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM missions").fetchone()[0], 0)

    def test_input_has_independent_assignment_receipts_and_cannot_cross_tickets(self):
        authorization = CoordinatorAuthorizationEngine(self.store)
        sources = SourceRegistry(self.store, ingest=authorization.receive_normalized)
        sources.create_instance("local", kind="local", config={"namespace": "test"})
        received = sources.receive_local("local", {"event_id": "event", "thread_id": "thread",
            "text": "input", "occurred_at": "2026-11-06T00:00:00Z"})
        with self.store.unit_of_work(actor={}) as uow:
            plan = self.plan(uow)
            other = self.plan(uow, "two")
            uow.db.execute("INSERT INTO ticket_inputs(input_id,ticket_id,trigger_id,kind,context_only,state,created_at) "
                           "VALUES('input','ticket-one',?,'trigger',0,'waiting',?)", (received["trigger_id"], _now()))
            for number, job in enumerate([plan["jobs"][0], plan["jobs"][1], other["jobs"][0]]):
                uow.assignments.create({"assignment_id": f"assignment-{number}", "job_id": job["job_id"],
                    "attempt_no": 1, "submission_key": f"submission-{number}"})
        deliveries = CoordinatorInputDelivery(self.store)
        first = deliveries.prepare("input", "assignment-0", actor={})
        self.assertEqual(deliveries.prepare("input", "assignment-0", actor={}), first)
        first = deliveries.transition("input", "assignment-0", "submitting", expected_revision=1, receipt=None, actor={})
        first = deliveries.transition("input", "assignment-0", "unknown", expected_revision=2, receipt=deliveries.make_receipt(first, "unknown", reason_code="lost-ack"), actor={})
        self.assertEqual(deliveries.prepare("input", "assignment-0", actor={})["state"], "unknown")
        with self.assertRaises(HarnessError):
            deliveries.transition("input", "assignment-0", "submitting", expected_revision=3, receipt=None, actor={})
        deliveries.transition("input", "assignment-0", "applied", expected_revision=3,
                              receipt=deliveries.make_receipt(first, "applied", native_receipt={"request_id": first["delivery_key"], "turn": 1, "status": "applied", "accepted_seq": 1, "applied_seq": 2}), actor={})
        deliveries.prepare("input", "assignment-1", actor={})
        with self.store.transaction() as db:
            self.assertEqual([r[0] for r in db.execute("SELECT state FROM input_deliveries ORDER BY assignment_id")],
                             ["applied", "pending"])
        second = deliveries.get("input", "assignment-1")
        second = deliveries.transition("input", "assignment-1", "submitting",
                                       expected_revision=1, receipt=None, actor={})
        second = deliveries.transition(
            "input", "assignment-1", "accepted", expected_revision=2,
            receipt=deliveries.make_receipt(second, "accepted", native_receipt={
                "request_id": second["delivery_key"], "turn": 1, "status": "pending",
                "accepted_seq": 1, "applied_seq": None,
            }), actor={},
        )
        with self.assertRaises(HarnessError):
            deliveries.transition(
                "input", "assignment-1", "unknown", expected_revision=3,
                receipt=deliveries.make_receipt(second, "unknown", reason_code="lookup-failed"), actor={},
            )
        with self.assertRaises(HarnessError):
            deliveries.prepare("input", "assignment-2", actor={})
        applied = deliveries.get("input", "assignment-0")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction(write=True) as db:
                db.execute(
                    "UPDATE input_deliveries SET state='pending',receipt_json='{}',"
                    "aggregate_revision=aggregate_revision+1 WHERE input_id=? AND assignment_id=?",
                    ("input", "assignment-0"),
                )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction(write=True) as db:
                db.execute(
                    "DELETE FROM input_deliveries WHERE input_id=? AND assignment_id=?",
                    ("input", "assignment-0"),
                )
        native = dict(applied["receipt"]["native_receipt"])
        native["applied_seq"] = native["accepted_seq"]
        corrupted = {**applied["receipt"], "native_receipt": native}
        with self.store.transaction(write=True) as db:
            db.execute("DROP TRIGGER input_deliveries_transition_guard")
            db.execute(
                "UPDATE input_deliveries SET receipt_json=? WHERE input_id=? AND assignment_id=?",
                (_json(corrupted), "input", "assignment-0"),
            )
        with self.assertRaises(HarnessError):
            deliveries.get("input", "assignment-0")

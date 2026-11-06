from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from harness.app.superagent_coordinator_host import CoordinatorHost
from harness.app.superagent_coordinator_store import _hash, _json, _now
from harness.app.superagent_executor import HarnessExecutorAdapter, WorkerProfile
from harness.core.errors import HarnessError


class FakeExecutionAdapter:
    def __init__(self, instance_id: str, profile: WorkerProfile, shared: dict | None = None):
        self.instance_id = instance_id
        self.profiles = {profile.profile_id: profile}
        self.max_workers = 8
        self.shared = shared or {
            "resources": {}, "runs": {}, "submit_calls": [], "input_receipts": {}, "input_calls": [],
            "workspace_calls": 0, "session_calls": 0, "fail_workspace_receipt": False,
            "fail_session_receipt": False, "fail_submit_ack": False, "fail_input_ack": False,
        }
        self.shared.setdefault("input_calls", [])
        self.shared.setdefault("fail_input_ack", False)
        self.shared.setdefault("instruction_calls", [])
        self.shared.setdefault("instruction_receipts", {})
        self.shared.setdefault("fail_instruction_ack", False)
        self.shared.setdefault("stop_calls", [])
        self.shared.setdefault("fail_stop", False)
        self.shared.setdefault("false_stop_receipt", False)
        self.shared.setdefault("fail_inspect", False)
        self.shared.setdefault("async_closed", False)
        self.shared.setdefault("artifact_conflict", False)
        self.shared.setdefault("artifact_capture_calls", [])
        self.shared.setdefault("artifact_import_calls", [])
        self.worker_lookup = None
        self.closed = False

    def set_worker_lookup(self, lookup):
        self.worker_lookup = lookup

    def profile_for_source(self, source_instance_id):
        return next((p for p in self.profiles.values() if source_instance_id in p.source_instance_ids), None)

    def base_commit(self, profile):
        return self.shared.get("base_commit", "a" * 40)

    def find_worker(self, worker, profile):
        value = self.shared["resources"].get(worker["worker_id"])
        return copy.deepcopy(value) if value is not None else None

    async def create_workspace(self, worker, profile):
        self.shared["workspace_calls"] += 1
        value = self.shared["resources"].setdefault(worker["worker_id"], {})
        value.setdefault("workspace_id", "workspace-" + worker["worker_id"])
        if self.shared["fail_workspace_receipt"]:
            self.shared["fail_workspace_receipt"] = False
            raise RuntimeError("workspace receipt lost")

    async def create_session(self, worker, profile):
        self.shared["session_calls"] += 1
        value = self.shared["resources"].setdefault(worker["worker_id"], {})
        value.setdefault("workspace_id", "workspace-" + worker["worker_id"])
        value.setdefault("session_id", "session-" + worker["worker_id"])
        if self.shared["fail_session_receipt"]:
            self.shared["fail_session_receipt"] = False
            raise RuntimeError("session receipt lost")

    @staticmethod
    def _snapshot(run_id, session_id, input_ids, *, status="running", settled=False, output_text=""):
        output_hash = hashlib.sha256(output_text.encode("utf-8")).hexdigest()
        reference = {
            "run_id": run_id, "session_id": session_id, "turn": 1,
            "status": status, "settled": settled, "output_hash": output_hash,
        }
        return {
            "found": True, "run_id": run_id, "status": status, "settled": settled,
            "started": status != "queued", "output_text": output_text, "output_hash": output_hash,
            "run_reference": reference, "run_reference_hash": _hash(_json(reference)),
            "run_payload_hash": output_hash, "tools": [], "backgrounds": [],
            "unconfirmed_tools": 0, "unsettled_background": 0, "input_ids": list(input_ids),
        }

    async def submit(self, worker, assignment, prompt, input_ids):
        assignment_id = assignment["assignment_id"]
        self.shared["submit_calls"].append(assignment_id)
        run_id = "run-" + assignment_id
        self.shared["runs"][assignment_id] = self._snapshot(
            run_id, worker["session_id"], input_ids,
        )
        if self.shared["fail_submit_ack"]:
            self.shared["fail_submit_ack"] = False
            raise RuntimeError("submission acknowledgement lost")
        return {"run_id": run_id, "status": "running"}

    def reconcile(self, worker, assignment):
        snapshot = self.shared["runs"].get(assignment["assignment_id"])
        return copy.deepcopy(snapshot) if snapshot is not None else {"found": False, "authoritative": True}

    def inspect(self, worker, assignment):
        if self.shared["fail_inspect"]:
            self.shared["fail_inspect"] = False
            raise RuntimeError("native inspection failed")
        return self.reconcile(worker, assignment)

    async def deliver_input(self, worker, assignment, prompt, delivery_key, before_accept):
        snapshot = self.shared["runs"].get(assignment["assignment_id"])
        if snapshot is None or snapshot.get("run_id") != assignment.get("run_id"):
            raise HarnessError("Input delivery does not match the original Run", code="superagent/run-conflict")
        if snapshot.get("settled") or snapshot.get("status") != "running":
            return {"accepted": False, "deferred": True}
        before_accept()
        self.shared["input_calls"].append(delivery_key)
        receipt = {"request_id": delivery_key, "turn": 1, "status": "applied",
                   "accepted_seq": 1, "applied_seq": 2}
        self.shared["input_receipts"][delivery_key] = receipt
        if self.shared.get("fail_input_ack"):
            self.shared["fail_input_ack"] = False
            raise RuntimeError("input delivery acknowledgement lost")
        return {"accepted": True, "deferred": False, "request_id": delivery_key,
                "turn": 1, "receipt": copy.deepcopy(receipt)}

    def reconcile_input(self, worker, assignment, delivery_key):
        return copy.deepcopy(self.shared["input_receipts"].get(delivery_key))

    def instruction_turn(self, worker, assignment, operation_key):
        snapshot = self.shared["runs"].get(assignment["assignment_id"])
        if snapshot is None or snapshot.get("run_id") != assignment.get("run_id"):
            raise HarnessError("Instruction target Run does not match its Assignment", code="superagent/run-conflict")
        if snapshot.get("settled") or snapshot.get("status") != "running":
            raise HarnessError("Instruction target Run is not active", code="run/stale-turn")
        return 1

    async def deliver_instruction(self, worker, assignment, text, operation_key, turn_id, before_accept):
        snapshot = self.shared["runs"].get(assignment["assignment_id"])
        if snapshot is None or snapshot.get("run_id") != assignment.get("run_id"):
            raise HarnessError("Instruction target Run does not match its Assignment", code="superagent/run-conflict")
        if snapshot.get("settled") or snapshot.get("status") != "running":
            raise HarnessError("Instruction target Run is not active", code="run/stale-turn")
        before_accept()
        self.shared["instruction_calls"].append(operation_key)
        receipt = {
            "request_id": operation_key, "turn": turn_id, "text": text,
            "status": "pending", "accepted_seq": 1, "applied_seq": None,
        }
        self.shared["instruction_receipts"][operation_key] = copy.deepcopy(receipt)
        if self.shared["fail_instruction_ack"]:
            self.shared["fail_instruction_ack"] = False
            raise RuntimeError("instruction acknowledgement lost")
        return {
            "accepted": True, "deferred": False, "request_id": operation_key,
            "turn": turn_id, "receipt": copy.deepcopy(receipt),
        }

    def reconcile_instruction(self, worker, assignment, operation_key, turn_id, expected_text):
        return copy.deepcopy(self.shared["instruction_receipts"].get(operation_key))

    async def stop(self, worker, assignment):
        self.shared["stop_calls"].append(assignment["assignment_id"])
        if self.shared["fail_stop"]:
            raise RuntimeError("native stop acknowledgement lost")
        if self.shared["false_stop_receipt"]:
            self.shared["false_stop_receipt"] = False
            return {"stopped": False}
        snapshot = self.shared["runs"][assignment["assignment_id"]]
        self.shared["runs"][assignment["assignment_id"]] = self._snapshot(
            snapshot["run_id"], worker["session_id"], snapshot["input_ids"],
            status="interrupted", settled=True, output_text=snapshot["output_text"],
        )
        return {"stopped": True, "status": "interrupted"}

    def finish(self, assignment_id, report):
        old = self.shared["runs"][assignment_id]
        output = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
        self.shared["runs"][assignment_id] = self._snapshot(
            old["run_id"], old["run_reference"]["session_id"], old["input_ids"],
            status="completed", settled=True, output_text=output,
        )

    def set_queued(self, assignment_id):
        old = self.shared["runs"][assignment_id]
        self.shared["runs"][assignment_id] = self._snapshot(
            old["run_id"], old["run_reference"]["session_id"], old["input_ids"],
            status="queued", settled=False,
        )

    def snapshot_workspace(self, worker):
        resource = self.shared["resources"][worker["worker_id"]]
        tree = resource.setdefault(
            "tree", _hash(f"{worker['base_commit']}\\0{worker['worker_id']}")[:40],
        )
        return {
            "tree": tree,
            "workspace": {
                "delivery": copy.deepcopy(resource.get("delivery")),
                "delivery_inputs": copy.deepcopy(resource.get("delivery_inputs")),
            },
        }

    async def capture_workspace(self, worker):
        resource = self.shared["resources"][worker["worker_id"]]
        tree = resource.setdefault(
            "tree", _hash(f"{worker['base_commit']}\\0{worker['worker_id']}")[:40],
        )
        self.shared["artifact_capture_calls"].append(worker["worker_id"])
        resource["delivery"] = {
            "tree": tree, "ref": f"refs/superagent/{worker['worker_id']}",
            "commit": "commit-" + worker["worker_id"],
        }

    def artifact_manifest(self, worker, receipt):
        return {
            "schema_version": 1, "worker_id": worker["worker_id"],
            "tree": receipt["tree"], "files": [],
        }

    async def plan_artifact_delivery(self, worker, artifacts):
        resource = self.shared["resources"][worker["worker_id"]]
        target_tree = resource.setdefault(
            "tree", _hash(f"{worker['base_commit']}\\0{worker['worker_id']}")[:40],
        )
        sources = [artifact["artifact_id"] for artifact in artifacts]
        return {
            "clean": not self.shared["artifact_conflict"],
            "tree": _hash(_json([target_tree, sources]))[:40],
            "target_tree": target_tree, "sources": sources,
        }

    async def apply_artifact_delivery(self, worker, artifacts, plan):
        resource = self.shared["resources"][worker["worker_id"]]
        self.shared["artifact_import_calls"].append(
            (worker["worker_id"], tuple(plan["sources"])),
        )
        resource["tree"] = plan["tree"]
        resource["delivery_inputs"] = {
            "tree": plan["tree"], "target_tree": plan["target_tree"],
            "sources": list(plan["sources"]),
        }

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True
        self.shared["async_closed"] = True


class SuperagentExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.instance_id = "phase3-execution-tests"
        self.profile = WorkerProfile(
            profile_id="local-worker", repo=str(self.root), description="Local test worker",
            source_instance_ids=("local-source",), actions=("read", "reply"), tools=(),
            max_turns=4, max_seconds=60,
        )
        self.shared = {
            "resources": {}, "runs": {}, "submit_calls": [], "input_receipts": {},
            "workspace_calls": 0, "session_calls": 0, "fail_workspace_receipt": False,
            "fail_session_receipt": False, "fail_submit_ack": False,
        }
        self.adapter = FakeExecutionAdapter(self.instance_id, self.profile, self.shared)
        self.planner = getattr(self, "planner", None)
        self.host = self._open_host(self.adapter, planner=self.planner)
        self.host.sources.create_instance(
            "local-source", kind="local", config={"namespace": "repo"},
            legacy_source_id="legacy-local",
        )

    async def asyncTearDown(self):
        if getattr(self, "host", None) is not None and not getattr(self.host, "_closed", True):
            self.host.close()
        self.temp.cleanup()

    async def test_host_aclose_awaits_executor_adapter_cleanup(self):
        await self.host.aclose()
        self.assertTrue(self.shared["async_closed"])
        self.assertTrue(self.adapter.closed)

    @staticmethod
    def _local_identity(event):
        metadata = event.get("metadata", {})
        return {
            "actor_id": metadata.get("actor_id", "human:1"),
            "actor_kind": metadata.get("actor_kind", "human"),
            "addressed": metadata.get("addressed", "direct"),
        }

    def _open_host(self, adapter, planner=None):
        return CoordinatorHost.open(
            self.root / "v2", instance_id=self.instance_id,
            identity={"project": "harness", "suite": "phase3"},
            legacy_state_dir=self.root / "v1",
            local_identity_verifier=self._local_identity,
            executor_adapter=adapter, planner=planner,
        )

    @staticmethod
    def _thread_id(thread_key):
        return "thr_" + _hash(_json(["local-source", "local/repo/" + thread_key]))

    @staticmethod
    def _proposal(evidence):
        return {
            "kind": "accept",
            "reason": "An explicit implementation request with a settled target.",
            "rule_assessments": [{
                "rule_id": "implementation", "matched": True,
                "reason": "The request asks for a concrete existing behavior fix.",
            }],
            "direction": {
                "implementation_requested": True, "target_basis": "specified",
                "requires_design_decision": False, "evidence": evidence,
            },
            "title": "Fix the existing typo",
            "intent_evidence": evidence,
        }

    def accept_event(self, *, event_id="origin", thread_key="work-thread",
                     text="Fix the existing typo.", max_turns=3):
        thread_id = self._thread_id(thread_key)
        scope = {
            "source_instance_ids": ["local-source"], "thread_ids": [thread_id],
            "actions": ["read", "reply"], "tools": [],
            "budget": {"max_turns": max_turns, "max_seconds": 60},
        }
        policy = {
            "mode": "auto", "timeout_seconds": 120,
            "rules": [{
                "id": "implementation", "name": "Implementation", "enabled": True,
                "instruction": "Only accept a settled implementation request.",
            }],
            "sources": {"local-source": {"allowed": [{"id": "human:1"}], "scope": scope}},
        }
        self.host.authorization.publish_policy(policy, actor={"kind": "human", "id": "policy-admin"})
        received = self.host.sources.receive_local("local-source", {
            "event_id": event_id, "thread_id": thread_key, "text": text,
            "occurred_at": "2026-11-06T12:00:00Z",
            "metadata": {"actor_id": "human:1", "actor_kind": "human", "addressed": "direct"},
        })
        candidate = self.host.authorization.begin_evaluation(received["thread_ids"][0], actor={"kind": "host"})
        accepted = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"], self._proposal(text), actor={"kind": "assessor"},
        )
        if accepted["kind"] != "accept":
            raise AssertionError(f"Ticket acceptance failed: {accepted}")
        return received, accepted, thread_id

    def receive_followup(self, event_id, thread_key="work-thread", text="Please also check the related test."):
        return self.host.sources.receive_local("local-source", {
            "event_id": event_id, "thread_id": thread_key, "text": text,
            "occurred_at": "2026-11-06T12:01:00Z",
            "metadata": {"actor_id": "human:1", "actor_kind": "human", "addressed": "direct"},
        })

    def add_reply_channel(self, thread_id):
        address = {"kind": "local-reply", "thread_id": thread_id}
        with self.host._store.transaction(write=True) as db:
            db.execute(
                "INSERT INTO channels(channel_id,source_instance_id,thread_id,address_json,address_hash,capabilities_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("reply-" + thread_id[-16:], "local-source", thread_id, _json(address),
                 _hash(_json(address)), _json({"reply": True}), _now()),
            )

    def mission_state(self, mission_id):
        with self.host._store.transaction() as db:
            row = db.execute("SELECT state FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
        return row["state"]

    def outbox_count(self):
        with self.host._store.transaction() as db:
            return db.execute("SELECT COUNT(*) FROM outbox_messages").fetchone()[0]

    async def test_original_run_recovery_and_report_effects_are_idempotent(self):
        received, accepted, thread_id = self.accept_event()
        self.add_reply_channel(thread_id)
        started = await self.host.execution.route_trigger(accepted["ticket_id"], received["trigger_id"])
        self.assertEqual(started["state"], "running")
        assignment_id = started["assignment_id"]
        run_id = self.shared["runs"][assignment_id]["run_id"]

        self.host.close()
        self.adapter = FakeExecutionAdapter(self.instance_id, self.profile, self.shared)
        self.host = self._open_host(self.adapter)
        resumed = await self.host.execution.route_trigger(accepted["ticket_id"], received["trigger_id"])
        self.assertEqual(resumed["state"], "running")
        self.assertEqual(self.shared["submit_calls"], [assignment_id])
        self.assertEqual(self.shared["runs"][assignment_id]["run_id"], run_id)

        self.adapter.finish(assignment_id, {"status": "completed", "summary": "Done", "reply": "Done"})
        service = self.host._execution_service
        finalize = service._finalize_report_effects

        def fail_once(*args, **kwargs):
            service._finalize_report_effects = finalize
            raise RuntimeError("simulated interruption after Report freeze")

        service._finalize_report_effects = fail_once
        with self.assertRaises(RuntimeError):
            await self.host.execution.reconcile(assignment_id)
        frozen = self.host.execution.report(assignment_id)
        self.assertEqual(frozen["state"], "succeeded")
        self.assertEqual(self.mission_state(started["mission_id"]), "running")
        self.assertEqual(self.outbox_count(), 0)

        recovered = await self.host.execution.reconcile(assignment_id)
        self.assertEqual(recovered["state"], "succeeded")
        self.assertEqual(self.mission_state(started["mission_id"]), "completed")
        self.assertEqual(self.outbox_count(), 1)
        with self.host._store.transaction() as db:
            message_id = db.execute("SELECT message_id FROM outbox_messages").fetchone()["message_id"]
        self.assertEqual(self.host.execution.outbox_body(message_id), "Done")
        await self.host.execution.reconcile(assignment_id)
        self.assertEqual(self.outbox_count(), 1)

    async def test_followup_handback_targets_only_the_delivered_ticket_input(self):
        origin, accepted, _ = self.accept_event(max_turns=2)
        started = await self.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        followup = self.receive_followup("followup-handback")
        delivered = await self.host.execution.route_trigger(accepted["ticket_id"], followup["trigger_id"])
        self.assertEqual(delivered["state"], "delivered")
        self.assertEqual(delivered["assignment_id"], started["assignment_id"])

        self.adapter.finish(started["assignment_id"], {
            "status": "handback", "summary": "Outside scope",
            "handback_reason": "This follow-up requires a different owner.",
            "handback_trigger_id": followup["trigger_id"],
        })
        result = await self.host.execution.reconcile(started["assignment_id"])
        self.assertEqual(result["state"], "succeeded")
        report = self.host.execution.report(started["assignment_id"])["report"]
        self.assertEqual(report["disposition"], "handback")
        self.assertEqual(report["handback_trigger_id"], followup["trigger_id"])
        input_row = self.host._execution_service._input(accepted["ticket_id"], followup["trigger_id"])
        self.assertEqual(input_row["state"], "handed_back")
        await self.host.execution.reconcile(started["assignment_id"])
        input_row = self.host._execution_service._input(accepted["ticket_id"], followup["trigger_id"])
        self.assertEqual(input_row["state"], "handed_back")

    async def test_turn_budget_blocks_new_missions_but_allows_existing_run_reconciliation(self):
        origin, accepted, _ = self.accept_event(max_turns=1)
        started = await self.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        self.adapter.finish(started["assignment_id"], {"status": "completed", "summary": "Done", "reply": ""})
        await self.host.execution.reconcile(started["assignment_id"])
        followup = self.receive_followup("budget-followup")
        with self.assertRaises(PermissionError):
            await self.host.execution.route_trigger(accepted["ticket_id"], followup["trigger_id"])
        self.assertEqual(self.shared["submit_calls"], [started["assignment_id"]])

    async def test_origin_handback_becomes_attention_and_human_input_resolves_it(self):
        origin, accepted, _ = self.accept_event(max_turns=2)
        started = await self.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        self.adapter.finish(started["assignment_id"], {
            "status": "handback", "summary": "Cannot proceed",
            "handback_reason": "The original request is outside scope.",
            "handback_trigger_id": origin["trigger_id"],
        })
        result = await self.host.execution.reconcile(started["assignment_id"])
        self.assertEqual(result["disposition"], "attention")
        attentions = self.host.execution.list_attentions(accepted["ticket_id"], state="open")
        self.assertEqual(len(attentions), 1)
        self.assertEqual(self.mission_state(started["mission_id"]), "blocked")
        answer = self.receive_followup("attention-answer")
        resolved = self.host.execution.resolve_attention(attentions[0]["attention_id"], answer["trigger_id"])
        self.assertEqual(resolved["state"], "answered")

    async def test_revoked_mandate_suppresses_mission_and_reply_candidate(self):
        origin, accepted, thread_id = self.accept_event()
        self.add_reply_channel(thread_id)
        started = await self.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        self.host.sources.update_instance("local-source", expected_revision=1, enabled=False)
        self.adapter.finish(started["assignment_id"], {
            "status": "completed", "summary": "Finished", "reply": "Must not be queued",
        })
        await self.host.execution.reconcile(started["assignment_id"])
        report = self.host.execution.report(started["assignment_id"])["report"]
        self.assertFalse(report["mandate_valid"])
        self.assertEqual(self.mission_state(started["mission_id"]), "suppressed")
        self.assertEqual(self.outbox_count(), 0)

    async def test_unknown_assignment_reconciles_to_the_exact_original_running_run(self):
        origin, accepted, _ = self.accept_event()
        started = await self.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        assignment = self.host._execution_service._assignment(started["assignment_id"])
        self.host._execution_service._mark_unknown(assignment, "test uncertainty")
        reconciled = await self.host.execution.reconcile(started["assignment_id"])
        self.assertEqual(reconciled["state"], "running")
        self.assertEqual(self.shared["submit_calls"], [started["assignment_id"]])

    async def test_authoritatively_absent_single_node_submission_blocks_mission_without_crashing(self):
        origin, accepted, _ = self.accept_event()

        async def reject_before_acceptance(worker, assignment, prompt, input_ids):
            self.shared["submit_calls"].append(assignment["assignment_id"])
            raise RuntimeError("native submission was rejected before acceptance")

        self.adapter.submit = reject_before_acceptance
        result = await self.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])

        self.assertEqual(result["state"], "rejected")
        assignment = self.host._execution_service._assignment(result["assignment_id"])
        self.assertEqual(assignment["state"], "rejected")
        with self.host._store.transaction() as db:
            job = db.execute("SELECT state FROM jobs WHERE job_id=?", (assignment["job_id"],)).fetchone()
            mission = db.execute("SELECT state FROM missions WHERE mission_id=?", (assignment["mission_id"],)).fetchone()
            ticket_input = db.execute(
                "SELECT state FROM ticket_inputs WHERE ticket_id=? AND trigger_id=?",
                (accepted["ticket_id"], origin["trigger_id"]),
            ).fetchone()
        self.assertEqual(job["state"], "blocked")
        self.assertEqual(mission["state"], "blocked")
        self.assertEqual(ticket_input["state"], "waiting")
        self.assertEqual(self.shared["submit_calls"], [result["assignment_id"]])

    async def test_lost_worker_creation_and_submission_receipts_are_reconciled_without_repeating(self):
        origin, accepted, _ = self.accept_event()
        self.shared["fail_workspace_receipt"] = True
        self.shared["fail_session_receipt"] = True
        self.shared["fail_submit_ack"] = True
        started = await self.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        self.assertEqual(started["state"], "running")
        self.assertEqual(self.shared["workspace_calls"], 1)
        self.assertEqual(self.shared["session_calls"], 1)
        self.assertEqual(self.shared["submit_calls"], [started["assignment_id"]])

    async def test_same_ticket_concurrent_triggers_share_one_active_run(self):
        origin, accepted, _ = self.accept_event(max_turns=3)
        followup = self.receive_followup("concurrent-followup")
        first, second = await asyncio.gather(
            self.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"]),
            self.host.execution.route_trigger(accepted["ticket_id"], followup["trigger_id"]),
        )
        self.assertEqual(self.shared["submit_calls"], [first["assignment_id"]])
        self.assertEqual({first["state"], second["state"]}, {"running", "delivered"})

    async def test_duplicate_trigger_reuses_the_original_assignment_and_run(self):
        origin, accepted, _ = self.accept_event()
        first = await self.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        second = await self.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        self.assertEqual(first["assignment_id"], second["assignment_id"])
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(self.shared["submit_calls"], [first["assignment_id"]])
        worker = self.host.execution.list_workers(accepted["ticket_id"])[0]
        assignment = self.host._execution_service._assignment(first["assignment_id"])
        self.assertEqual((worker["mission_id"], worker["job_id"], worker["stage"]),
                         (assignment["mission_id"], assignment["job_id"], 1))

    async def test_budget_cancellation_uncertainty_keeps_assignment_unknown(self):
        origin, accepted, _ = self.accept_event()
        started = await self.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        self.shared["runs"][started["assignment_id"]]["budget_failure"] = "RuntimeError"
        result = await self.host.execution.reconcile(started["assignment_id"])
        self.assertEqual(result["state"], "unknown")
        assignment = self.host._execution_service._assignment(started["assignment_id"])
        self.assertEqual(assignment["state"], "unknown")

    async def test_frozen_report_hash_mismatch_blocks_finalization(self):
        origin, accepted, _ = self.accept_event()
        started = await self.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        self.adapter.finish(started["assignment_id"], {
            "status": "completed", "summary": "Verified", "reply": "",
        })
        await self.host.execution.reconcile(started["assignment_id"])
        frozen = self.host.execution.report(started["assignment_id"])["report"]
        tampered = {**frozen, "summary": "Changed after freeze"}
        assignment = self.host._execution_service._assignment(started["assignment_id"])
        with self.assertRaises(HarnessError):
            self.host._execution_service._finalize_report_effects(assignment, tampered)

    async def test_native_budget_watchdog_cancels_only_the_matching_run(self):
        class Record:
            id = "run-budget"
            session_id = "worker-session"
            started_at = time.time() - 0.95

        record = Record()
        record.task = asyncio.create_task(asyncio.sleep(5))
        active_key = object()

        class Runtime:
            active = {"worker-session": active_key}
            _executions = {active_key: record}

        class Harness:
            runtime = Runtime()
            _agents = {"worker-session": SimpleNamespace(run_id=record.id)}

            @staticmethod
            def get_session(session_id):
                return SimpleNamespace(open_turn=7)

            def __init__(self):
                self.cancel_calls = []

            async def cancel(self, session_id, *, expected_turn):
                self.cancel_calls.append((session_id, expected_turn))
                record.task.cancel()
                return True

        harness = Harness()
        adapter = object.__new__(HarnessExecutorAdapter)
        adapter.harness = harness
        adapter._budget_failures = {}
        await adapter._enforce_run_budget(record, 1)
        self.assertEqual(harness.cancel_calls, [("worker-session", 7)])
        self.assertTrue(record.task.done())

    async def test_native_stop_removes_a_queued_original_run(self):
        class Record:
            id = "run-queued"
            session_id = "worker-session"
            status = "queued"
            started_at = None
            finished_at = None

        record = Record()

        class Runtime:
            closed = False
            active = {}
            _executions = {}

            def __init__(self):
                self.removed = []

            def get(self, run_id):
                self.asserted = run_id
                return record

            def remove(self, run_id):
                self.removed.append(run_id)
                record.status = "interrupted"
                record.finished_at = time.time()

        runtime = Runtime()
        harness = SimpleNamespace(
            runtime=runtime, worktrees=object(), sessions=SimpleNamespace(root=self.root),
            config=SimpleNamespace(enable_agents=False, enable_skills=False, enable_goal=False,
                enable_plan_mode=False, enable_subagent=False, enable_ralph=False),
            agent_mount=None, _close_task=None, _agents={}, jobs=SimpleNamespace(),
            get_session=lambda _session_id: SimpleNamespace(events=[]),
        )
        adapter = object.__new__(HarnessExecutorAdapter)
        adapter.closed = False
        adapter.harness = harness
        result = await adapter.stop({"session_id": "worker-session"}, {"run_id": record.id})
        self.assertTrue(result["stopped"])
        self.assertEqual(runtime.removed, [record.id])
        self.assertEqual(record.status, "interrupted")

    async def test_native_stop_kills_only_background_jobs_from_the_original_run(self):
        record = SimpleNamespace(
            id="run-background", session_id="worker-session",
            status="completed", started_at=1, finished_at=2,
        )

        class Runtime:
            closed = False

            def get(self, run_id):
                self.requested = run_id
                return record

        class Jobs:
            def __init__(self):
                self.calls = []

            async def kill(self, job_id, owner, *, reason=None):
                self.calls.append((job_id, owner, reason))
                return "killed", SimpleNamespace(job_id=job_id)

        jobs = Jobs()
        harness = SimpleNamespace(
            runtime=Runtime(), worktrees=object(), sessions=SimpleNamespace(root=object()),
            config=SimpleNamespace(enable_agents=False, enable_skills=False, enable_goal=False,
                enable_plan_mode=False, enable_subagent=False, enable_ralph=False),
            agent_mount=None, _agents={}, _close_task=None,
            get_session=lambda _session_id: SimpleNamespace(events=[]),
            jobs=jobs,
        )
        adapter = object.__new__(HarnessExecutorAdapter)
        adapter.closed = False
        adapter.harness = harness
        invocation = object()
        with (
            patch("harness.app.superagent_executor.turn_events", return_value=[object()]),
            patch("harness.app.superagent_executor.tool_invocations", return_value=[invocation]),
            patch("harness.app.superagent_executor.background_invocation", return_value=True),
            patch(
                "harness.app.superagent_executor.background_status",
                side_effect=[
                    {"confirmed": True, "settled": False, "job_id": "owned-background"},
                    {"confirmed": True, "settled": True, "job_id": "owned-background"},
                ],
            ),
        ):
            receipt = await adapter.stop(
                {"session_id": "worker-session"},
                {"assignment_id": "assignment-1", "run_id": "run-background"},
            )
        self.assertTrue(receipt["stopped"])
        self.assertEqual(receipt["backgrounds_stopped"], 1)
        self.assertEqual(jobs.calls[0][0:2], ("owned-background", "worker-session"))

    async def test_native_stop_refuses_unconfirmed_background_ownership(self):
        record = SimpleNamespace(
            id="run-background-unknown", session_id="worker-session",
            status="completed", started_at=1, finished_at=2,
        )

        class Runtime:
            closed = False

            def get(self, run_id):
                return record

        class Jobs:
            calls = []

            async def kill(self, job_id, owner, *, reason=None):
                self.calls.append((job_id, owner))
                return "killed", SimpleNamespace(job_id=job_id)

        jobs = Jobs()
        harness = SimpleNamespace(
            runtime=Runtime(), worktrees=object(), sessions=SimpleNamespace(root=object()),
            config=SimpleNamespace(enable_agents=False, enable_skills=False, enable_goal=False,
                enable_plan_mode=False, enable_subagent=False, enable_ralph=False),
            agent_mount=None, _agents={}, _close_task=None,
            get_session=lambda _session_id: SimpleNamespace(events=[]),
            jobs=jobs,
        )
        adapter = object.__new__(HarnessExecutorAdapter)
        adapter.closed = False
        adapter.harness = harness
        with (
            patch("harness.app.superagent_executor.turn_events", return_value=[object()]),
            patch("harness.app.superagent_executor.tool_invocations", return_value=[object()]),
            patch("harness.app.superagent_executor.background_invocation", return_value=True),
            patch(
                "harness.app.superagent_executor.background_status",
                return_value={"confirmed": False, "settled": False},
            ),
        ):
            with self.assertRaises(HarnessError):
                await adapter.stop(
                    {"session_id": "worker-session"},
                    {"assignment_id": "assignment-2", "run_id": "run-background-unknown"},
                )
        self.assertEqual(jobs.calls, [])


if __name__ == "__main__":
    unittest.main()

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
from harness.app.superagent_coordinator_service import CoordinatorApplicationService
from harness.app.superagent_coordinator_store import _hash, _json, _now
from harness.app.superagent_executor import HarnessExecutorAdapter, WorkerProfile
from harness.core.errors import HarnessError


def _advance_watchdog_activity(snapshot: dict) -> dict:
    assignments = []
    for item in snapshot.get("assignments", []):
        updated = dict(item)
        last = item.get("last_activity")
        event = dict(last) if isinstance(last, dict) else {
            "time": "2026-11-07T00:00:01Z", "type": "assistant_message",
        }
        event["seq"] = (-1 if last is None else last["seq"]) + 1
        updated["last_activity"] = event
        assignments.append(updated)
    return {**snapshot, "assignments": assignments}


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
        self.shared.setdefault("tool_effects", {})
        self.worker_lookup = None
        self.closed = False

    def set_worker_lookup(self, lookup):
        self.worker_lookup = lookup

    def profile_for_source(self, source_instance_id):
        return next((p for p in self.profiles.values() if source_instance_id in p.source_instance_ids), None)

    def tool_effects(self, profile):
        return {name: self.shared["tool_effects"].get(name, "unknown") for name in profile.tools}

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


class ArtifactReviewPlanner:
    def plan(self, *, trigger_id, profile, prompt):
        return {
            "plan": {"nodes": [{
                "node_id": "work", "role": "work", "profile_id": profile.profile_id,
                "instruction_ref": f"trigger:{trigger_id}", "instruction_hash": _hash(prompt),
                "dependencies": [],
            }]},
            "acceptance": {
                "kind": "artifact_review",
                "criteria": [{"id": "files", "kind": "inspection"}],
            },
        }


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

        )

    async def asyncTearDown(self):
        if getattr(self, "host", None) is not None and not getattr(self.host, "_closed", True):
            self.host.close()
        self.temp.cleanup()

    async def test_host_aclose_awaits_executor_adapter_cleanup(self):
        await self.host.aclose()
        self.assertTrue(self.shared["async_closed"])
        self.assertTrue(self.adapter.closed)


    async def test_artifact_plan_materializes_host_integrator_and_independent_reviewer(self):
        self._reopen_with_system_profiles(ArtifactReviewPlanner())
        received, accepted, _ = self.accept_event(
            actions=("read", "reply"),
            tools=("write_file", "read_file"),
        )
        started = await self.host.execution.route_trigger(accepted["ticket_id"], received["trigger_id"])
        self.assertEqual(started["state"], "running")
        mission_id = started["mission_id"]
        with self.host._store.transaction() as db:
            jobs = db.execute(
                "SELECT node_id,role,profile_id FROM jobs WHERE mission_id=? ORDER BY node_id",
                (mission_id,),
            ).fetchall()
            plan = db.execute(
                "SELECT plan_json,acceptance_json FROM mission_plans WHERE mission_id=? AND plan_revision=1",
                (mission_id,),
            ).fetchone()
            dependencies = db.execute(
                "SELECT child.node_id AS node_id,parent.node_id AS depends_on_node_id,dependency.requires_delivery "
                "FROM job_dependencies AS dependency "
                "JOIN jobs AS child ON child.job_id=dependency.job_id "
                "JOIN jobs AS parent ON parent.job_id=dependency.depends_on_job_id "
                "WHERE dependency.mission_id=?",
                (mission_id,),
            ).fetchall()
        self.assertEqual(
            {(row["node_id"], row["role"], row["profile_id"]) for row in jobs},
            {("work", "work", "local-worker"),
             ("__system__integrate", "integrate", "local-integrator"),
             ("__system__review", "review", "local-reviewer")},
        )
        self.assertEqual(
            {(row["node_id"], row["depends_on_node_id"], row["requires_delivery"]) for row in dependencies},
            {("__system__integrate", "work", 1),
             ("__system__review", "__system__integrate", 1)},
        )

        frozen_plan = json.loads(plan["plan_json"])
        acceptance = json.loads(plan["acceptance_json"])
        self.assertEqual([node["role"] for node in frozen_plan["nodes"]], ["work", "integrate", "review"])
        self.assertEqual(acceptance["kind"], "artifact_review")
        self.assertEqual(acceptance["schema_version"], 1)
        self.assertEqual(acceptance["criteria"], [{"id": "files", "kind": "inspection"}])

    async def _start_work_for_artifact_boundary_race(self):
        """建立真实 Host Work Run，等待原生执行完成但不提前捕获 Artifact。"""
        self._reopen_with_system_profiles(ArtifactReviewPlanner())
        received, accepted, _ = self.accept_event(
            actions=("read", "reply"), tools=("write_file", "read_file"), max_turns=12,
        )
        started = await self.host.execution.route_trigger(
            accepted["ticket_id"], received["trigger_id"],
        )
        self.adapter.finish(started["assignment_id"], {
            "status": "completed", "summary": "Files ready", "reply": "",
        })
        return started["mission_id"], started["assignment_id"]

    async def test_watchdog_activity_rejects_foreign_run_and_session_snapshots(self):
        """Watchdog 仅接纳当前 Assignment 原 Run 与 Session 的活动。"""
        mission_id, assignment_id = await self._start_work_for_artifact_boundary_race()
        original = copy.deepcopy(self.shared["runs"][assignment_id])
        self.assertEqual(
            self.host.execution.watchdog_activity(mission_id)["assignments"][0]["inspection"],
            "confirmed",
        )
        for mismatch in ("run_id", "session_id", "status", "settled", "found"):
            with self.subTest(mismatch=mismatch):
                changed = copy.deepcopy(original)
                if mismatch == "run_id":
                    changed["run_id"] = "run-from-other-assignment"
                elif mismatch == "session_id":
                    changed["run_reference"]["session_id"] = "session-from-other-worker"
                elif mismatch == "status":
                    changed["status"] = "running"
                elif mismatch == "settled":
                    changed["settled"] = "yes"
                else:
                    changed["found"] = "yes"
                self.shared["runs"][assignment_id] = changed
                activity = self.host.execution.watchdog_activity(mission_id)["assignments"][0]
                self.assertEqual(activity["inspection"], "invalid")
                self.assertNotIn("status", activity)
        self.shared["runs"][assignment_id] = original

    async def _start_integrator_for_import_boundary_race(self):
        """完成 Work 与 Artifact 冻结，让真实 Scheduler 准备 Integrator 导入。"""
        mission_id, work_id = await self._start_work_for_artifact_boundary_race()
        result = await self.host.execution.reconcile(work_id)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(self.mission_state(mission_id), "integrating")
        return mission_id, work_id

    async def test_capture_rechecks_control_after_effect_lease_before_git_write(self):
        """租约已认领但原生 Git 捕获尚未开始时暂停，必须留下确定的无副作用回执。"""
        mission_id, assignment_id = await self._start_work_for_artifact_boundary_race()
        artifacts = self.host._artifacts_service
        original_begin = artifacts._begin
        paused = [False]

        def lease_then_pause(kind, key, payload):
            effect, fresh = original_begin(kind, key, payload)
            if kind == "artifact.capture" and fresh and not paused[0]:
                paused[0] = True
                receipt = self.host.execution.control_mission(
                    mission_id, "pause", request_id="phase44-capture-lease-pause",
                    actor={"kind": "human"},
                )
                self.assertEqual(receipt["state"], "applied")
            return effect, fresh

        artifacts._begin = lease_then_pause
        try:
            first = await self.host.execution.reconcile(assignment_id)
        finally:
            artifacts._begin = original_begin
        self.assertEqual(first["state"], "capturing")
        self.assertEqual(self.shared["artifact_capture_calls"], [])
        with self.host._store.transaction() as db:
            effect = db.execute(
                "SELECT * FROM effects WHERE kind='artifact.capture' AND aggregate_id=?",
                (assignment_id,),
            ).fetchone()
            self.assertEqual(effect["state"], "failed")
            proof = json.loads(effect["receipt_json"])
            self.assertEqual(proof["outcome"], "not_started")
            self.assertEqual(proof["reason"], "superagent/mission-paused")
            self.assertTrue(db.execute(
                "SELECT 1 FROM audit_events WHERE aggregate_type='effect' AND aggregate_id=? "
                "AND event_type='effect.failed' AND json_extract(payload_json,'$.not_started')=1",
                (effect["effect_id"],),
            ).fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0)
        resumed = self.host.execution.control_mission(
            mission_id, "resume", request_id="phase44-capture-lease-resume",
            actor={"kind": "human"},
        )
        self.assertEqual(resumed["state"], "applied")
        recovered = await self.host.execution.reconcile(assignment_id)
        self.assertEqual(recovered["state"], "succeeded")
        self.assertEqual(
            self.shared["artifact_capture_calls"],
            [self.host._execution_service._assignment(assignment_id)["worker_id"]],
        )
        with self.host._store.transaction() as db:
            effects = db.execute(
                "SELECT state,idempotency_key FROM effects WHERE kind='artifact.capture' "
                "AND aggregate_id=? ORDER BY rowid",
                (assignment_id,),
            ).fetchall()
        self.assertEqual([row["state"] for row in effects], ["failed", "completed"])
        self.assertNotEqual(effects[0]["idempotency_key"], effects[1]["idempotency_key"])
        self.assertEqual(self.shared["submit_calls"], [assignment_id])

    async def test_capture_rechecks_mandate_after_effect_lease_before_git_write(self):
        """租约后来源撤权须形成无副作用失败证明，不能调用原生 Git 捕获。"""
        mission_id, assignment_id = await self._start_work_for_artifact_boundary_race()
        artifacts = self.host._artifacts_service
        original_begin = artifacts._begin
        revoked = [False]

        def lease_then_revoke(kind, key, payload):
            effect, fresh = original_begin(kind, key, payload)
            if kind == "artifact.capture" and fresh and not revoked[0]:
                revoked[0] = True
                self.host.sources.update_instance(
                    "local-source", expected_revision=1, enabled=False,
                )
            return effect, fresh

        artifacts._begin = lease_then_revoke
        try:
            result = await self.host.execution.reconcile(assignment_id)
        finally:
            artifacts._begin = original_begin
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.shared["artifact_capture_calls"], [])
        self.assertEqual(self.mission_state(mission_id), "blocked")
        with self.host._store.transaction() as db:
            effect = db.execute(
                "SELECT state,receipt_json FROM effects WHERE kind='artifact.capture' AND aggregate_id=?",
                (assignment_id,),
            ).fetchone()
            self.assertEqual(effect["state"], "failed")
            self.assertEqual(json.loads(effect["receipt_json"])["reason"], "superagent/mandate-unavailable")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0)

    async def test_import_rechecks_control_after_effect_lease_before_workspace_write(self):
        """Artifact 导入租约后暂停，必须保留 pending Delivery 并且不触碰目标工作树。"""
        mission_id, work_id = await self._start_integrator_for_import_boundary_race()
        artifacts = self.host._artifacts_service
        original_begin = artifacts._begin
        paused = [False]

        def lease_then_pause(kind, key, payload):
            effect, fresh = original_begin(kind, key, payload)
            if kind == "artifact.import" and fresh and not paused[0]:
                paused[0] = True
                receipt = self.host.execution.control_mission(
                    mission_id, "pause", request_id="phase44-import-lease-pause",
                    actor={"kind": "human"},
                )
                self.assertEqual(receipt["state"], "applied")
            return effect, fresh

        artifacts._begin = lease_then_pause
        try:
            first = await self.host.execution.schedule_dag_missions()
        finally:
            artifacts._begin = original_begin
        self.assertFalse(self.shared["artifact_import_calls"])
        self.assertEqual(self.mission_state(mission_id), "integrating")
        with self.host._store.transaction() as db:
            effect = db.execute(
                "SELECT * FROM effects WHERE kind='artifact.import' AND aggregate_id<>?",
                (work_id,),
            ).fetchone()
            self.assertIsNotNone(effect)
            self.assertEqual(effect["state"], "failed")
            self.assertEqual(json.loads(effect["receipt_json"])["outcome"], "not_started")
            self.assertEqual(db.execute(
                "SELECT state FROM deliveries WHERE target_assignment_id=?",
                (effect["aggregate_id"],),
            ).fetchone()[0], "pending")
            self.assertEqual(db.execute(
                "SELECT state FROM assignments WHERE assignment_id=?",
                (effect["aggregate_id"],),
            ).fetchone()[0], "pending")
        resumed = self.host.execution.control_mission(
            mission_id, "resume", request_id="phase44-import-lease-resume",
            actor={"kind": "human"},
        )
        self.assertEqual(resumed["state"], "applied")
        recovered = await self.host.execution.schedule_dag_missions()
        self.assertEqual(len(self.shared["artifact_import_calls"]), 1)
        self.assertFalse(recovered["errors"])
        with self.host._store.transaction() as db:
            effects = db.execute(
                "SELECT state,idempotency_key FROM effects WHERE kind='artifact.import' "
                "ORDER BY rowid",
            ).fetchall()
            delivery = db.execute("SELECT state FROM deliveries").fetchone()
        self.assertEqual([row["state"] for row in effects], ["failed", "completed"])
        self.assertNotEqual(effects[0]["idempotency_key"], effects[1]["idempotency_key"])
        self.assertEqual(delivery["state"], "applied")

    async def test_import_rechecks_mandate_after_effect_lease_before_workspace_write(self):
        """Artifact 导入租约后来源撤权，阻断 Job 与 Mission，不写入 Integrator 工作树。"""
        mission_id, work_id = await self._start_integrator_for_import_boundary_race()
        artifacts = self.host._artifacts_service
        original_begin = artifacts._begin
        revoked = [False]

        def lease_then_revoke(kind, key, payload):
            effect, fresh = original_begin(kind, key, payload)
            if kind == "artifact.import" and fresh and not revoked[0]:
                revoked[0] = True
                self.host.sources.update_instance(
                    "local-source", expected_revision=1, enabled=False,
                )
            return effect, fresh

        artifacts._begin = lease_then_revoke
        try:
            result = await self.host.execution.schedule_dag_missions()
        finally:
            artifacts._begin = original_begin
        self.assertFalse(self.shared["artifact_import_calls"])
        self.assertEqual(self.mission_state(mission_id), "blocked")
        with self.host._store.transaction() as db:
            effect = db.execute(
                "SELECT state,receipt_json FROM effects WHERE kind='artifact.import'",
            ).fetchone()
            self.assertEqual(effect["state"], "failed")
            self.assertEqual(json.loads(effect["receipt_json"])["reason"], "superagent/mandate-unavailable")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM deliveries WHERE state='applied'").fetchone()[0], 0)


    async def test_capture_watchdog_blocked_after_effect_lease_requires_fresh_progress(self):
        """原生捕获租约后持久 Watchdog blocked 必须在写入前阻止，真实进度后仅执行一次。"""
        from harness.app.superagent_watchdog import CoordinatorWatchdog

        mission_id, assignment_id = await self._start_work_for_artifact_boundary_race()
        scheduler = self.host._execution_service._dag_scheduler
        watchdog = CoordinatorWatchdog(
            self.host._store,
            limits={"stale_after": 1.0, "max_stale_ticks": 2, "action": "block"},
        )
        scheduler._watchdog = watchdog
        artifacts = self.host._artifacts_service
        original_begin = artifacts._begin
        blocked_once = [False]
        activity_snapshot = [None]

        def block_after_lease(kind, key, payload):
            effect, fresh = original_begin(kind, key, payload)
            if kind == "artifact.capture" and fresh and not blocked_once[0]:
                blocked_once[0] = True
                activity_snapshot[0] = self.host._execution_service.watchdog_activity(mission_id)
                self.assertTrue(activity_snapshot[0]["assignments"])
                healthy = watchdog.observe(
                    mission_id, activity=activity_snapshot[0], now="2026-11-07T00:00:00Z",
                )
                self.assertEqual(healthy["state"], "healthy")
                blocked = watchdog.observe(
                    mission_id, activity=activity_snapshot[0], now="2026-11-07T00:00:02Z",
                )
                self.assertEqual(blocked["state"], "blocked")
            return effect, fresh

        artifacts._begin = block_after_lease
        try:
            suspended = await self.host.execution.reconcile(assignment_id)
        finally:
            artifacts._begin = original_begin

        self.assertEqual(suspended["state"], "capturing")
        self.assertEqual(self.shared["artifact_capture_calls"], [])
        with self.host._store.transaction() as db:
            effect = db.execute(
                "SELECT state,receipt_json FROM effects WHERE kind='artifact.capture' "
                "AND aggregate_id=?", (assignment_id,),
            ).fetchone()
            self.assertEqual(effect["state"], "failed")
            self.assertEqual(json.loads(effect["receipt_json"])["outcome"], "not_started")

        # 仅重启 Watchdog 实例不应清除 blocked，必须观察到真正的新进度。
        scheduler._watchdog = CoordinatorWatchdog(
            self.host._store,
            limits={"stale_after": 1.0, "max_stale_ticks": 2, "action": "block"},
        )
        self.assertEqual(scheduler._watchdog.get(mission_id)["state"], "blocked")
        still_paused = await self.host.execution.reconcile(assignment_id)
        self.assertEqual(still_paused["state"], "capturing")
        self.assertEqual(self.shared["artifact_capture_calls"], [])
        progressed_activity = _advance_watchdog_activity(activity_snapshot[0])
        progressed = scheduler._watchdog.observe(
            mission_id, activity=progressed_activity, now="2026-11-07T00:00:03Z",
        )
        self.assertEqual(progressed["state"], "healthy")
        recovered = await self.host.execution.reconcile(assignment_id)
        self.assertEqual(recovered["state"], "succeeded")
        self.assertEqual(len(self.shared["artifact_capture_calls"]), 1)
        self.assertEqual(self.shared["submit_calls"], [assignment_id])
        with self.host._store.transaction() as db:
            states = [
                row["state"] for row in db.execute(
                    "SELECT state FROM effects WHERE kind='artifact.capture' "
                    "AND aggregate_id=? ORDER BY rowid", (assignment_id,),
                ).fetchall()
            ]
        self.assertEqual(states, ["failed", "completed"])

    async def test_import_watchdog_blocked_after_effect_lease_preserves_pending_delivery(self):
        """Integrator 导入租约后被持久 Watchdog 阻断，不得产生未知写入或重复原生 Run。"""
        from harness.app.superagent_watchdog import CoordinatorWatchdog

        mission_id, work_id = await self._start_work_for_artifact_boundary_race()
        scheduler = self.host._execution_service._dag_scheduler
        watchdog = CoordinatorWatchdog(
            self.host._store,
            limits={"stale_after": 1.0, "max_stale_ticks": 2, "action": "block"},
        )
        scheduler._watchdog = watchdog
        artifacts = self.host._artifacts_service
        original_begin = artifacts._begin
        blocked_once = [False]
        activity_snapshot = [None]

        def block_after_lease(kind, key, payload):
            effect, fresh = original_begin(kind, key, payload)
            if kind == "artifact.import" and fresh and not blocked_once[0]:
                blocked_once[0] = True
                activity_snapshot[0] = self.host._execution_service.watchdog_activity(mission_id)
                self.assertIsInstance(activity_snapshot[0].get("assignments"), list)
                watchdog.observe(
                    mission_id, activity=activity_snapshot[0], now="2026-11-07T00:00:00Z",
                )
                blocked = watchdog.observe(
                    mission_id, activity=activity_snapshot[0], now="2026-11-07T00:00:02Z",
                )
                self.assertEqual(blocked["state"], "blocked")
            return effect, fresh

        artifacts._begin = block_after_lease
        try:
            work_result = await self.host.execution.reconcile(work_id)
            tick = await self.host.execution.schedule_dag_missions()
        finally:
            artifacts._begin = original_begin
        self.assertEqual(work_result["state"], "succeeded")
        self.assertFalse(tick["errors"])
        self.assertEqual(self.mission_state(mission_id), "integrating")
        self.assertEqual(self.shared["artifact_import_calls"], [])
        with self.host._store.transaction() as db:
            effect = db.execute(
                "SELECT * FROM effects WHERE kind='artifact.import'",
            ).fetchone()
            self.assertIsNotNone(effect)
            self.assertEqual(effect["state"], "failed")
            self.assertEqual(json.loads(effect["receipt_json"])["outcome"], "not_started")
            self.assertEqual(db.execute(
                "SELECT state FROM deliveries WHERE target_assignment_id=?",
                (effect["aggregate_id"],),
            ).fetchone()[0], "pending")
        blocked_again = await self.host.execution.schedule_dag_missions()
        self.assertFalse(blocked_again["errors"])
        self.assertEqual(self.shared["artifact_import_calls"], [])
        with self.host._store.transaction() as db:
            effects = db.execute(
                "SELECT state,receipt_json,aggregate_id FROM effects "
                "WHERE kind='artifact.import' ORDER BY rowid",
            ).fetchall()
            delivery = db.execute(
                "SELECT state FROM deliveries WHERE target_assignment_id=?",
                (effects[0]["aggregate_id"] if effects else "",),
            ).fetchone() if effects else None
        self.assertEqual([row["state"] for row in effects], ["failed"])
        self.assertEqual(json.loads(effects[0]["receipt_json"])["outcome"], "not_started")
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery["state"], "pending")
        self.assertEqual(self.shared["artifact_import_calls"], [])
        self.assertEqual(self.shared["submit_calls"].count(work_id), 1)

    async def test_capture_stale_plan_snapshot_after_effect_lease_requires_new_admission(self):
        """过期 PlanRevision 快照不得创建原生 Git 写入；新快照只在无副作用回执后恢复。"""
        mission_id, assignment_id = await self._start_work_for_artifact_boundary_race()
        artifacts = self.host._artifacts_service
        original_admit = artifacts._authorize_and_mark_started
        stale_once = [False]

        def stale_snapshot_on_admission(effect, assignment, operation):
            if operation == "artifact.capture" and not stale_once[0]:
                stale_once[0] = True
                assignment = {**assignment, "plan_revision": assignment["plan_revision"] + 1}
            return original_admit(effect, assignment, operation)

        artifacts._authorize_and_mark_started = stale_snapshot_on_admission
        try:
            first = await self.host.execution.reconcile(assignment_id)
        finally:
            artifacts._authorize_and_mark_started = original_admit
        self.assertEqual(first["state"], "unknown")
        self.assertEqual(self.shared["artifact_capture_calls"], [])
        self.assertNotEqual(self.mission_state(mission_id), "completed")
        with self.host._store.transaction() as db:
            effect = db.execute(
                "SELECT state,receipt_json FROM effects WHERE kind='artifact.capture' "
                "AND aggregate_id=?", (assignment_id,),
            ).fetchone()
            self.assertEqual(effect["state"], "failed")
            self.assertEqual(json.loads(effect["receipt_json"])["reason"], "superagent/mission-corrupt")
        recovered = await self.host.execution.reconcile(assignment_id)
        self.assertEqual(recovered["state"], "succeeded")
        self.assertEqual(len(self.shared["artifact_capture_calls"]), 1)
        self.assertEqual(self.shared["submit_calls"], [assignment_id])
        with self.host._store.transaction() as db:
            states = [
                row["state"] for row in db.execute(
                    "SELECT state FROM effects WHERE kind='artifact.capture' "
                    "AND aggregate_id=? ORDER BY rowid", (assignment_id,),
                )
            ]
        self.assertEqual(states, ["failed", "completed"])

    async def test_import_stale_plan_snapshot_after_effect_lease_cannot_write_workspace(self):
        """Integrator 导入在 Effect 认领后遇过期计划快照，必须留下可核对的无副作用失败。"""
        mission_id, work_id = await self._start_integrator_for_import_boundary_race()
        artifacts = self.host._artifacts_service
        original_admit = artifacts._authorize_and_mark_started
        stale_once = [False]

        def stale_snapshot_on_admission(effect, assignment, operation):
            if operation == "artifact.import" and not stale_once[0]:
                stale_once[0] = True
                assignment = {**assignment, "plan_revision": assignment["plan_revision"] + 1}
            return original_admit(effect, assignment, operation)

        artifacts._authorize_and_mark_started = stale_snapshot_on_admission
        try:
            first = await self.host.execution.schedule_dag_missions()
        finally:
            artifacts._authorize_and_mark_started = original_admit
        self.assertFalse(self.shared["artifact_import_calls"])
        self.assertNotEqual(self.mission_state(mission_id), "completed")
        with self.host._store.transaction() as db:
            effect = db.execute(
                "SELECT state,receipt_json FROM effects WHERE kind='artifact.import'",
            ).fetchone()
            self.assertEqual(effect["state"], "failed")
            self.assertEqual(json.loads(effect["receipt_json"])["reason"], "superagent/mission-corrupt")
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM deliveries WHERE state='applied'",
            ).fetchone()[0], 0)
        restored = await self.host.execution.schedule_dag_missions()
        self.assertFalse(restored["errors"])
        self.assertEqual(len(self.shared["artifact_import_calls"]), 1)
        self.assertEqual(self.shared["submit_calls"].count(work_id), 1)
        with self.host._store.transaction() as db:
            states = [
                row["state"] for row in db.execute(
                    "SELECT state FROM effects WHERE kind='artifact.import' ORDER BY rowid",
                )
            ]
        self.assertEqual(states, ["failed", "completed"])

    async def test_unknown_capture_reconciles_original_worktree_receipt_without_second_write(self):
        """首次捕获已写入但 ACK 丢失后，原生 Run 回查不得重复执行 Git 捕获。"""
        self._reopen_with_system_profiles(ArtifactReviewPlanner())
        received, accepted, _ = self.accept_event(
            actions=("read", "reply"),
            tools=("write_file", "read_file"), max_turns=12,
        )
        started = await self.host.execution.route_trigger(
            accepted["ticket_id"], received["trigger_id"],
        )
        self.assertEqual(started["state"], "running")
        assignment_id = started["assignment_id"]
        self.adapter.finish(assignment_id, {
            "status": "completed", "summary": "Files ready", "reply": "",
        })
        original = self.adapter.capture_workspace

        async def lost_capture_receipt(worker):
            await original(worker)
            raise RuntimeError("original capture receipt was lost")

        self.adapter.capture_workspace = lost_capture_receipt
        first = await self.host.execution.reconcile(assignment_id)
        self.assertEqual(first["state"], "unknown")
        self.assertEqual(self.mission_state(started["mission_id"]), "unknown")
        self.assertEqual(len(self.shared["artifact_capture_calls"]), 1)

        self.adapter.capture_workspace = original
        recovered = await self.host.execution.reconcile(assignment_id)
        self.assertEqual(recovered["state"], "succeeded")
        self.assertEqual(len(self.shared["artifact_capture_calls"]), 1)
        self.assertEqual(self.shared["submit_calls"], [assignment_id])
        with self.host._store.transaction() as db:
            self.assertEqual(
                db.execute(
                    "SELECT state FROM effects WHERE kind='artifact.capture'",
                ).fetchone()[0], "completed",
            )
            self.assertEqual(
                db.execute(
                    "SELECT state FROM assignments WHERE assignment_id=?",
                    (assignment_id,),
                ).fetchone()[0], "succeeded",
            )


    async def test_unknown_capture_without_original_receipt_never_replays_git_write(self):
        """原捕获回执仍不可确认时，不再执行第二次 Git 捕获或推进质量门。"""
        self._reopen_with_system_profiles(ArtifactReviewPlanner())
        received, accepted, _ = self.accept_event(
            actions=("read", "reply"),
            tools=("write_file", "read_file"), max_turns=12,
        )
        started = await self.host.execution.route_trigger(
            accepted["ticket_id"], received["trigger_id"],
        )
        assignment_id = started["assignment_id"]
        self.adapter.finish(assignment_id, {
            "status": "completed", "summary": "Files ready", "reply": "",
        })

        async def no_original_receipt(worker):
            self.shared["artifact_capture_calls"].append(worker["worker_id"])
            raise RuntimeError("capture outcome cannot be determined")

        self.adapter.capture_workspace = no_original_receipt
        first = await self.host.execution.reconcile(assignment_id)
        second = await self.host.execution.reconcile(assignment_id)
        self.assertEqual((first["state"], second["state"]), ("unknown", "unknown"))
        self.assertEqual(self.mission_state(started["mission_id"]), "unknown")
        self.assertEqual(len(self.shared["artifact_capture_calls"]), 1)
        self.assertEqual(self.shared["submit_calls"], [assignment_id])
        with self.host._store.transaction() as db:
            self.assertEqual(
                db.execute(
                    "SELECT state FROM effects WHERE kind='artifact.capture'",
                ).fetchone()[0], "unknown",
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0)


    async def test_interrupted_integrator_must_settle_before_blocking_without_reviewer(self):
        """Integrator 原 Run 先报告未结算 interrupted，不得替换 Run 或提前派发 Reviewer。"""
        self._reopen_with_system_profiles(ArtifactReviewPlanner())
        received, accepted, _ = self.accept_event(
            actions=("read", "reply"),
            tools=("write_file", "read_file"), max_turns=12,
        )
        started = await self.host.execution.route_trigger(
            accepted["ticket_id"], received["trigger_id"],
        )
        mission_id = started["mission_id"]
        work_id = started["assignment_id"]
        self.adapter.finish(work_id, {
            "status": "completed", "summary": "Files ready", "reply": "",
        })
        work_result = await self.host.execution.reconcile(work_id)
        self.assertEqual(work_result["state"], "succeeded")
        await self.host.execution.schedule_dag_missions()
        with self.host._store.transaction() as db:
            integrator = db.execute(
                "SELECT assignment.* FROM assignments AS assignment "
                "JOIN jobs AS job ON job.job_id=assignment.job_id "
                "WHERE job.mission_id=? AND job.role='integrate' ORDER BY attempt_no DESC LIMIT 1",
                (mission_id,),
            ).fetchone()
        self.assertIsNotNone(integrator)
        integrator_id = integrator["assignment_id"]
        native = self.shared["runs"][integrator_id]
        self.shared["runs"][integrator_id] = self.adapter._snapshot(
            native["run_id"], native["run_reference"]["session_id"], native["input_ids"],
            status="interrupted", settled=False,
        )
        uncertain = await self.host.execution.reconcile(integrator_id)
        self.assertNotIn(uncertain["state"], {"failed", "interrupted", "succeeded"})
        await self.host.execution.schedule_dag_missions()
        with self.host._store.transaction() as db:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM assignments JOIN jobs ON jobs.job_id=assignments.job_id "
                    "WHERE jobs.mission_id=? AND jobs.role='review'", (mission_id,),
                ).fetchone()[0], 0,
            )

        self.shared["runs"][integrator_id]["settled"] = True
        terminal = await self.host.execution.reconcile(integrator_id)
        self.assertEqual(terminal["state"], "interrupted")
        await self.host.execution.schedule_dag_missions()
        await self.host.execution.schedule_dag_missions()
        with self.host._store.transaction() as db:
            self.assertEqual(
                db.execute(
                    "SELECT state FROM missions WHERE mission_id=?", (mission_id,),
                ).fetchone()[0], "blocked",
            )
            self.assertEqual(
                db.execute(
                    "SELECT state FROM jobs WHERE mission_id=? AND role='integrate'",
                    (mission_id,),
                ).fetchone()[0], "blocked",
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM assignments JOIN jobs ON jobs.job_id=assignments.job_id "
                    "WHERE jobs.mission_id=? AND jobs.role='review'", (mission_id,),
                ).fetchone()[0], 0,
            )
        self.assertEqual(self.shared["submit_calls"].count(integrator_id), 1)


    async def test_unknown_reviewer_original_run_can_freeze_invalid_review_without_replay(self):
        """Review 的原生检查短暂失败后，恢复原 Run 时冻结 invalid 结论而不生成新 Review Run。"""
        self._reopen_with_system_profiles(ArtifactReviewPlanner())
        received, accepted, _ = self.accept_event(
            actions=("read", "reply"), tools=("write_file", "read_file"), max_turns=12,
        )
        started = await self.host.execution.route_trigger(
            accepted["ticket_id"], received["trigger_id"],
        )
        mission_id = started["mission_id"]
        self.adapter.finish(started["assignment_id"], {
            "status": "completed", "summary": "Files ready", "reply": "",
        })
        self.assertEqual(
            (await self.host.execution.reconcile(started["assignment_id"]))["state"],
            "succeeded",
        )
        await self.host.execution.schedule_dag_missions()
        with self.host._store.transaction() as db:
            integ = db.execute(
                "SELECT assignment.assignment_id FROM assignments AS assignment "
                "JOIN jobs AS job ON job.job_id=assignment.job_id "
                "WHERE job.mission_id=? AND job.role='integrate'",
                (mission_id,),
            ).fetchone()
        self.assertIsNotNone(integ)
        integrator_id = integ["assignment_id"]
        self.adapter.finish(integrator_id, {
            "status": "completed", "summary": "Integrated files", "reply": "",
        })
        self.assertEqual((await self.host.execution.reconcile(integrator_id))["state"], "succeeded")
        await self.host.execution.schedule_dag_missions()
        with self.host._store.transaction() as db:
            reviewer = db.execute(
                "SELECT assignment.assignment_id FROM assignments AS assignment "
                "JOIN jobs AS job ON job.job_id=assignment.job_id "
                "WHERE job.mission_id=? AND job.role='review'",
                (mission_id,),
            ).fetchone()
        self.assertIsNotNone(reviewer)
        reviewer_id = reviewer["assignment_id"]
        self.shared["fail_inspect"] = True
        uncertain = await self.host.execution.reconcile(reviewer_id)
        self.assertEqual(uncertain["state"], "unknown")
        self.assertEqual(self.mission_state(mission_id), "unknown")
        with self.host._store.unit_of_work(actor={"kind": "system"}) as uow:
            app = CoordinatorApplicationService(uow)
            mission = uow.missions.get(mission_id)
            candidate = app._latest_integration_candidate(mission_id, mission["plan_revision"])
            evidence = self.host._execution_service._dag_scheduler._frozen_integration_resume_evidence(
                uow.db, mission, candidate,
            )
            self.assertIsNotNone(evidence)
            evidence["reconciliation_ref"] = f"mission:{mission_id}:frozen-integration"
            app._validate_mission_integration_evidence(mission_id, mission, evidence)
            # 即使其余证据真实，伪造 Run、产物、已应用交付或 PlanRevision 均不得恢复。
            for mutation in ("original_run", "artifact", "delivery", "plan_revision"):
                with self.subTest(forgery=mutation):
                    forged = copy.deepcopy(evidence)
                    if mutation == "original_run":
                        forged["original_integration"]["run_id"] = "run-forged"
                        forged["integration_artifact"]["run_id"] = "run-forged"
                    elif mutation == "artifact":
                        forged["integration_artifact"]["artifact_ref"] = "artifact:forged"
                    elif mutation == "delivery":
                        self.assertTrue(forged["deliveries"])
                        forged["deliveries"][0]["applied_tree_hash"] = "f" * 40
                    else:
                        forged["plan_revision"] = mission["plan_revision"] + 1
                    with self.assertRaises(PermissionError):
                        app._validate_mission_integration_evidence(mission_id, mission, forged)
        self.adapter.finish(reviewer_id, {
            "status": "completed", "summary": "Malformed Reviewer response", "reply": "",
        })
        recovered = await self.host.execution.reconcile(reviewer_id)
        self.assertEqual(recovered["state"], "succeeded")
        await self.host.execution.schedule_dag_missions()
        with self.host._store.transaction() as db:
            self.assertEqual(
                db.execute(
                    "SELECT state FROM reviews WHERE mission_id=?", (mission_id,),
                ).fetchone()[0], "invalid",
            )
            self.assertNotEqual(
                db.execute(
                    "SELECT state FROM missions WHERE mission_id=?", (mission_id,),
                ).fetchone()[0], "completed",
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM assignments JOIN jobs ON jobs.job_id=assignments.job_id "
                    "WHERE jobs.mission_id=? AND jobs.role='review'",
                    (mission_id,),
                ).fetchone()[0], 1,
            )
        self.assertEqual(self.shared["submit_calls"].count(reviewer_id), 1)


    async def test_unknown_reviewer_requires_original_run_evidence_before_freeze(self):
        """Reviewer 缺失原生 Run 证据时只能等待原操作回查。"""
        mission_id, reviewer_id = await self._start_reviewer_for_freeze_race()
        self.shared["fail_inspect"] = True
        self.assertEqual((await self.host.execution.reconcile(reviewer_id))["state"], "unknown")
        self.adapter.finish(reviewer_id, {
            "status": "completed", "summary": "Malformed Reviewer response", "reply": "",
        })
        scheduler = self.host._execution_service._dag_scheduler
        for references in ([], ["unrelated-evidence-id"]):
            with self.subTest(references=references):
                receipt = await scheduler.finish_review_success(
                    scheduler._execution._assignment(reviewer_id),
                    copy.deepcopy(self.shared["runs"][reviewer_id]), references,
                )
                self.assertEqual(receipt["state"], "unknown")
                with self.host._store.transaction() as db:
                    self.assertEqual(
                        db.execute("SELECT COUNT(*) FROM reviews WHERE mission_id=?", (mission_id,)).fetchone()[0], 0,
                    )
                    self.assertEqual(
                        db.execute("SELECT state FROM assignments WHERE assignment_id=?", (reviewer_id,)).fetchone()[0],
                        "unknown",
                    )
        confirmed = await self.host.execution.reconcile(reviewer_id)
        self.assertEqual(confirmed["state"], "succeeded")
        with self.host._store.transaction() as db:
            self.assertEqual(
                db.execute("SELECT state FROM reviews WHERE mission_id=?", (mission_id,)).fetchone()[0], "invalid",
            )
        self.assertEqual(self.shared["submit_calls"].count(reviewer_id), 1)

    async def _start_reviewer_for_freeze_race(self):
        """用真实 Host 路由工作、集成、审查三段，停在 Reviewer 原 Run 尚未结算处。"""
        self._reopen_with_system_profiles(ArtifactReviewPlanner())
        received, accepted, _ = self.accept_event(
            actions=("read", "reply"), tools=("write_file", "read_file"), max_turns=12,
        )
        started = await self.host.execution.route_trigger(
            accepted["ticket_id"], received["trigger_id"],
        )
        mission_id = started["mission_id"]
        self.adapter.finish(started["assignment_id"], {
            "status": "completed", "summary": "Work complete", "reply": "",
        })
        self.assertEqual(
            (await self.host.execution.reconcile(started["assignment_id"]))["state"],
            "succeeded",
        )
        await self.host.execution.schedule_dag_missions()
        with self.host._store.transaction() as db:
            integrator = db.execute(
                "SELECT assignment.assignment_id FROM assignments AS assignment "
                "JOIN jobs ON jobs.job_id=assignment.job_id "
                "WHERE jobs.mission_id=? AND jobs.role='integrate'",
                (mission_id,),
            ).fetchone()
        self.assertIsNotNone(integrator)
        self.adapter.finish(integrator["assignment_id"], {
            "status": "completed", "summary": "Integrated files", "reply": "",
        })
        self.assertEqual(
            (await self.host.execution.reconcile(integrator["assignment_id"]))["state"],
            "succeeded",
        )
        await self.host.execution.schedule_dag_missions()
        with self.host._store.transaction() as db:
            reviewer = db.execute(
                "SELECT assignment.assignment_id FROM assignments AS assignment "
                "JOIN jobs ON jobs.job_id=assignment.job_id "
                "WHERE jobs.mission_id=? AND jobs.role='review'",
                (mission_id,),
            ).fetchone()
        self.assertIsNotNone(reviewer)
        self.assertEqual(self.mission_state(mission_id), "reviewing")
        return mission_id, reviewer["assignment_id"]

    async def test_reviewer_freeze_rechecks_pause_inside_write_transaction(self):
        """预检之后、Review 落盘之前暂停，不得冻结 Verdict；resume 只结算原 Run。"""
        mission_id, reviewer_id = await self._start_reviewer_for_freeze_race()
        self.adapter.finish(reviewer_id, {
            "status": "completed", "summary": "Malformed Reviewer output", "reply": "",
        })
        scheduler = self.host._execution_service._dag_scheduler
        original = scheduler._prepare_review
        paused_once = [False]

        def pause_after_preflight(*args):
            prepared = original(*args)
            if not paused_once[0]:
                paused_once[0] = True
                receipt = self.host.execution.control_mission(
                    mission_id, "pause", request_id="phase44-review-freeze-pause",
                    actor={"kind": "human"},
                )
                self.assertEqual(receipt["state"], "applied")
            return prepared

        scheduler._prepare_review = pause_after_preflight
        suspended = await self.host.execution.reconcile(reviewer_id)
        self.assertEqual(suspended["state"], "capturing")
        with self.host._store.transaction() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM reviews WHERE mission_id=?", (mission_id,)).fetchone()[0], 0,
            )
            self.assertEqual(
                db.execute(
                    "SELECT state FROM assignments WHERE assignment_id=?", (reviewer_id,),
                ).fetchone()[0], "capturing",
            )
        receipt = self.host.execution.control_mission(
            mission_id, "resume", request_id="phase44-review-freeze-resume",
            actor={"kind": "human"},
        )
        self.assertEqual(receipt["state"], "applied")
        recovered = await self.host.execution.reconcile(reviewer_id)
        self.assertEqual(recovered["state"], "succeeded")
        with self.host._store.transaction() as db:
            self.assertEqual(
                db.execute(
                    "SELECT state FROM reviews WHERE mission_id=?", (mission_id,),
                ).fetchone()[0], "invalid",
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM reviews WHERE mission_id=?", (mission_id,)).fetchone()[0], 1,
            )
        self.assertEqual(self.shared["submit_calls"].count(reviewer_id), 1)

    async def test_reviewer_freeze_rechecks_source_revocation_inside_write_transaction(self):
        """解析原 Reviewer Run 后发生来源撤权，不得冻结新的 Review 或误发修正。"""
        mission_id, reviewer_id = await self._start_reviewer_for_freeze_race()
        self.adapter.finish(reviewer_id, {
            "status": "completed", "summary": "Malformed Reviewer output", "reply": "",
        })
        scheduler = self.host._execution_service._dag_scheduler
        original = scheduler._prepare_review

        def revoke_after_preflight(*args):
            prepared = original(*args)
            self.host.sources.update_instance(
                "local-source", expected_revision=1, enabled=False,
            )
            return prepared

        scheduler._prepare_review = revoke_after_preflight
        suspended = await self.host.execution.reconcile(reviewer_id)
        self.assertEqual(suspended["state"], "capturing")
        with self.host._store.transaction() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM reviews WHERE mission_id=?", (mission_id,)).fetchone()[0], 0,
            )
            self.assertEqual(
                db.execute(
                    "SELECT state FROM assignments WHERE assignment_id=?", (reviewer_id,),
                ).fetchone()[0], "capturing",
            )
            self.assertNotEqual(
                db.execute(
                    "SELECT state FROM missions WHERE mission_id=?", (mission_id,),
                ).fetchone()[0], "completed",
            )
        self.assertEqual(self.shared["submit_calls"].count(reviewer_id), 1)


    async def test_unsettled_interrupted_reviewer_never_freezes_review_or_repairs(self):
        """Reviewer 原 Run 的 interrupted 未确认 settled 前不能冻结结论或重复派工。"""
        mission_id, reviewer_id = await self._start_reviewer_for_freeze_race()
        native = self.shared["runs"][reviewer_id]
        self.shared["runs"][reviewer_id] = self.adapter._snapshot(
            native["run_id"], native["run_reference"]["session_id"], native["input_ids"],
            status="interrupted", settled=False,
        )
        interim = await self.host.execution.reconcile(reviewer_id)
        self.assertNotIn(interim["state"], {"interrupted", "failed", "succeeded"})
        await self.host.execution.schedule_dag_missions()
        with self.host._store.transaction() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM reviews WHERE mission_id=?", (mission_id,)).fetchone()[0], 0,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM assignments JOIN jobs ON jobs.job_id=assignments.job_id "
                    "WHERE jobs.mission_id=? AND jobs.role='review'",
                    (mission_id,),
                ).fetchone()[0], 1,
            )
        self.shared["runs"][reviewer_id]["settled"] = True
        result = await self.host.execution.reconcile(reviewer_id)
        self.assertEqual(result["state"], "interrupted")
        await self.host.execution.schedule_dag_missions()
        with self.host._store.transaction() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM reviews WHERE mission_id=?", (mission_id,)).fetchone()[0], 0,
            )
            self.assertEqual(
                db.execute("SELECT state FROM missions WHERE mission_id=?", (mission_id,)).fetchone()[0], "blocked",
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM assignments JOIN jobs ON jobs.job_id=assignments.job_id "
                    "WHERE jobs.mission_id=? AND jobs.role='review'",
                    (mission_id,),
                ).fetchone()[0], 1,
            )
        self.assertEqual(self.shared["submit_calls"].count(reviewer_id), 1)

    async def test_watchdog_blocks_reviewer_freeze_after_preflight_until_fresh_progress(self):
        """Watchdog 在 Reviewer 解析后升到 blocked 时，当前事务也须拒绝冻结。"""
        from harness.app.superagent_watchdog import CoordinatorWatchdog

        mission_id, reviewer_id = await self._start_reviewer_for_freeze_race()
        self.adapter.finish(reviewer_id, {
            "status": "completed", "summary": "Malformed Reviewer output", "reply": "",
        })
        scheduler = self.host._execution_service._dag_scheduler
        watchdog = CoordinatorWatchdog(
            self.host._store,
            limits={"stale_after": 1.0, "max_stale_ticks": 2, "action": "block"},
        )
        scheduler._watchdog = watchdog
        original = scheduler._prepare_review
        blocked_once = [False]
        activity_snapshot = [None]

        def watchdog_after_preflight(*args):
            prepared = original(*args)
            if not blocked_once[0]:
                blocked_once[0] = True
                activity_snapshot[0] = self.host._execution_service.watchdog_activity(mission_id)
                self.assertTrue(activity_snapshot[0]["assignments"])
                healthy = watchdog.observe(
                    mission_id, activity=activity_snapshot[0], now="2026-11-07T00:00:00Z",
                )
                self.assertEqual(healthy["state"], "healthy")
                blocked = watchdog.observe(
                    mission_id, activity=activity_snapshot[0], now="2026-11-07T00:00:02Z",
                )
                self.assertEqual(blocked["state"], "blocked")
            return prepared

        scheduler._prepare_review = watchdog_after_preflight
        suspended = await self.host.execution.reconcile(reviewer_id)
        self.assertEqual(suspended["state"], "capturing")
        with self.host._store.transaction() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM reviews WHERE mission_id=?", (mission_id,)).fetchone()[0], 0,
            )
        progressed_activity = _advance_watchdog_activity(activity_snapshot[0])
        progressed = watchdog.observe(
            mission_id, activity=progressed_activity, now="2026-11-07T00:00:03Z",
        )
        self.assertEqual(progressed["state"], "healthy")
        recovered = await self.host.execution.reconcile(reviewer_id)
        self.assertEqual(recovered["state"], "succeeded")
        self.assertEqual(self.shared["submit_calls"].count(reviewer_id), 1)


    async def test_artifact_plan_without_host_quality_profiles_fails_closed(self):
        self.host.close()
        self.adapter = FakeExecutionAdapter(self.instance_id, self.profile, self.shared)
        self.host = self._open_host(self.adapter, planner=ArtifactReviewPlanner())
        received, accepted, _ = self.accept_event()
        with self.assertRaises(HarnessError) as raised:
            await self.host.execution.route_trigger(accepted["ticket_id"], received["trigger_id"])
        self.assertEqual(raised.exception.code, "superagent/quality-gate-unavailable")
        with self.host._store.transaction() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM missions WHERE ticket_id=?", (accepted["ticket_id"],)).fetchone()[0],
                0,
            )

    async def test_writable_worker_cannot_downgrade_acceptance_to_single_report(self):
        class DowngradePlanner:
            def plan(self, *, trigger_id, profile, prompt):
                return {
                    "plan": {"nodes": [{
                        "node_id": "work", "role": "work", "profile_id": "local-integrator",
                        "dependencies": [],
                    }]},
                    "acceptance": {"kind": "single_worker_report", "schema_version": 1},
                }

        self._reopen_with_system_profiles(DowngradePlanner())
        received, accepted, _ = self.accept_event(
            actions=("read", "reply"),
            tools=("write_file", "read_file"),
        )
        with self.assertRaises(HarnessError) as raised:
            await self.host.execution.route_trigger(accepted["ticket_id"], received["trigger_id"])
        self.assertEqual(raised.exception.code, "superagent/acceptance-invalid")

    async def test_reviewer_profile_rejects_workspace_write_tools(self):
        self.host.close()
        self.shared["tool_effects"] = {"write_file": "workspace_write"}
        adapter = FakeExecutionAdapter(self.instance_id, self.profile, self.shared)
        adapter.profiles["local-integrator"] = WorkerProfile(
            profile_id="local-integrator", repo=str(self.root), description="Host Integrator",
            source_instance_ids=("local-source",),
            actions=("read", "reply"), tools=("write_file",),
            max_turns=4, max_seconds=60,
        )
        adapter.profiles["local-reviewer"] = WorkerProfile(
            profile_id="local-reviewer", repo=str(self.root), description="Invalid Reviewer",
            source_instance_ids=("local-source",), actions=("read",), tools=("write_file",),
            max_turns=4, max_seconds=60,
        )
        with self.assertRaisesRegex(ValueError, "read effect"):
            self._open_host(
                adapter, planner=ArtifactReviewPlanner(),
                system_profiles={"integrate": "local-integrator", "review": "local-reviewer"},
            )

    async def test_integrator_profile_requires_workspace_write_tools(self):
        self.host.close()
        self.shared["tool_effects"] = {"read_file": "read"}
        adapter = FakeExecutionAdapter(self.instance_id, self.profile, self.shared)
        adapter.profiles["local-integrator"] = WorkerProfile(
            profile_id="local-integrator", repo=str(self.root), description="Read-only Integrator",
            source_instance_ids=("local-source",), actions=("read", "reply"), tools=("read_file",),
            max_turns=4, max_seconds=60,
        )
        adapter.profiles["local-reviewer"] = WorkerProfile(
            profile_id="local-reviewer", repo=str(self.root), description="Independent Reviewer",
            source_instance_ids=("local-source",), actions=("read",), tools=("read_file",),
            max_turns=4, max_seconds=60,
        )
        with self.assertRaisesRegex(ValueError, "workspace-write tools"):
            self._open_host(
                adapter, planner=ArtifactReviewPlanner(),
                system_profiles={"integrate": "local-integrator", "review": "local-reviewer"},
            )

    async def test_readonly_single_report_allows_readonly_tools_without_quality_profiles(self):
        self.host.close()
        self.shared["tool_effects"] = {"read_file": "read"}
        self.adapter = FakeExecutionAdapter(self.instance_id, self.profile, self.shared)
        self.adapter.profiles["local-worker"] = WorkerProfile(
            profile_id="local-worker", repo=str(self.root), description="Read-only Local Worker",
            source_instance_ids=("local-source",), actions=("read", "reply"), tools=("read_file",),
            max_turns=4, max_seconds=60,
        )
        self.host = self._open_host(self.adapter)
        received, accepted, _ = self.accept_event(tools=("read_file",))
        started = await self.host.execution.route_trigger(accepted["ticket_id"], received["trigger_id"])
        self.assertEqual(started["state"], "running")
        with self.host._store.transaction() as db:
            plan = db.execute(
                "SELECT acceptance_json FROM mission_plans WHERE mission_id=? AND plan_revision=1",
                (started["mission_id"],),
            ).fetchone()
            jobs = db.execute(
                "SELECT role FROM jobs WHERE mission_id=? AND plan_revision=1",
                (started["mission_id"],),
            ).fetchall()
        self.assertEqual(json.loads(plan["acceptance_json"])["kind"], "single_worker_report")
        self.assertEqual([row["role"] for row in jobs], ["work"])

    @staticmethod
    def _local_identity(event):
        metadata = event.get("metadata", {})
        return {
            "actor_id": metadata.get("actor_id", "human:1"),
            "actor_kind": metadata.get("actor_kind", "human"),
            "addressed": metadata.get("addressed", "direct"),
        }

    def _open_host(self, adapter, planner=None, system_profiles=None):
        return CoordinatorHost.open(
            self.root / "v2", instance_id=self.instance_id,
            identity={"project": "harness", "suite": "phase3"},

            local_identity_verifier=self._local_identity,
            executor_adapter=adapter, planner=planner, system_profiles=system_profiles,
        )

    def _reopen_with_system_profiles(self, planner):
        self.host.close()
        self.shared["tool_effects"] = {
            "write_file": "workspace_write", "read_file": "read",
        }
        self.adapter = FakeExecutionAdapter(self.instance_id, self.profile, self.shared)
        self.adapter.profiles["local-integrator"] = WorkerProfile(
            profile_id="local-integrator", repo=str(self.root), description="Host Integrator",
            source_instance_ids=("local-source",),
            actions=("read", "reply"), tools=("write_file",),
            max_turns=4, max_seconds=60,
        )
        self.adapter.profiles["local-reviewer"] = WorkerProfile(
            profile_id="local-reviewer", repo=str(self.root), description="Independent Reviewer",
            source_instance_ids=("local-source",), actions=("read",), tools=("read_file",),
            max_turns=4, max_seconds=60,
        )
        self.host = self._open_host(
            self.adapter, planner=planner,
            system_profiles={"integrate": "local-integrator", "review": "local-reviewer"},
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
                     text="Fix the existing typo.", max_turns=3,
                     actions=("read", "reply"), tools=()):
        thread_id = self._thread_id(thread_key)
        scope = {
            "source_instance_ids": ["local-source"], "thread_ids": [thread_id],
            "actions": list(actions), "tools": list(tools),
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

    async def test_mission_budget_uses_mandate_scope_and_worker_profile_limits_separately(self):
        origin, accepted, _ = self.accept_event(max_turns=12)
        service = self.host._execution_service
        ticket = service._authorization.ticket(accepted["ticket_id"])
        trigger = service._sources.get_trigger(origin["trigger_id"])
        input_row = service._input(accepted["ticket_id"], origin["trigger_id"])

        _, default_profile, mission_budget = service._mandate_context(
            ticket, trigger, input_row, require_work=False,
        )
        _, _, work_budget = service._mandate_context(
            ticket, trigger, input_row, require_work=True,
        )

        self.assertEqual(default_profile.max_turns, 4)
        self.assertEqual(mission_budget["max_turns"], 12)
        self.assertEqual(work_budget["max_turns"], 4)

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

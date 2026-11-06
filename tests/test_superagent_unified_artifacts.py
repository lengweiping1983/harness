"""真实临时 Git 工作树上的统一产物捕获与未知导入回查。"""
from __future__ import annotations

import hashlib
import asyncio
import json
import sqlite3
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.app.git_process import run_git
from harness.app.superagent_artifacts import CoordinatorArtifacts
from harness.app.superagent_coordinator_store import CoordinatorStore, _hash
from harness.app.superagent_coordinator_service import CoordinatorApplicationService
from harness.app.superagent_executor import HarnessExecutorAdapter, WorkerProfile
from harness.core.errors import HarnessError
from tests.agent_configuration_fixture import RecordingTransport


class UnifiedArtifactTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "initial.txt").write_text("initial\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "fixture")
        self.base = self.git("rev-parse", "HEAD")
        flags = {field.name: False for field in fields(HarnessConfig) if field.name.startswith("enable_")}
        flags["enable_worktrees"] = True
        self.harness = Harness(HarnessConfig(home=self.root / "home", cwd=self.repo,
            api_key="fixture-only", model="fixture-model", language="en", **flags),
            codex_gateway_home=self.root / "gateway", transport=RecordingTransport())
        self.addAsyncCleanup(self.harness.close)
        owner = self.harness.create_session()
        self.builder = WorkerProfile("builder", str(self.repo), "Build", ("local",), ("read",), ())
        self.reviewer = WorkerProfile("reviewer", str(self.repo), "Review", ("local",), ("read",), ())
        self.adapter = HarnessExecutorAdapter(self.harness, instance_id="unified", owner_session_id=owner.id,
            profiles=[self.builder, self.reviewer], default_profiles={"local": "builder"})
        self.addAsyncCleanup(self.adapter.aclose)
        self.store = CoordinatorStore(self.root / "state" / "superagent.sqlite3", instance_id="unified", identity={})
        def lookup(identity):
            with self.store.unit_of_work(actor={}) as uow:
                return uow.workers.get(identity)
        self.adapter.set_worker_lookup(lookup)
        with self.store.unit_of_work(actor={}) as uow:
            uow.tickets.create({"ticket_id": "ticket", "mandate_id": "mandate", "origin_trigger_id": "trigger", "title": "Files"})
            uow.missions.create({"mission_id": "mission", "ticket_id": "ticket", "sequence": 1,
                                 "repo": str(self.repo), "base_commit": self.base})
            self.plan = uow.plans.create("mission", expected_mission_revision=1,
                plan={"nodes": [
                    {"node_id": "first", "role": "work", "profile_id": "builder", "dependencies": []},
                    {"node_id": "second", "role": "work", "profile_id": "builder",
                     "dependencies": [{"node_id": "first", "requires_delivery": True}]},
                    {"node_id": "independent", "role": "work", "profile_id": "builder", "dependencies": []},
                ]}, acceptance={},
                created_by_kind="host", created_by_ref="fixture")
        self.workers = []
        self.assignments = []
        ordered_jobs = sorted(
            self.plan["jobs"],
            key=lambda job: ("first", "second", "independent").index(job["node_id"]),
        )
        for index, job in enumerate(ordered_jobs):
            worker = {"worker_id": f"worker-{index}", "ticket_id": "ticket", "mission_id": "mission",
                "job_id": job["job_id"], "stage": 1, "profile_id": "builder", "repo": str(self.repo),
                "base_commit": self.base, "runtime_instance_id": "unified", "identity_hash": _hash(f"worker-{index}"),
                "workspace_id": None, "session_id": None}
            resource = await self.adapter.create_workspace(worker, self.builder)
            worker["workspace_id"] = resource["workspace_id"]
            worker["session_id"] = await self.adapter.create_session(worker, self.builder)
            with self.store.unit_of_work(actor={}) as uow:
                worker = uow.workers.create({**worker, "state": "ready"})
                assignment = uow.assignments.create({"assignment_id": f"assignment-{index}",
                    "job_id": job["job_id"], "attempt_no": 1, "submission_key": f"submission-{index}",
                    "worker_id": worker["worker_id"], "workspace_id": worker["workspace_id"], "session_id": worker["session_id"]})
            assignment["mission_id"] = "mission"
            self.workers.append(worker)
            self.assignments.append(assignment)
        assignment, worker = self.assignments[0], self.workers[0]
        with self.store.unit_of_work(actor={}) as uow:
            assignment = CoordinatorApplicationService(uow).transition("assignment", assignment["assignment_id"],
                expected_revision=1, target_state="submitting", facts={"authorized": True,
                "stable_submission_persisted": True, "mandate_valid": True, "control_allows": True})
        started = await self.adapter.submit(worker, {**assignment, "max_seconds": 60}, "Inspect the fixture.", [])
        record = self.harness.runtime.get(started["run_id"])
        await asyncio.wait_for(record.task, 5)
        self.assertEqual(record.status, "completed")
        with self.store.unit_of_work(actor={}) as uow:
            app = CoordinatorApplicationService(uow)
            assignment = app.transition("assignment", assignment["assignment_id"], expected_revision=2,
                target_state="accepted", facts={"authorized": True, "harness_acceptance_confirmed": True},
                changes={"run_id": started["run_id"]})
            assignment = app.transition("assignment", assignment["assignment_id"], expected_revision=3,
                target_state="capturing", facts={"authorized": True, "original_run_settled": True})
        self.assignments[0] = {**assignment, "mission_id": "mission"}
        self.admitted = True
        self.artifacts = CoordinatorArtifacts(self.store, self.adapter, self.admit)

    def git(self, *args):
        result = run_git(self.repo, *args)
        if result.returncode:
            raise RuntimeError(result.stderr.decode("utf-8"))
        return result.stdout.decode("utf-8").strip()

    def admit(self, mission_id):
        if not self.admitted:
            raise HarnessError("paused", code="fixture/paused")

    def path(self, index):
        return Path(self.harness.worktrees.get(self.workers[index]["workspace_id"])["path"])

    async def test_real_uncommitted_binary_and_deleted_files_are_frozen_and_delivered(self):
        source, target = self.path(0), self.path(1)
        (source / "initial.txt").unlink()
        (source / "new.bin").write_bytes(b"\0\xff\x01")
        artifact = await self.artifacts.capture(self.assignments[0], self.workers[0])
        manifest = json.loads(artifact["manifest_json"])
        self.assertEqual(manifest["deleted"], ["initial.txt"])
        self.assertEqual(manifest["files"][0]["sha256"], hashlib.sha256(b"\0\xff\x01").hexdigest())
        self.assertTrue(await self.artifacts.deliver(self.assignments[1], self.workers[1], [artifact]))
        self.assertEqual((target / "new.bin").read_bytes(), b"\0\xff\x01")
        self.assertFalse((target / "initial.txt").exists())
        self.assertEqual((self.repo / "initial.txt").read_text(encoding="utf-8"), "initial\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.base)
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT state FROM deliveries").fetchone()[0], "applied")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction(write=True) as db:
                db.execute("DELETE FROM deliveries")

    async def test_artifact_delivery_requires_a_frozen_dag_dependency(self):
        (self.path(0) / "dependency.txt").write_text("upstream\n", encoding="utf-8")
        artifact = await self.artifacts.capture(self.assignments[0], self.workers[0])
        with self.assertRaises(sqlite3.IntegrityError):
            await self.artifacts.deliver(self.assignments[2], self.workers[2], [artifact])
        with self.store.transaction() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM deliveries WHERE target_assignment_id=?",
                           (self.assignments[2]["assignment_id"],)).fetchone()[0],
                0,
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM effects WHERE kind='artifact.import'").fetchone()[0],
                0,
            )

    async def test_lost_capture_receipt_recovers_original_frozen_delivery_without_second_capture(self):
        original = self.adapter.capture_workspace
        calls = []

        async def lost(worker):
            calls.append(worker["worker_id"])
            await original(worker)
            raise RuntimeError("receipt lost")

        self.adapter.capture_workspace = lost
        with self.assertRaisesRegex(RuntimeError, "receipt lost"):
            await self.artifacts.capture(self.assignments[0], self.workers[0])
        artifact = await self.artifacts.capture(self.assignments[0], self.workers[0])
        self.assertEqual(calls, ["worker-0"])
        self.assertEqual(artifact["assignment_id"], "assignment-0")
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT state FROM effects").fetchone()[0], "completed")

    async def test_lost_import_receipt_recovers_exact_original_import_without_second_write(self):
        (self.path(0) / "new.txt").write_text("delivery\n", encoding="utf-8")
        artifact = await self.artifacts.capture(self.assignments[0], self.workers[0])
        original = self.adapter.apply_artifact_delivery
        calls = []

        async def lost(worker, artifacts, plan):
            calls.append(worker["worker_id"])
            await original(worker, artifacts, plan)
            raise RuntimeError("import receipt lost")

        self.adapter.apply_artifact_delivery = lost
        with self.assertRaisesRegex(RuntimeError, "import receipt lost"):
            await self.artifacts.deliver(self.assignments[1], self.workers[1], [artifact])
        self.assertTrue(await self.artifacts.deliver(self.assignments[1], self.workers[1], [artifact]))
        self.assertEqual(calls, ["worker-1"])
        self.assertEqual((self.path(1) / "new.txt").read_text(encoding="utf-8"), "delivery\n")

    async def test_pause_after_import_preflight_prevents_late_workspace_write(self):
        artifact = await self.artifacts.capture(self.assignments[0], self.workers[0])
        original = self.adapter.plan_artifact_delivery

        async def paused(worker, artifacts):
            plan = await original(worker, artifacts)
            self.admitted = False
            return plan

        self.adapter.plan_artifact_delivery = paused
        with self.assertRaises(HarnessError):
            await self.artifacts.deliver(self.assignments[1], self.workers[1], [artifact])
        workspace = self.harness.worktrees.get(self.workers[1]["workspace_id"])
        self.assertIsNone(workspace.get("delivery_inputs"))
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM effects WHERE kind='artifact.import'").fetchone()[0], 0)

    async def test_multiple_profiles_require_and_preserve_the_host_selected_default(self):
        self.assertEqual(self.adapter.profile_for_source("local"), self.builder)
        self.assertEqual(set(p.profile_id for p in self.adapter.profiles_for_source("local")), {"builder", "reviewer"})
        with self.assertRaises(ValueError):
            HarnessExecutorAdapter(self.harness, instance_id="invalid", owner_session_id=self.adapter.owner_session_id,
                profiles=[self.builder, self.reviewer])

    async def test_capture_without_a_settled_original_run_never_creates_an_effect(self):
        with self.assertRaises(HarnessError) as caught:
            await self.artifacts.capture(self.assignments[1], self.workers[1])
        self.assertEqual(caught.exception.code, "superagent/artifact-run-unsettled")
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM effects").fetchone()[0], 0)

    async def test_missing_capture_receipt_remains_unknown_without_repeating_the_operation(self):
        calls = []

        async def missing(worker):
            calls.append(worker["worker_id"])
            raise RuntimeError("outcome unavailable")

        self.adapter.capture_workspace = missing
        with self.assertRaisesRegex(RuntimeError, "outcome unavailable"):
            await self.artifacts.capture(self.assignments[0], self.workers[0])
        with self.assertRaises(HarnessError) as caught:
            await self.artifacts.capture(self.assignments[0], self.workers[0])
        self.assertEqual(caught.exception.code, "superagent/artifact-unknown")
        self.assertEqual(calls, ["worker-0"])
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT state FROM effects").fetchone()[0], "unknown")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0)

    async def test_completed_import_recovers_a_lost_delivery_projection_without_another_import(self):
        artifact = await self.artifacts.capture(self.assignments[0], self.workers[0])
        original = self.artifacts._record_applied
        calls = []
        apply = self.adapter.apply_artifact_delivery

        async def counted(worker, artifacts, plan):
            calls.append(worker["worker_id"])
            return await apply(worker, artifacts, plan)

        def lost(*args):
            raise RuntimeError("projection unavailable")

        self.adapter.apply_artifact_delivery = counted
        self.artifacts._record_applied = lost
        with self.assertRaisesRegex(RuntimeError, "projection unavailable"):
            await self.artifacts.deliver(self.assignments[1], self.workers[1], [artifact])
        self.artifacts._record_applied = original
        self.assertTrue(await self.artifacts.deliver(self.assignments[1], self.workers[1], [artifact]))
        self.assertEqual(calls, ["worker-1"])
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT state FROM deliveries").fetchone()[0], "applied")

    async def test_forged_artifact_workspace_cannot_supply_dependency_files(self):
        artifact = await self.artifacts.capture(self.assignments[0], self.workers[0])
        forged = {**artifact, "workspace_id": self.workers[1]["workspace_id"]}
        with self.assertRaises(HarnessError) as caught:
            await self.artifacts.deliver(self.assignments[1], self.workers[1], [forged])
        self.assertEqual(caught.exception.code, "superagent/delivery-scope")
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM effects WHERE kind='artifact.import'").fetchone()[0], 0)

    async def test_concurrent_captures_join_one_owned_operation(self):
        original = self.adapter.capture_workspace
        calls = []

        async def counted(worker):
            calls.append(worker["worker_id"])
            return await original(worker)

        self.adapter.capture_workspace = counted
        first, second = await asyncio.gather(
            self.artifacts.capture(self.assignments[0], self.workers[0]),
            self.artifacts.capture(self.assignments[0], self.workers[0]))
        self.assertEqual(first, second)
        self.assertEqual(calls, ["worker-0"])

    async def test_oversized_blob_preserves_files_and_the_confirmed_capture_receipt(self):
        (self.path(0) / "large.bin").write_bytes(b"\0" * (16 * 1024 * 1024 + 1))
        with self.assertRaises(HarnessError) as caught:
            await self.artifacts.capture(self.assignments[0], self.workers[0])
        self.assertEqual(caught.exception.code, "superagent/artifact-capacity")
        self.assertEqual((self.path(0) / "large.bin").stat().st_size, 16 * 1024 * 1024 + 1)
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT state FROM effects").fetchone()[0], "completed")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0)

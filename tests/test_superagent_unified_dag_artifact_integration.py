"""通过真实 Harness Worktree 与统一 DAG Scheduler 验证文件交付闭环。"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.app.git_process import run_git
from harness.app.superagent_coordinator_host import CoordinatorHost
from harness.app.superagent_coordinator_store import _hash, _json
from harness.app.superagent_executor import HarnessExecutorAdapter, WorkerProfile
from tests.agent_configuration_fixture import RecordingTransport


class _TwoRootJoinPlanner:
    """确定性规划两个独立产物及一个需要二者交付的 Join。"""

    def plan(self, *, trigger_id, profile, prompt):
        def node(node_id, instruction, dependencies=()):
            return {
                "node_id": node_id,
                "role": "work",
                "profile_id": profile.profile_id,
                "instruction_ref": f"trigger:{trigger_id}:{node_id}",
                "instruction": instruction,
                "instruction_hash": _hash(instruction),
                "dependencies": list(dependencies),
            }

        return {
            "plan": {"nodes": [
                node("source-a", "Prepare the first independent workstream."),
                node("source-b", "Prepare the second independent workstream."),
                node("join", "Combine both frozen workstreams.", [
                    {"node_id": "source-a", "requires_delivery": True},
                    {"node_id": "source-b", "requires_delivery": True},
                ]),
            ]},
            "acceptance": {"kind": "work_dag", "schema_version": 1},
        }


class DagSchedulerArtifactIntegrationTests(unittest.IsolatedAsyncioTestCase):
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
        self.git("commit", "-m", "fixture base")
        self.base_commit = self.git("rev-parse", "HEAD")

        flags = {
            field.name: False for field in fields(HarnessConfig)
            if field.name.startswith("enable_")
        }
        flags["enable_worktrees"] = True
        self.harness = Harness(
            HarnessConfig(
                home=self.root / "home", cwd=self.repo, api_key="fixture-only",
                model="fixture-model", language="en", **flags,
            ),
            codex_gateway_home=self.root / "gateway",
            transport=RecordingTransport(),
        )
        self.addAsyncCleanup(self.harness.close)
        owner = self.harness.create_session()
        self.profile = WorkerProfile(
            profile_id="builder", repo=str(self.repo), description="DAG artifact builder",
            source_instance_ids=("local-source",), actions=("read", "reply"), tools=(),
            max_turns=4, max_seconds=60,
        )
        self.adapter = HarnessExecutorAdapter(
            self.harness, instance_id="dag-artifact-integration", owner_session_id=owner.id,
            profiles=[self.profile], default_profiles={"local-source": "builder"},
        )
        self.host = CoordinatorHost.open(
            self.root / "state", instance_id="dag-artifact-integration",
            identity={"project": "harness", "suite": "dag-artifact-integration"},
            local_identity_verifier=self._local_identity,
            executor_adapter=self.adapter, planner=_TwoRootJoinPlanner(),
        )
        self.addAsyncCleanup(self.host.aclose)
        self.host.sources.create_instance(
            "local-source", kind="local", config={"namespace": "repo"},
            legacy_source_id="legacy-local",
        )

    @staticmethod
    def _local_identity(event):
        metadata = event.get("metadata", {})
        return {
            "actor_id": metadata.get("actor_id", "human:1"),
            "actor_kind": metadata.get("actor_kind", "human"),
            "addressed": metadata.get("addressed", "direct"),
        }

    def git(self, *args):
        result = run_git(self.repo, *args)
        if result.returncode:
            raise RuntimeError(result.stderr.decode("utf-8"))
        return result.stdout.decode("utf-8").strip()

    def rows(self, sql, args=()):
        with self.host._store.transaction() as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]

    def accept_event(self):
        thread_key = "dag-real-artifacts"
        thread_id = "thr_" + _hash(_json(["local-source", "local/repo/" + thread_key]))
        evidence = "Implement two independent workstreams and integrate their files."
        policy = {
            "mode": "auto",
            "timeout_seconds": 120,
            "rules": [{
                "id": "implementation", "name": "Implementation", "enabled": True,
                "instruction": "Accept only a settled implementation request.",
            }],
            "sources": {"local-source": {
                "allowed": [{"id": "human:1"}],
                "scope": {
                    "source_instance_ids": ["local-source"], "thread_ids": [thread_id],
                    "actions": ["read", "reply"], "tools": [],
                    "budget": {"max_turns": 4, "max_seconds": 60},
                },
            }},
        }
        self.host.authorization.publish_policy(
            policy, actor={"kind": "human", "id": "policy-admin"},
        )
        received = self.host.sources.receive_local("local-source", {
            "event_id": "dag-real-artifacts",
            "thread_id": thread_key,
            "text": evidence,
            "occurred_at": "2026-11-07T08:00:00Z",
            "metadata": {
                "actor_id": "human:1", "actor_kind": "human", "addressed": "direct",
            },
        })
        candidate = self.host.authorization.begin_evaluation(
            received["thread_ids"][0], actor={"kind": "host"},
        )
        accepted = self.host.authorization.complete_evaluation(
            candidate["evaluation_id"],
            {
                "kind": "accept",
                "reason": "The request specifies a concrete multi-workstream implementation.",
                "rule_assessments": [{
                    "rule_id": "implementation", "matched": True,
                    "reason": "The request requires coordinated file changes.",
                }],
                "direction": {
                    "implementation_requested": True, "target_basis": "specified",
                    "requires_design_decision": False, "evidence": evidence,
                },
                "title": "Integrate two workstreams",
                "intent_evidence": evidence,
            },
            actor={"kind": "assessor"},
        )
        if accepted["kind"] != "accept":
            raise AssertionError(f"Ticket acceptance failed: {accepted}")
        return received, accepted

    async def wait_for_run(self, assignment):
        record = self.harness.runtime.get(assignment["run_id"])
        if record.task is not None:
            await asyncio.wait_for(record.task, 15)
        self.assertEqual(record.status, "completed")
        self.assertTrue(record.settled)

    async def test_shared_dag_scheduler_delivers_real_binary_and_deletions_and_recovers_lost_import_receipt(self):
        received, accepted = self.accept_event()
        started = await self.host.execution.route_trigger(
            accepted["ticket_id"], received["trigger_id"],
        )
        self.assertIn(started["state"], {"accepted", "running"})
        mission = self.rows(
            "SELECT * FROM missions WHERE ticket_id=?", (accepted["ticket_id"],),
        )[0]
        self.assertEqual(mission["base_commit"], self.base_commit)
        roots = self.rows(
            "SELECT assignment.*,job.node_id,worker.worker_id,worker.workspace_id,"
            "worker.base_commit FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "JOIN workers AS worker ON worker.worker_id=assignment.worker_id "
            "WHERE job.mission_id=? ORDER BY job.node_id",
            (mission["mission_id"],),
        )
        self.assertEqual({row["node_id"] for row in roots}, {"source-a", "source-b"})
        self.assertEqual({row["base_commit"] for row in roots}, {self.base_commit})
        for assignment in roots:
            await self.wait_for_run(assignment)

        # Mission 创建后推进源仓库 HEAD；所有节点仍必须从冻结基线派生。
        (self.repo / "later-upstream.txt").write_text("later source change\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "advance source after mission freeze")
        self.assertNotEqual(self.git("rev-parse", "HEAD"), self.base_commit)

        for assignment in roots:
            workspace = self.harness.worktrees.get(assignment["workspace_id"])
            path = Path(workspace["path"])
            if assignment["node_id"] == "source-a":
                (path / "initial.txt").unlink()
                (path / "new.bin").write_bytes(b"\x00\xff\x01")
            else:
                (path / "other.txt").write_text("second workstream\n", encoding="utf-8")
            result = await self.host.execution.reconcile(assignment["assignment_id"])
            self.assertEqual(result["state"], "succeeded")

        self.assertEqual(
            self.rows("SELECT COUNT(*) AS total FROM artifacts WHERE assignment_id IN "
                      "(SELECT assignment_id FROM assignments WHERE job_id IN "
                      "(SELECT job_id FROM jobs WHERE mission_id=?))",
                      (mission["mission_id"],))[0]["total"],
            2,
        )

        original_apply = self.adapter.apply_artifact_delivery
        original_deliver = self.host._artifacts_service.deliver
        import_calls = []
        delivery_errors = []

        async def trace_deliver(*args, **kwargs):
            try:
                return await original_deliver(*args, **kwargs)
            except Exception as exc:
                delivery_errors.append((type(exc).__name__, str(exc), getattr(exc, "code", None)))
                raise

        async def apply_then_lose_receipt(worker, artifacts, plan):
            import_calls.append(worker["worker_id"])
            await original_apply(worker, artifacts, plan)
            raise RuntimeError("DAG import receipt lost after workspace write")

        self.adapter.apply_artifact_delivery = apply_then_lose_receipt
        self.host._artifacts_service.deliver = trace_deliver
        try:
            first_tick = await self.host.execution.schedule_dag_missions(limit=20)
            self.assertTrue(any(item["state"] == "unknown" for item in first_tick["assignments"]))
            join_rows = self.rows(
                "SELECT assignment.*,job.node_id,worker.worker_id,worker.workspace_id,"
                "worker.base_commit FROM assignments AS assignment "
                "JOIN jobs AS job ON job.job_id=assignment.job_id "
                "JOIN workers AS worker ON worker.worker_id=assignment.worker_id "
                "WHERE job.mission_id=? AND job.node_id='join'",
                (mission["mission_id"],),
            )
            self.assertEqual(len(join_rows), 1)
            self.assertEqual(join_rows[0]["state"], "pending")
            self.assertIsNone(join_rows[0]["run_id"])
            self.assertEqual(join_rows[0]["base_commit"], self.base_commit)
            self.assertEqual(import_calls, [join_rows[0]["worker_id"]])

            # 下一次 Tick 必须读取同一 Worktree 的原导入回执，而不是再次写入。
            recovered = await self.host.execution.schedule_dag_missions(limit=20)
            self.assertFalse(recovered["errors"])
            self.assertLessEqual(len(recovered["assignments"]), 1)
            join_state = self.rows(
                "SELECT assignment.state FROM assignments AS assignment "
                "JOIN jobs AS job ON job.job_id=assignment.job_id "
                "WHERE job.mission_id=? AND job.node_id='join'",
                (mission["mission_id"],),
            )[0]["state"]
            if join_state == "pending":
                # 一个 Tick 可以先结算未知 Effect；下一 Tick 再推进已恢复的 Mission。
                recovered = await self.host.execution.schedule_dag_missions(limit=20)
                self.assertFalse(recovered["errors"])
                self.assertLessEqual(len(recovered["assignments"]), 1)
            self.assertEqual(len(delivery_errors), 1, repr(delivery_errors))
        finally:
            self.adapter.apply_artifact_delivery = original_apply
            self.host._artifacts_service.deliver = original_deliver

        join = self.rows(
            "SELECT assignment.*,job.node_id,worker.worker_id,worker.workspace_id,"
            "worker.base_commit FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "JOIN workers AS worker ON worker.worker_id=assignment.worker_id "
            "WHERE job.mission_id=? AND job.node_id='join'",
            (mission["mission_id"],),
        )[0]
        self.assertIn(join["state"], {"accepted", "running", "succeeded"})
        self.assertEqual(join["base_commit"], self.base_commit)
        target = Path(self.harness.worktrees.get(join["workspace_id"])["path"])
        self.assertEqual((target / "new.bin").read_bytes(), b"\x00\xff\x01")
        self.assertEqual((target / "other.txt").read_text(encoding="utf-8"), "second workstream\n")
        self.assertFalse((target / "initial.txt").exists())
        self.assertEqual(import_calls, [join["worker_id"]])
        self.assertEqual(
            {row["state"] for row in self.rows(
                "SELECT state FROM deliveries WHERE target_assignment_id=?",
                (join["assignment_id"],),
            )},
            {"applied"},
        )
        effect = self.rows(
            "SELECT state FROM effects WHERE kind='artifact.import' AND aggregate_id=?",
            (join["assignment_id"],),
        )
        self.assertEqual([row["state"] for row in effect], ["completed"])
        self.assertEqual((self.repo / "initial.txt").read_text(encoding="utf-8"), "initial\n")
        self.assertNotEqual(self.git("rev-parse", "HEAD"), self.base_commit)

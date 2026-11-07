"""通过真实 Harness Worktree 与统一 DAG Scheduler 验证文件交付闭环。"""
from __future__ import annotations

import asyncio
import json
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
            "acceptance": {
                "kind": "artifact_review", "schema_version": 1,
                "criteria": [{"id": "all-workstreams-integrated", "kind": "binary-and-deletions"}],
                "max_repair_rounds": 1,
            },
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
        flags["enable_fs"] = True
        self.transport = RecordingTransport()
        self.harness = Harness(
            HarnessConfig(
                home=self.root / "home", cwd=self.repo, api_key="fixture-only",
                model="fixture-model", language="en", **flags,
            ),
            codex_gateway_home=self.root / "gateway",
            transport=self.transport,
        )
        self.addAsyncCleanup(self.harness.close)
        owner = self.harness.create_session()
        self.owner_session_id = owner.id
        self.profile = WorkerProfile(
            profile_id="builder", repo=str(self.repo), description="DAG artifact builder",
            source_instance_ids=("local-source",), actions=("read", "reply"), tools=(),
            max_turns=4, max_seconds=60,
        )
        self.integrator_profile = WorkerProfile(
            profile_id="integrator", repo=str(self.repo), description="Host-owned Integrator",
            source_instance_ids=("local-source",), actions=("read", "reply"),
            tools=("read", "write"), max_turns=4, max_seconds=60,
        )
        self.reviewer_profile = WorkerProfile(
            profile_id="reviewer", repo=str(self.repo), description="Host-owned independent Reviewer",
            source_instance_ids=("local-source",), actions=("read",),
            tools=("read",), max_turns=4, max_seconds=60,
        )
        self.adapter = HarnessExecutorAdapter(
            self.harness, instance_id="dag-artifact-integration", owner_session_id=owner.id,
            profiles=[self.profile, self.integrator_profile, self.reviewer_profile],
            default_profiles={"local-source": "builder"},
        )
        self.host = CoordinatorHost.open(
            self.root / "state", instance_id="dag-artifact-integration",
            identity={"project": "harness", "suite": "dag-artifact-integration"},
            local_identity_verifier=self._local_identity,
            executor_adapter=self.adapter, planner=_TwoRootJoinPlanner(),
            system_profiles={"integrate": "integrator", "review": "reviewer"},
        )
        self.addAsyncCleanup(self.host.aclose)
        # 真正的 Host 必须把相同持久 Watchdog 传给统一 Scheduler 质量门。
        self.assertIs(
            self.host._execution_service._dag_scheduler._watchdog,
            self.host._watchdog_service,
        )
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

    def accept_event(self, *, tools=("read", "write"), max_turns=12):
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
                    "actions": ["read", "reply"], "tools": list(tools),
                    "budget": {"max_turns": max_turns, "max_seconds": 60},
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

    async def test_failed_review_runs_one_fresh_repair_round_and_completes_quality_gate(self):
        review_verdicts = ["failed", "passed"]
        original_post_stream = self.transport.post_stream

        async def scripted_post_stream(url, *, headers, body, timeout):
            request = json.loads(body)
            reviewer_message = next(
                (message.get("content", "") for message in request.get("messages", [])
                 if isinstance(message.get("content"), str)
                 and "Host-owned independent Reviewer:" in message["content"]),
                None,
            )
            if reviewer_message is None:
                async for chunk in original_post_stream(
                    url, headers=headers, body=body, timeout=timeout,
                ):
                    yield chunk
                return

            self.transport.requests.append(request)
            marker = "Host-owned independent Reviewer:"
            contract_text = reviewer_message.split(marker, 1)[1].split("\n", 1)[1]
            contract, _ = json.JSONDecoder().raw_decode(contract_text.lstrip())
            available = contract.get("available_evidence") or []
            cited_evidence = next(
                (item.get("evidence_id") for item in available if item.get("evidence_id")),
                None,
            )
            if cited_evidence is None:
                raise AssertionError("Reviewer prompt did not include Work/Integrator evidence")
            verdict = review_verdicts.pop(0)
            criteria = [{
                "id": item["id"],
                "status": "passed" if verdict == "passed" else "failed",
                "reason": (
                    "The frozen candidate satisfies the acceptance criterion."
                    if verdict == "passed"
                    else "The first candidate needs a bounded integration correction."
                ),
                "evidence_ids": [cited_evidence],
            } for item in contract["criteria"]]
            output = {
                "schema_version": 1,
                "candidate_artifact_id": contract["candidate_artifact_id"],
                "acceptance_hash": contract["acceptance_hash"],
                "verdict": verdict,
                "criteria": criteria,
            }
            await asyncio.sleep(0.01)
            chunks = [
                {"choices": [{"delta": {"role": "assistant", "content": json.dumps(output)},
                              "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
            ]
            for chunk in chunks:
                yield ("data: " + json.dumps(chunk) + "\n\n").encode("utf-8")
            yield b"data: [DONE]\n\n"

        self.transport.post_stream = scripted_post_stream
        try:
            received, accepted = self.accept_event()
            started = await self.host.execution.route_trigger(
                accepted["ticket_id"], received["trigger_id"],
            )
            self.assertIn(started["state"], {"accepted", "running"})
            mission = self.rows(
                "SELECT * FROM missions WHERE ticket_id=?", (accepted["ticket_id"],),
            )[0]
            mission_id = mission["mission_id"]

            def assignment_for(role, attempt_no=1, node_id=None):
                sql = (
                    "SELECT assignment.*,job.job_id,job.node_id,job.role,"
                    "worker.workspace_id,worker.session_id,worker.profile_id "
                    "FROM assignments AS assignment "
                    "JOIN jobs AS job ON job.job_id=assignment.job_id "
                    "JOIN workers AS worker ON worker.worker_id=assignment.worker_id "
                    "WHERE job.mission_id=? AND job.role=? AND assignment.attempt_no=?"
                )
                args = [mission_id, role, attempt_no]
                if node_id is not None:
                    sql += " AND job.node_id=?"
                    args.append(node_id)
                rows = self.rows(sql, tuple(args))
                self.assertEqual(len(rows), 1, (role, attempt_no, node_id, rows))
                return rows[0]

            roots = self.rows(
                "SELECT assignment.*,job.job_id,job.node_id,job.role,worker.workspace_id,"
                "worker.session_id,worker.profile_id FROM assignments AS assignment "
                "JOIN jobs AS job ON job.job_id=assignment.job_id "
                "JOIN workers AS worker ON worker.worker_id=assignment.worker_id "
                "WHERE job.mission_id=? AND job.role='work' "
                "AND job.node_id IN ('source-a','source-b') AND assignment.attempt_no=1 "
                "ORDER BY job.node_id",
                (mission_id,),
            )
            self.assertEqual({row["node_id"] for row in roots}, {"source-a", "source-b"})
            for assignment in roots:
                await self.wait_for_run(assignment)
                workspace = Path(self.harness.worktrees.get(assignment["workspace_id"])["path"])
                (workspace / f"{assignment['node_id']}.txt").write_text(
                    f"{assignment['node_id']} output\\n", encoding="utf-8",
                )
                result = await self.host.execution.reconcile(assignment["assignment_id"])
                self.assertEqual(result["state"], "succeeded", result)

            scheduled = await self.host.execution.schedule_dag_missions(limit=20)
            self.assertFalse(scheduled["errors"])
            join = assignment_for("work", 1, "join")
            await self.wait_for_run(join)
            join_workspace = Path(self.harness.worktrees.get(join["workspace_id"])["path"])
            self.assertTrue((join_workspace / "source-a.txt").exists())
            self.assertTrue((join_workspace / "source-b.txt").exists())
            (join_workspace / "join.txt").write_text("joined workstreams\\n", encoding="utf-8")
            result = await self.host.execution.reconcile(join["assignment_id"])
            self.assertEqual(result["state"], "succeeded", result)
            self.assertEqual(
                self.rows("SELECT state FROM missions WHERE mission_id=?", (mission_id,))[0]["state"],
                "integrating",
            )

            scheduled = await self.host.execution.schedule_dag_missions(limit=20)
            self.assertFalse(scheduled["errors"])
            integrator_1 = assignment_for("integrate", 1)
            self.assertEqual(integrator_1["profile_id"], "integrator")
            await self.wait_for_run(integrator_1)
            integration_workspace_1 = Path(
                self.harness.worktrees.get(integrator_1["workspace_id"])["path"],
            )
            self.assertTrue((integration_workspace_1 / "source-a.txt").exists())
            self.assertTrue((integration_workspace_1 / "source-b.txt").exists())
            self.assertTrue((integration_workspace_1 / "join.txt").exists())
            (integration_workspace_1 / "integrated.txt").write_text(
                "first integration candidate\\n", encoding="utf-8",
            )
            result = await self.host.execution.reconcile(integrator_1["assignment_id"])
            self.assertEqual(result["state"], "succeeded", result)
            self.assertEqual(
                self.rows("SELECT state FROM missions WHERE mission_id=?", (mission_id,))[0]["state"],
                "reviewing",
            )

            scheduled = await self.host.execution.schedule_dag_missions(limit=20)
            self.assertFalse(scheduled["errors"])
            reviewer_1 = assignment_for("review", 1)
            self.assertEqual(reviewer_1["profile_id"], "reviewer")
            self.assertNotEqual(reviewer_1["workspace_id"], integrator_1["workspace_id"])
            self.assertNotEqual(reviewer_1["session_id"], integrator_1["session_id"])
            reviewer_workspace_1 = Path(
                self.harness.worktrees.get(reviewer_1["workspace_id"])["path"],
            )
            self.assertEqual(
                (reviewer_workspace_1 / "integrated.txt").read_text(encoding="utf-8"),
                "first integration candidate\\n",
            )
            await self.wait_for_run(reviewer_1)
            result = await self.host.execution.reconcile(reviewer_1["assignment_id"])
            self.assertEqual(result["state"], "succeeded", result)
            mission = self.rows("SELECT * FROM missions WHERE mission_id=?", (mission_id,))[0]
            self.assertEqual(mission["state"], "integrating")
            self.assertEqual(mission["round_no"], 1)
            # 旧轮次的成功 Assignment 不得再次结算已 checkpointed 的系统 Job。
            self.host._execution_service._dag_scheduler._finalize_terminal_assignments(mission_id)
            self.assertEqual(
                self.rows("SELECT state FROM missions WHERE mission_id=?", (mission_id,))[0]["state"],
                "integrating",
            )
            checkpoint_states = self.rows(
                "SELECT role,state FROM jobs WHERE mission_id=? AND role IN ('integrate','review')",
                (mission_id,),
            )
            self.assertEqual(
                {row["role"]: row["state"] for row in checkpoint_states},
                {"integrate": "checkpointed", "review": "checkpointed"},
            )

            review_rows = self.rows(
                "SELECT review_id,state,candidate_artifact_id,reviewer_assignment_id "
                "FROM reviews WHERE mission_id=? ORDER BY rowid",
                (mission_id,),
            )
            self.assertEqual([row["state"] for row in review_rows], ["failed"])
            first_candidate_id = review_rows[0]["candidate_artifact_id"]

            scheduled = await self.host.execution.schedule_dag_missions(limit=20)
            self.assertFalse(scheduled["errors"])
            integrator_2 = assignment_for("integrate", 2)
            # 新轮次已派发时，旧成功产物也不得让逻辑 Job 提前变成 done。
            self.host._execution_service._dag_scheduler._finalize_terminal_assignments(mission_id)
            self.assertEqual(
                self.rows("SELECT state FROM missions WHERE mission_id=?", (mission_id,))[0]["state"],
                "integrating",
            )
            self.assertNotEqual(
                self.rows("SELECT state FROM jobs WHERE job_id=?", (integrator_2["job_id"],))[0]["state"],
                "done",
            )
            self.assertEqual(integrator_2["job_id"], integrator_1["job_id"])
            self.assertNotEqual(integrator_2["workspace_id"], integrator_1["workspace_id"])
            self.assertNotEqual(integrator_2["session_id"], integrator_1["session_id"])
            integration_workspace_2 = Path(
                self.harness.worktrees.get(integrator_2["workspace_id"])["path"],
            )
            self.assertEqual(
                (integration_workspace_2 / "integrated.txt").read_text(encoding="utf-8"),
                "first integration candidate\\n",
            )
            await self.wait_for_run(integrator_2)
            (integration_workspace_2 / "integrated.txt").write_text(
                "repaired integration\\n", encoding="utf-8",
            )
            result = await self.host.execution.reconcile(integrator_2["assignment_id"])
            self.assertEqual(result["state"], "succeeded", result)
            self.assertEqual(
                self.rows("SELECT state FROM missions WHERE mission_id=?", (mission_id,))[0]["state"],
                "reviewing",
            )

            scheduled = await self.host.execution.schedule_dag_missions(limit=20)
            self.assertFalse(scheduled["errors"])
            reviewer_2 = assignment_for("review", 2)
            self.assertEqual(reviewer_2["job_id"], reviewer_1["job_id"])
            self.assertNotEqual(reviewer_2["workspace_id"], reviewer_1["workspace_id"])
            self.assertNotEqual(reviewer_2["session_id"], reviewer_1["session_id"])
            self.assertNotEqual(reviewer_2["workspace_id"], integrator_2["workspace_id"])
            self.assertNotEqual(reviewer_2["session_id"], integrator_2["session_id"])
            reviewer_workspace_2 = Path(
                self.harness.worktrees.get(reviewer_2["workspace_id"])["path"],
            )
            self.assertEqual(
                (reviewer_workspace_2 / "integrated.txt").read_text(encoding="utf-8"),
                "repaired integration\\n",
            )
            await self.wait_for_run(reviewer_2)
            result = await self.host.execution.reconcile(reviewer_2["assignment_id"])
            self.assertEqual(result["state"], "succeeded", result)

            mission = self.rows("SELECT * FROM missions WHERE mission_id=?", (mission_id,))[0]
            self.assertEqual(mission["state"], "completed")
            self.assertEqual(mission["round_no"], 1)
            self.assertFalse(review_verdicts)
            review_rows = self.rows(
                "SELECT review_id,state,candidate_artifact_id,reviewer_assignment_id "
                "FROM reviews WHERE mission_id=? ORDER BY rowid",
                (mission_id,),
            )
            self.assertEqual([row["state"] for row in review_rows], ["failed", "passed"])
            self.assertEqual(review_rows[0]["candidate_artifact_id"], first_candidate_id)
            self.assertNotEqual(
                review_rows[0]["candidate_artifact_id"],
                review_rows[1]["candidate_artifact_id"],
            )
            system_jobs = self.rows(
                "SELECT job_id,role,state FROM jobs WHERE mission_id=? AND role IN ('integrate','review')",
                (mission_id,),
            )
            self.assertEqual({row["role"] for row in system_jobs}, {"integrate", "review"})
            self.assertEqual({row["state"] for row in system_jobs}, {"done"})
            integrator_attempts = self.rows(
                "SELECT assignment.attempt_no,assignment.state FROM assignments AS assignment "
                "JOIN jobs AS job ON job.job_id=assignment.job_id "
                "WHERE job.mission_id=? AND job.role='integrate' ORDER BY assignment.attempt_no",
                (mission_id,),
            )
            reviewer_attempts = self.rows(
                "SELECT assignment.attempt_no,assignment.state FROM assignments AS assignment "
                "JOIN jobs AS job ON job.job_id=assignment.job_id "
                "WHERE job.mission_id=? AND job.role='review' ORDER BY assignment.attempt_no",
                (mission_id,),
            )
            self.assertEqual([row["attempt_no"] for row in integrator_attempts], [1, 2])
            self.assertEqual([row["attempt_no"] for row in reviewer_attempts], [1, 2])
            self.assertEqual({row["state"] for row in integrator_attempts}, {"succeeded"})
            self.assertEqual({row["state"] for row in reviewer_attempts}, {"succeeded"})
        finally:
            self.transport.post_stream = original_post_stream

"""统一 DAG 的有界阶段接续，不借用旧目标编排。"""
from __future__ import annotations

import json
import unittest
from dataclasses import replace

from harness.app.superagent_coordinator_service import CoordinatorApplicationService
from harness.app.superagent_coordinator_store import _hash, _json
from tests import test_superagent_execution as fixtures


class _StagedPlanner(fixtures.ArtifactReviewPlanner):
    def plan(self, **kwargs):
        result = super().plan(**kwargs)
        result["plan"]["nodes"].append({
            "node_id": "join", "role": "work", "profile_id": kwargs["profile"].profile_id,
            "dependencies": [{"node_id": "work", "requires_delivery": True}],
        })
        return result


class UnifiedCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = fixtures.SuperagentExecutionTests()
        await self.f.asyncSetUp()
        self.addAsyncCleanup(self.f.asyncTearDown)
        self.f.profile = replace(self.f.profile, max_work_turns=3)
        self.f._reopen_with_system_profiles(_StagedPlanner())
        self.prompts = []
        submit = self.f.adapter.submit

        async def record(worker, assignment, prompt, input_ids):
            self.prompts.append(prompt)
            return await submit(worker, assignment, prompt, input_ids)

        self.f.adapter.submit = record

    def rows(self, sql, params=()):
        with self.f.host._store.transaction() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    async def start(self, max_turns=12):
        origin, accepted, _ = self.f.accept_event(
            tools=("write_file", "read_file"), max_turns=max_turns,
        )
        started = await self.f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        self.mission_id = started["mission_id"]
        return self.f.host._execution_service._assignment(started["assignment_id"])

    async def finish(self, assignment, status="continue", summary="First stage complete"):
        self.f.adapter.finish(assignment["assignment_id"], {
            "status": status, "summary": summary,
            "next_steps": ["Finish the remaining change"] if status == "continue" else [],
        })
        return await self.f.host.execution.reconcile(assignment["assignment_id"])

    def job(self, assignment):
        return self.rows("SELECT * FROM jobs WHERE job_id=?", (assignment["job_id"],))[0]

    async def schedule(self):
        result = await self.f.host.execution.schedule_dag_missions()
        self.assertFalse(result["errors"], result)
        return result

    async def test_continuation_uses_new_resources_and_unlocks_join_only_after_ready(self):
        first = await self.start()
        await self.finish(first)
        self.assertEqual(self.job(first)["state"], "checkpointed")
        self.assertEqual(len(self.f.shared["submit_calls"]), 1)
        artifact = self.rows("SELECT * FROM artifacts WHERE assignment_id=?", (first["assignment_id"],))[0]
        # 多次恢复扫描不能把旧成功 Assignment 结算为最终节点结果。
        scheduler = self.f.host._execution_service._dag_scheduler
        scheduler._finalize_terminal_assignments(self.mission_id)
        scheduler._finalize_terminal_assignments(self.mission_id)
        self.assertEqual(self.job(first)["state"], "checkpointed")
        await self.schedule()
        second = self.rows("SELECT * FROM assignments WHERE job_id=? AND attempt_no=2", (first["job_id"],))[0]
        for key in ("assignment_id", "worker_id", "session_id", "workspace_id", "submission_key", "run_id"):
            self.assertNotEqual(first[key], second[key], key)
        delivery = self.rows("SELECT * FROM deliveries WHERE target_assignment_id=?", (second["assignment_id"],))
        self.assertEqual([(r["artifact_id"], r["state"]) for r in delivery], [(artifact["artifact_id"], "applied")])
        self.assertIn("First stage complete", self.prompts[1])
        self.assertEqual(self.rows("SELECT state FROM jobs WHERE node_id='join'")[0]["state"], "pending")
        await self.finish(second, "ready", "All changes complete")
        await self.schedule()
        self.assertEqual(self.job(first)["state"], "done")
        joins = self.rows("SELECT a.* FROM assignments a JOIN jobs j ON j.job_id=a.job_id WHERE j.node_id='join'")
        self.assertEqual(len(joins), 1)
        final_artifact = self.rows("SELECT artifact_id FROM artifacts WHERE assignment_id=?", (second["assignment_id"],))[0]
        imported = self.rows("SELECT artifact_id FROM deliveries WHERE target_assignment_id=?", (joins[0]["assignment_id"],))
        self.assertEqual(imported, [final_artifact])
        self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM mission_plans")[0]["n"], 1)

    async def test_restart_and_lost_import_receipt_reconcile_original_stage_once(self):
        first = await self.start()
        await self.finish(first)
        shared = self.f.shared
        self.f.host.close()
        self.f._reopen_with_system_profiles(_StagedPlanner())
        original = self.f.adapter.apply_artifact_delivery

        async def lose(worker, artifacts, plan):
            await original(worker, artifacts, plan)
            raise RuntimeError("import response lost")

        self.f.adapter.apply_artifact_delivery = lose
        await self.schedule()
        self.assertEqual(self.rows("SELECT state FROM missions")[0]["state"], "unknown")
        self.f.adapter.apply_artifact_delivery = original
        await self.schedule()
        self.assertEqual(len(shared["artifact_import_calls"]), 1)
        self.assertEqual(len(shared["submit_calls"]), 2)
        self.assertEqual(self.rows("SELECT MAX(attempt_no) AS n FROM assignments")[0]["n"], 2)

    async def test_budget_exhaustion_blocks_without_creating_another_stage(self):
        first = await self.start(max_turns=1)
        await self.finish(first)
        await self.schedule()
        self.assertEqual(self.job(first)["state"], "blocked")
        self.assertEqual(self.rows("SELECT state FROM missions")[0]["state"], "blocked")
        self.assertEqual(len(self.f.shared["submit_calls"]), 1)

    async def test_stage_budget_and_unchanged_checkpoint_block_without_unlocking_join(self):
        first = await self.start()
        await self.finish(first)
        await self.schedule()
        second = self.rows("SELECT * FROM assignments WHERE job_id=? AND attempt_no=2", (first["job_id"],))[0]
        # 同树同报告不能被当作真实进度；保留两个冻结产物和报告。
        prior_tree = self.f.shared["resources"][first["worker_id"]]["tree"]
        self.f.shared["resources"][second["worker_id"]]["tree"] = prior_tree
        await self.finish(second)
        await self.schedule()
        self.assertEqual(self.job(first)["state"], "blocked")
        self.assertEqual(len(self.rows("SELECT * FROM artifacts")), 2)
        self.assertEqual(len(self.f.shared["submit_calls"]), 2)

    async def test_invalid_checkpoint_and_unconfirmed_tools_never_start_continuation(self):
        first = await self.start()
        self.f.adapter.finish(first["assignment_id"], {"status": "continue", "summary": "missing next steps"})
        snapshot = self.f.shared["runs"][first["assignment_id"]]
        snapshot["unconfirmed_tools"] = 1
        result = await self.f.host.execution.reconcile(first["assignment_id"])
        self.assertEqual(result["state"], "unknown")
        self.assertFalse(self.rows("SELECT * FROM artifacts"))
        snapshot["unconfirmed_tools"] = 0
        # 原 Run 检查恢复后仍是非法交接，不能重提或生成新阶段。
        await self.f.host.execution.reconcile(first["assignment_id"])
        await self.schedule()
        self.assertEqual(len(self.f.shared["submit_calls"]), 1)
        self.assertFalse(self.rows("SELECT * FROM artifacts"))

    async def test_continuation_report_cannot_be_promoted_to_done(self):
        first = await self.start()
        await self.finish(first)
        with self.f.host._store.unit_of_work(actor={"kind": "host"}) as uow:
            job = uow.jobs.get(first["job_id"])
            job = uow.jobs._transition(
                job["job_id"], expected_revision=job["aggregate_revision"],
                changes={"state": "running"}, event_type="job.fixture.running",
            )
            # 绕过 Scheduler 的宿主调用也不能把 continue 报告当作最终完成。
            with self.assertRaises(PermissionError):
                CoordinatorApplicationService(uow).transition(
                    "job", job["job_id"], expected_revision=job["aggregate_revision"],
                    target_state="done", facts={"authorized": True, "role_result_validated": True,
                                               "artifact_frozen": True, "report_frozen": True},
                    changes={"result_hash": "a" * 64},
                )

    async def test_corrupt_frozen_checkpoint_is_preserved_and_refused(self):
        first = await self.start()
        await self.finish(first)
        with self.f.host._store.transaction(write=True) as db:
            report = json.loads(db.execute("SELECT report_json FROM assignments WHERE assignment_id=?", (first["assignment_id"],)).fetchone()[0])
            report["checkpoint"]["status"] = "ready"
            report["checkpoint"]["next_steps"] = []
            report["checkpoint_output"] = _json(report["checkpoint"])
            report["run_output_hash"] = _hash(report["checkpoint_output"])
            report["report_hash"] = _hash(_json({key: value for key, value in report.items() if key != "report_hash"}))
            db.execute("UPDATE assignments SET report_json=? WHERE assignment_id=?", (json.dumps(report), first["assignment_id"]))
        result = await self.f.host.execution.schedule_dag_missions()
        self.assertTrue(result["errors"])
        self.assertEqual(len(self.f.shared["submit_calls"]), 1)
        self.assertEqual(json.loads(self.rows("SELECT report_json FROM assignments")[0]["report_json"])["checkpoint"]["status"], "ready")

    def test_work_stage_limit_is_host_owned_and_strict(self):
        for value in (True, 0, -1, 33, 101, 1.5, "2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(self.f.profile, max_work_turns=value)

    async def test_pause_after_checkpoint_defers_new_stage_and_reuses_frozen_resources(self):
        first = await self.start()
        await self.finish(first)
        self.f.host.execution.control_mission(self.mission_id, "pause", request_id="pause-stage", actor={"kind": "human"})
        await self.schedule()
        self.assertEqual(len(self.f.shared["submit_calls"]), 1)
        self.assertEqual(len(self.f.shared["resources"]), 1)
        self.f.host.execution.control_mission(self.mission_id, "resume", request_id="resume-stage", actor={"kind": "human"})
        await self.schedule()
        self.assertEqual(len(self.f.shared["submit_calls"]), 2)

    async def test_final_stage_budget_preserves_checkpoint_and_does_not_claim_ready(self):
        first = await self.start()
        await self.finish(first)
        for attempt in (2, 3):
            await self.schedule()
            current = self.rows("SELECT * FROM assignments WHERE job_id=? AND attempt_no=?", (first["job_id"], attempt))[0]
            await self.finish(current, summary=f"Stage {attempt} still has work")
        await self.schedule()
        self.assertEqual(self.job(first)["state"], "blocked")
        self.assertEqual(len(self.f.shared["submit_calls"]), 3)
        report = json.loads(self.rows("SELECT report_json FROM assignments WHERE attempt_no=3")[0]["report_json"])
        self.assertEqual(report["checkpoint"]["status"], "continue")
        self.assertEqual(self.rows("SELECT state FROM jobs WHERE node_id='join'")[0]["state"], "pending")

    async def test_language_round_trip_is_consumed_by_each_actual_stage_submission(self):
        from harness.core.language import current_language
        token = current_language.set("en")
        try:
            first = await self.start()
            await self.finish(first)
            current_language.set("zh-CN")
            await self.schedule()
            second = self.rows("SELECT * FROM assignments WHERE job_id=? AND attempt_no=2", (first["job_id"],))[0]
            await self.finish(second, summary="Second stage still has work")
            current_language.set("en")
            await self.schedule()
            self.assertTrue(self.prompts[0].startswith("Execute this authorized work node"))
            self.assertTrue(self.prompts[1].startswith("仅用已配置的工具"))
            self.assertTrue(self.prompts[2].startswith("Execute this authorized work node"))
            self.assertIn("First stage complete", self.prompts[1])
            self.assertIn("Second stage still has work", self.prompts[2])
        finally:
            current_language.reset(token)

    async def test_dependent_node_continuation_retains_each_required_delivery(self):
        first = await self.start()
        await self.finish(first, "ready")
        await self.schedule()
        join = self.rows("SELECT a.* FROM assignments a JOIN jobs j ON j.job_id=a.job_id WHERE j.node_id='join'")[0]
        await self.finish(join)
        await self.schedule()
        continued = self.rows("SELECT * FROM assignments WHERE job_id=? AND attempt_no=2", (join["job_id"],))[0]
        imported = self.rows("SELECT * FROM deliveries WHERE target_assignment_id=?", (continued["assignment_id"],))
        self.assertEqual(len(imported), 2)
        self.assertEqual({r["state"] for r in imported}, {"applied"})
        await self.finish(continued, "ready")
        self.assertEqual(self.rows("SELECT state FROM missions")[0]["state"], "integrating")

    async def test_native_millisecond_activity_advances_watchdog_and_invalid_time_stays_blocked(self):
        from harness.app.superagent_watchdog import CoordinatorWatchdog
        first = await self.start()
        snapshot = self.f.shared["runs"][first["assignment_id"]]
        original = {"seq": 1, "time": 1794096000000, "type": "turn/start"}
        snapshot["run_reference"]["last_activity"] = original
        activity = self.f.host.execution.watchdog_activity(self.mission_id)
        self.assertEqual(activity["assignments"][0]["last_activity"]["time"], "2026-11-08T00:00:00.000+00:00")
        self.assertIs(type(original["time"]), int)
        watchdog = CoordinatorWatchdog(self.f.host._store, limits={"stale_after": 1.0, "max_stale_ticks": 2, "action": "block"})
        watchdog.observe(self.mission_id, activity=activity, now="2026-11-08T00:00:00Z")
        self.assertEqual(watchdog.observe(self.mission_id, activity=activity, now="2026-11-08T00:00:02Z")["state"], "blocked")
        for stamp in (-1, 10**100):
            snapshot["run_reference"]["last_activity"] = {**original, "time": stamp}
            invalid = self.f.host.execution.watchdog_activity(self.mission_id)
            self.assertEqual(invalid["assignments"][0]["inspection"], "invalid")
            self.assertEqual(watchdog.observe(self.mission_id, activity=invalid, now="2026-11-08T00:00:03Z")["state"], "blocked")
        snapshot["run_reference"]["last_activity"] = {**original, "seq": 2, "time": 1794096004000}
        advanced = self.f.host.execution.watchdog_activity(self.mission_id)
        self.assertEqual(watchdog.observe(self.mission_id, activity=advanced, now="2026-11-08T00:00:04Z")["state"], "healthy")

    async def test_repeated_earlier_checkpoint_cannot_cycle_through_a_different_stage(self):
        self.f.profile = replace(self.f.profile, max_work_turns=4)
        self.f._reopen_with_system_profiles(_StagedPlanner())
        first = await self.start()
        await self.finish(first)
        original_tree = self.f.shared["resources"][first["worker_id"]]["tree"]
        await self.schedule()
        second = self.rows("SELECT * FROM assignments WHERE job_id=? AND attempt_no=2", (first["job_id"],))[0]
        await self.finish(second, summary="Different intermediate checkpoint")
        await self.schedule()
        third = self.rows("SELECT * FROM assignments WHERE job_id=? AND attempt_no=3", (first["job_id"],))[0]
        self.f.shared["resources"][third["worker_id"]]["tree"] = original_tree
        await self.finish(third)
        await self.schedule()
        self.assertEqual(self.job(first)["state"], "blocked")
        self.assertEqual(len(self.f.shared["submit_calls"]), 3)

"""Phase 4.4 修正授权必须绑定冻结的失败 Review、当前候选与预算。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness.app.superagent_coordination import CoordinatorControl
from harness.app.superagent_coordinator_service import CoordinatorApplicationService
from harness.app.superagent_coordinator_store import CoordinatorStore, _hash, _json
from harness.app.superagent_dag_scheduler import CoordinatorDagScheduler
from harness.app.superagent_watchdog import CoordinatorWatchdog


class _PersistedSchedulerExecution:
    """由真实 CoordinatorStore 驱动 Scheduler 的只读查询和状态转移。"""

    def __init__(self, store):
        self._store = store

    def _read_one(self, sql, params=()):
        with self._store.transaction() as db:
            row = db.execute(sql, params).fetchone()
            return dict(row) if row is not None else None

    def _read_all(self, sql, params=()):
        with self._store.transaction() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def _transition(self, aggregate_type, identity, target, *, facts,
                    changes=None, audit_payload=None):
        with self._store.unit_of_work(actor={"kind": "host"}) as uow:
            repository = {"mission": uow.missions, "job": uow.jobs}[aggregate_type]
            current = repository.get(identity)
            return CoordinatorApplicationService(uow).transition(
                aggregate_type, identity,
                expected_revision=current["aggregate_revision"],
                target_state=target, facts={"authorized": True, **facts},
                changes=changes, audit_payload=audit_payload,
            )


class Phase44RepairGuardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = CoordinatorStore(
            Path(temp.name) / "repair.sqlite3",
            instance_id="phase44-repair-guard",
            identity={"profile": "test"},
        )

    def _fixture(self, uow, suffix, *, budget=1, round_no=0, corruption=None,
                 verdict_state="failed", multi_work=False):
        """独立数据库中物化当前 Plan、Integrator 产物和 Reviewer 冻结记录。"""
        uow.tickets.create({
            "ticket_id": f"ticket-{suffix}",
            "mandate_id": f"mandate-{suffix}",
            "origin_trigger_id": f"trigger-{suffix}",
            "title": "Repair guard",
        })
        uow.missions.create({
            "mission_id": f"mission-{suffix}", "ticket_id": f"ticket-{suffix}",
            "sequence": 1, "repo": "D:/repo", "base_commit": "a" * 40,
        })
        mission_id = f"mission-{suffix}"
        uow.plans.create(
            mission_id, expected_mission_revision=1,
            plan={"nodes": (
                [{"node_id": "work", "role": "work", "profile_id": "coder"}]
                + ([{"node_id": "work-secondary", "role": "work", "profile_id": "coder"}]
                   if multi_work else [])
            )},
            acceptance={"criteria": ["result verified"], "max_repair_rounds": budget},
            created_by_kind="human", created_by_ref="actor:test",
            system_profiles={"integrate": "integrator", "review": "reviewer"},
        )
        mission = uow.missions.get(mission_id)
        plan = uow.plans.get(mission_id, mission["plan_revision"])
        jobs = {job["role"]: job for job in plan["jobs"]}
        for role in ("integrate", "review"):
            job = jobs[role]
            uow.jobs._transition(
                job["job_id"], expected_revision=job["aggregate_revision"],
                changes={"state": "checkpointed"},
                event_type="job.fixture.checkpointed",
            )

        start = "2026-11-07T00:00:00+00:00"
        end = "2026-11-07T00:01:00+00:00"
        integrator_assignment = f"integrator-{suffix}"
        reviewer_assignment = f"reviewer-{suffix}"
        integrator_workspace = f"workspace-integrator-{suffix}"
        reviewer_workspace = f"workspace-reviewer-{suffix}"
        artifact_id = f"artifact-{suffix}"
        manifest_hash = "e" * 64
        integration_report = {
            "kind": "work_artifact_report", "artifact_id": artifact_id,
            "manifest_hash": manifest_hash,
        }
        integration_report["report_hash"] = _hash(_json(integration_report))
        uow.assignments.create({
            "assignment_id": integrator_assignment, "job_id": jobs["integrate"]["job_id"],
            "attempt_no": 1, "submission_key": f"submission-integrator-{suffix}",
            "workspace_id": integrator_workspace, "session_id": f"session-integrator-{suffix}",
            "run_id": f"run-integrator-{suffix}", "state": "succeeded", "report_json": integration_report,
            "started_at": start, "ended_at": end,
        })
        uow.db.execute(
            "INSERT INTO artifacts(artifact_id,assignment_id,workspace_id,repo,base_commit,"
            "commit_id,tree_hash,manifest_json,manifest_hash,receipt_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                artifact_id, integrator_assignment, integrator_workspace,
                mission["repo"], mission["base_commit"], "b" * 40, "f" * 64,
                "{}", manifest_hash, "{}", end,
            ),
        )

        review_id = f"review-{suffix}"
        verdict = {"verdict": verdict_state}
        acceptance_hash = plan["acceptance_hash"]
        report_acceptance_hash = ("8" * 64 if corruption == "acceptance_hash"
                                  else acceptance_hash)
        candidate_id = ("artifact-stale" if corruption == "candidate"
                        else artifact_id)
        report = {
            "kind": "review_verdict_report", "review_id": review_id,
            "candidate_artifact_id": candidate_id,
            "acceptance_hash": report_acceptance_hash,
            "state": verdict_state, "verdict": verdict,
            "evidence_set_hash": "9" * 64,
        }
        report["report_hash"] = _hash(_json(report))
        if corruption == "report_hash":
            report["report_hash"] = "7" * 64
        uow.assignments.create({
            "assignment_id": reviewer_assignment, "job_id": jobs["review"]["job_id"],
            "attempt_no": 1, "submission_key": f"submission-reviewer-{suffix}",
            "workspace_id": reviewer_workspace, "session_id": f"session-reviewer-{suffix}",
            "run_id": f"run-reviewer-{suffix}", "state": "succeeded", "report_json": report,
            "started_at": start, "ended_at": end,
        })
        if corruption != "missing_delivery":
            uow.db.execute(
                "INSERT INTO deliveries(delivery_id,artifact_id,target_assignment_id,"
                "target_workspace_id,idempotency_key,state,import_plan_json,receipt_json,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    f"delivery-{suffix}", artifact_id, reviewer_assignment,
                    reviewer_workspace, f"import-{suffix}", "applied", "{}",
                    "{}", start, end,
                ),
            )
        uow.db.execute(
            "INSERT INTO reviews(review_id,mission_id,reviewer_assignment_id,"
            "candidate_artifact_id,plan_revision,acceptance_hash,verdict_json,"
            "evidence_set_hash,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                review_id, mission_id, reviewer_assignment, artifact_id,
                mission["plan_revision"], acceptance_hash, _json(verdict),
                "9" * 64, verdict_state, end,
            ),
        )
        if corruption in {"newer_pending", "newer_settled"}:
            extra = {
                "assignment_id": f"reviewer-replayed-{suffix}",
                "job_id": jobs["review"]["job_id"],
                "attempt_no": 2,
                "submission_key": f"submission-replayed-{suffix}",
            }
            if corruption == "newer_settled":
                extra.update({
                    "state": "failed", "started_at": start,
                    "ended_at": end,
                })
            uow.assignments.create(extra)
        return uow.missions._transition(
            mission_id, expected_revision=mission["aggregate_revision"],
            changes={"state": "reviewing", "round_no": round_no},
            event_type="mission.fixture.reviewing",
        )

    def test_repair_requires_frozen_failed_review_and_available_round(self):
        """拒绝过期验收、伪造报告和超额修正；合法冻结失败允许一次修正。"""
        cases = (
            ("valid", 1, 0, None, True),
            ("zero", 0, 0, None, False),
            ("exhausted", 1, 1, None, False),
            ("report-hash", 1, 0, "report_hash", False),
            ("acceptance-hash", 1, 0, "acceptance_hash", False),
            ("candidate", 1, 0, "candidate", False),
            ("missing-delivery", 1, 0, "missing_delivery", False),
            ("newer-reviewer-pending", 1, 0, "newer_pending", False),
            ("newer-reviewer-settled", 1, 0, "newer_settled", False),
        )
        facts = {
            "authorized": True, "repair_authorized": True,
            "repair_budget_available": True, "mandate_valid": True,
            "control_allows": True,
        }
        for suffix, budget, round_no, corruption, allowed in cases:
            with self.subTest(scenario=suffix):
                with self.store.unit_of_work(actor={"kind": "system"}) as uow:
                    mission = self._fixture(
                        uow, suffix, budget=budget, round_no=round_no,
                        corruption=corruption,
                    )
                    service = CoordinatorApplicationService(uow)
                    arguments = {
                        "aggregate_type": "mission", "identity": mission["mission_id"],
                        "expected_revision": mission["aggregate_revision"],
                        "target_state": "integrating", "facts": facts,
                        "changes": {"round_no": round_no + 1},
                    }
                    if allowed:
                        result = service.transition(**arguments)
                        self.assertEqual(result["state"], "integrating")
                        self.assertEqual(result["round_no"], round_no + 1)
                    else:
                        with self.assertRaises(PermissionError):
                            service.transition(**arguments)
                        actual = uow.missions.get(mission["mission_id"])
                        self.assertEqual(actual["state"], "reviewing")
                        self.assertEqual(actual["round_no"], round_no)


    def _scheduler(self):
        scheduler = CoordinatorDagScheduler(_PersistedSchedulerExecution(self.store), policy=object())
        scheduler._mission_authorization = lambda mission: (True, True)
        # 此夹具只持久化质量门事实，复用可控的授权观察来模拟事务内竞争。
        scheduler._mission_authorization_in_transaction = (
            lambda mission, db: scheduler._mission_authorization(mission)
        )
        return scheduler

    def _complete_fixture_jobs(self, uow, mission_id):
        """将工作与两份质量门 Job 置于已冻结终态，用于测试调度出口。"""
        rows = uow.db.execute(
            "SELECT job_id,role,aggregate_revision FROM jobs WHERE mission_id=?",
            (mission_id,),
        ).fetchall()
        for row in rows:
            uow.jobs._transition(
                row["job_id"], expected_revision=row["aggregate_revision"],
                changes={"state": "done", "result_hash": "d" * 64},
                event_type="job.fixture.completed",
            )

    def test_scheduler_blocks_invalid_review_and_exhausted_repair_budget(self):
        """真正走 Scheduler 状态推进，而非仅验证服务授权守卫。"""
        scenarios = (
            ("invalid", 1, 0, "invalid", "review-invalid"),
            ("zero-budget-exit", 0, 0, "failed", "review-repair-budget-exhausted"),
            ("one-budget-exit", 1, 1, "failed", "review-repair-budget-exhausted"),
        )
        for suffix, budget, round_no, state, outcome in scenarios:
            with self.subTest(case=suffix):
                with self.store.unit_of_work(actor={"kind": "system"}) as uow:
                    mission = self._fixture(
                        uow, suffix, budget=budget, round_no=round_no,
                        verdict_state=state,
                    )
                    self._complete_fixture_jobs(uow, mission["mission_id"])
                scheduler = self._scheduler()
                scheduler._advance_mission_state(mission["mission_id"])
                with self.store.unit_of_work(actor={"kind": "system"}) as uow:
                    actual = uow.missions.get(mission["mission_id"])
                    self.assertEqual(actual["state"], "blocked")
                    self.assertEqual(actual["outcome"], outcome)
                    self.assertEqual(actual["round_no"], round_no)
                    self.assertEqual(
                        uow.db.execute(
                            "SELECT COUNT(*) FROM assignments JOIN jobs USING(job_id) "
                            "WHERE jobs.mission_id=? AND jobs.role='integrate'",
                            (mission["mission_id"],),
                        ).fetchone()[0], 1,
                    )

    def test_second_failed_review_exhausts_budget_preserves_both_frozen_attempts(self):
        """两次独立 Review 均失败时保留候选及历史，不启动第三次 Integrator。"""
        suffix = "two-failed-reviews"
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._fixture(uow, suffix, budget=1, round_no=1)
            mission_id = mission["mission_id"]
            plan = uow.plans.get(mission_id, mission["plan_revision"])
            jobs = {row["role"]: row for row in plan["jobs"]}
            artifact_id = f"artifact-second-{suffix}"
            integrator_assignment = f"integrator-second-{suffix}"
            reviewer_assignment = f"reviewer-second-{suffix}"
            integration_workspace = f"workspace-integrate-second-{suffix}"
            reviewer_workspace = f"workspace-review-second-{suffix}"
            integration_report = {
                "kind": "work_artifact_report", "artifact_id": artifact_id,
                "manifest_hash": "a" * 64,
            }
            integration_report["report_hash"] = _hash(_json(integration_report))
            uow.assignments.create({
                "assignment_id": integrator_assignment,
                "job_id": jobs["integrate"]["job_id"], "attempt_no": 2,
                "submission_key": "submit-integrator-second",
                "workspace_id": integration_workspace,
                "session_id": "session-integrator-second",
                "run_id": "run-integrator-second",
                "state": "succeeded", "report_json": integration_report,
                "started_at": "2026-11-07T00:02:00+00:00",
                "ended_at": "2026-11-07T00:03:00+00:00",
            })
            uow.db.execute(
                "INSERT INTO artifacts(artifact_id,assignment_id,workspace_id,repo,base_commit,"
                "commit_id,tree_hash,manifest_json,manifest_hash,receipt_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    artifact_id, integrator_assignment, integration_workspace,
                    mission["repo"], mission["base_commit"], "c" * 40,
                    "c" * 64, "{}", "a" * 64, "{}",
                    "2026-11-07T00:03:00+00:00",
                ),
            )
            review_id = f"review-second-{suffix}"
            verdict = {"verdict": "failed"}
            report = {
                "kind": "review_verdict_report", "review_id": review_id,
                "candidate_artifact_id": artifact_id,
                "acceptance_hash": plan["acceptance_hash"],
                "state": "failed", "verdict": verdict,
                "evidence_set_hash": "4" * 64,
            }
            report["report_hash"] = _hash(_json(report))
            uow.assignments.create({
                "assignment_id": reviewer_assignment,
                "job_id": jobs["review"]["job_id"], "attempt_no": 2,
                "submission_key": "submit-reviewer-second",
                "workspace_id": reviewer_workspace,
                "session_id": "session-reviewer-second",
                "run_id": "run-reviewer-second",
                "state": "succeeded", "report_json": report,
                "started_at": "2026-11-07T00:03:00+00:00",
                "ended_at": "2026-11-07T00:04:00+00:00",
            })
            uow.db.execute(
                "INSERT INTO deliveries(delivery_id,artifact_id,target_assignment_id,"
                "target_workspace_id,idempotency_key,state,import_plan_json,receipt_json,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    f"delivery-second-{suffix}", artifact_id, reviewer_assignment,
                    reviewer_workspace, f"import-second-{suffix}", "applied", "{}",
                    "{}", "2026-11-07T00:04:00+00:00",
                    "2026-11-07T00:04:00+00:00",
                ),
            )
            uow.db.execute(
                "INSERT INTO reviews(review_id,mission_id,reviewer_assignment_id,"
                "candidate_artifact_id,plan_revision,acceptance_hash,verdict_json,"
                "evidence_set_hash,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    review_id, mission_id, reviewer_assignment, artifact_id,
                    mission["plan_revision"], plan["acceptance_hash"],
                    _json(verdict), "4" * 64, "failed",
                    "2026-11-07T00:04:00+00:00",
                ),
            )
            self._complete_fixture_jobs(uow, mission_id)

        scheduler = self._scheduler()
        scheduler._advance_mission_state(mission_id)
        scheduler._advance_mission_state(mission_id)
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            current = uow.missions.get(mission_id)
            self.assertEqual(current["state"], "blocked")
            self.assertEqual(current["outcome"], "review-repair-budget-exhausted")
            self.assertEqual(current["round_no"], 1)
            self.assertEqual(
                [row[0] for row in uow.db.execute(
                    "SELECT state FROM reviews WHERE mission_id=? ORDER BY created_at",
                    (mission_id,),
                )], ["failed", "failed"],
            )
            self.assertEqual(
                {row["role"]: row["count"] for row in uow.db.execute(
                    "SELECT jobs.role,COUNT(*) AS count FROM assignments "
                    "JOIN jobs ON jobs.job_id=assignments.job_id "
                    "WHERE jobs.mission_id=? AND jobs.role IN ('integrate','review') "
                    "GROUP BY jobs.role",
                    (mission_id,),
                )},
                {"integrate": 2, "review": 2},
            )


    def test_unknown_integrator_job_cannot_be_resumed_as_confirmed_mission(self):
        """未知 Integrator Job 未经回查，不得因没有活动 Assignment 恢复阶段。"""
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._fixture(uow, "unknown-integrator")
            jobs = uow.db.execute(
                "SELECT job_id,role,aggregate_revision FROM jobs WHERE mission_id=?",
                (mission["mission_id"],),
            ).fetchall()
            for job in jobs:
                if job["role"] == "work":
                    uow.jobs._transition(
                        job["job_id"], expected_revision=job["aggregate_revision"],
                        changes={"state": "done", "result_hash": "d" * 64},
                        event_type="job.fixture.completed",
                    )
                elif job["role"] == "integrate":
                    uow.jobs._transition(
                        job["job_id"], expected_revision=job["aggregate_revision"],
                        changes={"state": "unknown"}, event_type="job.fixture.uncertain",
                    )
            uow.missions._transition(
                mission["mission_id"],
                expected_revision=mission["aggregate_revision"],
                changes={"state": "unknown"}, event_type="mission.fixture.uncertain",
            )
        scheduler = self._scheduler()
        scheduler._advance_mission_state(mission["mission_id"])
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            actual = uow.missions.get(mission["mission_id"])
            self.assertEqual(actual["state"], "unknown")
            self.assertEqual(
                uow.db.execute(
                    "SELECT state FROM jobs WHERE mission_id=? AND role='integrate'",
                    (mission["mission_id"],),
                ).fetchone()[0], "unknown",
            )

    def test_review_settlement_rechecks_authorization_after_opening_transaction(self):
        """在第一次授权观察之后撤权，不能结算修正阶段与生成新 Assignment。"""
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._fixture(uow, "revoked-in-review")
            self._complete_fixture_jobs(uow, mission["mission_id"])
        scheduler = self._scheduler()
        checks = iter(((True, True), (False, False)))
        scheduler._mission_authorization = lambda current: next(checks)
        scheduler._advance_mission_state(mission["mission_id"])
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            actual = uow.missions.get(mission["mission_id"])
            self.assertEqual(actual["state"], "reviewing")
            self.assertEqual(actual["round_no"], 0)
            self.assertEqual(
                [row[0] for row in uow.db.execute(
                    "SELECT state FROM jobs WHERE mission_id=? "
                    "AND role IN ('integrate','review') ORDER BY role",
                    (mission["mission_id"],),
                )], ["done", "done"],
            )


    def test_persisted_pause_and_resume_preserve_review_without_replaying_original_runs(self):
        """持久控制在初次授权后暂停，恢复只推进原冻结结果，不再执行旧 Run。"""
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._fixture(uow, "persisted-pause")
            self._complete_fixture_jobs(uow, mission["mission_id"])
        control = CoordinatorControl(self.store)
        scheduler = self._scheduler()
        observed = {"initial": False}

        def authorization(current):
            if not observed["initial"]:
                observed["initial"] = True
                paused = control.command(
                    current["mission_id"], "pause",
                    request_id="phase44-persisted-pause",
                    actor={"kind": "human"},
                )
                self.assertEqual(paused["state"], "applied")
                return True, True
            return True, control.allows(current["mission_id"])

        scheduler._mission_authorization = authorization
        scheduler._advance_mission_state(mission["mission_id"])
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            current = uow.missions.get(mission["mission_id"])
            self.assertEqual((current["state"], current["round_no"]), ("reviewing", 0))
        self.assertFalse(control.allows(mission["mission_id"]))
        resumed = control.command(
            mission["mission_id"], "resume",
            request_id="phase44-persisted-resume", actor={"kind": "human"},
        )
        self.assertEqual(resumed["state"], "applied")
        scheduler._advance_mission_state(mission["mission_id"])
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            current = uow.missions.get(mission["mission_id"])
            self.assertEqual((current["state"], current["round_no"]), ("integrating", 1))
            roles = [
                row["role"] for row in uow.db.execute(
                    "SELECT jobs.role FROM assignments JOIN jobs ON jobs.job_id=assignments.job_id "
                    "WHERE jobs.mission_id=? ORDER BY jobs.role",
                    (mission["mission_id"],),
                )
            ]
            self.assertEqual(roles, ["integrate", "review"])
            self.assertEqual({
                row["role"]: row["state"] for row in uow.db.execute(
                    "SELECT role,state FROM jobs WHERE mission_id=? AND role IN ('integrate','review')",
                    (mission["mission_id"],),
                )
            }, {"integrate": "checkpointed", "review": "checkpointed"})


    @staticmethod
    def _watchdog_active_assignment(uow, mission_id, suffix):
        """在实际账本中创建与 Watchdog 活动观察对应的执行中 Assignment。"""
        job = uow.db.execute(
            "SELECT job_id FROM jobs WHERE mission_id=? AND node_id='work'", (mission_id,),
        ).fetchone()
        identity = f"active-{suffix}"
        uow.assignments.create({
            "assignment_id": identity, "job_id": job["job_id"], "attempt_no": 1,
            "submission_key": f"submission-{suffix}", "state": "running",
            "run_id": f"run-{suffix}", "workspace_id": f"workspace-{suffix}",
            "session_id": f"session-{suffix}", "started_at": "2026-11-07T00:00:00Z",
        })
        return identity

    @staticmethod
    def _watchdog_activity_item(assignment_id, suffix, sequence):
        return {
            "assignment_id": assignment_id, "run_id": f"run-{suffix}",
            "session_id": f"session-{suffix}", "inspection": "confirmed",
            "status": "running", "settled": False,
            "last_activity": {
                "seq": sequence, "time": f"2026-11-07T00:00:0{sequence}Z",
                "type": "assistant_message",
            },
        }

    def test_watchdog_inspection_failure_does_not_clear_block_after_restart(self):
        """原生 Run 检查失败或结果缺失不能解除持久 blocked。"""
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._fixture(uow, "watchdog-observe")
            active = self._watchdog_active_assignment(uow, mission["mission_id"], "watchdog-observe")
        identity = mission["mission_id"]
        limits = {"stale_after": 1.0, "max_stale_ticks": 2, "action": "block"}
        watchdog = CoordinatorWatchdog(self.store, limits=limits)
        original = {"assignments": [self._watchdog_activity_item(active, "watchdog-observe", 1)]}
        watchdog.observe(identity, activity=original, now="2026-11-07T00:00:00Z")
        blocked = watchdog.observe(identity, activity=original, now="2026-11-07T00:00:02Z")
        self.assertEqual(blocked["state"], "blocked")
        restarted = CoordinatorWatchdog(self.store, limits=limits)
        for inspection, second in (("unknown", "03"), ("invalid", "04"), ("not_found", "05")):
            with self.subTest(inspection=inspection):
                failed = {"assignments": [{"assignment_id": active,
                           "inspection": inspection, "error_type": "TimeoutError"}]}
                state = restarted.observe(identity, activity=failed,
                                          now=f"2026-11-07T00:00:{second}Z")
                self.assertEqual(state["state"], "blocked")
                self.assertEqual(state["last_progress_at"], blocked["last_progress_at"])
        self.assertEqual(restarted.observe(identity, activity=original,
                         now="2026-11-07T00:00:06Z")["state"], "blocked")
        advanced = {"assignments": [self._watchdog_activity_item(active, "watchdog-observe", 2)]}
        self.assertEqual(restarted.observe(identity, activity=advanced,
                         now="2026-11-07T00:00:07Z")["state"], "healthy")

    def test_repeated_unconfirmed_ticks_do_not_refresh_stagnation(self):
        """检查异常与原状态交替出现，不能伪造活性、回拨时间或解除阻断。"""
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._fixture(uow, "watchdog-flapping")
            active = self._watchdog_active_assignment(uow, mission["mission_id"], "watchdog-flapping")
        mission_id = mission["mission_id"]
        limits = {"stale_after": 1.0, "max_stale_ticks": 2, "action": "block"}
        watchdog = CoordinatorWatchdog(self.store, limits=limits)
        confirmed = {"assignments": [self._watchdog_activity_item(active, "watchdog-flapping", 1)]}
        invalid = {"assignments": [{"assignment_id": active, "inspection": "unknown"}]}
        baseline = watchdog.observe(mission_id, activity=confirmed, now="2026-11-07T00:00:00Z")
        for stamp, activity in (("00.500", invalid), ("00.800", confirmed),
                                ("01.100", {"assignments": {"inspection": "unknown"}}), ("01.200", confirmed),
                                ("01.400", {"assignments": [{"inspection": "confirmed"}]}), ("01.500", confirmed)):
            current = watchdog.observe(mission_id, activity=activity,
                                       now=f"2026-11-07T00:00:{stamp}Z")
            self.assertEqual(current["last_progress_at"], baseline["last_progress_at"])
        self.assertEqual(current["state"], "stalled")
        restarted = CoordinatorWatchdog(self.store, limits=limits)
        older = restarted.observe(mission_id, activity=invalid, now="2026-11-07T00:00:01.300Z")
        self.assertEqual(older["observed_at"], current["observed_at"])
        self.assertEqual(older["last_progress_at"], baseline["last_progress_at"])
        blocked = restarted.observe(mission_id, activity=confirmed, now="2026-11-07T00:00:02Z")
        self.assertEqual(blocked["state"], "blocked")
        advanced = {"assignments": [self._watchdog_activity_item(active, "watchdog-flapping", 2)]}
        self.assertEqual(restarted.observe(mission_id, activity=advanced,
                         now="2026-11-07T00:00:02.100Z")["state"], "healthy")

    def test_watchdog_incomplete_active_assignment_snapshot_never_unblocks(self):
        """活动快照漏采仍运行的 Assignment 时，不能误报进度恢复。"""
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._fixture(uow, "watchdog-active-coverage")
            active = self._watchdog_active_assignment(uow, mission["mission_id"], "watchdog-coverage")
        mission_id = mission["mission_id"]
        watchdog = CoordinatorWatchdog(self.store, limits={
            "stale_after": 1.0, "max_stale_ticks": 2, "action": "block",
        })
        confirmed = {"assignments": [self._watchdog_activity_item(active, "watchdog-coverage", 1)]}
        watchdog.observe(mission_id, activity=confirmed, now="2026-11-07T00:00:00Z")
        blocked = watchdog.observe(mission_id, activity=confirmed, now="2026-11-07T00:00:02Z")
        self.assertEqual(blocked["state"], "blocked")
        for i, snapshot in enumerate((
            {"assignments": []},
            {"assignments": [{"assignment_id": "other-assignment", "inspection": "confirmed"}]},
            {"assignments": confirmed["assignments"] * 2},
            {"run_status": "running"},
        ), start=3):
            with self.subTest(snapshot=snapshot):
                current = watchdog.observe(mission_id, activity=snapshot,
                                           now=f"2026-11-07T00:00:0{i}Z")
                self.assertEqual(current["state"], "blocked")
                self.assertEqual(current["last_progress_at"], blocked["last_progress_at"])
        real = {"assignments": [self._watchdog_activity_item(active, "watchdog-coverage", 2)]}
        self.assertEqual(watchdog.observe(mission_id, activity=real,
                         now="2026-11-07T00:00:07Z")["state"], "healthy")
        self.assertEqual(watchdog.observe(mission_id, activity=real,
                         now="2026-11-07T00:00:09Z")["state"], "blocked")
        # 账本确实完成时，先收到过期 Run 快照仍需阻断，补采新快照后才恢复。
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            current = uow.assignments.get(active)
            uow.assignments._transition(
                active, expected_revision=current["aggregate_revision"],
                changes={"state": "succeeded", "ended_at": "2026-11-07T00:00:09Z"},
                event_type="assignment.fixture.succeeded",
            )
        self.assertEqual(watchdog.observe(mission_id, activity=real,
                         now="2026-11-07T00:00:10Z")["state"], "blocked")
        self.assertEqual(watchdog.observe(mission_id, activity={"assignments": []},
                         now="2026-11-07T00:00:11Z")["state"], "healthy")

    def test_watchdog_first_trusted_snapshot_after_untrusted_baseline_does_not_unblock(self):
        """首次观察不可信时，首个可信快照只建立基线，不伪造进度。"""
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._fixture(uow, "watchdog-untrusted-baseline")
            active = self._watchdog_active_assignment(
                uow, mission["mission_id"], "watchdog-untrusted-baseline",
            )
        identity = mission["mission_id"]
        limits = {"stale_after": 1.0, "max_stale_ticks": 2, "action": "block"}
        watchdog = CoordinatorWatchdog(self.store, limits=limits)
        invalid = {"assignments": [{"assignment_id": active, "inspection": "unknown"}]}
        first = watchdog.observe(identity, activity=invalid, now="2026-11-07T00:00:00Z")
        self.assertFalse(first["activity_baseline_trusted"])
        blocked = watchdog.observe(identity, activity=invalid, now="2026-11-07T00:00:02Z")
        self.assertEqual(blocked["state"], "blocked")
        self.assertFalse(blocked["activity_baseline_trusted"])

        restarted = CoordinatorWatchdog(self.store, limits=limits)
        confirmed = {"assignments": [
            self._watchdog_activity_item(active, "watchdog-untrusted-baseline", 1),
        ]}
        baseline = restarted.observe(
            identity, activity=confirmed, now="2026-11-07T00:00:03Z",
        )
        self.assertTrue(baseline["activity_baseline_trusted"])
        self.assertEqual(baseline["state"], "blocked")
        self.assertEqual(baseline["last_progress_at"], first["last_progress_at"])
        self.assertEqual(restarted.observe(
            identity, activity=confirmed, now="2026-11-07T00:00:04Z",
        )["state"], "blocked")
        advanced = {"assignments": [
            self._watchdog_activity_item(active, "watchdog-untrusted-baseline", 2),
        ]}
        self.assertEqual(restarted.observe(
            identity, activity=advanced, now="2026-11-07T00:00:05Z",
        )["state"], "healthy")

    def test_watchdog_recovers_verified_ledger_progress_before_activity_baseline(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._fixture(uow, "watchdog-ledger-recovery")
            active = self._watchdog_active_assignment(uow, mission["mission_id"], "watchdog-ledger-recovery")
        mid = mission["mission_id"]
        limits = {"stale_after": 1.0, "max_stale_ticks": 2, "action": "block"}
        watchdog = CoordinatorWatchdog(self.store, limits=limits)
        invalid = {"assignments": [{"assignment_id": active, "inspection": "unknown"}]}
        watchdog.observe(mid, activity=invalid, now="2026-11-07T00:00:00Z")
        self.assertEqual(watchdog.observe(mid, activity=invalid,
            now="2026-11-07T00:00:02Z")["state"], "blocked")
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            current = uow.assignments.get(active)
            uow.assignments._transition(
                active, expected_revision=current["aggregate_revision"],
                changes={"state": "succeeded", "ended_at": "2026-11-07T00:00:02Z"},
                event_type="assignment.fixture.succeeded",
            )
        recovered = CoordinatorWatchdog(self.store, limits=limits).observe(
            mid, activity={"assignments": []}, now="2026-11-07T00:00:03Z",
        )
        self.assertEqual(recovered["state"], "healthy")
        self.assertTrue(recovered["activity_baseline_trusted"])
        self.assertEqual(recovered["last_progress_at"], "2026-11-07T00:00:03.000Z")

    def test_watchdog_tracks_interleaved_run_cursors_across_restart(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._fixture(uow, "watchdog-multi-run", multi_work=True)
            first = self._watchdog_active_assignment(uow, mission["mission_id"], "watchdog-multi-a")
            second_job = uow.db.execute(
                "SELECT job_id FROM jobs WHERE mission_id=? AND node_id='work-secondary'",
                (mission["mission_id"],),
            ).fetchone()["job_id"]
            second = "active-watchdog-multi-b"
            uow.assignments.create({
                "assignment_id": second, "job_id": second_job, "attempt_no": 1,
                "submission_key": "submission-watchdog-multi-b", "state": "running",
                "run_id": "run-watchdog-multi-b", "workspace_id": "workspace-watchdog-multi-b",
                "session_id": "session-watchdog-multi-b", "started_at": "2026-11-07T00:00:00Z",
            })
        mid = mission["mission_id"]
        limits = {"stale_after": 1.0, "max_stale_ticks": 2, "action": "block"}
        watchdog = CoordinatorWatchdog(self.store, limits=limits)
        snap = lambda a, b: {"assignments": [
            self._watchdog_activity_item(first, "watchdog-multi-a", a),
            self._watchdog_activity_item(second, "watchdog-multi-b", b),
        ]}
        watchdog.observe(mid, activity=snap(2, 5), now="2026-11-07T00:00:00Z")
        # 两个 Run 必须整体通过原生身份与单调游标核验；B 倒退时不得认可 A 的新进度。
        self.assertEqual(watchdog.observe(mid, activity=snap(3, 4),
            now="2026-11-07T00:00:02Z")["state"], "blocked")
        with self.store.transaction() as db:
            cursors = {
                row["assignment_id"]: row["last_event_seq"]
                for row in db.execute(
                    "SELECT assignment_id,last_event_seq FROM watchdog_assignment_cursors WHERE mission_id=?",
                    (mid,),
                )
            }
        self.assertEqual(cursors, {first: 2, second: 5})
        # B 虽保留原序号但篡改原始事件类型，不能借 A 的有效序号重置阻断。
        altered = snap(3, 5)
        altered["assignments"][1]["last_activity"]["type"] = "inconsistent-event"
        self.assertEqual(watchdog.observe(
            mid, activity=altered, now="2026-11-07T00:00:02.500Z",
        )["state"], "blocked")
        restarted = CoordinatorWatchdog(self.store, limits=limits)
        # 漏采 B 时同样不得提前消耗 A 的原生进度，重建后仍必须 blocked。
        self.assertEqual(restarted.observe(
            mid, activity={"assignments": [
                self._watchdog_activity_item(first, "watchdog-multi-a", 4),
            ]}, now="2026-11-07T00:00:03Z",
        )["state"], "blocked")
        with self.store.transaction() as db:
            cursors = {
                row["assignment_id"]: row["last_event_seq"]
                for row in db.execute(
                    "SELECT assignment_id,last_event_seq FROM watchdog_assignment_cursors WHERE mission_id=?",
                    (mid,),
                )
            }
        self.assertEqual(cursors, {first: 2, second: 5})
        self.assertEqual(restarted.observe(mid, activity=snap(3, 5),
            now="2026-11-07T00:00:04Z")["state"], "healthy")
        self.assertEqual(restarted.observe(mid, activity=snap(3, 5),
            now="2026-11-07T00:00:06Z")["state"], "blocked")
        self.assertEqual(restarted.observe(mid, activity=snap(3, 6),
            now="2026-11-07T00:00:07Z")["state"], "healthy")

    def test_blocking_watchdog_prevents_review_settlement_until_true_progress(self):
        """Watchdog 已确认 blocked 时不推进质量门，新真实进度恢复后才解锁。"""
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._fixture(uow, "watchdog-blocked")
            self._complete_fixture_jobs(uow, mission["mission_id"])
        watchdog = CoordinatorWatchdog(
            self.store,
            limits={"stale_after": 1.0, "max_stale_ticks": 2, "action": "block"},
        )
        first = watchdog.observe(
            mission["mission_id"], activity={"run_status": "running", "event_seq": 1},
            now="2026-11-07T00:00:00Z",
        )
        self.assertEqual(first["state"], "healthy")
        blocked = watchdog.observe(
            mission["mission_id"], activity={"run_status": "running", "event_seq": 1},
            now="2026-11-07T00:00:02Z",
        )
        self.assertEqual(blocked["state"], "blocked")

        scheduler = self._scheduler()
        del scheduler._mission_authorization
        scheduler._watchdog = watchdog
        scheduler._execution._authorization = SimpleNamespace(
            ticket=lambda ticket_id: {"mandate": {"mandate_id": "mandate-watchdog"}},
            revalidate_mandate=lambda mandate_id, *, actor: {"status": "valid"},
        )
        scheduler._execution._control = CoordinatorControl(self.store)
        scheduler._advance_mission_state(mission["mission_id"])
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            current = uow.missions.get(mission["mission_id"])
            self.assertEqual((current["state"], current["round_no"]), ("reviewing", 0))

        progressed = watchdog.observe(
            mission["mission_id"], activity={"run_status": "finished", "event_seq": 2},
            now="2026-11-07T00:00:03Z",
        )
        self.assertEqual(progressed["state"], "healthy")
        scheduler._advance_mission_state(mission["mission_id"])
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            current = uow.missions.get(mission["mission_id"])
            self.assertEqual((current["state"], current["round_no"]), ("integrating", 1))


if __name__ == "__main__":
    unittest.main()

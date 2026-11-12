"""Phase 4.4 Reviewer 的候选证据必须绑定当前 Integrator 尝试。"""
from __future__ import annotations

import json
import sqlite3
import unittest
from types import SimpleNamespace

from harness.app.superagent_coordinator_service import CoordinatorApplicationService
from harness.app.superagent_coordinator_store import _hash, _json
from harness.app.superagent_dag_scheduler import CoordinatorDagScheduler


class _ExecutionFixture:
    """使用隔离 SQLite 运行真实 Scheduler 查询，不调用外部模型。"""

    def __init__(self, db):
        self.db = db

    def _read_one(self, query, parameters):
        row = self.db.execute(query, parameters).fetchone()
        return dict(row) if row is not None else None

    def _read_all(self, query, parameters):
        return [dict(row) for row in self.db.execute(query, parameters).fetchall()]


class Phase44ReviewerEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE jobs(
                job_id TEXT, mission_id TEXT, plan_revision INTEGER,
                role TEXT, node_id TEXT
            );
            CREATE TABLE assignments(
                assignment_id TEXT, job_id TEXT, attempt_no INTEGER, state TEXT,
                workspace_id TEXT, session_id TEXT
            );
            CREATE TABLE artifacts(
                artifact_id TEXT, assignment_id TEXT, manifest_hash TEXT
            );
            CREATE TABLE evidence(
                evidence_id TEXT, assignment_id TEXT, evidence_type TEXT,
                reference_hash TEXT, payload_hash TEXT
            );
        """)
        self.db.executemany(
            "INSERT INTO jobs VALUES(?,?,?,?,?)", (
                ("job-work", "mission", 1, "work", "work"),
                ("job-integrate", "mission", 1, "integrate", "__system__integrate"),
                ("job-review", "mission", 1, "review", "__system__review"),
            ),
        )
        self.db.executemany(
            "INSERT INTO assignments VALUES(?,?,?,?,?,?)", (
                ("assignment-work", "job-work", 1, "succeeded", "workspace-work", "session-work"),
                ("assignment-old", "job-integrate", 1, "succeeded", "workspace-old", "session-old"),
                ("assignment-new", "job-integrate", 2, "succeeded", "workspace-new", "session-new"),
                ("assignment-review", "job-review", 2, "succeeded", "workspace-review", "session-review"),
            ),
        )
        self.db.executemany(
            "INSERT INTO artifacts VALUES(?,?,?)", (
                ("candidate-old", "assignment-old", "a" * 64),
                ("candidate-new", "assignment-new", "b" * 64),
            ),
        )
        self.db.executemany(
            "INSERT INTO evidence VALUES(?,?,?,?,?)", (
                ("evidence-work", "assignment-work", "run", "1" * 64, "2" * 64),
                ("evidence-old", "assignment-old", "run", "3" * 64, "4" * 64),
                ("evidence-new", "assignment-new", "run", "5" * 64, "6" * 64),
                ("evidence-reviewer-run", "assignment-review", "run", "7" * 64, "8" * 64),
            ),
        )
        self.mission = {"mission_id": "mission", "plan_revision": 1}
        self.job = {"role": "review"}
        self.assignment = {"assignment_id": "assignment-review"}
        self.plan = {
            "acceptance_hash": "c" * 64,
            "acceptance": {"criteria": [{"id": "criterion", "kind": "binary"}]},
        }
        self.scheduler = CoordinatorDagScheduler(_ExecutionFixture(self.db), policy=object())
        self.scheduler._load_plan = lambda mission: (self.plan, None, None, None)

    def test_reviewer_prompt_lists_only_current_integrator_and_work_evidence(self):
        """失败候选的旧 Integrator 证据不得出现于新轮次提示。"""
        prompt = self.scheduler._role_prompt(
            "instruction", self.mission, self.job, attempt_no=2,
        )
        contract = json.loads(prompt.rsplit("\n", 1)[1])
        self.assertEqual(contract["candidate_artifact_id"], "candidate-new")
        seen = {item["evidence_id"] for item in contract["available_evidence"]}
        self.assertIn("evidence-work", seen)
        self.assertIn("evidence-new", seen)
        self.assertNotIn("evidence-old", seen)
        self.assertNotIn("evidence-reviewer-run", seen)

    def test_reviewer_cannot_use_prior_integrator_evidence_to_pass_new_candidate(self):
        """即使模型直接引用旧轮次 ID，验收冻结环节也应判 invalid。"""
        def evaluate(cited_id):
            output = {
                "schema_version": 1, "candidate_artifact_id": "candidate-new",
                "acceptance_hash": self.plan["acceptance_hash"],
                "verdict": "passed", "criteria": [{
                    "id": "criterion", "status": "passed",
                    "reason": "Candidate checked", "evidence_ids": [cited_id],
                }],
            }
            return self.scheduler._prepare_review(
                self.mission, self.job, self.assignment,
                {"output_text": json.dumps(output), "output_hash": "9" * 64},
                ["evidence-reviewer-run"],
            )

        stale = evaluate("evidence-old")
        self.assertEqual(stale["state"], "invalid")
        valid = evaluate("evidence-new")
        self.assertEqual(valid["state"], "passed")
        self.assertNotEqual(stale["evidence_set_hash"], valid["evidence_set_hash"])


    def test_review_recovery_selects_run_evidence_not_first_tool_evidence(self):
        """回查审查时必须采用 Run 类型证据，不将同轮较早的工具 ID 当作运行凭据。"""
        self.db.execute(
            "INSERT INTO evidence VALUES(?,?,?,?,?)",
            ("evidence-aaa-tool", "assignment-review", "tool", "9" * 64, "a" * 64),
        )
        output = {
            "schema_version": 1, "candidate_artifact_id": "candidate-new",
            "acceptance_hash": self.plan["acceptance_hash"],
            "verdict": "passed", "criteria": [{
                "id": "criterion", "status": "passed", "reason": "Checked",
                "evidence_ids": ["evidence-new"],
            }],
        }
        result = self.scheduler._prepare_review(
            self.mission, self.job, self.assignment,
            {"output_text": json.dumps(output), "output_hash": "b" * 64},
            ["evidence-aaa-tool", "evidence-reviewer-run"],
        )
        self.assertEqual(result["state"], "passed")
        self.assertEqual(result["reviewer_run_evidence_ref"], "evidence-reviewer-run")

    def test_completion_rejects_evidence_from_prior_integrator_attempt(self):
        """冻结记录即使哈希一致，旧候选证据也不能证明当前候选通过。"""
        accepted_plan = {
            "acceptance_hash": self.plan["acceptance_hash"],
            "acceptance_json": {"criteria": [{"id": "criterion"}]},
        }
        uow = SimpleNamespace(
            db=self.db,
            plans=SimpleNamespace(get=lambda mission_id, revision: accepted_plan),
        )
        service = CoordinatorApplicationService(uow)
        service._latest_integration_candidate = lambda mission_id, revision: {
            "artifact_id": "candidate-new", "assignment_id": "assignment-new",
        }

        def verify(cited_id):
            evidence_ids = [cited_id, "evidence-reviewer-run"]
            rows = self.db.execute(
                "SELECT evidence_id,assignment_id,evidence_type,reference_hash,payload_hash "
                "FROM evidence WHERE evidence_id IN (?,?)",
                evidence_ids,
            ).fetchall()
            records = sorted(
                [dict(row) for row in rows], key=lambda row: row["evidence_id"],
            )
            evidence_set_hash = _hash(_json(records))
            verdict = {
                "schema_version": 1, "candidate_artifact_id": "candidate-new",
                "acceptance_hash": self.plan["acceptance_hash"],
                "verdict": "passed",
                "criteria": [{
                    "id": "criterion", "status": "passed",
                    "reason": "Evidence reviewed", "evidence_ids": [cited_id],
                }],
            }
            report = {
                "kind": "review_verdict_report", "review_id": "review-frozen",
                "candidate_artifact_id": "candidate-new",
                "acceptance_hash": self.plan["acceptance_hash"],
                "state": "passed", "verdict": verdict,
                "evidence_set_hash": evidence_set_hash,
                "reviewer_evidence_ids": ["evidence-reviewer-run"],
            }
            report["report_hash"] = _hash(_json(report))
            review = {
                "review_id": "review-frozen", "state": "passed",
                "job_state": "done", "assignment_state": "succeeded",
                "candidate_artifact_id": "candidate-new",
                "acceptance_hash": self.plan["acceptance_hash"],
                "reviewer_assignment_id": "assignment-review",
                "report_json": _json(report), "verdict_json": _json(verdict),
                "evidence_set_hash": evidence_set_hash,
            }
            service._latest_review = lambda mission_id, revision: review
            return service._review_passed("mission", self.mission)

        self.assertFalse(verify("evidence-old"))
        self.assertTrue(verify("evidence-new"))


if __name__ == "__main__":
    unittest.main()

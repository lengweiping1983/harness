"""下一代 Superagent 的宿主应用服务与状态转移守卫。"""
from __future__ import annotations

import json
import re
from typing import Any
from ..i18n import tr

from .superagent_coordinator_store import CoordinatorUnitOfWork, _hash, _json
from .superagent_checkpoints import frozen_work_checkpoint


_REPOSITORIES = {
    "ticket": "tickets",
    "mission": "missions",
    "job": "jobs",
    "assignment": "assignments",
    "worker": "workers",
    "attention": "attentions",
}

_TRANSITIONS = {
    "ticket": {"open": {"ended"}, "ended": {"open"}},
    "mission": {
        "planning": {"running", "rejected", "blocked", "suppressed", "unknown", "cancelling"},
        "running": {"completed", "integrating", "blocked", "suppressed", "unknown", "cancelling"},
        "integrating": {"reviewing", "blocked", "unknown", "cancelling"},
        "reviewing": {"completed", "integrating", "blocked", "unknown", "cancelling"},
        "cancelling": {"cancelled", "unknown"},
        "unknown": {
            "planning", "running", "integrating", "reviewing", "completed",
            "blocked", "rejected", "suppressed", "cancelling", "cancelled",
        },
    },
    "job": {
        "pending": {"ready", "blocked", "unknown", "cancelled"},
        "ready": {"running", "checkpointed", "done", "blocked", "unknown", "cancelled"},
        "running": {"checkpointed", "done", "blocked", "unknown"},
        "checkpointed": {"running", "blocked", "unknown", "cancelled"},
        "done": {"checkpointed"},
        "unknown": {"running", "done", "blocked", "cancelled"},
    },
    "worker": {
        "planned": {"workspace_creating", "stopped"},
        "workspace_creating": {"workspace_ready", "unknown"},
        "workspace_ready": {"session_creating", "unknown", "stopped"},
        "session_creating": {"ready", "unknown"},
        "ready": {"unknown", "stopped"},
        "unknown": {"workspace_ready", "ready", "stopped"},
    },
    "attention": {"open": {"answered", "dismissed"}, "answered": set(), "dismissed": set()},
    "assignment": {
        "pending": {"submitting", "cancelled"},
        "submitting": {"accepted", "rejected", "unknown"},
        "accepted": {"running", "capturing", "stopping", "failed", "interrupted", "unknown"},
        "running": {"capturing", "stopping", "failed", "interrupted", "unknown"},
        "capturing": {"stopping", "succeeded", "failed", "unknown"},
        "stopping": {"cancelled", "succeeded", "failed", "interrupted", "unknown"},
        "unknown": {
            "accepted", "running", "capturing", "rejected", "succeeded",
            "failed", "interrupted", "cancelled",
        },
    },
}

_REQUIRED_FACTS = {
    ("ticket", "ended", "open"): ("human_followup", "mandate_valid"),
    ("mission", "planning", "running"): ("plan_valid", "base_commit_frozen", "mandate_valid", "control_allows"),
    ("mission", "planning", "rejected"): ("explicit_plan_rejection",),
    ("mission", "running", "completed"): ("single_node_report_frozen", "all_work_jobs_done", "mandate_valid", "control_allows"),
    ("mission", "running", "integrating"): ("all_work_jobs_done", "required_deliveries_applied", "mandate_valid", "control_allows"),
    ("mission", "integrating", "reviewing"): (
        "integration_artifact_frozen", "all_work_jobs_done", "required_deliveries_applied",
        "mandate_valid", "control_allows",
    ),
    ("mission", "reviewing", "completed"): (
        "acceptance_verified", "integration_artifact_frozen",
        "all_work_jobs_done", "required_deliveries_applied",
        "mandate_valid", "control_allows",
    ),
    ("mission", "reviewing", "integrating"): ("repair_authorized", "repair_budget_available", "mandate_valid", "control_allows"),
    ("mission", "cancelling", "cancelled"): ("owned_operations_settled",),
    ("mission", "unknown", "cancelling"): ("cancel_accepted",),
    ("mission", "unknown", "*"): ("reconciliation_verified",),
    ("mission", "unknown", "planning"): (
        "reconciliation_verified", "original_operation_not_started", "mandate_valid", "control_allows", "next_step_safe",
    ),
    ("mission", "unknown", "running"): (
        "reconciliation_verified", "original_run_settled", "mandate_valid", "control_allows", "next_step_safe",
    ),
    ("mission", "unknown", "integrating"): ("reconciliation_verified", "all_work_jobs_done", "required_deliveries_applied", "original_delivery_applied", "mandate_valid", "control_allows"),
    ("mission", "unknown", "reviewing"): (
        "reconciliation_verified", "original_integration_settled",
        "integration_artifact_frozen", "all_work_jobs_done", "required_deliveries_applied",
        "mandate_valid", "control_allows",
    ),
    # Role-specific completion facts are enforced after inspecting the frozen PlanRevision.
    ("mission", "unknown", "completed"): ("reconciliation_verified",),
    ("mission", "unknown", "blocked"): ("reconciliation_verified", "known_failure_or_human_decision"),
    ("mission", "unknown", "rejected"): ("reconciliation_verified", "explicit_plan_rejection"),
    ("mission", "running", "suppressed"): ("mandate_unavailable",),
    ("mission", "unknown", "suppressed"): ("reconciliation_verified", "mandate_unavailable"),
    ("mission", "unknown", "cancelled"): ("reconciliation_verified", "owned_operations_settled"),
    ("job", "pending", "ready"): ("dependencies_satisfied", "mandate_valid", "control_allows"),
    ("job", "ready", "running"): ("assignment_accepted", "control_allows"),
    ("job", "ready", "done"): ("role_result_validated", "report_frozen"),
    ("job", "running", "checkpointed"): ("checkpoint_and_capture_frozen",),
    ("job", "ready", "checkpointed"): ("checkpoint_and_capture_frozen",),
    ("job", "running", "done"): ("role_result_validated",),
    ("job", "checkpointed", "running"): ("new_assignment_created", "checkpoint_and_capture_imported", "control_allows"),
    ("job", "done", "checkpointed"): ("review_failed", "repair_budget_available", "mandate_valid", "control_allows"),
    ("job", "unknown", "*"): ("reconciliation_verified",),
    ("job", "unknown", "running"): ("reconciliation_verified", "exact_original_assignment_active", "control_allows"),
    ("job", "unknown", "done"): ("reconciliation_verified", "role_result_validated", "report_frozen"),
    ("job", "unknown", "blocked"): ("reconciliation_verified", "known_failure_or_human_decision"),
    ("job", "unknown", "cancelled"): ("reconciliation_verified",),
    ("assignment", "pending", "submitting"): ("stable_submission_persisted", "mandate_valid", "control_allows"),
    ("assignment", "submitting", "accepted"): ("harness_acceptance_confirmed",),
    ("assignment", "submitting", "unknown"): ("operation_outcome_uncertain",),
    ("assignment", "submitting", "rejected"): ("explicit_preexecution_rejection",),
    ("assignment", "accepted", "running"): ("exact_original_run_active", "control_allows"),
    ("assignment", "accepted", "capturing"): ("original_run_settled",),
    ("assignment", "accepted", "failed"): ("original_run_failure_confirmed",),
    ("assignment", "accepted", "interrupted"): ("original_run_interruption_confirmed",),
    ("assignment", "running", "capturing"): ("original_run_settled",),
    ("assignment", "pending", "cancelled"): ("no_external_side_effect_started",),
    ("assignment", "accepted", "stopping"): ("stop_requested", "control_allows"),
    ("assignment", "running", "stopping"): ("stop_requested", "control_allows"),
    ("assignment", "capturing", "stopping"): ("stop_requested", "control_allows", "original_run_settled"),
    ("assignment", "stopping", "capturing"): ("original_run_settled",),
    ("assignment", "stopping", "failed"): ("original_run_failure_confirmed",),
    ("assignment", "stopping", "interrupted"): ("original_run_interruption_confirmed",),
    ("assignment", "stopping", "unknown"): ("operation_outcome_uncertain",),
    ("assignment", "running", "failed"): ("original_run_failure_confirmed",),
    ("assignment", "running", "interrupted"): ("original_run_interruption_confirmed",),
    ("assignment", "capturing", "succeeded"): ("output_frozen_and_validated",),
    ("assignment", "capturing", "failed"): ("capture_or_validation_failure_confirmed",),
    ("assignment", "capturing", "unknown"): ("operation_outcome_uncertain",),
    ("assignment", "stopping", "cancelled"): ("owned_operations_settled",),
    ("assignment", "stopping", "succeeded"): ("original_run_completed", "output_frozen_and_validated"),
    ("assignment", "unknown", "*"): ("reconciliation_verified",),
    ("assignment", "unknown", "accepted"): ("reconciliation_verified", "harness_acceptance_confirmed"),
    ("assignment", "unknown", "running"): ("reconciliation_verified", "exact_original_run_active"),
    ("assignment", "unknown", "capturing"): ("reconciliation_verified", "original_run_settled"),
    ("assignment", "unknown", "rejected"): ("reconciliation_verified", "original_submission_not_started"),
    ("assignment", "unknown", "succeeded"): (
        "reconciliation_verified", "original_run_completed", "output_frozen_and_validated",
    ),
    ("assignment", "unknown", "failed"): ("reconciliation_verified", "original_run_failure_confirmed"),
    ("assignment", "unknown", "interrupted"): ("reconciliation_verified", "original_run_interruption_confirmed"),
    ("assignment", "unknown", "cancelled"): (
        "reconciliation_verified", "original_run_cancelled", "owned_operations_settled",
    ),
    ("worker", "planned", "workspace_creating"): ("worker_profile_valid", "mandate_valid", "control_allows"),
    ("worker", "workspace_creating", "workspace_ready"): ("workspace_identity_verified",),
    ("worker", "workspace_creating", "unknown"): ("operation_outcome_uncertain",),
    ("worker", "workspace_ready", "unknown"): ("operation_outcome_uncertain",),
    ("worker", "workspace_ready", "session_creating"): ("workspace_identity_verified", "mandate_valid", "control_allows"),
    ("worker", "session_creating", "ready"): ("session_identity_verified", "tools_restricted"),
    ("worker", "session_creating", "unknown"): ("operation_outcome_uncertain",),
    ("worker", "ready", "unknown"): ("operation_outcome_uncertain",),
    ("worker", "unknown", "workspace_ready"): ("reconciliation_verified", "workspace_identity_verified"),
    ("worker", "unknown", "ready"): ("reconciliation_verified", "session_identity_verified", "tools_restricted"),
    ("worker", "ready", "stopped"): ("no_owned_run_active",),
    ("worker", "unknown", "stopped"): ("reconciliation_verified", "no_owned_run_active"),
    ("attention", "open", "answered"): ("human_answer_trigger_verified",),
    ("attention", "open", "dismissed"): ("human_dismissal_verified",),
}


class CoordinatorApplicationService:
    """在 Repository 之上核验宿主授权事实和领域状态机。

    facts 必须来自宿主对原始回执、授权和资源状态的核验；模型报告不能作为事实来源。
    本服务不执行外部操作，也不自动重放 unknown。
    """

    def __init__(self, uow: CoordinatorUnitOfWork):
        if uow.db is None:
            raise RuntimeError("application service requires an active Unit of Work")
        self._uow = uow

    @staticmethod
    def _require_digest(value: object, name: str) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        return value

    def _mission_jobs_done(self, mission_id: str, plan_revision: int) -> bool:
        if type(plan_revision) is not int or plan_revision < 1:
            return False
        if self._uow.plans.get(mission_id, plan_revision) is None:
            return False
        jobs = self._uow.db.execute(
            "SELECT job_id,state,result_hash FROM jobs "
            "WHERE mission_id=? AND plan_revision=? AND role='work' ORDER BY job_id",
            (mission_id, plan_revision),
        ).fetchall()
        if not jobs:
            return False
        for job in jobs:
            if job["state"] != "done":
                return False
            try:
                self._require_digest(job["result_hash"], f"Job {job['job_id']} result_hash")
            except ValueError:
                return False
        active = self._uow.db.execute(
            "SELECT COUNT(*) FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "WHERE job.mission_id=? AND job.plan_revision=? AND job.role='work' "
            "AND assignment.state IN ('pending','submitting','accepted','running','capturing','stopping','unknown')",
            (mission_id, plan_revision),
        ).fetchone()[0]
        return active == 0

    def _mission_has_active_assignments(self, mission_id: str) -> bool:
        count = self._uow.db.execute(
            "SELECT COUNT(*) FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "WHERE job.mission_id=? AND assignment.state IN "
            "('pending','submitting','accepted','running','capturing','stopping','unknown')",
            (mission_id,),
        ).fetchone()[0]
        return count > 0

    def _mission_has_inflight_assignments(self, mission_id: str) -> bool:
        count = self._uow.db.execute(
            "SELECT COUNT(*) FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "WHERE job.mission_id=? AND assignment.state IN "
            "('submitting','accepted','running','capturing','stopping','unknown')",
            (mission_id,),
        ).fetchone()[0]
        return count > 0

    def _latest_integration_candidate(self, mission_id: str, plan_revision: int,
                                      *, allow_checkpointed: bool = False) -> dict | None:
        row = self._uow.db.execute(
            "SELECT job.job_id,job.state AS job_state,assignment.assignment_id,assignment.run_id,"
            "assignment.report_json,artifact.artifact_id,artifact.manifest_hash,artifact.tree_hash,"
            "artifact.workspace_id,artifact.repo,artifact.base_commit "
            "FROM jobs AS job JOIN assignments AS assignment ON assignment.job_id=job.job_id "
            "JOIN artifacts AS artifact ON artifact.assignment_id=assignment.assignment_id "
            "WHERE job.mission_id=? AND job.plan_revision=? AND job.role='integrate' "
            "AND assignment.state='succeeded' ORDER BY assignment.attempt_no DESC LIMIT 1",
            (mission_id, plan_revision),
        ).fetchone()
        # checkpointed 只为修正授权核对旧轮冻结候选，正常验收仍要求 done。
        allowed_states = {"done", "checkpointed"} if allow_checkpointed else {"done"}
        if row is None or row["job_state"] not in allowed_states:
            return None
        result = dict(row)
        try:
            report = json.loads(result["report_json"])
        except (TypeError, ValueError):
            return None
        report_hash = report.get("report_hash") if isinstance(report, dict) else None
        if (not isinstance(report, dict) or report.get("kind") != "work_artifact_report"
                or report.get("artifact_id") != result["artifact_id"]
                or report.get("manifest_hash") != result["manifest_hash"]
                or not isinstance(report_hash, str)
                or _hash(_json({key: value for key, value in report.items() if key != "report_hash"})) != report_hash):
            return None
        return result


    def _job_deliveries_applied(self, job_id: str, assignment_id: str) -> bool:
        dependencies = self._uow.db.execute(
            "SELECT parent.job_id FROM job_dependencies AS dependency "
            "JOIN jobs AS parent ON parent.job_id=dependency.depends_on_job_id "
            "WHERE dependency.job_id=? AND dependency.requires_delivery=1", (job_id,),
        ).fetchall()
        for dependency in dependencies:
            source = self._uow.db.execute(
                "SELECT artifact.artifact_id FROM assignments AS assignment "
                "JOIN artifacts AS artifact ON artifact.assignment_id=assignment.assignment_id "
                "WHERE assignment.job_id=? AND assignment.state='succeeded' "
                "ORDER BY assignment.attempt_no DESC LIMIT 1", (dependency["job_id"],),
            ).fetchone()
            if source is None or self._uow.db.execute(
                "SELECT 1 FROM deliveries WHERE target_assignment_id=? AND artifact_id=? AND state='applied'",
                (assignment_id, source["artifact_id"]),
            ).fetchone() is None:
                return False
        return True

    def _latest_review(self, mission_id: str, plan_revision: int) -> dict | None:
        row = self._uow.db.execute(
            "SELECT review.*,assignment.attempt_no,assignment.state AS assignment_state,"
            "assignment.report_json,job.job_id,job.state AS job_state "
            "FROM reviews AS review JOIN assignments AS assignment "
            "ON assignment.assignment_id=review.reviewer_assignment_id "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "WHERE review.mission_id=? AND review.plan_revision=? AND job.role='review' "
            "ORDER BY assignment.attempt_no DESC LIMIT 1", (mission_id, plan_revision),
        ).fetchone()
        return dict(row) if row is not None else None

    def _review_frozen_for_assignment(self, mission_id: str, job: dict,
                                      assignment_id: str, report: object,
                                      *, allow_checkpointed_candidate: bool = False) -> bool:
        plan = self._uow.plans.get(mission_id, job["plan_revision"])
        candidate = self._latest_integration_candidate(
            mission_id, job["plan_revision"],
            allow_checkpointed=allow_checkpointed_candidate,
        )
        review = self._uow.db.execute(
            "SELECT * FROM reviews WHERE reviewer_assignment_id=?", (assignment_id,),
        ).fetchone()
        if plan is None or candidate is None or review is None:
            return False
        try:
            row = dict(review)
            verdict = json.loads(row["verdict_json"])
            if not isinstance(report, dict):
                return False
            unsigned = {key: value for key, value in report.items() if key != "report_hash"}
            return bool(
                row["mission_id"] == mission_id and row["plan_revision"] == job["plan_revision"]
                and row["candidate_artifact_id"] == candidate["artifact_id"]
                and row["acceptance_hash"] == plan["acceptance_hash"]
                and row["state"] in {"passed", "failed", "invalid"}
                and report.get("kind") == "review_verdict_report"
                and report.get("review_id") == row["review_id"]
                and report.get("candidate_artifact_id") == row["candidate_artifact_id"]
                and report.get("acceptance_hash") == row["acceptance_hash"]
                and report.get("state") == row["state"] and report.get("verdict") == verdict
                and report.get("evidence_set_hash") == row["evidence_set_hash"]
                and _hash(_json(unsigned)) == report.get("report_hash")
            )
        except (KeyError, TypeError, ValueError):
            return False

    def _review_passed(self, mission_id: str, mission: dict) -> bool:
        plan = self._uow.plans.get(mission_id, mission["plan_revision"])
        candidate = self._latest_integration_candidate(mission_id, mission["plan_revision"])
        review = self._latest_review(mission_id, mission["plan_revision"])
        if (plan is None or candidate is None or review is None or review["state"] != "passed"
                or review["job_state"] != "done" or review["assignment_state"] != "succeeded"
                or review["candidate_artifact_id"] != candidate["artifact_id"]
                or review["acceptance_hash"] != plan["acceptance_hash"]):
            return False
        try:
            report, verdict = json.loads(review["report_json"]), json.loads(review["verdict_json"])
            criteria = verdict["criteria"]
            expected = {item["id"] for item in plan["acceptance_json"]["criteria"]}
            cited = {identity for item in criteria for identity in item["evidence_ids"]}
            reviewer_ids = set(report["reviewer_evidence_ids"])
            if (report.get("kind") != "review_verdict_report" or report.get("review_id") != review["review_id"]
                    or report.get("candidate_artifact_id") != candidate["artifact_id"]
                    or report.get("acceptance_hash") != plan["acceptance_hash"]
                    or report.get("state") != "passed" or report.get("verdict") != verdict
                    or report.get("evidence_set_hash") != review["evidence_set_hash"]
                    or {item["id"] for item in criteria} != expected
                    or any(item["status"] != "passed" or not item["evidence_ids"] for item in criteria)
                    or not reviewer_ids):
                return False
            ids = sorted(cited | reviewer_ids)
            marks = ",".join("?" for _ in ids)
            rows = self._uow.db.execute(
                "SELECT evidence.evidence_id,evidence.assignment_id,evidence.evidence_type,"
                "evidence.reference_hash,evidence.payload_hash,job.role FROM evidence "
                "JOIN assignments ON assignments.assignment_id=evidence.assignment_id "
                "JOIN jobs AS job ON job.job_id=assignments.job_id "
                f"WHERE job.mission_id=? AND job.plan_revision=? AND evidence.evidence_id IN ({marks})",
                (mission_id, mission["plan_revision"], *ids),
            ).fetchall()

            if {row["evidence_id"] for row in rows} != set(ids):
                return False
            if any(
                row["role"] not in {"work", "integrate"}
                or (row["role"] == "integrate"
                    and row["assignment_id"] != candidate["assignment_id"])
                for row in rows if row["evidence_id"] in cited
            ):
                return False
            reviewer_rows = [row for row in rows if row["evidence_id"] in reviewer_ids]
            if (any(row["assignment_id"] != review["reviewer_assignment_id"] for row in reviewer_rows)
                    or not any(row["evidence_type"] == "run" for row in reviewer_rows)):
                return False
            records = sorted(
                [{"evidence_id": row["evidence_id"], "assignment_id": row["assignment_id"],
                  "evidence_type": row["evidence_type"], "reference_hash": row["reference_hash"],
                  "payload_hash": row["payload_hash"]} for row in rows], key=lambda item: item["evidence_id"],
            )
            unsigned = {key: value for key, value in report.items() if key != "report_hash"}
            if (_hash(_json(records)) != review["evidence_set_hash"]
                    or _hash(_json(unsigned)) != report.get("report_hash")):
                return False
            reviewer = self._uow.db.execute(
                "SELECT workspace_id,session_id FROM assignments WHERE assignment_id=?",
                (review["reviewer_assignment_id"],),
            ).fetchone()
            integrated = self._uow.db.execute(
                "SELECT workspace_id,session_id FROM assignments WHERE assignment_id=?",
                (candidate["assignment_id"],),
            ).fetchone()
            return bool(reviewer and integrated and reviewer["workspace_id"] != integrated["workspace_id"]
                        and reviewer["session_id"] != integrated["session_id"])
        except (KeyError, TypeError, ValueError):
            return False

    def _single_node_report_plan(self, mission_id: str, mission: dict):
        revision = mission.get("plan_revision")
        if type(revision) is not int or revision < 1:
            return None
        plan = self._uow.plans.get(mission_id, revision)
        if plan is None or plan["acceptance_json"].get("kind") != "single_worker_report":
            return None
        nodes = plan["plan_json"].get("nodes", [])
        if (len(nodes) != 1 or nodes[0].get("role") != "work"
                or nodes[0].get("dependencies") or len(plan["jobs"]) != 1
                or plan["dependencies"]):
            return None
        return plan

    def _single_node_report_job(self, job: dict) -> bool:
        mission = self._uow.missions.get(job["mission_id"])
        return bool(job["role"] == "work" and mission is not None
                    and self._single_node_report_plan(job["mission_id"], mission))

    def _validate_frozen_report(self, assignment_id: str, assignment: dict, report: object) -> dict:
        if not isinstance(report, dict) or report.get("kind") != "single_worker_report":
            raise PermissionError("single Worker completion requires a frozen structured Report")
        run_id = assignment.get("run_id")
        output_ref = f"harness-run:{assignment.get('session_id')}:{run_id}"
        if not run_id or report.get("run_id") != run_id or report.get("output_ref") != output_ref:
            raise PermissionError("Report must bind the exact persisted Harness Run")
        self._require_digest(report.get("output_hash"), "report.output_hash")
        report_hash = self._require_digest(report.get("report_hash"), "report.report_hash")
        unsigned = {key: value for key, value in report.items() if key != "report_hash"}
        if _hash(_json(unsigned)) != report_hash:
            raise PermissionError("Report hash does not match its immutable contents")
        evidence_ids = report.get("evidence_ids")
        if (not isinstance(evidence_ids, list) or not evidence_ids
                or any(not isinstance(item, str) or not item or len(item) > 128 for item in evidence_ids)
                or len(evidence_ids) != len(set(evidence_ids))):
            raise PermissionError("Report requires unique persisted execution evidence references")
        placeholders = ",".join("?" for _ in evidence_ids)
        rows = self._uow.db.execute(
            f"SELECT evidence_id,evidence_type FROM evidence WHERE assignment_id=? AND evidence_id IN ({placeholders})",
            (assignment_id, *evidence_ids),
        ).fetchall()
        if {row["evidence_id"] for row in rows} != set(evidence_ids) or not any(row["evidence_type"] == "run" for row in rows):
            raise PermissionError("Report evidence references must resolve to this Assignment's persisted Run evidence")
        return report

    @staticmethod
    def _require_evidence_text(value: object, name: str) -> str:
        if (
            not isinstance(value, str) or not value.strip()
            or value != value.strip() or len(value) > 2048
        ):
            raise ValueError(f"{name} must be a trimmed evidence reference of at most 2048 characters")
        return value

    @staticmethod
    def _require_git_tree_hash(value: object, name: str) -> str:
        """接受当前 Git 使用的 SHA-1 / SHA-256 Tree OID，不能把它误当固定长度 64 的证据哈希。"""
        if (not isinstance(value, str) or len(value) not in {40, 64}
                or any(char not in "0123456789abcdef" for char in value)):
            raise ValueError(f"{name} must be a canonical Git tree object identity")
        return value

    def _validate_mission_integration_evidence(self, mission_id: str, current: dict,
                                               evidence: object) -> None:
        if not isinstance(evidence, dict):
            raise ValueError("Mission reconciliation requires structured audit evidence")
        if evidence.get("mission_id") != mission_id:
            raise PermissionError("Mission reconciliation evidence identity does not match")
        if type(evidence.get("plan_revision")) is not int or evidence["plan_revision"] != current["plan_revision"]:
            raise PermissionError("Mission reconciliation evidence must bind the current PlanRevision")
        self._require_evidence_text(evidence.get("reconciliation_ref"), "reconciliation_ref")
        integration = evidence.get("original_integration")
        if not isinstance(integration, dict):
            raise ValueError("Mission reconciliation requires the original Integrator operation receipt")
        integration_assignment_id = self._require_evidence_text(
            integration.get("assignment_id"), "original_integration.assignment_id",
        )
        integration_run_id = self._require_evidence_text(
            integration.get("run_id"), "original_integration.run_id",
        )
        self._require_evidence_text(integration.get("evidence_ref"), "original_integration.evidence_ref")
        if integration.get("outcome") != "succeeded":
            raise PermissionError("the original Integrator operation must be confirmed succeeded")
        artifact = evidence.get("integration_artifact")
        if not isinstance(artifact, dict):
            raise ValueError("Mission reconciliation requires a frozen Integration Artifact reference")
        if (
            artifact.get("assignment_id") != integration_assignment_id
            or artifact.get("run_id") != integration_run_id
        ):
            raise PermissionError("Integration Artifact must belong to the exact original Integrator Assignment and Run")
        self._require_evidence_text(artifact.get("artifact_ref"), "integration_artifact.artifact_ref")
        self._require_git_tree_hash(artifact.get("tree_hash"), "integration_artifact.tree_hash")
        self._require_digest(artifact.get("manifest_hash"), "integration_artifact.manifest_hash")
        self._require_evidence_text(artifact.get("evidence_ref"), "integration_artifact.evidence_ref")
        # 结构完整不等于真实证明：必须与本轮已冻结 Integrator 原 Run 和 Artifact 一致。
        candidate = self._latest_integration_candidate(mission_id, current["plan_revision"])
        if (candidate is None
                or integration_assignment_id != candidate["assignment_id"]
                or integration_run_id != candidate["run_id"]
                or artifact.get("artifact_ref") != f"artifact:{candidate['artifact_id']}"
                or artifact.get("tree_hash") != candidate["tree_hash"]
                or artifact.get("manifest_hash") != candidate["manifest_hash"]
                or artifact.get("evidence_ref") !=
                    f"artifact:{candidate['artifact_id']}:{candidate['manifest_hash']}"):
            raise PermissionError("Mission recovery evidence must match the current frozen Integrator candidate")
        original_evidence = self._uow.db.execute(
            "SELECT 1 FROM evidence WHERE evidence_id=? AND assignment_id=? "
            "AND evidence_type='run' LIMIT 1",
            (integration["evidence_ref"], integration_assignment_id),
        ).fetchone()
        if original_evidence is None:
            raise PermissionError("Mission recovery must cite persisted original Integrator Run evidence")

        required_edges = {
            (row["job_id"], row["depends_on_job_id"])
            for row in self._uow.db.execute(
                "SELECT job_id,depends_on_job_id FROM job_dependencies "
                "WHERE mission_id=? AND plan_revision=? AND requires_delivery=1",
                (mission_id, current["plan_revision"]),
            ).fetchall()
        }
        deliveries = evidence.get("deliveries")
        if not isinstance(deliveries, list):
            raise ValueError("Mission reconciliation requires a Delivery evidence array")
        observed_edges = set()
        observed_delivery_ids = set()
        for delivery in deliveries:
            if not isinstance(delivery, dict):
                raise ValueError("Delivery evidence entries must be objects")
            job_id = self._require_evidence_text(delivery.get("job_id"), "delivery.job_id")
            depends_on_job_id = self._require_evidence_text(
                delivery.get("depends_on_job_id"), "delivery.depends_on_job_id",
            )
            edge = (job_id, depends_on_job_id)
            if edge not in required_edges or edge in observed_edges:
                raise PermissionError("Delivery evidence must uniquely cover a required dependency edge")
            delivery_id = self._require_evidence_text(delivery.get("delivery_id"), "delivery.delivery_id")
            if delivery_id in observed_delivery_ids:
                raise ValueError("Delivery evidence must not reuse a delivery_id")
            observed_delivery_ids.add(delivery_id)
            self._require_evidence_text(delivery.get("evidence_ref"), "delivery.evidence_ref")
            self._require_git_tree_hash(delivery.get("applied_tree_hash"), "delivery.applied_tree_hash")
            # Delivery 回执和 DAG 边必须同时对应当前目标 Assignment 与父级冻结产物。
            target = self._uow.db.execute(
                "SELECT assignment_id FROM assignments WHERE job_id=? "
                "ORDER BY attempt_no DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            source = self._uow.db.execute(
                "SELECT artifact.artifact_id FROM assignments AS assignment "
                "JOIN artifacts AS artifact ON artifact.assignment_id=assignment.assignment_id "
                "WHERE assignment.job_id=? AND assignment.state='succeeded' "
                "ORDER BY assignment.attempt_no DESC LIMIT 1",
                (depends_on_job_id,),
            ).fetchone()
            persisted = self._uow.db.execute(
                "SELECT artifact_id,target_assignment_id,state,receipt_json "
                "FROM deliveries WHERE delivery_id=?",
                (delivery_id,),
            ).fetchone()
            if (target is None or source is None or persisted is None
                    or persisted["artifact_id"] != source["artifact_id"]
                    or persisted["target_assignment_id"] != target["assignment_id"]
                    or persisted["state"] != "applied"
                    or delivery["evidence_ref"] != f"delivery:{delivery_id}:applied"):
                raise PermissionError("Mission recovery Delivery evidence does not match a persisted applied DAG edge")
            try:
                receipt = json.loads(persisted["receipt_json"])
            except (TypeError, ValueError) as exc:
                raise PermissionError("Mission recovery Delivery receipt is invalid") from exc
            if (not isinstance(receipt, dict)
                    or receipt.get("tree") != delivery["applied_tree_hash"]):
                raise PermissionError("Mission recovery applied tree does not match the original Delivery receipt")
            observed_edges.add(edge)
        if observed_edges != required_edges:
            raise PermissionError("Mission reconciliation is missing applied evidence for a required Delivery")

    def _validate_job_cancel_evidence(self, job_id: str, evidence: object) -> None:
        if not isinstance(evidence, dict):
            raise ValueError("unknown Job cancellation requires structured ownership evidence")
        self._require_evidence_text(evidence.get("reconciliation_ref"), "reconciliation_ref")
        self._require_evidence_text(evidence.get("ownership_evidence_ref"), "ownership_evidence_ref")
        settled = evidence.get("settled_assignments")
        if not isinstance(settled, list):
            raise ValueError("Job cancellation requires the settled Assignment inventory")
        rows = self._uow.db.execute(
            "SELECT assignment_id,state,run_id FROM assignments WHERE job_id=? ORDER BY assignment_id",
            (job_id,),
        ).fetchall()
        expected = {
            row["assignment_id"]: (row["state"], row["run_id"])
            for row in rows
        }
        observed = {}
        terminal = {"succeeded", "failed", "interrupted", "rejected", "cancelled"}
        for item in settled:
            if not isinstance(item, dict):
                raise ValueError("settled Assignment evidence entries must be objects")
            assignment_id = self._require_evidence_text(item.get("assignment_id"), "assignment_id")
            if assignment_id in observed:
                raise ValueError("settled Assignment evidence contains a duplicate identity")
            observed[assignment_id] = (item.get("state"), item.get("run_id"))
            if observed[assignment_id][0] not in terminal:
                raise PermissionError("Job ownership evidence contains an unsettled Assignment")
        if observed != expected:
            raise PermissionError("Job ownership evidence must exactly match every persisted Assignment and Run")

    def _validate_assignment_reconciliation_evidence(self, current: dict, target: str,
                                                       changes: dict, evidence: object) -> None:
        if not isinstance(evidence, dict):
            raise ValueError("Assignment reconciliation requires structured original Run evidence")
        self._require_evidence_text(evidence.get("reconciliation_ref"), "reconciliation_ref")
        run_id = self._require_evidence_text(evidence.get("run_id"), "run_id")
        if current.get("run_id") is None or run_id != current["run_id"]:
            raise PermissionError("Assignment reconciliation evidence must identify the persisted original Run")
        self._require_evidence_text(evidence.get("run_evidence_ref"), "run_evidence_ref")
        if target == "succeeded":
            output_ref = self._require_evidence_text(evidence.get("output_ref"), "output_ref")
            output_hash = self._require_digest(evidence.get("output_hash"), "output_hash")
            report = changes.get("report_json")
            if not isinstance(report, dict) or report.get("output_ref") != output_ref or report.get("output_hash") != output_hash:
                raise PermissionError("succeeded Assignment report must match its frozen original Run output evidence")
        else:
            self._require_evidence_text(evidence.get("cancel_receipt_ref"), "cancel_receipt_ref")
            self._require_evidence_text(evidence.get("ownership_evidence_ref"), "ownership_evidence_ref")

    def _validate_single_node_report_reconciliation(self, mission_id: str, current: dict, evidence: object) -> None:
        if not isinstance(evidence, dict) or evidence.get("mission_id") != mission_id:
            raise ValueError("single-node Mission reconciliation requires structured Report evidence")
        if evidence.get("plan_revision") != current["plan_revision"]:
            raise PermissionError("Report reconciliation must bind the current PlanRevision")
        self._require_evidence_text(evidence.get("reconciliation_ref"), "reconciliation_ref")
        assignment_id = self._require_evidence_text(evidence.get("assignment_id"), "assignment_id")
        row = self._uow.db.execute("SELECT assignment.* FROM assignments AS assignment JOIN jobs AS job ON job.job_id=assignment.job_id WHERE assignment.assignment_id=? AND job.mission_id=? AND job.plan_revision=? AND job.role='work'", (assignment_id, mission_id, current["plan_revision"])).fetchone()
        if row is None or row["state"] != "succeeded":
            raise PermissionError("reconciliation must identify the original succeeded Assignment")
        assignment = dict(row); report = json.loads(assignment["report_json"])
        self._validate_frozen_report(assignment_id, assignment, report)
        if evidence.get("run_id") != assignment["run_id"] or evidence.get("report_ref") != f"report:{assignment_id}:{report['report_hash']}" or evidence.get("report_hash") != report["report_hash"]:
            raise PermissionError("reconciliation Report reference does not match the original Assignment")

    def _validate_ticket(self, current: dict, source: str, target: str,
                         facts: dict[str, bool], changes: dict[str, Any]) -> None:
        if source == "open" and target == "ended":
            basis = {
                "worker_follow_condition", "nonrecoverable_failure",
                "human_stop", "mandate_revoked",
            }
            if not basis.intersection(key for key, value in facts.items() if value):
                raise PermissionError("ticket ending requires a verified terminal basis")
            if facts.get("worker_follow_condition"):
                if facts.get("follows_done") is not True:
                    raise PermissionError("worker completion requires the Ticket Follow Condition")
                if changes.get("outcome") != "completed" or changes.get("ended_by") != "executor":
                    raise ValueError("Follow Condition completion must be recorded as executor-owned completed")
            if facts.get("nonrecoverable_failure") and facts.get("no_unknown_operations") is not True:
                raise PermissionError("failed Ticket requires all owned operations to be known")
            if not {"outcome", "ended_by", "ended_at"} <= set(changes):
                raise ValueError("ending a Ticket requires outcome, ended_by and ended_at")
            if facts.get("human_stop"):
                if changes.get("outcome") != "stopped" or changes.get("ended_by") != "human":
                    raise ValueError("human stop must produce a human-owned stopped outcome")
            if facts.get("mandate_revoked"):
                if changes.get("outcome") != "revoked" or changes.get("ended_by") not in {"policy", "system"}:
                    raise ValueError("mandate revocation must produce a policy-owned revoked outcome")
            if facts.get("nonrecoverable_failure"):
                if changes.get("outcome") != "failed" or changes.get("ended_by") not in {"executor", "system"}:
                    raise ValueError("nonrecoverable failure must produce an executor-owned failed outcome")
        elif source == "ended" and target == "open":
            if current.get("outcome") in {"stopped", "revoked"}:
                raise PermissionError("human-stopped or revoked Tickets cannot be reopened")
            if not {"outcome", "ended_by", "ended_at"} <= set(changes):
                raise ValueError("reopening a Ticket must clear its terminal fields")
            if any(changes[field] is not None for field in ("outcome", "ended_by", "ended_at")):
                raise ValueError("reopening a Ticket must clear its terminal fields")

    def _validate_mission(self, identity: str, current: dict, source: str, target: str,
                          facts: dict[str, bool], changes: dict[str, Any]) -> None:
        if target == "running":
            revision = current["plan_revision"]
            if type(revision) is not int or revision < 1 or self._uow.plans.get(identity, revision) is None:
                raise PermissionError("running requires an existing immutable accepted PlanRevision")
        if source == "planning" and target == "rejected":
            if not (facts.get("plan_schema_invalid") or facts.get("planning_budget_exhausted")):
                raise PermissionError("rejected requires an invalid Plan or exhausted planning budget")
        if source in {"integrating", "unknown"} and target == "reviewing":
            if not self._mission_jobs_done(identity, current["plan_revision"]):
                raise PermissionError("reviewing requires every Work Job and required Delivery to be settled")
            if source == "unknown" and self._mission_has_inflight_assignments(identity):
                raise PermissionError("unknown Mission cannot enter reviewing while any original Assignment remains active")
            active_integrators = self._uow.db.execute(
                "SELECT COUNT(*) FROM assignments AS assignment "
                "JOIN jobs AS job ON job.job_id=assignment.job_id "
                "WHERE job.mission_id=? AND job.plan_revision=? AND job.role='integrate' "
                "AND assignment.state IN ('pending','submitting','accepted','running','capturing','stopping','unknown')",
                (identity, current["plan_revision"]),
            ).fetchone()[0]
            if active_integrators:
                raise PermissionError("reviewing requires the original Integrator Assignment to be settled")
            integrators = self._uow.db.execute(
                "SELECT job_id,state FROM jobs WHERE mission_id=? AND plan_revision=? AND role='integrate'",
                (identity, current["plan_revision"]),
            ).fetchall()
            candidate = self._latest_integration_candidate(identity, current["plan_revision"])
            if (len(integrators) != 1 or integrators[0]["state"] != "done" or candidate is None
                    or not self._job_deliveries_applied(candidate["job_id"], candidate["assignment_id"])):
                raise PermissionError("reviewing requires the frozen Integrator candidate and applied Work Deliveries")
        if target in {"completed", "blocked", "rejected", "suppressed", "cancelled"}:
            if self._mission_has_active_assignments(identity):
                raise PermissionError("Mission terminal transition requires all owned Assignments to be settled")
        if target == "blocked" and facts.get("known_failure_or_human_decision") is not True:
            raise PermissionError("blocked requires a known failure or human decision")
        if target == "suppressed" and facts.get("mandate_revoked") is not True:
            raise PermissionError("suppressed requires confirmed Mandate revocation")
        if target == "cancelling" and facts.get("cancel_accepted") is not True:
            raise PermissionError("cancelling requires an accepted host cancel command")
        if source in {"running", "unknown"} and target == "integrating":
            if not self._mission_jobs_done(identity, current["plan_revision"]):
                raise PermissionError("integrating requires every Work Job settled and no active Assignment in the PlanRevision")
        if source == "reviewing" and target == "integrating":
            next_round = changes.get("round_no")
            plan = self._uow.plans.get(identity, current["plan_revision"])
            review = self._latest_review(identity, current["plan_revision"])
            maximum = (plan or {}).get("acceptance_json", {}).get("max_repair_rounds", 1)
            repair_jobs = self._uow.db.execute(
                "SELECT role,state FROM jobs WHERE mission_id=? AND plan_revision=? AND role IN ('integrate','review')",
                (identity, current["plan_revision"]),
            ).fetchall()
            review_job = self._uow.jobs.get(review["job_id"]) if review is not None else None
            # 只允许结算最新 Reviewer Assignment；旧失败记录不可跨新尝试重放。
            latest_reviewer = self._uow.db.execute(
                "SELECT assignment_id FROM assignments WHERE job_id=? "
                "ORDER BY attempt_no DESC LIMIT 1",
                (review["job_id"],),
            ).fetchone() if review is not None else None
            try:
                review_report = json.loads(review["report_json"]) if review is not None else None
            except (TypeError, ValueError):
                review_report = None
            review_frozen = bool(
                review is not None and review_job is not None
                and latest_reviewer is not None
                and latest_reviewer["assignment_id"] == review["reviewer_assignment_id"]
                and not self._mission_has_active_assignments(identity)
                and review["assignment_state"] == "succeeded"
                and review["job_state"] in {"done", "checkpointed"}
                and self._review_frozen_for_assignment(
                    identity, review_job, review["reviewer_assignment_id"], review_report,
                    allow_checkpointed_candidate=True,
                )
                and self._job_deliveries_applied(
                    review["job_id"], review["reviewer_assignment_id"],
                )
            )
            if (type(next_round) is not int or next_round != current["round_no"] + 1
                    or type(maximum) is not int or current["round_no"] >= maximum
                    or review is None or review["state"] != "failed" or not review_frozen
                    or len(repair_jobs) != 2 or any(row["state"] != "checkpointed" for row in repair_jobs)):
                raise PermissionError("repair requires a current frozen failed Review, applied evidence, checkpointed system Jobs and remaining repair budget")
        if source == "unknown" and target == "planning" and facts.get("mandate_valid") is not True:
            raise PermissionError("resuming planning requires a currently valid Mandate")
        if source == "unknown" and target == "running" and facts.get("next_step_safe") is not True:
            raise PermissionError("resuming a Mission requires proof that the next step is safe")
        if target == "completed":
            if not self._mission_jobs_done(identity, current["plan_revision"]):
                raise PermissionError("Mission completion requires every Work Job and Assignment to be settled")
            if self._single_node_report_plan(identity, current):
                if not all(facts.get(name) is True for name in (
                    "single_node_report_frozen", "all_work_jobs_done", "original_run_settled",
                    "mandate_valid", "control_allows",
                )):
                    raise PermissionError("single-node Mission completion requires a frozen Report, settled Run and current authorization")
                rows = self._uow.db.execute("SELECT assignment.* FROM assignments AS assignment JOIN jobs AS job ON job.job_id=assignment.job_id WHERE job.mission_id=? AND job.plan_revision=? AND job.role='work' AND assignment.state='succeeded'", (identity, current["plan_revision"])).fetchall()
                if len(rows) != 1:
                    raise PermissionError("single-node Mission requires exactly one succeeded Worker Report")
                row = dict(rows[0]); self._validate_frozen_report(row["assignment_id"], row, json.loads(row["report_json"]))
                if json.loads(row["report_json"]).get("disposition") == "attention":
                    raise PermissionError("a Mission awaiting human Attention cannot be completed")
            else:
                if not all(facts.get(name) is True for name in (
                    "all_work_jobs_done", "original_integration_settled", "integration_artifact_frozen",
                    "required_deliveries_applied", "acceptance_verified", "mandate_valid", "control_allows",
                )):
                    raise PermissionError("integrated Mission completion requires settled Work and Integrator operations, applied Deliveries, accepted output and current authorization")
                review = self._latest_review(identity, current["plan_revision"])
                if (not self._review_passed(identity, current) or review is None
                        or not self._job_deliveries_applied(review["job_id"], review["reviewer_assignment_id"])):
                    raise PermissionError("Mission completion requires a persisted passing Review and applied candidate Delivery")

    def _validate_job(self, identity: str, current: dict, source: str, target: str,
                      facts: dict[str, bool], changes: dict[str, Any]) -> None:
        plan = self._uow.plans.get(current["mission_id"], current["plan_revision"]) if current["plan_revision"] else None
        node = next((item for item in (plan or {}).get("plan_json", {}).get("nodes", [])
                     if item["node_id"] == current["node_id"]), {})
        maximum = node.get("max_work_turns")
        if maximum is not None and (type(maximum) is not int or not 1 <= maximum <= 32):
            raise PermissionError(tr("superagent.invalid", field="max_work_turns"))
        if target == "ready":
            mission = self._uow.missions.get(current["mission_id"])
            expected_stage = {"work": "running", "integrate": "integrating", "review": "reviewing"}.get(current["role"])
            if mission is None or expected_stage is None or mission["state"] != expected_stage:
                raise PermissionError("Job can become ready only in its role-specific Mission stage")
            if current["plan_revision"] != mission["plan_revision"]:
                raise PermissionError("Job plan revision must match the active Mission PlanRevision")
            for dependency in self._uow.dependencies.for_job(identity):
                parent = self._uow.jobs.get(dependency["depends_on_job_id"])
                if parent is None or parent["state"] != "done":
                    raise PermissionError("Job dependencies are not all complete")
                if dependency["requires_delivery"] and facts.get("required_deliveries_applied") is not True:
                    raise PermissionError("required dependency Deliveries are not confirmed applied")
        if source == "ready" and target == "running":
            assignment = self._uow.db.execute(
                "SELECT 1 FROM assignments WHERE job_id=? AND state='running' AND run_id IS NOT NULL LIMIT 1",
                (identity,),
            ).fetchone()
            if assignment is None:
                raise PermissionError("running requires an Assignment bound to an active original Run")
        if target == "checkpointed":
            if source == "done":
                mission = self._uow.missions.get(current["mission_id"])
                plan = self._uow.plans.get(current["mission_id"], current["plan_revision"])
                review = self._latest_review(current["mission_id"], current["plan_revision"])
                maximum = (plan or {}).get("acceptance_json", {}).get("max_repair_rounds", 1)
                if (current["role"] not in {"integrate", "review"} or mission is None
                        or mission["state"] != "reviewing" or review is None or review["state"] != "failed"
                        or type(maximum) is not int or mission["round_no"] >= maximum):
                    raise PermissionError("completed system Jobs can resume only after a failed Review with remaining repair budget")
            elif source in {"ready", "running"}:
                if current["role"] not in {"work", "integrate"}:
                    raise PermissionError("only Work or Integrator Jobs can checkpoint a running Assignment")
                if current["role"] == "work":
                    latest = self._uow.db.execute(
                        "SELECT * FROM assignments WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1",
                        (identity,),
                    ).fetchone()
                    if latest is None or latest["state"] != "succeeded":
                        raise PermissionError(tr("superagent.corrupt"))
                    checkpoint = frozen_work_checkpoint(self._uow.db, latest)
                    if (checkpoint["status"] != "continue" or maximum is None
                            or latest["attempt_no"] >= maximum):
                        raise PermissionError(tr("superagent.corrupt"))
                    report = json.loads(latest["report_json"])
                    if changes.get("result_hash") != report["report_hash"]:
                        raise PermissionError(tr("superagent.corrupt"))
                elif source == "ready":
                    raise PermissionError(tr("superagent.corrupt"))
            else:
                raise PermissionError("Job checkpoint source state is invalid")
            active_assignments = self._uow.db.execute(
                "SELECT COUNT(*) FROM assignments WHERE job_id=? "
                "AND state IN ('pending','submitting','accepted','running','capturing','stopping','unknown')",
                (identity,),
            ).fetchone()[0]
            if active_assignments:
                raise PermissionError("checkpointed requires the original Assignment to be settled")
        if source == "checkpointed" and target == "running":
            mission = self._uow.missions.get(current["mission_id"])
            stage = {"work": "running", "integrate": "integrating", "review": "reviewing"}.get(current["role"])
            if mission is None or stage is None or mission["state"] != stage:
                raise PermissionError("checkpoint resume requires the Job's role-specific Mission stage")
            assignment = self._uow.db.execute(
                "SELECT assignment_id,attempt_no,state,run_id,report_json FROM assignments WHERE job_id=? "
                "ORDER BY attempt_no DESC LIMIT 1", (identity,),
            ).fetchone()
            previous = self._uow.db.execute(
                "SELECT * FROM assignments WHERE job_id=? "
                "ORDER BY attempt_no DESC LIMIT 1 OFFSET 1", (identity,),
            ).fetchone()
            if (assignment is None or previous is None
                    or assignment["state"] not in {"running", "succeeded"}
                    or assignment["run_id"] is None or assignment["attempt_no"] < 2
                    or previous["state"] != "succeeded"):
                raise PermissionError("checkpoint resume requires a new Assignment bound to its original Run")
            if assignment["state"] == "succeeded":
                # 原生 Run 可在首次 running 观察前完成；必须先有持久的角色产物。
                if current["role"] in {"work", "integrate"}:
                    frozen = self._uow.db.execute(
                        "SELECT 1 FROM artifacts WHERE assignment_id=?",
                        (assignment["assignment_id"],),
                    ).fetchone()
                    if frozen is None:
                        raise PermissionError("settled checkpoint resume requires the frozen Integrator Artifact")
                else:
                    try:
                        report = json.loads(assignment["report_json"])
                    except (TypeError, ValueError) as exc:
                        raise PermissionError("settled checkpoint resume requires the frozen Review Report") from exc
                    if not self._review_frozen_for_assignment(
                        current["mission_id"], current, assignment["assignment_id"], report,
                    ):
                        raise PermissionError("settled checkpoint resume requires the frozen Review Verdict")
            if current["role"] == "work":
                if (maximum is None or assignment["attempt_no"] > maximum
                        or frozen_work_checkpoint(self._uow.db, previous)["status"] != "continue"):
                    raise PermissionError(tr("superagent.corrupt"))
            if current["role"] in {"work", "integrate"}:
                source_artifact = self._uow.db.execute(
                    "SELECT artifact_id FROM artifacts WHERE assignment_id=?", (previous["assignment_id"],),
                ).fetchone()
            else:
                candidate = self._latest_integration_candidate(current["mission_id"], current["plan_revision"])
                source_artifact = {"artifact_id": candidate["artifact_id"]} if candidate else None
            if (source_artifact is None or self._uow.db.execute(
                "SELECT 1 FROM deliveries WHERE target_assignment_id=? AND artifact_id=? AND state='applied'",
                (assignment["assignment_id"], source_artifact["artifact_id"]),
            ).fetchone() is None):
                raise PermissionError("checkpoint resume requires the frozen candidate to be imported into the new workspace")
        if source == "unknown" and target == "running":
            assignment = self._uow.db.execute(
                "SELECT 1 FROM assignments WHERE job_id=? AND state='running' AND run_id IS NOT NULL LIMIT 1",
                (identity,),
            ).fetchone()
            if assignment is None:
                raise PermissionError("unknown Job can resume only after its exact original Assignment is reconciled active")
        if target in {"done", "blocked", "cancelled"}:
            active_assignments = self._uow.db.execute(
                "SELECT COUNT(*) FROM assignments WHERE job_id=? "
                "AND state IN ('pending','submitting','accepted','running','capturing','stopping','unknown')",
                (identity,),
            ).fetchone()[0]
            if active_assignments:
                raise PermissionError("terminal Job transition requires every Assignment to be settled")
        if target == "done":
            self._require_digest(changes.get("result_hash"), "result_hash")
            if current["role"] == "work":
                if maximum is not None:
                    latest = self._uow.db.execute(
                        "SELECT * FROM assignments WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1",
                        (identity,),
                    ).fetchone()
                    if (latest is None or latest["state"] != "succeeded"
                            or frozen_work_checkpoint(self._uow.db, latest)["status"] != "ready"):
                        raise PermissionError(tr("superagent.corrupt"))
            single_report = current["role"] == "work" and self._single_node_report_job(current)
            role_fact = ("report_frozen" if single_report else {
                "plan": "plan_revision_frozen", "work": "artifact_frozen",
                "integrate": "artifact_frozen", "review": "review_verdict_and_evidence_frozen",
            }[current["role"]])
            if facts.get(role_fact) is not True:
                raise PermissionError(f"done requires role-specific frozen output: {role_fact}")
            if single_report:
                row = self._uow.db.execute("SELECT assignment.* FROM assignments AS assignment WHERE assignment.job_id=? AND assignment.state='succeeded'", (identity,)).fetchone()
                if row is None:
                    raise PermissionError("single-node Job completion requires its succeeded Worker Assignment")
                report = self._validate_frozen_report(row["assignment_id"], dict(row), json.loads(row["report_json"]))
                if changes.get("result_hash") != report["report_hash"]:
                    raise PermissionError("single-node Job result_hash must equal its frozen Report hash")
            elif current["role"] == "review":
                row = self._uow.db.execute(
                    "SELECT assignment_id,report_json FROM assignments WHERE job_id=? AND state='succeeded' "
                    "ORDER BY attempt_no DESC LIMIT 1", (identity,),
                ).fetchone()
                if (row is None or not self._review_frozen_for_assignment(
                    current["mission_id"], current, row["assignment_id"], json.loads(row["report_json"]),
                )):
                    raise PermissionError("Review Job completion requires its persisted immutable Review record")
        if target == "blocked" and facts.get("known_failure_or_human_decision") is not True:
            raise PermissionError("blocked requires a known failure or human decision")
        if target == "cancelled":
            mission = self._uow.missions.get(current["mission_id"])
            if mission is None or mission["state"] not in {"cancelling", "cancelled"}:
                raise PermissionError("Job cancellation requires its Mission to be cancelling or cancelled")
            if source == "unknown":
                if not (facts.get("no_external_side_effect_started") is True
                        or facts.get("owned_operations_settled") is True):
                    raise PermissionError("unknown Job cancellation requires proof of no side effect or settled ownership")
            elif facts.get("no_external_side_effect_started") is not True:
                raise PermissionError("Job cancellation requires proof that no external side effect started")

    def _validate_assignment(self, identity: str, current: dict, source: str, target: str,
                             facts: dict[str, bool], changes: dict[str, Any]) -> None:
        job = self._uow.jobs.get(current["job_id"])
        if job is None:
            raise LookupError("Assignment Job does not exist")
        if source == "accepted" and target in {"running", "capturing", "failed", "interrupted"}:
            if current.get("run_id") is None and changes.get("run_id") is None:
                raise PermissionError("settling an accepted Assignment requires the exact original Run identity")
        if source in {"running", "capturing", "stopping"} and target in {
            "capturing", "stopping", "succeeded", "failed", "interrupted", "cancelled",
        } and current.get("run_id") is None:
            raise PermissionError("Run settlement requires the persisted original Run identity")
        if source in {"running", "capturing", "stopping"} and target == "unknown" and current.get("run_id") is None:
            raise PermissionError("uncertain Run outcome requires the persisted original Run identity")
        if source == "unknown" and target in {"capturing", "succeeded", "failed", "interrupted", "cancelled"}:
            if current.get("run_id") is None:
                raise PermissionError("reconciling an original Run requires its persisted identity")
        if source == "pending" and target == "cancelled":
            if current.get("run_id") is not None or facts.get("no_external_side_effect_started") is not True:
                raise PermissionError("a pending Assignment can be cancelled only before any Run was accepted")
        if target == "succeeded":
            single_report = job["role"] == "work" and self._single_node_report_job(job)
            role_fact = ("report_frozen" if single_report else {
                "plan": "plan_revision_frozen", "work": "artifact_frozen",
                "integrate": "artifact_frozen", "review": "review_verdict_and_evidence_frozen",
            }[job["role"]])
            if facts.get(role_fact) is not True:
                raise PermissionError(f"succeeded requires role-specific frozen output: {role_fact}")
            if single_report:
                assignment = {**current, **changes}
                self._validate_frozen_report(identity, assignment, assignment.get("report_json"))
            elif job["role"] == "review" and not self._review_frozen_for_assignment(
                job["mission_id"], job, identity, changes.get("report_json"),
            ):
                raise PermissionError("Review Assignment success requires its persisted immutable Review record")
        if target == "failed":
            required_failure_fact = (
                "capture_or_validation_failure_confirmed"
                if source == "capturing" else "original_run_failure_confirmed"
            )
            if facts.get(required_failure_fact) is not True:
                raise PermissionError(f"failed requires verified evidence: {required_failure_fact}")
        if target == "interrupted" and facts.get("original_run_interruption_confirmed") is not True:
            raise PermissionError("interrupted requires confirmation of the original Run interruption")
        if source == "unknown" and target == "running" and current.get("run_id") is None:
            raise PermissionError("unknown can resume running only with the original Run identity")

    def _validate_worker(self, identity: str, current: dict, source: str, target: str, facts: dict[str, bool], changes: dict[str, Any]) -> None:
        if current.get("workspace_id") is not None and changes.get("workspace_id", current["workspace_id"]) != current["workspace_id"]:
            raise PermissionError("Worker workspace identity is immutable once bound")
        if current.get("session_id") is not None and changes.get("session_id", current["session_id"]) != current["session_id"]:
            raise PermissionError("Worker session identity is immutable once bound")
        if target == "workspace_ready":
            self._require_evidence_text(changes.get("workspace_id") or current.get("workspace_id"), "workspace_id")
        if target == "session_creating" and not current.get("workspace_id"):
            raise PermissionError("Worker session creation requires a verified workspace")
        if target == "ready":
            self._require_evidence_text(changes.get("session_id") or current.get("session_id"), "session_id")
            self._require_evidence_text(changes.get("workspace_id") or current.get("workspace_id"), "workspace_id")
        if target == "stopped":
            active = self._uow.db.execute("SELECT COUNT(*) FROM assignments WHERE worker_id=? AND state IN ('pending','submitting','accepted','running','capturing','stopping','unknown')", (identity,)).fetchone()[0]
            if active or facts.get("no_owned_run_active") is not True:
                raise PermissionError("Worker can stop only after all owned Assignments are settled")

    def _validate_attention(self, identity: str, current: dict, source: str, target: str, facts: dict[str, bool], changes: dict[str, Any]) -> None:
        if source != "open":
            raise PermissionError("only open Attention can be resolved")
        if target == "dismissed":
            if changes.get("answer_trigger_id") is not None or not changes.get("resolved_at"):
                raise ValueError("dismissed Attention must have a resolution time and no answer Trigger")
            return
        answer_id = self._require_evidence_text(changes.get("answer_trigger_id"), "answer_trigger_id")
        if not changes.get("resolved_at"):
            raise ValueError("answered Attention requires a resolution time")
        row = self._uow.db.execute("SELECT input.context_only,input.state,trigger.actor_json FROM ticket_inputs AS input JOIN triggers AS trigger ON trigger.trigger_id=input.trigger_id WHERE input.ticket_id=? AND input.trigger_id=?", (current["ticket_id"], answer_id)).fetchone()
        if row is None or row["context_only"] or row["state"] == "handed_back":
            raise PermissionError("Attention answer must be a work input belonging to the same Ticket")
        actor = json.loads(row["actor_json"])
        if actor.get("kind", actor.get("actor_kind")) != "human":
            raise PermissionError("Attention can be answered only by a human Trigger")

    def transition(
        self,
        aggregate_type: str,
        identity: str,
        *,
        expected_revision: int,
        target_state: str,
        facts: dict[str, bool],
        changes: dict[str, Any] | None = None,
        event_type: str | None = None,
        audit_payload: object | None = None,
        effect: dict | None = None,
    ) -> dict | None:
        """核验状态转移事实，再通过 Repository 执行 CAS、审计和 Effect 入队。"""
        if aggregate_type not in _REPOSITORIES:
            raise ValueError("unsupported aggregate type; Outbox is advanced by OutboxEffectService")
        if not isinstance(facts, dict) or any(
            not isinstance(key, str) or type(value) is not bool for key, value in facts.items()
        ):
            raise ValueError("facts must map fact names to boolean evidence")
        if facts.get("authorized") is not True:
            raise PermissionError("host authorization is required")
        repository = getattr(self._uow, _REPOSITORIES[aggregate_type])
        current = repository.get(identity)
        if current is None:
            raise LookupError(f"{aggregate_type} does not exist")
        source_state = current["state"]
        if target_state not in _TRANSITIONS[aggregate_type].get(source_state, set()):
            raise ValueError(f"illegal {aggregate_type} transition: {source_state} -> {target_state}")
        required = _REQUIRED_FACTS.get((aggregate_type, source_state, target_state))
        if required is None:
            required = _REQUIRED_FACTS.get((aggregate_type, source_state, "*"), ())
        missing = [fact for fact in required if facts.get(fact) is not True]
        if missing:
            raise PermissionError(f"transition evidence missing: {', '.join(missing)}")
        if target_state == "unknown" and facts.get("operation_outcome_uncertain") is not True:
            raise PermissionError("unknown requires evidence that the original operation outcome is uncertain")

        payload = dict(changes or {})
        if "state" in payload:
            raise ValueError("state must be supplied only as target_state")
        if aggregate_type == "ticket":
            self._validate_ticket(current, source_state, target_state, facts, payload)
        elif aggregate_type == "mission":
            self._validate_mission(identity, current, source_state, target_state, facts, payload)
        elif aggregate_type == "job":
            self._validate_job(identity, current, source_state, target_state, facts, payload)
        elif aggregate_type == "assignment":
            self._validate_assignment(identity, current, source_state, target_state, facts, payload)
        elif aggregate_type == "worker":
            self._validate_worker(identity, current, source_state, target_state, facts, payload)
        elif aggregate_type == "attention":
            self._validate_attention(identity, current, source_state, target_state, facts, payload)
        if source_state == "unknown":
            if not isinstance(audit_payload, dict):
                raise ValueError("unknown reconciliation requires a structured audit payload")
            self._require_evidence_text(audit_payload.get("reconciliation_ref"), "reconciliation_ref")
            if aggregate_type == "mission" and target_state == "completed" and self._single_node_report_plan(identity, current):
                self._validate_single_node_report_reconciliation(identity, current, audit_payload)
            elif aggregate_type == "mission" and target_state in {"reviewing", "completed"}:
                self._validate_mission_integration_evidence(identity, current, audit_payload)
            elif aggregate_type == "job" and target_state == "cancelled":
                self._validate_job_cancel_evidence(identity, audit_payload)
            elif aggregate_type == "assignment" and target_state in {"succeeded", "cancelled"}:
                self._validate_assignment_reconciliation_evidence(
                    current, target_state, payload, audit_payload,
                )

        payload["state"] = target_state
        transition_audit = {
            "from": source_state,
            "to": target_state,
            "revision": expected_revision + 1,
            "facts": dict(facts),
            "changes": payload,
        }
        if audit_payload is not None:
            transition_audit["evidence"] = audit_payload
        transition_event = (
            f"{aggregate_type}.reconciled.{target_state}"
            if source_state == "unknown" else event_type or f"{aggregate_type}.{target_state}"
        )
        return repository._transition(
            identity,
            expected_revision=expected_revision,
            changes=payload,
            event_type=transition_event,
            audit_payload=transition_audit,
            effect=effect,
        )

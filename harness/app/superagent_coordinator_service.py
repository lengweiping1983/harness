"""下一代 Superagent 的宿主应用服务与状态转移守卫。"""
from __future__ import annotations

import re
from typing import Any

from .superagent_coordinator_store import CoordinatorUnitOfWork


_REPOSITORIES = {
    "ticket": "tickets",
    "mission": "missions",
    "job": "jobs",
    "assignment": "assignments",
}

_TRANSITIONS = {
    "ticket": {"open": {"ended"}, "ended": {"open"}},
    "mission": {
        "planning": {"running", "rejected", "blocked", "suppressed", "unknown", "cancelling"},
        "running": {"integrating", "blocked", "suppressed", "unknown", "cancelling"},
        "integrating": {"reviewing", "blocked", "unknown", "cancelling"},
        "reviewing": {"completed", "integrating", "blocked", "unknown", "cancelling"},
        "cancelling": {"cancelled", "unknown"},
        "unknown": {
            "planning", "running", "integrating", "reviewing", "completed",
            "blocked", "rejected", "suppressed", "cancelled",
        },
    },
    "job": {
        "pending": {"ready", "blocked", "unknown", "cancelled"},
        "ready": {"running", "blocked", "unknown", "cancelled"},
        "running": {"checkpointed", "done", "blocked", "unknown"},
        "checkpointed": {"running", "blocked", "unknown", "cancelled"},
        "unknown": {"running", "done", "blocked", "cancelled"},
    },
    "assignment": {
        "pending": {"submitting"},
        "submitting": {"accepted", "rejected", "unknown"},
        "accepted": {"running", "capturing", "failed", "interrupted", "unknown"},
        "running": {"capturing", "stopping", "failed", "interrupted", "unknown"},
        "capturing": {"succeeded", "failed", "unknown"},
        "stopping": {"cancelled", "succeeded", "unknown"},
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
    ("mission", "running", "integrating"): ("all_work_jobs_done", "required_deliveries_applied", "mandate_valid", "control_allows"),
    ("mission", "integrating", "reviewing"): (
        "integration_artifact_frozen", "all_work_jobs_done", "required_deliveries_applied",
    ),
    ("mission", "reviewing", "completed"): (
        "acceptance_verified", "integration_artifact_frozen",
        "all_work_jobs_done", "required_deliveries_applied",
    ),
    ("mission", "reviewing", "integrating"): ("repair_authorized", "repair_budget_available", "mandate_valid", "control_allows"),
    ("mission", "cancelling", "cancelled"): ("owned_operations_settled",),
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
    ),
    ("mission", "unknown", "completed"): (
        "reconciliation_verified", "acceptance_verified", "original_integration_settled",
        "integration_artifact_frozen", "all_work_jobs_done", "required_deliveries_applied",
    ),
    ("mission", "unknown", "blocked"): ("reconciliation_verified", "known_failure_or_human_decision"),
    ("mission", "unknown", "rejected"): ("reconciliation_verified", "explicit_plan_rejection"),
    ("mission", "unknown", "suppressed"): ("reconciliation_verified", "mandate_revoked"),
    ("mission", "unknown", "cancelled"): ("reconciliation_verified", "owned_operations_settled"),
    ("job", "pending", "ready"): ("dependencies_satisfied", "mandate_valid", "control_allows"),
    ("job", "ready", "running"): ("assignment_accepted", "control_allows"),
    ("job", "running", "checkpointed"): ("checkpoint_and_capture_frozen",),
    ("job", "running", "done"): ("role_result_validated",),
    ("job", "checkpointed", "running"): ("new_assignment_created", "checkpoint_and_capture_imported", "control_allows"),
    ("job", "unknown", "*"): ("reconciliation_verified",),
    ("job", "unknown", "running"): ("reconciliation_verified", "exact_original_assignment_active", "control_allows"),
    ("job", "unknown", "done"): ("reconciliation_verified", "role_result_validated"),
    ("job", "unknown", "blocked"): ("reconciliation_verified", "known_failure_or_human_decision"),
    ("job", "unknown", "cancelled"): ("reconciliation_verified",),
    ("assignment", "pending", "submitting"): ("stable_submission_persisted", "mandate_valid", "control_allows"),
    ("assignment", "submitting", "accepted"): ("harness_acceptance_confirmed",),
    ("assignment", "submitting", "rejected"): ("explicit_preexecution_rejection",),
    ("assignment", "accepted", "running"): ("exact_original_run_active", "control_allows"),
    ("assignment", "accepted", "capturing"): ("original_run_settled",),
    ("assignment", "accepted", "failed"): ("original_run_failure_confirmed",),
    ("assignment", "accepted", "interrupted"): ("original_run_interruption_confirmed",),
    ("assignment", "running", "capturing"): ("original_run_settled",),
    ("assignment", "running", "stopping"): ("cancel_accepted",),
    ("assignment", "running", "failed"): ("original_run_failure_confirmed",),
    ("assignment", "running", "interrupted"): ("original_run_interruption_confirmed",),
    ("assignment", "capturing", "succeeded"): ("output_frozen_and_validated",),
    ("assignment", "capturing", "failed"): ("capture_or_validation_failure_confirmed",),
    ("assignment", "stopping", "cancelled"): ("owned_operations_settled",),
    ("assignment", "stopping", "succeeded"): ("original_run_completed",),
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

    @staticmethod
    def _require_evidence_text(value: object, name: str) -> str:
        if (
            not isinstance(value, str) or not value.strip()
            or value != value.strip() or len(value) > 2048
        ):
            raise ValueError(f"{name} must be a trimmed evidence reference of at most 2048 characters")
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
        self._require_digest(artifact.get("tree_hash"), "integration_artifact.tree_hash")
        self._require_digest(artifact.get("manifest_hash"), "integration_artifact.manifest_hash")
        self._require_evidence_text(artifact.get("evidence_ref"), "integration_artifact.evidence_ref")

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
            self._require_digest(delivery.get("applied_tree_hash"), "delivery.applied_tree_hash")
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
            if source == "unknown" and self._mission_has_active_assignments(identity):
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
            if type(next_round) is not int or next_round != current["round_no"] + 1:
                raise ValueError("a repair round must advance round_no exactly once")
        if source == "unknown" and target == "planning" and facts.get("mandate_valid") is not True:
            raise PermissionError("resuming planning requires a currently valid Mandate")
        if source == "unknown" and target == "running" and facts.get("next_step_safe") is not True:
            raise PermissionError("resuming a Mission requires proof that the next step is safe")
        if target == "completed":
            if not self._mission_jobs_done(identity, current["plan_revision"]):
                raise PermissionError("Mission completion requires every Work Job and Assignment to be settled")
            if facts.get("integration_artifact_frozen") is not True or facts.get("required_deliveries_applied") is not True:
                raise PermissionError("Mission completion requires a frozen integration artifact and applied required Deliveries")

    def _validate_job(self, identity: str, current: dict, source: str, target: str,
                      facts: dict[str, bool], changes: dict[str, Any]) -> None:
        if target == "ready":
            mission = self._uow.missions.get(current["mission_id"])
            if mission is None or mission["state"] != "running":
                raise PermissionError("Job can become ready only while its Mission is running")
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
            if current["role"] not in {"work", "integrate"}:
                raise PermissionError("only resumable work or integration Jobs can be checkpointed")
            active_assignments = self._uow.db.execute(
                "SELECT COUNT(*) FROM assignments WHERE job_id=? "
                "AND state IN ('pending','submitting','accepted','running','capturing','stopping','unknown')",
                (identity,),
            ).fetchone()[0]
            if active_assignments:
                raise PermissionError("checkpointed requires the original Assignment to be settled")
        if source == "checkpointed" and target == "running":
            assignment = self._uow.db.execute(
                "SELECT 1 FROM assignments WHERE job_id=? AND state='running' AND run_id IS NOT NULL "
                "ORDER BY attempt_no DESC LIMIT 1",
                (identity,),
            ).fetchone()
            if assignment is None:
                raise PermissionError("checkpoint resume requires a new Assignment bound to its original Run")
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
            role_fact = {
                "plan": "plan_revision_frozen",
                "work": "artifact_frozen",
                "integrate": "artifact_frozen",
                "review": "review_verdict_and_evidence_frozen",
            }[current["role"]]
            if facts.get(role_fact) is not True:
                raise PermissionError(f"done requires role-specific frozen output: {role_fact}")
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
        if target == "succeeded":
            role_fact = {
                "plan": "plan_revision_frozen",
                "work": "artifact_frozen",
                "integrate": "artifact_frozen",
                "review": "review_verdict_and_evidence_frozen",
            }[job["role"]]
            if facts.get(role_fact) is not True:
                raise PermissionError(f"succeeded requires role-specific frozen output: {role_fact}")
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
        if source_state == "unknown":
            if not isinstance(audit_payload, dict):
                raise ValueError("unknown reconciliation requires a structured audit payload")
            self._require_evidence_text(audit_payload.get("reconciliation_ref"), "reconciliation_ref")
            if aggregate_type == "mission" and target_state in {"reviewing", "completed"}:
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

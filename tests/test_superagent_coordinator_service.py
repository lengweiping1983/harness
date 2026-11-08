"""下一代 Superagent 应用服务状态转移守卫回归。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness.app.superagent_coordinator_service import CoordinatorApplicationService
from harness.app.superagent_coordinator_store import CoordinatorStore


class CoordinatorApplicationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CoordinatorStore(
            Path(self.temp.name) / "superagent.sqlite3",
            instance_id="service-test",
            identity={"profile": "test"},
        )

    def _create_ticket(self, uow, suffix="1"):
        return uow.tickets.create({
            "ticket_id": f"ticket-{suffix}",
            "mandate_id": f"mandate-{suffix}",
            "origin_trigger_id": f"trigger-{suffix}",
            "title": "Test ticket",
        })

    def _create_mission(self, uow, suffix="1"):
        ticket = self._create_ticket(uow, suffix)
        return uow.missions.create({
            "mission_id": f"mission-{suffix}",
            "ticket_id": ticket["ticket_id"],
            "sequence": 1,
            "repo": "D:/repo",
            "base_commit": "a" * 40,
        })

    def _create_plan(self, uow, mission_id):
        return uow.plans.create(
            mission_id, expected_mission_revision=1,
            plan={"nodes": [{
                "node_id": "work", "role": "work", "profile_id": "coder",
            }]},
            acceptance={"criteria": ["result verified"]},
            created_by_kind="human", created_by_ref="actor:test",
        )

    def test_authorization_is_required_before_transition(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            self._create_mission(uow)
            service = CoordinatorApplicationService(uow)
            with self.assertRaises(PermissionError):
                service.transition(
                    "mission", "mission-1", expected_revision=1,
                    target_state="running", facts={},
                )

    def test_mission_cannot_run_without_a_persisted_plan_revision(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            self._create_mission(uow)
            service = CoordinatorApplicationService(uow)
            with self.assertRaises(PermissionError):
                service.transition(
                    "mission", "mission-1", expected_revision=1,
                    target_state="running",
                    facts={"authorized": True, "plan_valid": True, "base_commit_frozen": True, "mandate_valid": True, "control_allows": True},
                )

    def test_mission_plan_acceptance_requires_frozen_base_and_audit_is_atomic(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            self._create_mission(uow)
            self._create_plan(uow, "mission-1")
            service = CoordinatorApplicationService(uow)
            with self.assertRaises(PermissionError):
                service.transition(
                    "mission", "mission-1", expected_revision=2,
                    target_state="running",
                    facts={"authorized": True, "plan_valid": True, "mandate_valid": True, "control_allows": True},
                )
            changed = service.transition(
                "mission", "mission-1", expected_revision=2,
                target_state="running",
                facts={"authorized": True, "plan_valid": True, "base_commit_frozen": True, "mandate_valid": True, "control_allows": True},
                event_type="mission.plan_accepted",
            )
            self.assertEqual(changed["state"], "running")
            self.assertEqual(changed["aggregate_revision"], 3)
            self.assertEqual(
                uow.db.execute(
                    "SELECT event_type FROM audit_events WHERE aggregate_id='mission-1' ORDER BY seq DESC LIMIT 1"
                ).fetchone()[0],
                "mission.plan_accepted",
            )

    def test_unknown_mission_requires_reconciliation_before_resume(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            self._create_mission(uow)
            self._create_plan(uow, "mission-1")
            CoordinatorApplicationService(uow).transition(
                "mission", "mission-1", expected_revision=2, target_state="unknown",
                facts={"authorized": True, "operation_outcome_uncertain": True},
            )
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            service = CoordinatorApplicationService(uow)
            with self.assertRaises(PermissionError):
                service.transition(
                    "mission", "mission-1", expected_revision=3,
                    target_state="running",
                    facts={"authorized": True, "reconciliation_verified": True},
                )
            resume_facts = {
                "authorized": True, "reconciliation_verified": True,
                "original_run_settled": True, "mandate_valid": True,
                "control_allows": True, "next_step_safe": True,
            }
            with self.assertRaises(ValueError):
                service.transition(
                    "mission", "mission-1", expected_revision=3,
                    target_state="running", facts=resume_facts,
                )
            result = service.transition(
                "mission", "mission-1", expected_revision=3,
                target_state="running", facts=resume_facts,
                audit_payload={"reconciliation_ref": "evidence://mission/original-run-resume"},
            )
            self.assertEqual(result["state"], "running")
            event = uow.db.execute(
                "SELECT event_type,payload_json FROM audit_events "
                "WHERE aggregate_type='mission' AND aggregate_id='mission-1' ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(event["event_type"], "mission.reconciled.running")
            self.assertTrue(json.loads(event["payload_json"])["facts"]["reconciliation_verified"])

    def test_job_done_requires_role_specific_result_validation_and_hash(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._create_mission(uow)
            uow.jobs.create({
                "job_id": "job-1", "mission_id": mission["mission_id"],
                "plan_revision": 0, "node_id": "__system__plan", "role": "plan",
                "state": "running",
                "spec_hash": "b" * 64,
            })
            service = CoordinatorApplicationService(uow)
            with self.assertRaises(PermissionError):
                service.transition(
                    "job", "job-1", expected_revision=1, target_state="done",
                    facts={"authorized": True}, changes={"result_hash": "c" * 64},
                )
            with self.assertRaises(ValueError):
                service.transition(
                    "job", "job-1", expected_revision=1, target_state="done",
                    facts={"authorized": True, "role_result_validated": True},
                )
            result = service.transition(
                "job", "job-1", expected_revision=1, target_state="done",
                facts={"authorized": True, "role_result_validated": True, "plan_revision_frozen": True},
                changes={"result_hash": "c" * 64},
            )
            self.assertEqual(result["state"], "done")

    def test_assignment_unknown_requires_reconciliation_and_original_submission_proof(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            self._create_mission(uow, "assignment")
            uow.jobs.create({
                "job_id": "job-assignment", "mission_id": "mission-assignment",
                "plan_revision": 0, "node_id": "__system__plan", "role": "plan",
                "spec_hash": "d" * 64,
            })
            uow.assignments.create({
                "assignment_id": "assignment-1", "job_id": "job-assignment",
                "attempt_no": 1, "submission_key": "submission-1",
            })
            service = CoordinatorApplicationService(uow)
            submitting = service.transition(
                "assignment", "assignment-1", expected_revision=1,
                target_state="submitting",
                facts={"authorized": True, "stable_submission_persisted": True, "mandate_valid": True, "control_allows": True},
            )
            with self.assertRaises(PermissionError):
                service.transition(
                    "assignment", "assignment-1", expected_revision=2,
                    target_state="unknown", facts={"authorized": True},
                )
            uncertain = service.transition(
                "assignment", "assignment-1", expected_revision=2,
                target_state="unknown",
                facts={"authorized": True, "operation_outcome_uncertain": True},
            )
            with self.assertRaises(PermissionError):
                service.transition(
                    "assignment", "assignment-1", expected_revision=3,
                    target_state="rejected",
                    facts={"authorized": True, "reconciliation_verified": True},
                )
            rejection_facts = {
                "authorized": True, "reconciliation_verified": True,
                "original_submission_not_started": True,
            }
            with self.assertRaises(ValueError):
                service.transition(
                    "assignment", "assignment-1", expected_revision=3,
                    target_state="rejected", facts=rejection_facts,
                )
            rejected = service.transition(
                "assignment", "assignment-1", expected_revision=3,
                target_state="rejected", facts=rejection_facts,
                audit_payload={"reconciliation_ref": "evidence://assignment/submission-not-started"},
            )
            self.assertEqual((submitting["state"], uncertain["state"], rejected["state"]),
                             ("submitting", "unknown", "rejected"))

    def test_ticket_reopen_requires_clearing_old_terminal_fields(self):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            self._create_ticket(uow)
            CoordinatorApplicationService(uow).transition(
                "ticket", "ticket-1", expected_revision=1, target_state="ended",
                facts={
                    "authorized": True, "worker_follow_condition": True,
                    "follows_done": True,
                },
                changes={
                    "outcome": "completed", "ended_by": "executor",
                    "ended_at": "2026-11-04T16:00:00Z",
                },
            )
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            service = CoordinatorApplicationService(uow)
            with self.assertRaises(ValueError):
                service.transition(
                    "ticket", "ticket-1", expected_revision=2, target_state="open",
                    facts={"authorized": True, "human_followup": True, "mandate_valid": True},
                )
            reopened = service.transition(
                "ticket", "ticket-1", expected_revision=2, target_state="open",
                facts={"authorized": True, "human_followup": True, "mandate_valid": True},
                changes={"outcome": None, "ended_by": None, "ended_at": None},
            )
            self.assertEqual(reopened["state"], "open")
            self.assertIsNone(reopened["ended_at"])

    def test_assignment_running_requires_exact_original_run_identity(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            self._create_mission(uow, "run")
            uow.jobs.create({
                "job_id": "job-run", "mission_id": "mission-run",
                "plan_revision": 0, "node_id": "__system__plan", "role": "plan",
                "spec_hash": "f" * 64,
            })
            uow.assignments.create({
                "assignment_id": "assignment-run", "job_id": "job-run",
                "attempt_no": 1, "submission_key": "submission-run",
            })
            service = CoordinatorApplicationService(uow)
            service.transition(
                "assignment", "assignment-run", expected_revision=1,
                target_state="submitting",
                facts={"authorized": True, "stable_submission_persisted": True, "mandate_valid": True, "control_allows": True},
            )
            service.transition(
                "assignment", "assignment-run", expected_revision=2,
                target_state="accepted",
                facts={"authorized": True, "harness_acceptance_confirmed": True},
            )
            with self.assertRaises(PermissionError):
                service.transition(
                    "assignment", "assignment-run", expected_revision=3,
                    target_state="running",
                    facts={"authorized": True, "exact_original_run_active": True, "control_allows": True},
                )
            running = service.transition(
                "assignment", "assignment-run", expected_revision=3,
                target_state="running",
                facts={"authorized": True, "exact_original_run_active": True, "control_allows": True},
                changes={"run_id": "run-original"},
            )
            self.assertEqual((running["state"], running["run_id"]), ("running", "run-original"))

    def test_mission_cannot_integrate_until_every_work_job_is_done(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            self._create_mission(uow, "integrate")
            self._create_plan(uow, "mission-integrate")
            service = CoordinatorApplicationService(uow)
            service.transition(
                "mission", "mission-integrate", expected_revision=2,
                target_state="running",
                facts={"authorized": True, "plan_valid": True, "base_commit_frozen": True, "mandate_valid": True, "control_allows": True},
            )
            with self.assertRaises(PermissionError):
                service.transition(
                    "mission", "mission-integrate", expected_revision=3,
                    target_state="integrating",
                    facts={
                        "authorized": True, "all_work_jobs_done": True,
                        "required_deliveries_applied": True, "mandate_valid": True, "control_allows": True,
                    },
                )

    def test_outbox_state_must_be_advanced_by_linked_effect_service(self):
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            ticket = self._create_ticket(uow, "outbox-service")
            uow.outbox.create({
                "message_id": "message-service", "ticket_id": ticket["ticket_id"],
                "channel_id": "channel-1", "kind": "result",
                "body_ref": "artifact://body/service", "body_hash": "e" * 64,
                "idempotency_key": "outbox-service-key",
            })
            service = CoordinatorApplicationService(uow)
            with self.assertRaises(ValueError):
                service.transition(
                    "outbox_message", "message-service", expected_revision=1,
                    target_state="sending",
                    facts={"authorized": True, "effect_claimed": True},
                )


    def test_ticket_completion_requires_follow_condition_evidence(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            self._create_ticket(uow)
            service = CoordinatorApplicationService(uow)
            changes = {
                "outcome": "completed", "ended_by": "executor",
                "ended_at": "2026-11-04T16:00:00Z",
            }
            with self.assertRaises(PermissionError):
                service.transition(
                    "ticket", "ticket-1", expected_revision=1, target_state="ended",
                    facts={"authorized": True, "worker_follow_condition": True, "follows_done": False},
                    changes=changes,
                )
            with self.assertRaises(ValueError):
                service.transition(
                    "ticket", "ticket-1", expected_revision=1, target_state="ended",
                    facts={"authorized": True, "worker_follow_condition": True, "follows_done": True},
                    changes={**changes, "outcome": "failed"},
                )
            ended = service.transition(
                "ticket", "ticket-1", expected_revision=1, target_state="ended",
                facts={"authorized": True, "worker_follow_condition": True, "follows_done": True},
                changes=changes,
            )
            self.assertEqual(ended["outcome"], "completed")

    def test_mission_suppression_and_cancellation_require_host_facts(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            self._create_mission(uow, "control")
            service = CoordinatorApplicationService(uow)
            with self.assertRaises(PermissionError):
                service.transition(
                    "mission", "mission-control", expected_revision=1,
                    target_state="suppressed",
                    facts={"authorized": True, "mandate_revoked": False},
                )
            suppressed = service.transition(
                "mission", "mission-control", expected_revision=1,
                target_state="suppressed",
                facts={"authorized": True, "mandate_revoked": True, "mandate_unavailable": True},
            )
            self.assertEqual(suppressed["state"], "suppressed")

        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            self._create_mission(uow, "cancel")
            service = CoordinatorApplicationService(uow)
            with self.assertRaises(PermissionError):
                service.transition(
                    "mission", "mission-cancel", expected_revision=1,
                    target_state="cancelling", facts={"authorized": True},
                )
            cancelling = service.transition(
                "mission", "mission-cancel", expected_revision=1,
                target_state="cancelling",
                facts={"authorized": True, "cancel_accepted": True},
            )
            with self.assertRaises(PermissionError):
                service.transition(
                    "mission", "mission-cancel", expected_revision=2,
                    target_state="cancelled",
                    facts={"authorized": True, "owned_operations_settled": False},
                )
            cancelled = service.transition(
                "mission", "mission-cancel", expected_revision=2,
                target_state="cancelled",
                facts={"authorized": True, "owned_operations_settled": True},
            )
            self.assertEqual((cancelling["state"], cancelled["state"]), ("cancelling", "cancelled"))

    def test_running_assignment_requires_accepted_stop_and_run_identity_for_unknown(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            self._create_mission(uow, "stop")
            uow.jobs.create({
                "job_id": "job-stop", "mission_id": "mission-stop",
                "plan_revision": 0, "node_id": "__system__plan", "role": "plan",
                "spec_hash": "a" * 64,
            })
            uow.assignments.create({
                "assignment_id": "assignment-stop", "job_id": "job-stop",
                "attempt_no": 1, "submission_key": "submission-stop",
            })
            service = CoordinatorApplicationService(uow)
            service.transition(
                "assignment", "assignment-stop", expected_revision=1,
                target_state="submitting",
                facts={"authorized": True, "stable_submission_persisted": True,
                       "mandate_valid": True, "control_allows": True},
            )
            service.transition(
                "assignment", "assignment-stop", expected_revision=2,
                target_state="accepted",
                facts={"authorized": True, "harness_acceptance_confirmed": True},
            )
            service.transition(
                "assignment", "assignment-stop", expected_revision=3,
                target_state="running",
                facts={"authorized": True, "exact_original_run_active": True, "control_allows": True},
                changes={"run_id": "run-stop"},
            )
            with self.assertRaises(PermissionError):
                service.transition(
                    "assignment", "assignment-stop", expected_revision=4,
                    target_state="stopping", facts={"authorized": True},
                )
            stopping = service.transition(
                "assignment", "assignment-stop", expected_revision=4,
                target_state="stopping",
                facts={"authorized": True, "stop_requested": True, "mandate_valid": True, "control_allows": True},
            )
            self.assertEqual(stopping["state"], "stopping")

        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            self._create_mission(uow, "unknown-run")
            uow.jobs.create({
                "job_id": "job-unknown-run", "mission_id": "mission-unknown-run",
                "plan_revision": 0, "node_id": "__system__plan", "role": "plan",
                "spec_hash": "b" * 64,
            })
            uow.assignments.create({
                "assignment_id": "assignment-unknown-run", "job_id": "job-unknown-run",
                "attempt_no": 1, "submission_key": "submission-unknown-run",
                "state": "running",
            })
            service = CoordinatorApplicationService(uow)
            with self.assertRaises(PermissionError):
                service.transition(
                    "assignment", "assignment-unknown-run", expected_revision=1,
                    target_state="unknown",
                    facts={"authorized": True, "operation_outcome_uncertain": True},
                )

    def test_interrupted_work_cannot_checkpoint_or_resume_without_frozen_artifacts(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._create_mission(uow, "checkpoint")
            self._create_plan(uow, mission["mission_id"])
            job_id = uow.db.execute(
                "SELECT job_id FROM jobs WHERE mission_id=? AND role='work'",
                (mission["mission_id"],),
            ).fetchone()[0]
            service = CoordinatorApplicationService(uow)
            service.transition(
                "mission", mission["mission_id"], expected_revision=2,
                target_state="running",
                facts={"authorized": True, "plan_valid": True, "base_commit_frozen": True,
                       "mandate_valid": True, "control_allows": True},
            )
            service.transition(
                "job", job_id, expected_revision=1, target_state="ready",
                facts={"authorized": True, "dependencies_satisfied": True,
                       "mandate_valid": True, "control_allows": True},
            )
            uow.assignments.create({
                "assignment_id": "assignment-checkpoint-1", "job_id": job_id,
                "attempt_no": 1, "submission_key": "submission-checkpoint-1",
            })
            service.transition(
                "assignment", "assignment-checkpoint-1", expected_revision=1,
                target_state="submitting",
                facts={"authorized": True, "stable_submission_persisted": True,
                       "mandate_valid": True, "control_allows": True},
            )
            service.transition(
                "assignment", "assignment-checkpoint-1", expected_revision=2,
                target_state="accepted",
                facts={"authorized": True, "harness_acceptance_confirmed": True},
            )
            service.transition(
                "assignment", "assignment-checkpoint-1", expected_revision=3,
                target_state="running",
                facts={"authorized": True, "exact_original_run_active": True, "control_allows": True},
                changes={"run_id": "run-checkpoint-1"},
            )
            service.transition(
                "job", job_id, expected_revision=2, target_state="running",
                facts={"authorized": True, "assignment_accepted": True, "control_allows": True},
            )
            with self.assertRaises(PermissionError):
                service.transition(
                    "job", job_id, expected_revision=3, target_state="checkpointed",
                    facts={"authorized": True, "checkpoint_and_capture_frozen": True},
                )
            service.transition(
                "assignment", "assignment-checkpoint-1", expected_revision=4,
                target_state="interrupted",
                facts={"authorized": True, "original_run_interruption_confirmed": True},
            )
            with self.assertRaises(PermissionError):
                service.transition(
                    "job", job_id, expected_revision=3, target_state="checkpointed",
                    facts={"authorized": True, "checkpoint_and_capture_frozen": True},
                )
            # 模拟持久状态被绕过守卫改写，恢复仍须拒绝没有原成功产物的续跑。
            checkpointed = uow.jobs._transition(
                job_id, expected_revision=3, changes={"state": "checkpointed"},
                event_type="job.fixture.invalid_checkpoint",
            )
            self.assertEqual(checkpointed["state"], "checkpointed")
            uow.assignments.create({
                "assignment_id": "assignment-checkpoint-2", "job_id": job_id,
                "attempt_no": 2, "submission_key": "submission-checkpoint-2",
            })
            service.transition(
                "assignment", "assignment-checkpoint-2", expected_revision=1,
                target_state="submitting",
                facts={"authorized": True, "stable_submission_persisted": True,
                       "mandate_valid": True, "control_allows": True},
            )
            service.transition(
                "assignment", "assignment-checkpoint-2", expected_revision=2,
                target_state="accepted",
                facts={"authorized": True, "harness_acceptance_confirmed": True},
            )
            service.transition(
                "assignment", "assignment-checkpoint-2", expected_revision=3,
                target_state="running",
                facts={"authorized": True, "exact_original_run_active": True, "control_allows": True},
                changes={"run_id": "run-checkpoint-2"},
            )
            with self.assertRaises(PermissionError):
                service.transition(
                    "job", job_id, expected_revision=4, target_state="running",
                    facts={"authorized": True, "new_assignment_created": True,
                           "checkpoint_and_capture_imported": True, "control_allows": True},
                )
            self.assertEqual(uow.jobs.get(job_id)["state"], "checkpointed")


    def test_unknown_mission_completion_requires_a_persisted_passing_review(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._create_mission(uow, "complete")
            self._create_plan(uow, mission["mission_id"])
            job_id = uow.db.execute(
                "SELECT job_id FROM jobs WHERE mission_id=? AND role='work'",
                (mission["mission_id"],),
            ).fetchone()[0]
            CoordinatorApplicationService(uow).transition(
                "mission", mission["mission_id"], expected_revision=2,
                target_state="unknown",
                facts={"authorized": True, "operation_outcome_uncertain": True},
            )
            service = CoordinatorApplicationService(uow)
            base_facts = {
                "authorized": True, "reconciliation_verified": True,
                "acceptance_verified": True, "original_integration_settled": True,
                "integration_artifact_frozen": True,
                "all_work_jobs_done": True, "required_deliveries_applied": True,
                "mandate_valid": True, "control_allows": True,
            }
            with self.assertRaises(PermissionError):
                service.transition(
                    "mission", mission["mission_id"], expected_revision=3,
                    target_state="completed",
                    facts=base_facts,
                )
            uow.jobs._transition(
                job_id, expected_revision=1,
                changes={"state": "done", "result_hash": "d" * 64},
                event_type="job.result_frozen",
            )
            with self.assertRaises(PermissionError):
                service.transition(
                    "mission", mission["mission_id"], expected_revision=3,
                    target_state="completed",
                    facts=base_facts,
                    audit_payload={
                        "mission_id": mission["mission_id"], "plan_revision": 1,
                        "reconciliation_ref": "evidence://mission/acceptance-already-satisfied",
                        "original_integration": {
                            "assignment_id": "assignment-integrator-complete",
                            "run_id": "run-integrator-complete",
                            "outcome": "succeeded",
                            "evidence_ref": "evidence://integrator/complete",
                        },
                        "integration_artifact": {
                            "assignment_id": "assignment-integrator-complete",
                            "run_id": "run-integrator-complete",
                            "artifact_ref": "artifact://integration/complete",
                            "tree_hash": "a" * 64, "manifest_hash": "b" * 64,
                            "evidence_ref": "evidence://integration/complete",
                        },
                        "deliveries": [],
                    },
                )
            self.assertEqual(uow.missions.get(mission["mission_id"])["state"], "unknown")


    def test_unknown_mission_cannot_enter_reviewing_from_audit_payload_alone(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._create_mission(uow, "review-reconcile")
            uow.plans.create(
                mission["mission_id"], expected_mission_revision=1,
                plan={"nodes": [
                    {"node_id": "work-a", "role": "work", "profile_id": "coder"},
                    {"node_id": "work-b", "role": "work", "profile_id": "coder",
                     "dependencies": [{"node_id": "work-a", "requires_delivery": True}]},
                ]},
                acceptance={"criteria": ["result verified"]},
                created_by_kind="human", created_by_ref="actor:test",
            )
            jobs = {
                row["node_id"]: row["job_id"]
                for row in uow.db.execute(
                    "SELECT node_id,job_id FROM jobs WHERE mission_id=? AND plan_revision=1",
                    (mission["mission_id"],),
                ).fetchall()
            }
            service = CoordinatorApplicationService(uow)
            service.transition(
                "mission", mission["mission_id"], expected_revision=2,
                target_state="unknown",
                facts={"authorized": True, "operation_outcome_uncertain": True},
            )
            for index, job_id in enumerate(jobs.values(), start=1):
                uow.jobs._transition(
                    job_id, expected_revision=1,
                    changes={"state": "done", "result_hash": str(index) * 64},
                    event_type="job.result_frozen",
                )
            facts = {
                "authorized": True, "reconciliation_verified": True,
                "original_integration_settled": True, "integration_artifact_frozen": True,
                "all_work_jobs_done": True, "required_deliveries_applied": True,
                "mandate_valid": True, "control_allows": True,
            }
            audit_only_evidence = {
                "mission_id": mission["mission_id"], "plan_revision": 1,
                "reconciliation_ref": "evidence://mission/reconcile",
                "original_integration": {
                    "assignment_id": "assignment-not-persisted",
                    "run_id": "run-not-persisted", "outcome": "succeeded",
                    "evidence_ref": "evidence://integrator/not-persisted",
                },
                "integration_artifact": {
                    "assignment_id": "assignment-not-persisted",
                    "run_id": "run-not-persisted",
                    "artifact_ref": "artifact://integration/not-persisted",
                    "tree_hash": "a" * 64, "manifest_hash": "b" * 64,
                    "evidence_ref": "evidence://integration/not-persisted",
                },
                "deliveries": [],
            }
            with self.assertRaises(PermissionError):
                service.transition(
                    "mission", mission["mission_id"], expected_revision=3,
                    target_state="reviewing", facts=facts,
                    audit_payload=audit_only_evidence,
                )

    def test_unknown_job_cancellation_requires_exact_settled_assignment_inventory(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._create_mission(uow, "job-cancel")
            self._create_plan(uow, mission["mission_id"])
            job_id = uow.db.execute(
                "SELECT job_id FROM jobs WHERE mission_id=? AND role='work'",
                (mission["mission_id"],),
            ).fetchone()[0]
            service = CoordinatorApplicationService(uow)
            service.transition(
                "mission", mission["mission_id"], expected_revision=2,
                target_state="cancelling",
                facts={"authorized": True, "cancel_accepted": True},
            )
            uow.assignments.create({
                "assignment_id": "assignment-job-cancel", "job_id": job_id,
                "attempt_no": 1, "submission_key": "submission-job-cancel",
                "state": "rejected",
            })
            CoordinatorApplicationService(uow).transition(
                "job", job_id, expected_revision=1, target_state="unknown",
                facts={"authorized": True, "operation_outcome_uncertain": True},
            )
            facts = {
                "authorized": True, "reconciliation_verified": True,
                "owned_operations_settled": True,
            }
            uow.assignments.create({
                "assignment_id": "assignment-job-cancel-active", "job_id": job_id,
                "attempt_no": 2, "submission_key": "submission-job-cancel-active",
                "state": "unknown", "run_id": "run-job-cancel-active",
            })
            evidence = {
                "reconciliation_ref": "evidence://job/reconcile",
                "ownership_evidence_ref": "evidence://job/ownership",
                "settled_assignments": [
                    {"assignment_id": "assignment-job-cancel", "state": "rejected", "run_id": None},
                    {"assignment_id": "assignment-job-cancel-active",
                     "state": "unknown", "run_id": "run-job-cancel-active"},
                ],
            }
            with self.assertRaises(PermissionError):
                service.transition(
                    "job", job_id, expected_revision=2, target_state="cancelled",
                    facts=facts, audit_payload=evidence,
                )
            service.transition(
                "assignment", "assignment-job-cancel-active", expected_revision=1,
                target_state="cancelled",
                facts={
                    "authorized": True, "reconciliation_verified": True,
                    "original_run_cancelled": True, "owned_operations_settled": True,
                },
                audit_payload={
                    "reconciliation_ref": "evidence://assignment/job-cancel-active",
                    "run_id": "run-job-cancel-active",
                    "run_evidence_ref": "evidence://run/job-cancel-active",
                    "cancel_receipt_ref": "evidence://run/job-cancel-active-cancel",
                    "ownership_evidence_ref": "evidence://run/job-cancel-active-owned",
                },
            )
            evidence["settled_assignments"][1]["state"] = "cancelled"
            with self.assertRaises(PermissionError):
                service.transition(
                    "job", job_id, expected_revision=2, target_state="cancelled",
                    facts=facts, audit_payload={**evidence, "settled_assignments": []},
                )
            cancelled = service.transition(
                "job", job_id, expected_revision=2, target_state="cancelled",
                facts=facts, audit_payload=evidence,
            )
            self.assertEqual(cancelled["state"], "cancelled")
            event = uow.db.execute(
                "SELECT event_type,payload_json FROM audit_events "
                "WHERE aggregate_type='job' AND aggregate_id=? ORDER BY seq DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            self.assertEqual(event["event_type"], "job.reconciled.cancelled")
            self.assertEqual(
                json.loads(event["payload_json"])["evidence"]["settled_assignments"][0]["assignment_id"],
                "assignment-job-cancel",
            )

    def test_unknown_assignment_success_and_cancellation_bind_to_original_run_evidence(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            mission = self._create_mission(uow, "assignment-reconcile")
            job = uow.jobs.create({
                "job_id": "job-assignment-reconcile", "mission_id": mission["mission_id"],
                "plan_revision": 0, "node_id": "__system__plan", "role": "plan",
                "spec_hash": "f" * 64,
            })
            service = CoordinatorApplicationService(uow)

            def make_unknown(assignment_id, attempt_no, run_id):
                uow.assignments.create({
                    "assignment_id": assignment_id, "job_id": job["job_id"],
                    "attempt_no": attempt_no, "submission_key": f"submission-{assignment_id}",
                })
                service.transition(
                    "assignment", assignment_id, expected_revision=1, target_state="submitting",
                    facts={"authorized": True, "stable_submission_persisted": True,
                           "mandate_valid": True, "control_allows": True},
                )
                service.transition(
                    "assignment", assignment_id, expected_revision=2, target_state="accepted",
                    facts={"authorized": True, "harness_acceptance_confirmed": True},
                )
                service.transition(
                    "assignment", assignment_id, expected_revision=3, target_state="running",
                    facts={"authorized": True, "exact_original_run_active": True, "control_allows": True},
                    changes={"run_id": run_id},
                )
                service.transition(
                    "assignment", assignment_id, expected_revision=4, target_state="unknown",
                    facts={"authorized": True, "operation_outcome_uncertain": True},
                )

            make_unknown("assignment-run-success", 1, "run-original-success")
            success_evidence = {
                "reconciliation_ref": "evidence://assignment/success-reconcile",
                "run_id": "run-original-success",
                "run_evidence_ref": "evidence://run/success",
                "output_ref": "artifact://plan/frozen",
                "output_hash": "1" * 64,
            }
            success_facts = {
                "authorized": True, "reconciliation_verified": True,
                "original_run_completed": True, "output_frozen_and_validated": True,
                "plan_revision_frozen": True,
            }
            report = {"output_ref": "artifact://plan/frozen", "output_hash": "1" * 64}
            with self.assertRaises(PermissionError):
                service.transition(
                    "assignment", "assignment-run-success", expected_revision=5,
                    target_state="succeeded", facts=success_facts,
                    changes={"report_json": report},
                    audit_payload={**success_evidence, "run_id": "run-not-original"},
                )
            succeeded = service.transition(
                "assignment", "assignment-run-success", expected_revision=5,
                target_state="succeeded", facts=success_facts,
                changes={"report_json": report}, audit_payload=success_evidence,
            )
            self.assertEqual((succeeded["state"], succeeded["run_id"]), ("succeeded", "run-original-success"))

            make_unknown("assignment-run-cancel", 2, "run-original-cancel")
            cancel_evidence = {
                "reconciliation_ref": "evidence://assignment/cancel-reconcile",
                "run_id": "run-original-cancel",
                "run_evidence_ref": "evidence://run/cancel",
                "cancel_receipt_ref": "evidence://run/cancel-receipt",
                "ownership_evidence_ref": "evidence://run/owned-work-settled",
            }
            cancel_facts = {
                "authorized": True, "reconciliation_verified": True,
                "original_run_cancelled": True, "owned_operations_settled": True,
            }
            with self.assertRaises(PermissionError):
                service.transition(
                    "assignment", "assignment-run-cancel", expected_revision=5,
                    target_state="cancelled",
                    facts={**cancel_facts, "original_run_cancelled": False},
                    audit_payload=cancel_evidence,
                )
            cancelled = service.transition(
                "assignment", "assignment-run-cancel", expected_revision=5,
                target_state="cancelled", facts=cancel_facts,
                audit_payload=cancel_evidence,
            )
            self.assertEqual((cancelled["state"], cancelled["run_id"]), ("cancelled", "run-original-cancel"))
            event = uow.db.execute(
                "SELECT event_type,payload_json FROM audit_events "
                "WHERE aggregate_type='assignment' AND aggregate_id=? ORDER BY seq DESC LIMIT 1",
                ("assignment-run-cancel",),
            ).fetchone()
            self.assertEqual(event["event_type"], "assignment.reconciled.cancelled")
            self.assertEqual(json.loads(event["payload_json"])["evidence"]["run_id"], "run-original-cancel")

    def test_assignment_capture_failure_is_not_misreported_as_run_failure(self):
        with self.store.unit_of_work(actor={"kind": "system"}) as uow:
            self._create_mission(uow, "capture")
            uow.jobs.create({
                "job_id": "job-capture", "mission_id": "mission-capture",
                "plan_revision": 0, "node_id": "__system__plan", "role": "plan",
                "spec_hash": "9" * 64,
            })
            uow.assignments.create({
                "assignment_id": "assignment-capture", "job_id": "job-capture",
                "attempt_no": 1, "submission_key": "submission-capture",
            })
            service = CoordinatorApplicationService(uow)
            service.transition(
                "assignment", "assignment-capture", expected_revision=1,
                target_state="submitting",
                facts={"authorized": True, "stable_submission_persisted": True, "mandate_valid": True, "control_allows": True},
            )
            service.transition(
                "assignment", "assignment-capture", expected_revision=2,
                target_state="accepted",
                facts={"authorized": True, "harness_acceptance_confirmed": True},
                changes={"run_id": "run-capture"},
            )
            service.transition(
                "assignment", "assignment-capture", expected_revision=3,
                target_state="capturing",
                facts={"authorized": True, "original_run_settled": True},
            )
            with self.assertRaises(PermissionError):
                service.transition(
                    "assignment", "assignment-capture", expected_revision=4,
                    target_state="failed",
                    facts={"authorized": True, "original_run_failure_confirmed": True},
                )
            failed = service.transition(
                "assignment", "assignment-capture", expected_revision=4,
                target_state="failed",
                facts={"authorized": True, "capture_or_validation_failure_confirmed": True},
            )
            self.assertEqual(failed["state"], "failed")


if __name__ == "__main__":
    unittest.main()

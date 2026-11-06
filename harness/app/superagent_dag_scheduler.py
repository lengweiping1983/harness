"""Persistent DAG scheduling over the unified Coordinator execution boundary.

The policy object stays pure; this class owns the durable dispatch, artifact
handoff, recovery, and mission progression that make the policy executable.
"""
from __future__ import annotations

import asyncio
import json

from ..core.errors import HarnessError
from .superagent_coordinator_service import CoordinatorApplicationService
from .superagent_coordinator_store import _hash, _json, _now
from .superagent_executor import WorkerProfile


_ACTOR = {"kind": "host", "component": "superagent-v2-dag-scheduler"}
_ACTIVE_ASSIGNMENTS = ("pending", "submitting", "accepted", "running", "capturing", "stopping", "unknown")
_UNCERTAIN_ASSIGNMENTS = ("submitting", "capturing", "stopping", "unknown")


def _object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class CoordinatorDagScheduler:
    """Execute immutable Work DAGs through one bounded Scheduler policy."""

    def __init__(self, execution, policy):
        self._execution = execution
        self._policy = policy
        self._artifacts = None
        self._capacity_lock = asyncio.Lock()
        self._reserved_global = 0
        self._reserved_by_mission = {}

    def set_artifacts(self, artifacts):
        if artifacts is None or not callable(getattr(artifacts, "capture", None)) or not callable(
            getattr(artifacts, "deliver", None)
        ):
            raise TypeError("CoordinatorDagScheduler requires CoordinatorArtifacts")
        self._artifacts = artifacts

    def uses_artifact_workflow(self, assignment):
        e = self._execution
        mission = e._read_one("SELECT plan_revision FROM missions WHERE mission_id=?", (assignment["mission_id"],))
        if mission is None:
            raise LookupError("Assignment Mission not found")
        row = e._read_one(
            "SELECT acceptance_json FROM mission_plans WHERE mission_id=? AND plan_revision=?",
            (assignment["mission_id"], mission["plan_revision"]),
        )
        if row is None:
            raise HarnessError("Mission PlanRevision is missing", code="superagent/mission-corrupt")
        acceptance = _object(row.get("acceptance_json"))
        count = e._read_one(
            "SELECT COUNT(*) AS total FROM jobs WHERE mission_id=? AND plan_revision=? AND role='work'",
            (assignment["mission_id"], mission["plan_revision"]),
        )["total"]
        return not (acceptance.get("kind") == "single_worker_report" and count == 1)

    async def schedule_pending(self, *, limit=100):
        e = self._execution
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer between 1 and 200")
        if limit == 200:
            missions = e.active_mission_states(limit=limit)
            total = e._read_one(
                "SELECT COUNT(*) AS total FROM missions WHERE state IN "
                "('planning','running','integrating','reviewing','cancelling','unknown')",
                (),
            )["total"]
            backlog = total > limit
        else:
            missions = e.active_mission_states(limit=limit + 1)
            backlog = len(missions) > limit
        missions = missions[:limit]
        results, errors = [], []

        # Unknown Missions are reconciled first, but each Mission advances independently.
        ordered = [
            mission for mission in missions if mission.get("state") == "unknown"
        ] + self._policy.order_missions(missions)

        async def process(mission):
            try:
                return ("ok", await self.schedule_mission(mission["mission_id"]))
            except Exception as exc:
                return ("error", {
                    "mission_id": mission["mission_id"],
                    "code": getattr(exc, "code", type(exc).__name__),
                })

        # A slow Workspace/Session creation in one Mission must not block other Missions.
        outcomes = await asyncio.gather(*(process(mission) for mission in ordered))
        for kind, value in outcomes:
            if kind == "error":
                errors.append(value)
            else:
                results.extend(value)
        return {
            "processed": len(missions),
            "scheduled": sum(1 for item in results if item.get("state") in {"running", "accepted"}),
            "assignments": results,
            "errors": errors,
            "backlog": backlog,
        }

    async def schedule_mission(self, mission_id):
        e = self._execution
        async with e._lock(f"mission:{mission_id}"):
            return await self._schedule_mission_locked(mission_id)

    async def _schedule_mission_locked(self, mission_id):
        e = self._execution
        if self._artifacts is None:
            raise HarnessError("CoordinatorArtifacts is not connected", code="superagent/artifact-unavailable")
        mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        if mission is None:
            raise LookupError("Mission not found")
        if mission["state"] not in {"planning", "running", "unknown"}:
            return []

        self._finalize_terminal_assignments(mission_id)
        mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        plan, nodes, jobs, dependencies = self._load_plan(mission)
        if mission["state"] == "unknown":
            if not await self._reconcile_pending_deliveries(mission, jobs, dependencies):
                return [{"state": "unknown", "mission_id": mission_id}]
            self._advance_mission_state(mission_id)
            mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
            if mission["state"] == "integrating" or mission["state"] in {"blocked", "cancelled", "suppressed"}:
                return []
            if not self._resume_unknown_mission(mission_id):
                return [{"state": "unknown", "mission_id": mission_id}]
            mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        elif mission["state"] == "planning":
            e._admit_mission(mission_id)
            e._transition(
                "mission", mission_id, "running",
                facts={
                    "plan_valid": True, "base_commit_frozen": True,
                    "mandate_valid": True, "control_allows": True,
                },
            )
            mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))

        if mission["state"] != "running":
            return []
        ticket, trigger, mandate, default_profile, budget = self._mission_context(mission)
        self._advance_mission_state(mission_id)
        mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        if mission["state"] != "running":
            return []

        jobs = self._load_jobs(mission_id, mission["plan_revision"])
        dependencies = self._load_dependencies(mission_id, mission["plan_revision"])
        active = self._active_assignments(mission_id)
        active_by_job = {}
        for assignment in active:
            active_by_job.setdefault(assignment["job_id"], []).append(assignment)
        dispatchable = {
            job["node_id"] for job in jobs
            if not active_by_job.get(job["job_id"])
            or all(item["state"] == "pending" for item in active_by_job[job["job_id"]])
        }
        ready = self._policy.select_ready_jobs(jobs, dependencies, dispatchable_node_ids=dispatchable)
        reserved = [
            job for job in ready
            if any(item["state"] == "pending" for item in active_by_job.get(job["job_id"], []))
        ]
        new_jobs = [job for job in ready if not active_by_job.get(job["job_id"])]
        mission_active = len(active)
        slots = await self._reserve_slots(mission_id, mission_active, len(new_jobs))
        reserved_new_jobs = new_jobs[:slots]
        reserved_job_ids = {job["job_id"] for job in reserved_new_jobs}
        reservations_remaining = slots
        candidates = reserved + reserved_new_jobs
        results = []

        try:
            for candidate in candidates:
                mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
                if mission is None or mission["state"] != "running":
                    break
                assignment = self._assignment_for_job(candidate["job_id"])
                if assignment is None:
                    used = self._count_mission_assignments(mission_id)
                    if used >= budget["max_turns"]:
                        self._block_job(candidate, "mission-turn-budget-exhausted")
                        continue
                reserved_candidate = candidate["job_id"] in reserved_job_ids
                try:
                    try:
                        result = await self._start_job(
                            mission, candidate, nodes, ticket, trigger, mandate, default_profile, budget,
                        )
                    except HarnessError as exc:
                        if exc.code == "superagent/mission-paused":
                            results.append({"state": "queued", "mission_id": mission_id, "job_id": candidate["job_id"]})
                            break
                        raise
                    if result is not None:
                        results.append(result)
                finally:
                    if reserved_candidate:
                        await self._release_slots(mission_id, 1)
                        reservations_remaining -= 1
            self._advance_mission_state(mission_id)
            return results
        finally:
            if reservations_remaining:
                await self._release_slots(mission_id, reservations_remaining)

    def _load_plan(self, mission):
        e = self._execution
        row = e._read_one(
            "SELECT plan_json,acceptance_json FROM mission_plans WHERE mission_id=? AND plan_revision=?",
            (mission["mission_id"], mission["plan_revision"]),
        )
        if row is None:
            raise HarnessError("Mission PlanRevision is missing", code="superagent/mission-corrupt")
        plan = _object(row.get("plan_json"))
        acceptance = _object(row.get("acceptance_json"))
        if not isinstance(plan.get("nodes"), list) or not plan["nodes"]:
            raise HarnessError("Mission PlanRevision has no nodes", code="superagent/mission-corrupt")
        return {**plan, "acceptance": acceptance}, {
            node["node_id"]: node for node in plan["nodes"] if isinstance(node, dict)
        }, self._load_jobs(mission["mission_id"], mission["plan_revision"]), self._load_dependencies(
            mission["mission_id"], mission["plan_revision"],
        )

    def _load_jobs(self, mission_id, revision):
        return self._execution._read_all(
            "SELECT * FROM jobs WHERE mission_id=? AND plan_revision=? ORDER BY node_id",
            (mission_id, revision),
        )

    def _load_dependencies(self, mission_id, revision):
        return self._execution._read_all(
            "SELECT child.node_id AS node_id,parent.node_id AS depends_on_node_id,dependency.requires_delivery "
            "FROM job_dependencies AS dependency "
            "JOIN jobs AS child ON child.job_id=dependency.job_id "
            "JOIN jobs AS parent ON parent.job_id=dependency.depends_on_job_id "
            "WHERE dependency.mission_id=? AND dependency.plan_revision=? "
            "ORDER BY child.node_id,dependency.ordinal",
            (mission_id, revision),
        )

    def _mission_context(self, mission):
        e = self._execution
        ticket = e._authorization.ticket(mission["ticket_id"])
        trigger = e._sources.get_trigger(mission["goal_ref"])
        input_row = e._input(mission["ticket_id"], mission["goal_ref"])
        if ticket is None or trigger is None or input_row is None:
            raise HarnessError("Mission origin context is missing", code="superagent/context-corrupt")
        mandate, profile, budget = e._mandate_context(ticket, trigger, input_row, require_work=False)
        return ticket, trigger, mandate, profile, budget

    def _profile_for_job(self, mission, job, trigger, mandate, default_profile, budget):
        e = self._execution
        profile = e._profiles.get(job.get("profile_id"))
        if not isinstance(profile, WorkerProfile):
            raise PermissionError("Plan references a WorkerProfile not owned by this Host")
        scope = mandate.get("scope") or {}
        source_id = trigger.get("source_instance_id")
        if profile.repo != mission["repo"] or source_id not in profile.source_instance_ids:
            raise PermissionError("Plan WorkerProfile does not share the frozen Mission repository and source scope")
        if not set(profile.actions) <= set(scope.get("actions", [])):
            raise PermissionError("Plan WorkerProfile actions exceed the Ticket Mandate")
        if not set(profile.tools) <= set(scope.get("tools", [])):
            raise PermissionError("Plan WorkerProfile tools exceed the Ticket Mandate")
        mission_budget = _object(mission.get("budgets_json"))
        max_seconds = min(
            profile.max_seconds,
            budget["max_seconds"],
            mission_budget.get("max_seconds", budget["max_seconds"]),
        )
        max_turns = min(
            profile.max_turns,
            budget["max_turns"],
            mission_budget.get("max_turns", budget["max_turns"]),
        )
        if max_seconds < 1 or max_turns < 1:
            raise PermissionError("Plan WorkerProfile has no remaining execution budget")
        return profile, {"max_seconds": max_seconds, "max_turns": max_turns}

    def _node_prompt(self, base_prompt, node):
        instruction = node.get("instruction")
        instruction_hash = node.get("instruction_hash")
        if instruction is None:
            if instruction_hash not in {None, _hash(base_prompt)}:
                raise HarnessError(
                    "Plan instruction reference cannot be resolved to the frozen prompt",
                    code="superagent/instruction-unavailable",
                )
            return base_prompt
        if (not isinstance(instruction, str) or not instruction.strip()
                or len(instruction.encode("utf-8")) > 32768
                or instruction_hash != _hash(instruction)):
            raise HarnessError("Plan node instruction is invalid or does not match its hash", code="superagent/plan-invalid")
        return base_prompt + "\n\nWork unit instruction:\n" + instruction

    def _assignment_for_job(self, job_id):
        row = self._execution._read_one(
            "SELECT assignment_id FROM assignments WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1",
            (job_id,),
        )
        return self._execution._assignment(row["assignment_id"]) if row is not None else None

    def _active_assignments(self, mission_id):
        marks = ",".join("?" for _ in _ACTIVE_ASSIGNMENTS)
        return self._execution._read_all(
            "SELECT assignment.* FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            f"WHERE job.mission_id=? AND assignment.state IN ({marks}) "
            "ORDER BY COALESCE(assignment.started_at,''),assignment.assignment_id",
            (mission_id, *_ACTIVE_ASSIGNMENTS),
        )

    def _count_active_assignments(self):
        marks = ",".join("?" for _ in _ACTIVE_ASSIGNMENTS)
        return self._execution._read_one(
            f"SELECT COUNT(*) AS total FROM assignments WHERE state IN ({marks})",
            _ACTIVE_ASSIGNMENTS,
        )["total"]

    async def _reserve_slots(self, mission_id, mission_active, requested):
        async with self._capacity_lock:
            mission_reserved = self._reserved_by_mission.get(mission_id, 0)
            slots = self._policy.available_slots(
                active_assignments=self._count_active_assignments() + self._reserved_global,
                mission_active_assignments=mission_active + mission_reserved,
                requested=requested,
            )
            if slots:
                self._reserved_global += slots
                self._reserved_by_mission[mission_id] = mission_reserved + slots
            return slots

    async def _release_slots(self, mission_id, count):
        if type(count) is not int or count < 1:
            raise ValueError("reservation release count must be a positive integer")
        async with self._capacity_lock:
            mission_reserved = self._reserved_by_mission.get(mission_id, 0)
            if count > self._reserved_global or count > mission_reserved:
                raise RuntimeError("DAG Scheduler capacity reservation underflow")
            self._reserved_global -= count
            mission_reserved -= count
            if mission_reserved:
                self._reserved_by_mission[mission_id] = mission_reserved
            else:
                self._reserved_by_mission.pop(mission_id, None)

    def _count_mission_assignments(self, mission_id):
        return self._execution._read_one(
            "SELECT COUNT(*) AS total FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "WHERE job.mission_id=? AND assignment.state<>'rejected'",
            (mission_id,),
        )["total"]

    async def _start_job(self, mission, job, nodes, ticket, trigger, mandate, default_profile, budget):
        e = self._execution
        node = nodes.get(job["node_id"])
        if node is None:
            raise HarnessError("Plan Job has no immutable node specification", code="superagent/mission-corrupt")
        profile, job_budget = self._profile_for_job(
            mission, job, trigger, mandate, default_profile, budget,
        )
        e._admit_mission(mission["mission_id"])
        worker = await e._ensure_worker(
            ticket["ticket_id"], profile, True, mission=mission, job=job, stage=1,
        )
        assignment = self._assignment_for_job(job["job_id"])
        if assignment is not None and assignment["state"] != "pending":
            return None
        if assignment is None:
            base_prompt, input_ids = e._build_prompt(ticket, trigger, mandate)
            prompt = self._node_prompt(base_prompt, node)
            assignment = self._create_pending_assignment(mission, job, worker, prompt, input_ids)
            assignment = e._assignment(assignment["assignment_id"])
        else:
            worker = e._worker_row(assignment["worker_id"])
            if worker is None or worker["state"] != "ready":
                raise HarnessError("Pending Assignment Worker is unavailable", code="superagent/worker-unavailable")
            pending = _object(assignment.get("report_json"))
            prompt = pending.get("pending_prompt")
            input_ids = pending.get("pending_input_ids", [])
            if not isinstance(prompt, str) or not isinstance(input_ids, list):
                raise HarnessError("Pending Assignment prompt snapshot is corrupt", code="superagent/assignment-corrupt")
            if assignment["worker_id"] != worker["worker_id"] or worker["profile_id"] != profile.profile_id:
                raise HarnessError("Pending Assignment Worker does not match its frozen Plan", code="superagent/worker-conflict")

        required_artifacts = self._required_artifacts(job, mission["mission_id"])
        if required_artifacts:
            try:
                applied = await self._artifacts.deliver(assignment, worker, required_artifacts)
            except Exception as exc:
                self._mark_mission_unknown(
                    mission["mission_id"], assignment["assignment_id"],
                    getattr(exc, "code", type(exc).__name__),

                )
                return {"state": "unknown", "mission_id": mission["mission_id"], "assignment_id": assignment["assignment_id"]}
            if not applied:
                self._block_job(job, "artifact-delivery-conflict", assignment=assignment)
                self._advance_mission_state(mission["mission_id"])
                return {"state": "blocked", "mission_id": mission["mission_id"], "job_id": job["job_id"]}

        e._admit_mission(mission["mission_id"])
        with e._store.unit_of_work(
            actor=_ACTOR, correlation_id=f"mission:{mission['mission_id']}:job:{job['job_id']}",
        ) as uow:
            app = CoordinatorApplicationService(uow)
            current_mission = uow.missions.get(mission["mission_id"])
            current_job = uow.jobs.get(job["job_id"])
            current_assignment = uow.assignments.get(assignment["assignment_id"])
            if current_mission is None or current_mission["state"] != "running":
                raise HarnessError("Mission is not admitted for dispatch", code="superagent/mission-paused")
            if current_job["state"] == "pending":
                facts = {
                    "authorized": True, "dependencies_satisfied": True,
                    "mandate_valid": True, "control_allows": True,
                }
                if required_artifacts:
                    facts["required_deliveries_applied"] = True
                current_job = app.transition(
                    "job", current_job["job_id"], expected_revision=current_job["aggregate_revision"],
                    target_state="ready", facts=facts,
                )
            if current_assignment["state"] == "pending":
                current_assignment = app.transition(
                    "assignment", current_assignment["assignment_id"],
                    expected_revision=current_assignment["aggregate_revision"],
                    target_state="submitting",
                    facts={
                        "authorized": True, "stable_submission_persisted": True,
                        "mandate_valid": True, "control_allows": True,
                    },
                )
        assignment = {**e._assignment(assignment["assignment_id"]), "max_seconds": job_budget["max_seconds"]}
        try:
            await e._executor.submit(worker, assignment, prompt, input_ids)
        except Exception:
            # The stable submission key is the only identity; a lost acknowledgement
            # is resolved by reconcile(), never by submitting a second Run.
            pass
        return await e.reconcile(assignment["assignment_id"])

    def _create_pending_assignment(self, mission, job, worker, prompt, input_ids):
        e = self._execution
        assignment_id = "assignment_" + _hash(f"{mission['mission_id']}\0{job['node_id']}\0{1}")[:32]
        with e._store.unit_of_work(
            actor=_ACTOR, correlation_id=f"mission:{mission['mission_id']}:job:{job['job_id']}",
        ) as uow:
            old = uow.assignments.get(assignment_id)
            if old is not None:
                return old
            current_mission = uow.missions.get(mission["mission_id"])
            current_job = uow.jobs.get(job["job_id"])
            if current_mission is None or current_mission["state"] != "running":
                raise HarnessError("Mission is not admitted for Assignment creation", code="superagent/mission-paused")
            assignment = uow.assignments.create({
                "assignment_id": assignment_id, "job_id": job["job_id"], "attempt_no": 1,
                "worker_id": worker["worker_id"], "session_id": worker["session_id"],
                "workspace_id": worker["workspace_id"], "submission_key": f"superagent-v2:{assignment_id}",
                "report_json": {"pending_prompt": prompt, "pending_input_ids": input_ids},
            })
            for input_id in input_ids:
                row = uow.db.execute("SELECT state FROM ticket_inputs WHERE input_id=?", (input_id,)).fetchone()
                if row is not None and row["state"] == "waiting":
                    e._mark_input_db(
                        uow.db, input_id, "delivered", assignment_id=assignment_id,
                        delivery_key=f"superagent-v2:input:{input_id}:{assignment_id}",
                    )
        return e._assignment(assignment_id)

    def _required_artifacts(self, job, mission_id):
        dependencies = self._execution._read_all(
            "SELECT dependency.depends_on_job_id,dependency.requires_delivery "
            "FROM job_dependencies AS dependency WHERE dependency.job_id=? "
            "ORDER BY dependency.ordinal",
            (job["job_id"],),
        )
        artifacts = []
        for dependency in dependencies:
            if not dependency["requires_delivery"]:
                continue
            source = self._execution._read_one(
                "SELECT assignment.assignment_id FROM assignments AS assignment "
                "JOIN jobs AS job ON job.job_id=assignment.job_id "
                "WHERE job.job_id=? AND assignment.state='succeeded' "
                "ORDER BY assignment.attempt_no DESC LIMIT 1",
                (dependency["depends_on_job_id"],),
            )
            if source is None:
                raise HarnessError("Completed dependency has no succeeded source Assignment", code="superagent/artifact-missing")
            artifact = self._artifacts.get(source["assignment_id"])
            if artifact is None:
                raise HarnessError("Completed dependency has no frozen Artifact", code="superagent/artifact-missing")
            artifacts.append(artifact)
        return artifacts

    async def _reconcile_pending_deliveries(self, mission, jobs, dependencies):
        for job in jobs:
            if job["state"] not in {"pending", "ready"}:
                continue
            assignment = self._assignment_for_job(job["job_id"])
            if assignment is None or assignment["state"] != "pending":
                continue
            required = self._required_artifacts(job, mission["mission_id"])
            if not required:
                continue
            key = "superagent:delivery:" + assignment["assignment_id"]
            effect = self._execution._read_one(
                "SELECT * FROM effects WHERE kind='artifact.import' AND idempotency_key=?", (key,),
            )
            if effect is None or effect["state"] not in {"leased", "unknown", "completed"}:
                continue
            worker = self._execution._worker_row(assignment["worker_id"])
            if worker is None or worker["state"] != "ready":
                return False
            try:
                applied = await self._artifacts.deliver(assignment, worker, required)
            except Exception:
                return False
            if not applied:
                self._block_job(job, "artifact-delivery-conflict", assignment=assignment)
        return True

    def _mark_mission_unknown(self, mission_id, assignment_id, reason):
        e = self._execution
        mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        if mission is not None and mission["state"] == "running":
            e._transition(
                "mission", mission_id, "unknown",
                facts={"operation_outcome_uncertain": True},
                changes={"outcome": "artifact_delivery_unknown"},
                audit_payload={
                    "reconciliation_ref": f"artifact-delivery:{assignment_id}",
                    "assignment_id": assignment_id, "reason": str(reason)[:256],
                },
            )

    def _has_uncertain_assignments(self, mission_id, *, exclude_assignment_id=None):
        e = self._execution
        marks = ",".join("?" for _ in _UNCERTAIN_ASSIGNMENTS)
        rows = e._read_all(
            "SELECT assignment.assignment_id FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            f"WHERE job.mission_id=? AND assignment.state IN ({marks})",
            (mission_id, *_UNCERTAIN_ASSIGNMENTS),
        )
        return any(row["assignment_id"] != exclude_assignment_id for row in rows)

    def _has_uncertain_effects(self, mission_id, *, allow_capture_assignment_id=None):
        e = self._execution
        rows = e._read_all(
            "SELECT effect.kind,effect.state,effect.aggregate_id FROM effects AS effect "
            "JOIN assignments AS assignment ON assignment.assignment_id=effect.aggregate_id "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "WHERE job.mission_id=? AND effect.state IN ('leased','unknown')",
            (mission_id,),
        )
        for row in rows:
            if (row["aggregate_id"] == allow_capture_assignment_id
                    and row["kind"] == "artifact.capture"):
                continue
            return True
        return False

    def _resume_unknown_mission(self, mission_id, *, allow_capture_assignment_id=None):
        e = self._execution
        mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        if mission is None or mission["state"] != "unknown":
            return mission is not None and mission["state"] == "running"
        if self._has_uncertain_assignments(mission_id, exclude_assignment_id=allow_capture_assignment_id):
            return False
        if self._has_uncertain_effects(mission_id, allow_capture_assignment_id=allow_capture_assignment_id):
            return False
        ticket = e._authorization.ticket(mission["ticket_id"])
        mandate = (ticket or {}).get("mandate") or {}
        mandate_id = mandate.get("mandate_id")

        if not mandate_id or e._authorization.revalidate_mandate(mandate_id, actor=_ACTOR).get("status") not in {"valid", "affected"}:
            return False
        if not e._control.allows(mission_id):
            return False
        e._transition(
            "mission", mission_id, "running",
            facts={
                "reconciliation_verified": True, "original_run_settled": True,
                "mandate_valid": True, "control_allows": True, "next_step_safe": True,
            },
            audit_payload={
                "reconciliation_ref": f"mission:{mission_id}:dag-next-step",
                "plan_revision": mission["plan_revision"],
            },
        )
        return True

    async def finish_work_success(self, assignment, snapshot, evidence_ids):
        e = self._execution
        current = e._assignment(assignment["assignment_id"])
        if current is None:
            raise LookupError("Assignment not found")
        job = e._read_one("SELECT * FROM jobs WHERE job_id=?", (current["job_id"],))
        if job is None:
            raise LookupError("Assignment Job not found")
        mission_id = job["mission_id"]
        if current["state"] in {"accepted", "running"}:
            current = e._transition(
                "assignment", current["assignment_id"], "capturing",
                facts={"original_run_settled": True},
            )
        elif current["state"] == "stopping":
            current = e._transition(
                "assignment", current["assignment_id"], "unknown",
                facts={"operation_outcome_uncertain": True},
                changes={"error_ref": f"artifact-capture-after-stop:{current['assignment_id']}"},
                audit_payload={"reconciliation_ref": f"assignment:{current['assignment_id']}:capture-after-stop"},
            )
        if current["state"] not in {"capturing", "unknown"}:
            return {"state": current["state"], "assignment_id": current["assignment_id"]}
        # State transitions return the raw Assignment row; restore its trusted
        # Mission context before crossing the Artifact service boundary.
        current = e._assignment(current["assignment_id"])
        if current is None:
            raise LookupError("Assignment disappeared during artifact capture")
        if not self._resume_unknown_mission(
            mission_id,
            allow_capture_assignment_id=current["assignment_id"] if current["state"] == "unknown" else None,
        ):
            mission = e._read_one("SELECT state FROM missions WHERE mission_id=?", (mission_id,))
            if mission and mission["state"] == "unknown":
                return {"state": "unknown", "assignment_id": current["assignment_id"]}
        try:
            e._admit_mission(mission_id)
            artifact = await self._artifacts.capture(current, e._worker_row(current["worker_id"]))
        except HarnessError as exc:
            if exc.code == "superagent/mission-paused":
                return {"state": "capturing", "assignment_id": current["assignment_id"]}
            if exc.code == "superagent/mandate-unavailable":
                self._fail_capture(current, "mandate-unavailable")
                return {"state": "failed", "assignment_id": current["assignment_id"]}
            e._mark_unknown(current, f"artifact capture outcome is uncertain: {exc.code}", error_ref=f"artifact-capture:{current['assignment_id']}")
            return {"state": "unknown", "assignment_id": current["assignment_id"]}
        except Exception as exc:
            e._mark_unknown(current, f"artifact capture outcome is uncertain: {type(exc).__name__}", error_ref=f"artifact-capture:{current['assignment_id']}")
            return {"state": "unknown", "assignment_id": current["assignment_id"]}

        report_body = {
            "kind": "work_artifact_report", "assignment_id": current["assignment_id"],
            "run_id": current["run_id"], "artifact_id": artifact["artifact_id"],
            "manifest_hash": artifact["manifest_hash"], "tree_hash": artifact["tree_hash"],
            "commit_id": artifact["commit_id"], "output_ref": f"artifact:{artifact['artifact_id']}",
            "output_hash": artifact["manifest_hash"], "evidence_ids": list(evidence_ids),
            "run_output_hash": snapshot.get("output_hash"),
        }
        report_hash = _hash(_json(report_body))
        report = {**report_body, "report_hash": report_hash}
        assignment_facts = {"output_frozen_and_validated": True, "artifact_frozen": True}
        audit = None
        if current["state"] == "unknown":
            assignment_facts.update({"reconciliation_verified": True, "original_run_completed": True})
            audit = {
                "reconciliation_ref": f"assignment:{current['assignment_id']}:artifact-frozen",
                "artifact_id": artifact["artifact_id"], "manifest_hash": artifact["manifest_hash"],
            }
        e._transition(
            "assignment", current["assignment_id"], "succeeded",
            facts=assignment_facts,
            changes={"ended_at": _now(), "report_json": report},
            audit_payload=audit,
        )
        self.finalize_succeeded_assignment(e._assignment(current["assignment_id"]))
        self._advance_mission_state(mission_id)
        return {"state": "succeeded", "assignment_id": current["assignment_id"], "artifact_id": artifact["artifact_id"]}

    def finalize_succeeded_assignment(self, assignment):
        e = self._execution
        current = e._assignment(assignment["assignment_id"])
        if current is None or current["state"] != "succeeded":
            return
        report = _object(current.get("report_json"))
        if report.get("kind") != "work_artifact_report":
            return
        artifact = self._artifacts.get(current["assignment_id"])
        if (artifact is None or artifact["artifact_id"] != report.get("artifact_id")
                or artifact["manifest_hash"] != report.get("manifest_hash")
                or artifact["tree_hash"] != report.get("tree_hash")):
            raise HarnessError("Succeeded Work Assignment lost its frozen Artifact", code="superagent/artifact-corrupt")
        job = e._read_one("SELECT * FROM jobs WHERE job_id=?", (current["job_id"],))
        if job is None or job["state"] == "done":
            return
        if job["state"] not in {"ready", "running", "unknown"}:
            raise HarnessError("Succeeded Artifact Assignment has a non-completable Job", code="superagent/job-state-conflict")
        facts = {
            "role_result_validated": True, "artifact_frozen": True, "report_frozen": True,
        }
        audit = None
        if job["state"] == "unknown":
            facts["reconciliation_verified"] = True
            audit = {
                "reconciliation_ref": f"job:{job['job_id']}:artifact-frozen",
                "artifact_id": artifact["artifact_id"], "manifest_hash": artifact["manifest_hash"],
            }
        e._transition(
            "job", job["job_id"], "done", facts=facts,
            changes={"result_hash": artifact["manifest_hash"]},
            audit_payload=audit,
        )

    def _fail_capture(self, assignment, reason):
        e = self._execution
        current = e._assignment(assignment["assignment_id"])
        if current["state"] == "capturing":
            e._transition(
                "assignment", current["assignment_id"], "failed",
                facts={"capture_or_validation_failure_confirmed": True},
                changes={"ended_at": _now(), "error_ref": reason,
                         "report_json": {"kind": "artifact_capture_failure", "reason": reason}},
            )
        job = e._read_one("SELECT * FROM jobs WHERE job_id=?", (current["job_id"],))
        if job and job["state"] in {"ready", "running", "unknown"}:
            facts = {"known_failure_or_human_decision": True}
            if job["state"] == "unknown":
                facts["reconciliation_verified"] = True
            e._transition("job", job["job_id"], "blocked", facts=facts)
        if job is not None:
            self._advance_mission_state(job["mission_id"])

    def _block_job(self, job, reason, *, assignment=None):
        e = self._execution
        current_assignment = assignment or self._assignment_for_job(job["job_id"])
        if current_assignment is not None and current_assignment["state"] == "pending":
            # A never-started Assignment has no started_at; the schema therefore
            # requires its terminal cancellation to keep ended_at unset.
            e._transition(
                "assignment", current_assignment["assignment_id"], "cancelled",
                facts={"no_external_side_effect_started": True},
                changes={"error_ref": reason},
            )
        current_job = e._read_one("SELECT * FROM jobs WHERE job_id=?", (job["job_id"],))
        if current_job and current_job["state"] in {"pending", "ready", "running", "unknown"}:
            facts = {"known_failure_or_human_decision": True}
            if current_job["state"] == "unknown":
                facts["reconciliation_verified"] = True
            e._transition("job", current_job["job_id"], "blocked", facts=facts)

    def _finalize_terminal_assignments(self, mission_id):
        e = self._execution
        rows = e._read_all(
            "SELECT assignment.* FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "WHERE job.mission_id=? AND assignment.state IN ('succeeded','failed','interrupted','rejected','cancelled','blocked') "
            "ORDER BY COALESCE(assignment.started_at,''),assignment.assignment_id",
            (mission_id,),
        )
        for assignment in rows:
            job = e._read_one("SELECT * FROM jobs WHERE job_id=?", (assignment["job_id"],))
            if job is None:
                continue
            if assignment["state"] == "succeeded":
                if job["state"] in {"blocked", "cancelled"}:
                    continue
                report = _object(assignment.get("report_json"))
                if report.get("kind") == "single_worker_report":
                    # The shared scheduler also recovers frozen single-node Report effects.
                    e._finalize_report_effects(assignment, report)
                else:
                    self.finalize_succeeded_assignment(assignment)
                continue
            if job["state"] in {"done", "blocked", "cancelled"}:
                continue
            if assignment["state"] in {"failed", "interrupted", "rejected", "blocked"}:
                facts = {"known_failure_or_human_decision": True}
                if job["state"] == "unknown":
                    facts["reconciliation_verified"] = True
                if job["state"] in {"pending", "ready", "running", "unknown"}:
                    e._transition("job", job["job_id"], "blocked", facts=facts)
            elif assignment["state"] == "cancelled":
                mission = e._read_one("SELECT state FROM missions WHERE mission_id=?", (mission_id,))
                if mission and mission["state"] in {"cancelling", "cancelled"} and job["state"] in {"pending", "ready", "running", "unknown"}:
                    facts = {"owned_operations_settled": True}
                    if job["state"] == "unknown":
                        facts["reconciliation_verified"] = True
                    e._transition("job", job["job_id"], "cancelled", facts=facts)

    def _required_deliveries_applied(self, mission_id):
        e = self._execution
        row = e._read_one(
            "SELECT COUNT(*) AS total FROM job_dependencies AS dependency "
            "JOIN jobs AS child ON child.job_id=dependency.job_id "
            "WHERE child.mission_id=? AND dependency.requires_delivery=1 "
            "AND NOT EXISTS (SELECT 1 FROM deliveries AS delivery "
            "JOIN assignments AS target ON target.assignment_id=delivery.target_assignment_id "
            "WHERE target.job_id=dependency.job_id AND delivery.artifact_id IN "
            "(SELECT artifact.artifact_id FROM artifacts AS artifact "
            "JOIN assignments AS source ON source.assignment_id=artifact.assignment_id "
            "WHERE source.job_id=dependency.depends_on_job_id) AND delivery.state='applied')",
            (mission_id,),
        )
        return row["total"] == 0

    def _advance_mission_state(self, mission_id):
        e = self._execution
        mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        if mission is None or mission["state"] not in {"running", "unknown"}:
            return
        if self._active_assignments(mission_id):
            return
        jobs = self._load_jobs(mission_id, mission["plan_revision"])
        dependencies = self._load_dependencies(mission_id, mission["plan_revision"])
        all_work_done = all(job["state"] == "done" for job in jobs if job["role"] == "work")
        required_applied = self._required_deliveries_applied(mission_id)
        if all_work_done and required_applied:
            ticket = e._authorization.ticket(mission["ticket_id"])
            mandate = (ticket or {}).get("mandate") or {}
            status = e._authorization.revalidate_mandate(mandate.get("mandate_id"), actor=_ACTOR) if mandate.get("mandate_id") else {}
            if status.get("status") not in {"valid", "affected"} or not e._control.allows(mission_id):
                return
            facts = {
                "all_work_jobs_done": True, "required_deliveries_applied": True,
                "mandate_valid": True, "control_allows": True,
            }
            audit = None
            if mission["state"] == "unknown":
                facts.update({"reconciliation_verified": True, "original_delivery_applied": True})
                audit = {
                    "reconciliation_ref": f"mission:{mission_id}:all-work-artifacts-delivered",
                    "plan_revision": mission["plan_revision"],
                }
            e._transition("mission", mission_id, "integrating", facts=facts, audit_payload=audit)
            return
        if any(job["state"] == "unknown" for job in jobs):
            return
        ready = self._policy.select_ready_jobs(jobs, dependencies)
        if ready:
            return
        if any(job["state"] in {"blocked", "cancelled"} for job in jobs):
            facts = {"known_failure_or_human_decision": True}
            audit = None
            if mission["state"] == "unknown":
                facts["reconciliation_verified"] = True
                audit = {"reconciliation_ref": f"mission:{mission_id}:work-dag-blocked"}
            e._transition("mission", mission_id, "blocked", facts=facts, audit_payload=audit)

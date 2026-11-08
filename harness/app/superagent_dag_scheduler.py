"""Persistent DAG scheduling over the unified Coordinator execution boundary.

The policy object stays pure; this class owns the durable dispatch, artifact
handoff, recovery, and mission progression that make the policy executable.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from ..core.errors import HarnessError
from .superagent_coordinator_service import CoordinatorApplicationService
from .superagent_coordinator_store import _hash, _json, _now
from .superagent_executor import WorkerProfile
from .superagent_checkpoints import validate_checkpoint, frozen_work_checkpoint
from .superagent_common import _json as strict_loads, _text
from ..i18n import tr


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

def _run_started_at(snapshot):
    """优先使用原生 Run 的开始时间；缺失时记录首次确认完成的时间。"""
    reference = snapshot.get("run_reference")
    value = reference.get("started_at") if isinstance(reference, dict) else None
    if type(value) in {int, float} and value > 0:
        try:
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    return _now()


class CoordinatorDagScheduler:
    """Execute immutable Work DAGs through one bounded Scheduler policy."""

    def __init__(self, execution, policy, *, watchdog=None):
        self._execution = execution
        self._policy = policy
        self._watchdog = watchdog
        self._artifacts = None
        self._mission_instructions = None
        self._capacity_lock = asyncio.Lock()
        self._reserved_global = 0
        self._reserved_by_mission = {}

    def set_artifacts(self, artifacts):
        if artifacts is None or not callable(getattr(artifacts, "capture", None)) or not callable(
            getattr(artifacts, "deliver", None)
        ):
            raise TypeError("CoordinatorDagScheduler requires CoordinatorArtifacts")
        self._artifacts = artifacts

    def set_mission_instructions(self, instructions):
        """由同一宿主装配指令事实读取，不从旧引导文件推断上下文。"""
        self._mission_instructions = instructions

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

        # 先回查未知 Mission；各 Mission 独立推进，避免未知结果阻塞正常派工。
        unknown = [
            mission for mission in missions if mission.get("state") == "unknown"
        ]
        unknown_ids = {mission["mission_id"] for mission in unknown}
        ordered = unknown + [
            mission for mission in self._policy.order_missions(missions)
            if mission["mission_id"] not in unknown_ids
        ]

        async def process(mission):
            try:
                return ("ok", await self.schedule_mission(mission["mission_id"]))
            except Exception as exc:
                return ("error", {
                    "mission_id": mission["mission_id"],
                    "code": getattr(exc, "code", type(exc).__name__),
                })

        # 单个 Mission 的 Workspace / Session 创建较慢时，不阻塞其他 Mission。
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
        if mission["state"] not in {"planning", "running", "integrating", "reviewing", "unknown"}:
            return []

        self._finalize_terminal_assignments(mission_id)
        mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        plan, nodes, jobs, dependencies = self._load_plan(mission)
        if mission["state"] == "unknown":
            if not await self._reconcile_pending_deliveries(mission, jobs, dependencies):
                return [{"state": "unknown", "mission_id": mission_id}]
            self._advance_mission_state(mission_id)
            mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
            if mission["state"] == "unknown":
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

        if mission["state"] not in {"running", "integrating", "reviewing"}:
            return []
        ticket, trigger, mandate, default_profile, budget = self._mission_context(mission)
        self._advance_mission_state(mission_id)
        mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        if mission["state"] not in {"running", "integrating", "reviewing"}:
            return []
        if self._has_attention(mission_id):
            return []
        stage_state = mission["state"]
        allowed_roles = {"running": {"work"}, "integrating": {"integrate"}, "reviewing": {"review"}}[stage_state]

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
        ready = self._policy.select_ready_jobs(
            jobs, dependencies, dispatchable_node_ids=dispatchable, allowed_roles=allowed_roles,
        )
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
                if mission is None or mission["state"] != stage_state:
                    break
                assignment = self._assignment_for_job(candidate["job_id"])
                retrying_checkpoint = candidate["state"] == "checkpointed" and assignment is not None and assignment["state"] == "succeeded"
                if assignment is None or retrying_checkpoint:
                    used = self._count_mission_assignments(mission_id)
                    if used >= budget["max_turns"]:
                        self._block_job(candidate, "mission-turn-budget-exhausted")
                        continue
                reserved_candidate = candidate["job_id"] in reserved_job_ids
                reservation_held = reserved_candidate

                async def release_reservation():
                    nonlocal reservation_held, reservations_remaining
                    if reservation_held:
                        await self._release_slots(mission_id, 1)
                        reservations_remaining -= 1
                        reservation_held = False

                try:
                    try:
                        result = await self._start_job(
                            mission, candidate, nodes, ticket, trigger, mandate, default_profile, budget,
                            on_assignment_persisted=release_reservation if reserved_candidate else None,
                        )
                    except HarnessError as exc:
                        if exc.code == "superagent/mission-paused":
                            results.append({"state": "queued", "mission_id": mission_id, "job_id": candidate["job_id"]})
                            break
                        raise
                    if result is not None:
                        results.append(result)
                finally:
                    if reservation_held:
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
            "SELECT plan_json,acceptance_json,acceptance_hash FROM mission_plans WHERE mission_id=? AND plan_revision=?",
            (mission["mission_id"], mission["plan_revision"]),
        )
        if row is None:
            raise HarnessError("Mission PlanRevision is missing", code="superagent/mission-corrupt")
        plan = _object(row.get("plan_json"))
        acceptance_json = row.get("acceptance_json")
        acceptance = _object(acceptance_json)
        acceptance_hash = row.get("acceptance_hash")
        if not isinstance(acceptance_hash, str) or _hash(acceptance_json or "") != acceptance_hash:
            raise HarnessError("Mission PlanRevision Acceptance hash is missing or invalid", code="superagent/mission-corrupt")
        if not isinstance(plan.get("nodes"), list) or not plan["nodes"]:
            raise HarnessError("Mission PlanRevision has no nodes", code="superagent/mission-corrupt")
        return {**plan, "acceptance": acceptance, "acceptance_hash": acceptance_hash}, {
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

    def _work_disposition_prompt(self, mission, input_ids):
        e = self._execution
        ticket = e._authorization.ticket(mission["ticket_id"])
        origin = (ticket or {}).get("origin_trigger_id")
        eligible = []
        if input_ids and isinstance(origin, str):
            marks = ",".join("?" for _ in input_ids)
            rows = e._read_all(
                "SELECT trigger_id FROM ticket_inputs WHERE ticket_id=? "
                f"AND input_id IN ({marks}) AND context_only=0 AND state<>'handed_back' "
                "AND trigger_id<>? ORDER BY created_at,input_id",
                (mission["ticket_id"], *input_ids, origin),
            )
            eligible = list(dict.fromkeys(row["trigger_id"] for row in rows))
        return tr("superagent.mission.work_disposition",
                  context=_json({"eligible_handback_trigger_ids": eligible}))

    def _handback_status(self, assignment, trigger_id, reason):
        e = self._execution
        if not isinstance(trigger_id, str) or not trigger_id or not isinstance(reason, str) or not reason.strip():
            return "invalid"
        ticket = e._authorization.ticket(assignment["ticket_id"])
        if ticket is None or ticket.get("state") != "open" or ticket.get("origin_trigger_id") == trigger_id:
            return "invalid"
        row = e._input(assignment["ticket_id"], trigger_id)
        if row is None or row["context_only"]:
            return "invalid"
        if row["state"] == "handed_back":
            return "applied" if row.get("handback_reason") == reason else "invalid"
        delivery = e._read_one(
            "SELECT delivery.state FROM input_deliveries AS delivery "
            "JOIN assignments AS owner ON owner.assignment_id=delivery.assignment_id "
            "JOIN jobs AS job ON job.job_id=owner.job_id "
            "WHERE delivery.input_id=? AND delivery.assignment_id=? "
            "AND job.mission_id=? AND job.role='work'",
            (row["input_id"], assignment["assignment_id"], assignment["mission_id"]),
        )
        if delivery is None or delivery["state"] != "applied":
            return "invalid"
        other = e._read_one(
            "SELECT COUNT(*) AS total FROM input_deliveries WHERE input_id=? "
            "AND assignment_id<>? AND state NOT IN ('rejected','unapplied')",
            (row["input_id"], assignment["assignment_id"]),
        )
        return "ready" if other["total"] == 0 else "shared"

    def _role_prompt(self, base_prompt, mission, job, attempt_no, *, input_ids=()):
        guidance = self._mission_instructions.applied_context(
            mission["mission_id"], job_id=job["job_id"] if job["role"] == "work" else None,
        ) if self._mission_instructions is not None else {"items": []}
        if job["role"] == "work":
            _, nodes, _, _ = self._load_plan(mission)
            maximum = nodes[job["node_id"]].get("max_work_turns")
            if maximum is not None:
                frame = {"mission_id": mission["mission_id"], "job_id": job["job_id"],
                         "instruction": base_prompt, "attempt": attempt_no,
                         "max_work_turns": maximum, "applied_guidance": guidance}
                if attempt_no > 1:
                    previous = self._assignment_for_job(job["job_id"])
                    with self._execution._store.transaction() as db:
                        frame["checkpoint"] = frozen_work_checkpoint(db, previous)
                return tr("superagent.mission.checkpoint", frame=_json(frame)) + "\n\n" + self._work_disposition_prompt(mission, input_ids)
            return (base_prompt + ("\n\n" + tr("superagent.mission.applied_context", context=_json(guidance))
                                  if guidance["items"] else "") + "\n\n"
                    + self._work_disposition_prompt(mission, input_ids))
        if guidance["items"]:
            base_prompt += "\n\n" + tr("superagent.mission.applied_context", context=_json(guidance))
        plan, _, _, _ = self._load_plan(mission)
        acceptance = plan["acceptance"]
        if job["role"] == "integrate":
            prompt = base_prompt + "\n\n" + tr('superagent.coordinator.integrator', criteria=_json(acceptance['criteria']))
            if attempt_no > 1:
                prior = self._execution._read_one(
                    "SELECT review.verdict_json FROM reviews AS review "
                    "JOIN assignments AS assignment ON assignment.assignment_id=review.reviewer_assignment_id "
                    "JOIN jobs AS reviewer ON reviewer.job_id=assignment.job_id "
                    "WHERE review.mission_id=? AND review.plan_revision=? AND reviewer.role='review' "
                    "ORDER BY assignment.attempt_no DESC LIMIT 1",
                    (mission["mission_id"], mission["plan_revision"]),
                )
                if prior is None:
                    raise HarnessError("Repair Integrator has no frozen failed Review", code="superagent/review-missing")
                prompt += "\n\n" + tr('superagent.coordinator.repair', review=prior['verdict_json'])
            return prompt

        if job["role"] != "review":
            raise HarnessError("Plan contains an unsupported system role", code="superagent/plan-invalid")
        candidate = self._execution._read_one(
            "SELECT artifact.artifact_id,artifact.manifest_hash,assignment.assignment_id "
            "FROM jobs AS integrator "
            "JOIN assignments AS assignment ON assignment.job_id=integrator.job_id "
            "JOIN artifacts AS artifact ON artifact.assignment_id=assignment.assignment_id "
            "WHERE integrator.mission_id=? AND integrator.plan_revision=? AND integrator.role='integrate' "
            "AND assignment.state='succeeded' ORDER BY assignment.attempt_no DESC LIMIT 1",
            (mission["mission_id"], mission["plan_revision"]),
        )
        if candidate is None:
            raise HarnessError("Reviewer has no frozen Integrator candidate", code="superagent/artifact-missing")
        evidence = self._execution._read_all(
            "SELECT evidence.evidence_id,evidence.evidence_type,job.role,job.node_id "
            "FROM evidence JOIN assignments ON assignments.assignment_id=evidence.assignment_id "
            "JOIN jobs AS job ON job.job_id=assignments.job_id "
            "WHERE job.mission_id=? AND job.plan_revision=? AND job.role IN ('work','integrate') "
            "AND (job.role='work' OR assignments.assignment_id=?) "
            "ORDER BY evidence.evidence_id LIMIT 500",
            (mission["mission_id"], mission["plan_revision"], candidate["assignment_id"]),
        )
        contract = {
            "schema_version": 1, "candidate_artifact_id": candidate["artifact_id"],
            "acceptance_hash": plan["acceptance_hash"], "criteria": acceptance["criteria"],
            "available_evidence": evidence,
            "output_shape": {
                "schema_version": 1, "candidate_artifact_id": candidate["artifact_id"],
                "acceptance_hash": plan["acceptance_hash"], "verdict": "passed|failed",
                "criteria": [{"id": "criterion-id", "status": "passed|failed",
                              "reason": "specific finding", "evidence_ids": ["persisted-evidence-id"]}],
            },
        }
        return base_prompt + "\n\n" + tr('superagent.coordinator.reviewer', contract=_json(contract))

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

    async def _start_job(self, mission, job, nodes, ticket, trigger, mandate, default_profile, budget, *, on_assignment_persisted=None):
        e = self._execution
        node = nodes.get(job["node_id"])
        if node is None:
            raise HarnessError("Plan Job has no immutable node specification", code="superagent/mission-corrupt")
        profile, job_budget = self._profile_for_job(
            mission, job, trigger, mandate, default_profile, budget,
        )
        assignment = self._assignment_for_job(job["job_id"])
        attempt_no = 1 if assignment is None else assignment["attempt_no"]
        if assignment is not None and assignment["state"] != "pending":
            if job["state"] == "checkpointed" and assignment["state"] == "succeeded":
                attempt_no = assignment["attempt_no"] + 1
                assignment = None
            else:
                return None
        maximum = node.get("max_work_turns") if job["role"] == "work" else None
        if maximum is not None and attempt_no > maximum:
            self._block_job(job, "work-stage-budget-exhausted")
            return None
        e._admit_mission(mission["mission_id"])
        worker = await e._ensure_worker(
            ticket["ticket_id"], profile, True, mission=mission, job=job, stage=attempt_no,
        )
        if assignment is None:
            base_prompt, input_ids = e._build_prompt(ticket, trigger, mandate)
            # DAG 的每个 Assignment 都要保留独立输入身份，不能依赖首次消费的全局状态。
            selected_inputs = e._read_all(
                "SELECT input_id FROM ticket_inputs WHERE ticket_id=? "
                "AND state IN ('waiting','delivered','read') "
                "AND (context_only=1 OR trigger_id=?) ORDER BY created_at,input_id",
                (ticket["ticket_id"], trigger["trigger_id"]),
            )
            input_ids = list(dict.fromkeys([
                *input_ids, *(item["input_id"] for item in selected_inputs),
            ]))
            if maximum is not None:
                node_prompt = node.get("work_instruction")
                if not isinstance(node_prompt, dict):
                    raise HarnessError(tr("superagent.corrupt"), code="superagent/plan-corrupt")
            else:
                node_prompt = self._node_prompt(base_prompt, node)
            prompt = self._role_prompt(node_prompt, mission, job, attempt_no, input_ids=input_ids)
            assignment = self._create_pending_assignment(
                mission, job, worker, prompt, input_ids, attempt_no=attempt_no,
            )
            assignment = e._assignment(assignment["assignment_id"])
            if on_assignment_persisted is not None:
                await on_assignment_persisted()
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
            except HarnessError as exc:
                if exc.code == "superagent/mission-paused":
                    raise
                if exc.code == "superagent/mandate-unavailable":
                    self._block_job(job, "mandate-unavailable", assignment=assignment)
                    self._advance_mission_state(mission["mission_id"])
                    return {
                        "state": "blocked", "mission_id": mission["mission_id"],
                        "job_id": job["job_id"], "assignment_id": assignment["assignment_id"],
                    }
                self._mark_mission_unknown(
                    mission["mission_id"], assignment["assignment_id"], exc.code,
                )
                return {"state": "unknown", "mission_id": mission["mission_id"], "assignment_id": assignment["assignment_id"]}
            except Exception as exc:
                self._mark_mission_unknown(
                    mission["mission_id"], assignment["assignment_id"], type(exc).__name__,
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
            expected_stage = {"work": "running", "integrate": "integrating", "review": "reviewing"}.get(current_job["role"])
            if current_mission is None or expected_stage is None or current_mission["state"] != expected_stage:
                raise HarnessError("Mission is not admitted for this Job role", code="superagent/mission-paused")
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

    def _create_pending_assignment(self, mission, job, worker, prompt, input_ids, *, attempt_no=1):
        e = self._execution
        assignment_id = "assignment_" + _hash(f"{mission['mission_id']}\0{job['node_id']}\0{attempt_no}")[:32]
        with e._store.unit_of_work(
            actor=_ACTOR, correlation_id=f"mission:{mission['mission_id']}:job:{job['job_id']}",
        ) as uow:
            old = uow.assignments.get(assignment_id)
            if old is not None:
                return old
            current_mission = uow.missions.get(mission["mission_id"])
            current_job = uow.jobs.get(job["job_id"])
            expected_stage = {"work": "running", "integrate": "integrating", "review": "reviewing"}.get(current_job["role"])
            if current_mission is None or expected_stage is None or current_mission["state"] != expected_stage:
                raise HarnessError("Mission is not admitted for Assignment creation", code="superagent/mission-paused")
            assignment = uow.assignments.create({
                "assignment_id": assignment_id, "job_id": job["job_id"], "attempt_no": attempt_no,
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
                elif row is not None and row["state"] in {"delivered", "read"}:
                    # 已由其他节点消费的 Input 仍需为本节点固定独立回执。
                    delivery = e._input_deliveries.prepare_in_transaction(
                        uow.db, input_id, assignment_id, actor=_ACTOR,
                        delivery_key=f"superagent-v2:input:{input_id}:{assignment_id}",
                        initial_state="submitting",
                    )
                    if delivery["state"] not in {"pending", "submitting"}:
                        raise HarnessError("DAG InputDelivery state conflicts with its original submission",
                                           code="superagent/input-delivery-conflict")
                else:
                    raise HarnessError("DAG input is missing or no longer deliverable",
                                       code="superagent/context-corrupt")
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
        if job["role"] in {"work", "integrate"} and job["state"] == "checkpointed":
            # 新轮次 pending Assignment 已可能落库，必须按成功状态查找冻结的旧候选。
            previous = self._execution._read_one(
                "SELECT assignment_id FROM assignments WHERE job_id=? AND state='succeeded' "
                "ORDER BY attempt_no DESC LIMIT 1",
                (job["job_id"],),
            )
            artifact = self._artifacts.get(previous["assignment_id"]) if previous else None
            if artifact is None:
                raise HarnessError("Checkpointed Integrator has no frozen prior candidate", code="superagent/artifact-missing")
            if all(item["artifact_id"] != artifact["artifact_id"] for item in artifacts):
                artifacts.append(artifact)
        return artifacts

    async def _reconcile_pending_deliveries(self, mission, jobs, dependencies):
        for job in jobs:
            if job["state"] not in {"pending", "ready", "checkpointed"}:
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
        if mission is not None and mission["state"] in {"running", "integrating", "reviewing"}:
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

    def _has_inflight_assignments(self, mission_id, *, exclude_assignment_id=None):
        e = self._execution
        rows = e._read_all(
            "SELECT assignment.assignment_id FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "WHERE job.mission_id=? AND assignment.state IN "
            "('submitting','accepted','running','capturing','stopping','unknown')",
            (mission_id,),
        )
        return any(row["assignment_id"] != exclude_assignment_id for row in rows)

    def _mission_authorization(self, mission):
        e = self._execution
        ticket = e._authorization.ticket(mission["ticket_id"])
        mandate = (ticket or {}).get("mandate") or {}
        mandate_id = mandate.get("mandate_id")
        status = (
            e._authorization.revalidate_mandate(mandate_id, actor=_ACTOR)
            if mandate_id else {}
        )
        control_allows = e._control.allows(mission["mission_id"])
        if control_allows and self._watchdog is not None:
            observed = self._watchdog.get(mission["mission_id"])
            # 仅显式达到 Watchdog blocked 的安全升级阻止继续；stalled 只是关注。
            if observed is not None and observed["state"] == "blocked":
                control_allows = False
        return status.get("status") in {"valid", "affected"}, control_allows

    def _mission_authorization_in_transaction(self, mission, db):
        """在同一协调写事务中复核来源授权、持久控制及安全 Watchdog 状态。"""
        e = self._execution
        ticket = db.execute(
            "SELECT mandate_id FROM tickets WHERE ticket_id=?", (mission["ticket_id"],),
        ).fetchone()
        mandate = (
            db.execute("SELECT * FROM mandates WHERE mandate_id=?", (ticket["mandate_id"],)).fetchone()
            if ticket is not None else None
        )
        if mandate is None:
            return False, False
        # 在当前事务的 db 上复用授权引擎的原判定，不能嵌套发起写事务。
        status = e._authorization._mandate_status(db, mandate, actor=_ACTOR, now=_now())
        control_allows = e._control._mode(db, mission["mission_id"])["mode"] == "running"
        if control_allows and self._watchdog is not None:
            snapshot = db.execute(
                "SELECT * FROM watchdog_snapshots WHERE mission_id=?", (mission["mission_id"],),
            ).fetchone()
            if (snapshot is not None
                    and self._watchdog._decode(snapshot, self._watchdog.limits)["state"] == "blocked"):
                control_allows = False
        return status in {"valid", "affected"}, control_allows

    def admit_artifact_effect(self, mission_id):
        """新 Artifact Effect 前先核对持久 Watchdog，避免 blocked 期间反复认领无效租约。"""
        mission = self._execution._admit_mission(mission_id)
        if self._watchdog is not None:
            observed = self._watchdog.get(mission_id)
            if observed is not None and observed["state"] == "blocked":
                raise HarnessError(
                    "Watchdog blocks a new Artifact Effect",
                    code="superagent/mission-paused",
                )
        return mission

    def admit_artifact_effect_in_transaction(self, db, assignment, operation):
        """在 Effect 租约后、原生 Git 调用前原子复核 Mission、Plan 与授权。"""
        row = db.execute(
            "SELECT assignment.*,job.mission_id AS mission_id,job.role AS job_role,job.plan_revision AS job_plan_revision,"
            "mission.state AS mission_state,mission.plan_revision AS mission_plan_revision,"
            "mission.ticket_id FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "JOIN missions AS mission ON mission.mission_id=job.mission_id "
            "WHERE assignment.assignment_id=?",
            (assignment["assignment_id"],),
        ).fetchone()
        if row is None or any(
            row[field] != assignment.get(field)
            for field in ("assignment_id", "job_id", "worker_id", "workspace_id", "session_id", "run_id")
        ):
            raise HarnessError("Artifact Effect no longer matches its Assignment", code="superagent/assignment-conflict")
        if (row["mission_plan_revision"] != row["job_plan_revision"]
                or assignment.get("plan_revision") != row["mission_plan_revision"]):
            raise HarnessError("Artifact Effect PlanRevision is no longer current", code="superagent/mission-corrupt")
        expected_stage = {"work": "running", "integrate": "integrating", "review": "reviewing"}.get(row["job_role"])
        if expected_stage is None or row["mission_state"] != expected_stage:
            raise HarnessError("Mission stage no longer admits this Artifact Effect", code="superagent/mission-paused")
        if operation == "artifact.capture":
            if row["job_role"] not in {"work", "integrate"} or row["state"] not in {"capturing", "unknown"} or not row["run_id"]:
                raise HarnessError("Artifact capture requires the settled original Run", code="superagent/artifact-run-unsettled")
        elif operation == "artifact.import":
            if row["state"] != "pending" or row["run_id"] is not None:
                raise HarnessError("Artifact import requires an unstarted Assignment", code="superagent/delivery-already-started")
        else:
            raise ValueError("unsupported Artifact Effect operation")
        mandate_valid, control_allows = self._mission_authorization_in_transaction(row, db)
        if not control_allows:
            raise HarnessError("Mission control or Watchdog blocks this Artifact Effect", code="superagent/mission-paused")
        if not mandate_valid:
            raise HarnessError("Mission Mandate no longer authorizes this Artifact Effect", code="superagent/mandate-unavailable")

    @staticmethod
    def _frozen_integration_resume_evidence(db, mission, candidate):
        """从当前 Mission 的真实 Run、Artifact 与 applied Delivery 构造只读恢复证据。"""
        original_run = db.execute(
            "SELECT evidence_id FROM evidence WHERE assignment_id=? AND evidence_type='run' "
            "ORDER BY evidence_id LIMIT 1",
            (candidate["assignment_id"],),
        ).fetchone()
        if original_run is None or not candidate.get("run_id"):
            return None
        dependencies = db.execute(
            "SELECT job_id,depends_on_job_id FROM job_dependencies "
            "WHERE mission_id=? AND plan_revision=? AND requires_delivery=1 "
            "ORDER BY job_id,depends_on_job_id",
            (mission["mission_id"], mission["plan_revision"]),
        ).fetchall()
        deliveries = []
        for dep in dependencies:
            source = db.execute(
                "SELECT artifact.artifact_id FROM assignments AS assignment "
                "JOIN artifacts AS artifact ON artifact.assignment_id=assignment.assignment_id "
                "WHERE assignment.job_id=? AND assignment.state='succeeded' "
                "ORDER BY assignment.attempt_no DESC LIMIT 1",
                (dep["depends_on_job_id"],),
            ).fetchone()
            target = db.execute(
                "SELECT assignment_id FROM assignments WHERE job_id=? "
                "ORDER BY attempt_no DESC LIMIT 1",
                (dep["job_id"],),
            ).fetchone()
            if source is None or target is None:
                return None
            delivery = db.execute(
                "SELECT delivery_id,receipt_json FROM deliveries "
                "WHERE artifact_id=? AND target_assignment_id=? AND state='applied'",
                (source["artifact_id"], target["assignment_id"]),
            ).fetchone()
            if delivery is None:
                return None
            try:
                receipt = json.loads(delivery["receipt_json"])
            except (TypeError, ValueError):
                return None
            tree = receipt.get("tree") if isinstance(receipt, dict) else None
            if (not isinstance(tree, str) or len(tree) not in {40, 64}
                    or any(char not in "0123456789abcdef" for char in tree)):
                return None
            deliveries.append({
                "job_id": dep["job_id"], "depends_on_job_id": dep["depends_on_job_id"],
                "delivery_id": delivery["delivery_id"],
                "evidence_ref": f"delivery:{delivery['delivery_id']}:applied",
                "applied_tree_hash": tree,
            })
        return {
            "mission_id": mission["mission_id"], "plan_revision": mission["plan_revision"],
            "original_integration": {
                "assignment_id": candidate["assignment_id"], "run_id": candidate["run_id"],
                "outcome": "succeeded", "evidence_ref": original_run["evidence_id"],
            },
            "integration_artifact": {
                "assignment_id": candidate["assignment_id"], "run_id": candidate["run_id"],
                "artifact_ref": f"artifact:{candidate['artifact_id']}",
                "tree_hash": candidate["tree_hash"],
                "manifest_hash": candidate["manifest_hash"],
                "evidence_ref": f"artifact:{candidate['artifact_id']}:{candidate['manifest_hash']}",
            },
            "deliveries": deliveries,
        }

    def _resume_unknown_mission(
        self, mission_id, *, allow_capture_assignment_id=None, capture_run_evidence_ref=None,
    ):
        e = self._execution
        mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        if mission is None or mission["state"] != "unknown":
            return mission is not None and mission["state"] in {"running", "integrating", "reviewing"}
        if self._has_inflight_assignments(
            mission_id, exclude_assignment_id=allow_capture_assignment_id,
        ):
            return False
        if self._has_uncertain_effects(
            mission_id, allow_capture_assignment_id=allow_capture_assignment_id,
        ):
            return False

        mandate_valid, control_allows = self._mission_authorization(mission)
        if not mandate_valid or not control_allows:
            return False

        jobs = self._load_jobs(mission_id, mission["plan_revision"])
        # 唯一例外：本轮原生 Run 已明确完成、同一个 Artifact Effect 被持久证明
        # 从未进入外部 Git 调用，才允许把它对应的 unknown Job 恢复到原阶段。
        proven_capture = None
        recovery_job_id = None
        if (allow_capture_assignment_id and capture_run_evidence_ref
                and self._artifacts is not None):
            original_assignment = e._assignment(allow_capture_assignment_id)
            effect = self._artifacts._latest_effect(
                "artifact.capture", allow_capture_assignment_id,
            )
            original_evidence = e._read_one(
                "SELECT 1 FROM evidence WHERE evidence_id=? AND assignment_id=? "
                "AND run_id=? AND evidence_type='run'",
                (
                    capture_run_evidence_ref, allow_capture_assignment_id,
                    original_assignment["run_id"] if original_assignment else None,
                ),
            )
            if (original_assignment is not None and original_evidence is not None
                    and original_assignment["mission_id"] == mission_id
                    and original_assignment["plan_revision"] == mission["plan_revision"]
                    and original_assignment["state"] == "unknown"
                    and self._artifacts._proves_no_effect(effect)):
                matching = [
                    job for job in jobs if job["job_id"] == original_assignment["job_id"]
                    and job["role"] in {"work", "integrate"} and job["state"] == "unknown"
                ]
                if len(matching) == 1:
                    recovery_job_id = matching[0]["job_id"]
                    proven_capture = {
                        "assignment_id": allow_capture_assignment_id,
                        "run_id": original_assignment["run_id"],
                        "run_evidence_ref": capture_run_evidence_ref,
                        "capture_effect_id": effect["effect_id"],
                        "capture_effect_generation": effect["generation"],
                        "original_capture_not_started": True,
                    }
        if any(job["state"] == "unknown" and job["job_id"] != recovery_job_id for job in jobs):
            return False
        work_jobs = [job for job in jobs if job["role"] == "work"]
        integrators = [job for job in jobs if job["role"] == "integrate"]
        reviewers = [job for job in jobs if job["role"] == "review"]
        if not work_jobs:
            return False

        all_work_done = all(job["state"] == "done" for job in work_jobs)
        integration_evidence = None
        if not integrators and not reviewers:
            if all_work_done:
                return False
            target = "running"
            facts = {
                "reconciliation_verified": True, "original_run_settled": True,
                "mandate_valid": True, "control_allows": True, "next_step_safe": True,
            }
        else:
            if len(integrators) != 1 or len(reviewers) != 1:
                return False
            work_deliveries_applied = self._required_deliveries_applied(
                mission_id, child_roles={"work"},
            )
            if not all_work_done:
                target = "running"
                facts = {
                    "reconciliation_verified": True, "original_run_settled": True,
                    "mandate_valid": True, "control_allows": True, "next_step_safe": True,
                }
            else:
                if not work_deliveries_applied:
                    return False
                integrator = integrators[0]
                if integrator["state"] == "done":
                    with e._store.unit_of_work(
                        actor=_ACTOR, correlation_id=f"mission:{mission_id}:unknown-resume-check",
                    ) as uow:
                        app = CoordinatorApplicationService(uow)
                        candidate = app._latest_integration_candidate(
                            mission_id, mission["plan_revision"],
                        )
                        if (candidate is None or not app._job_deliveries_applied(
                            integrator["job_id"], candidate["assignment_id"],
                        )):
                            return False
                        integration_evidence = self._frozen_integration_resume_evidence(
                            uow.db, mission, candidate,
                        )
                        if integration_evidence is None:
                            return False
                    target = "reviewing"
                    facts = {
                        "reconciliation_verified": True, "original_integration_settled": True,
                        "integration_artifact_frozen": True, "all_work_jobs_done": True,
                        "required_deliveries_applied": True,
                        "mandate_valid": True, "control_allows": True,
                    }
                else:
                    target = "integrating"
                    facts = {
                        "reconciliation_verified": True, "all_work_jobs_done": True,
                        "required_deliveries_applied": True, "original_delivery_applied": True,
                        "mandate_valid": True, "control_allows": True,
                    }

        audit_payload = {
            "reconciliation_ref": f"mission:{mission_id}:dag-resume-{target}",
            "plan_revision": mission["plan_revision"],
        }
        if integration_evidence is not None:
            audit_payload.update(integration_evidence)
        if proven_capture is not None:
            audit_payload.update(proven_capture)
        e._transition(
            "mission", mission_id, target, facts=facts,
            audit_payload=audit_payload,
        )
        return True

    def _prepare_review(self, mission, job, assignment, snapshot, evidence_ids):
        e = self._execution
        plan, _, _, _ = self._load_plan(mission)
        acceptance = plan["acceptance"]
        candidate = e._read_one(
            "SELECT artifact.artifact_id,source.assignment_id FROM jobs AS integrator "
            "JOIN assignments AS source ON source.job_id=integrator.job_id "
            "JOIN artifacts AS artifact ON artifact.assignment_id=source.assignment_id "
            "WHERE integrator.mission_id=? AND integrator.plan_revision=? AND integrator.role='integrate' "
            "AND source.state='succeeded' ORDER BY source.attempt_no DESC LIMIT 1",
            (mission["mission_id"], mission["plan_revision"]),
        )
        if candidate is None:
            raise HarnessError("Review has no frozen Integrator candidate", code="superagent/artifact-missing")
        reviewer_ids = sorted(set(evidence_ids))
        reviewer_rows = e._read_all(
            "SELECT evidence_id,assignment_id,evidence_type,reference_hash,payload_hash FROM evidence "
            "WHERE assignment_id=? ORDER BY evidence_id", (assignment["assignment_id"],),
        )
        reviewer_rows = [row for row in reviewer_rows if row["evidence_id"] in reviewer_ids]
        invalid_reason = None
        if ({row["evidence_id"] for row in reviewer_rows} != set(reviewer_ids)
                or not any(row["evidence_type"] == "run" for row in reviewer_rows)):
            invalid_reason = "reviewer-run-evidence-missing"
        source_rows, normalized = [], []
        try:
            if invalid_reason:
                raise ValueError(invalid_reason)
            output_text = snapshot.get("output_text")
            if not isinstance(output_text, str) or len(output_text.encode("utf-8")) > 131072:
                raise ValueError("review-output-missing-or-too-large")

            output = json.loads(output_text)
            if (not isinstance(output, dict) or type(output.get("schema_version")) is not int
                    or output.get("schema_version") != 1
                    or output.get("candidate_artifact_id") != candidate["artifact_id"]
                    or output.get("acceptance_hash") != plan["acceptance_hash"]):
                raise ValueError("review-output-contract-mismatch")
            criteria = output.get("criteria")
            expected = {item["id"] for item in acceptance["criteria"]}
            if not isinstance(criteria, list) or len(criteria) != len(expected):
                raise ValueError("review-criteria-set-mismatch")
            seen, cited = set(), set()
            for item in criteria:
                if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                        or item["id"] not in expected or item["id"] in seen
                        or item.get("status") not in {"passed", "failed"}
                        or not isinstance(item.get("reason"), str) or not item["reason"].strip()
                        or len(item["reason"]) > 2048
                        or not isinstance(item.get("evidence_ids"), list) or not item["evidence_ids"]
                        or any(not isinstance(value, str) or not value.strip() for value in item["evidence_ids"])
                        or len(item["evidence_ids"]) != len(set(item["evidence_ids"]))):
                    raise ValueError("review-criterion-invalid")
                seen.add(item["id"])
                cited.update(item["evidence_ids"])
                normalized.append({"id": item["id"], "status": item["status"],
                                   "reason": item["reason"].strip(), "evidence_ids": item["evidence_ids"]})
            source_rows = e._read_all(
                "SELECT evidence.evidence_id,evidence.assignment_id,evidence.evidence_type,"
                "evidence.reference_hash,evidence.payload_hash,job.role FROM evidence "
                "JOIN assignments ON assignments.assignment_id=evidence.assignment_id "
                "JOIN jobs AS job ON job.job_id=assignments.job_id "
                "WHERE job.mission_id=? AND job.plan_revision=? AND job.role IN ('work','integrate') "
                "AND (job.role='work' OR assignments.assignment_id=?) "
                "ORDER BY evidence.evidence_id",
                (mission["mission_id"], mission["plan_revision"], candidate["assignment_id"]),
            )
            source_rows = [row for row in source_rows if row["evidence_id"] in cited]
            if ({row["evidence_id"] for row in source_rows} != cited
                    or any(row["role"] not in {"work", "integrate"} for row in source_rows)):
                raise ValueError("review-cites-unknown-evidence")
            computed = "passed" if all(item["status"] == "passed" for item in normalized) else "failed"
            if output.get("verdict") != computed:
                raise ValueError("review-verdict-does-not-match-criteria")
            state = computed
            verdict = {"schema_version": 1, "candidate_artifact_id": candidate["artifact_id"],
                       "acceptance_hash": plan["acceptance_hash"], "verdict": computed,
                       "criteria": sorted(normalized, key=lambda item: item["id"])}

        except (KeyError, TypeError, ValueError) as exc:
            state = "invalid"
            source_rows = []
            verdict = {"schema_version": 1, "candidate_artifact_id": candidate["artifact_id"],
                       "acceptance_hash": plan["acceptance_hash"], "verdict": "invalid",
                       "reason": str(exc)[:512], "output_hash": snapshot.get("output_hash")}
        records = sorted(
            [{"evidence_id": row["evidence_id"], "assignment_id": row["assignment_id"],
              "evidence_type": row["evidence_type"], "reference_hash": row["reference_hash"],
              "payload_hash": row["payload_hash"]} for row in [*source_rows, *reviewer_rows]],
            key=lambda item: item["evidence_id"],
        )
        run_evidence_ref = next(
            (row["evidence_id"] for row in reviewer_rows if row["evidence_type"] == "run"),
            None,
        )
        return {"state": state, "candidate_artifact_id": candidate["artifact_id"],
                "acceptance_hash": plan["acceptance_hash"], "verdict": verdict,
                "evidence_set_hash": _hash(_json(records)),
                "reviewer_evidence_ids": reviewer_ids, "reviewer_run_evidence_ref": run_evidence_ref,
                "output_hash": snapshot.get("output_hash")}

    async def finish_review_success(self, assignment, snapshot, evidence_ids):
        e = self._execution
        current = e._assignment(assignment["assignment_id"])
        if current is None:
            raise LookupError("Reviewer Assignment not found")
        job = e._read_one("SELECT * FROM jobs WHERE job_id=?", (current["job_id"],))
        if job is None or job["role"] != "review":
            raise HarnessError("Review completion is bound to a non-Reviewer Job", code="superagent/review-scope")
        mission_id = job["mission_id"]
        if current["state"] in {"accepted", "running"}:
            changes = {"started_at": current.get("started_at") or _run_started_at(snapshot)}
            current = e._transition(
                "assignment", current["assignment_id"], "capturing",
                facts={"original_run_settled": True}, changes=changes,
            )
        elif current["state"] == "stopping":
            current = e._transition(
                "assignment", current["assignment_id"], "unknown",
                facts={"operation_outcome_uncertain": True},
                changes={"error_ref": f"review-after-stop:{current['assignment_id']}"},
                audit_payload={"reconciliation_ref": f"assignment:{current['assignment_id']}:review-after-stop"},
            )
        if current["state"] not in {"capturing", "unknown"}:
            return {"state": current["state"], "assignment_id": current["assignment_id"]}
        current = e._assignment(current["assignment_id"])
        mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        if mission is None:
            raise LookupError("Reviewer Mission not found")
        if mission["state"] == "unknown":
            if (self._has_uncertain_assignments(mission_id, exclude_assignment_id=current["assignment_id"])
                    or self._has_uncertain_effects(mission_id)):
                return {"state": "unknown", "assignment_id": current["assignment_id"]}
            ticket = e._authorization.ticket(mission["ticket_id"])
            mandate = (ticket or {}).get("mandate") or {}
            mandate_id = mandate.get("mandate_id")
            status = e._authorization.revalidate_mandate(mandate_id, actor=_ACTOR) if mandate_id else {}
            if status.get("status") not in {"valid", "affected"} or not e._control.allows(mission_id):
                return {"state": "unknown", "assignment_id": current["assignment_id"]}
        else:
            e._admit_mission(mission_id)
        prepared = self._prepare_review(mission, job, current, snapshot, evidence_ids)
        # unknown 状态只能用当前 Reviewer 的真实 Run 证据确认原操作，不能借工具证据或伪造 ID 结算。
        if current["state"] == "unknown" and prepared["reviewer_run_evidence_ref"] is None:
            return {"state": "unknown", "assignment_id": current["assignment_id"]}

        with e._store.unit_of_work(
            actor=_ACTOR, correlation_id=f"mission:{mission_id}:review:{current['assignment_id']}",
        ) as uow:
            app = CoordinatorApplicationService(uow)
            persisted = uow.assignments.get(current["assignment_id"])
            if persisted is None or persisted["state"] not in {"capturing", "unknown"}:
                raise HarnessError("Reviewer Assignment changed before Review freeze", code="superagent/assignment-conflict")
            current_mission = uow.missions.get(mission_id)
            if (current_mission is None or current_mission["state"] not in {"reviewing", "unknown"}
                    or current_mission["plan_revision"] != job["plan_revision"]):
                return {"state": persisted["state"], "assignment_id": current["assignment_id"]}
            # 预检与冻结之间可能发生暂停、撤权或 Watchdog 阻断。
            # 与 Review 持久化共用一笔写事务，拒绝迟到的结论和修正派工。
            mandate_valid, control_allows = self._mission_authorization_in_transaction(
                current_mission, uow.db,
            )
            if not mandate_valid or not control_allows:
                return {"state": persisted["state"], "assignment_id": current["assignment_id"]}
            review = uow.reviews.create(
                mission_id=mission_id, reviewer_assignment_id=current["assignment_id"],
                candidate_artifact_id=prepared["candidate_artifact_id"],
                plan_revision=job["plan_revision"], acceptance_hash=prepared["acceptance_hash"],
                verdict=prepared["verdict"], evidence_set_hash=prepared["evidence_set_hash"],
                state=prepared["state"],
            )
            body = {
                "kind": "review_verdict_report", "schema_version": 1,
                "assignment_id": current["assignment_id"], "run_id": current["run_id"],
                "review_id": review["review_id"], "candidate_artifact_id": prepared["candidate_artifact_id"],
                "acceptance_hash": prepared["acceptance_hash"], "state": prepared["state"],
                "verdict": prepared["verdict"], "evidence_set_hash": prepared["evidence_set_hash"],
                "reviewer_evidence_ids": prepared["reviewer_evidence_ids"],
                "output_ref": f"review:{review['review_id']}",
                "output_hash": prepared["output_hash"],
            }
            report = {**body, "report_hash": _hash(_json(body))}
            facts = {"authorized": True, "output_frozen_and_validated": True,
                     "review_verdict_and_evidence_frozen": True}
            audit = None
            if persisted["state"] == "unknown":
                facts.update({"reconciliation_verified": True, "original_run_completed": True})
                audit = {
                    "reconciliation_ref": f"assignment:{current['assignment_id']}:review-frozen",
                    "run_id": current["run_id"],
                    "run_evidence_ref": prepared["reviewer_run_evidence_ref"],
                    "output_ref": report["output_ref"], "output_hash": report["output_hash"],
                    "review_id": review["review_id"],
                }
            app.transition(
                "assignment", current["assignment_id"],
                expected_revision=persisted["aggregate_revision"], target_state="succeeded",
                facts=facts, changes={"ended_at": _now(), "report_json": report},
                audit_payload=audit,
            )
        self.finalize_succeeded_review_assignment(e._assignment(current["assignment_id"]), report)
        return {"state": "succeeded", "assignment_id": current["assignment_id"],
                "review_id": review["review_id"], "review_state": prepared["state"]}

    async def finish_work_success(self, assignment, snapshot, evidence_ids):
        e = self._execution
        current = e._assignment(assignment["assignment_id"])
        if current is None:
            raise LookupError("Assignment not found")
        job = e._read_one("SELECT * FROM jobs WHERE job_id=?", (current["job_id"],))
        if job is None:
            raise LookupError("Assignment Job not found")
        mission_id = job["mission_id"]
        disposition = e._parse_worker_report(snapshot.get("output_text", "")) if job["role"] == "work" else {"status": "completed"}
        if disposition.get("status") == "handback":
            if self._handback_status(
                current, disposition.get("handback_trigger_id"), disposition.get("handback_reason", ""),
            ) not in {"ready", "applied"}:
                disposition = {
                    "status": "attention", "summary": disposition.get("summary", ""),
                    "question": disposition.get("handback_reason", "") or "A human decision is required to route this follow-up.",
                    "options": [],
                }
        if job["role"] == "review":
            return await self.finish_review_success(current, snapshot, evidence_ids)
        if current["state"] in {"accepted", "running"}:
            changes = {}
            if current["state"] == "accepted":
                changes["started_at"] = current.get("started_at") or _run_started_at(snapshot)
            current = e._transition(
                "assignment", current["assignment_id"], "capturing",
                facts={"original_run_settled": True}, changes=changes,
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
        mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        _, nodes, _, _ = self._load_plan(mission)
        checkpoint = None
        if (job["role"] == "work" and nodes[job["node_id"]].get("max_work_turns") is not None
                and disposition.get("status") not in {"attention", "handback"}):
            try:
                raw = snapshot.get("output_text")
                _text(raw, "checkpoint", limit=65536)
                checkpoint = validate_checkpoint(strict_loads(raw))
                if _hash(raw) != snapshot.get("output_hash"):
                    raise ValueError
            except (ValueError, TypeError, RecursionError, HarnessError):
                if current["state"] == "unknown":
                    effect = self._artifacts._latest_effect("artifact.capture", current["assignment_id"])
                    if effect is not None and effect["state"] in {"leased", "unknown"}:
                        return {"state": "unknown", "assignment_id": current["assignment_id"]}
                    current = e._transition(
                        "assignment", current["assignment_id"], "capturing",
                        facts={"reconciliation_verified": True, "original_run_settled": True},
                        audit_payload={"reconciliation_ref": f"assignment:{current['assignment_id']}:invalid-checkpoint",
                                       "run_id": current["run_id"]},
                    )
                self._fail_capture(current, "work-checkpoint-invalid")
                return {"state": "failed", "assignment_id": current["assignment_id"]}
        # 状态转换返回原始 Assignment 行；跨 Artifact 边界前恢复可信的 Mission 上下文。
        current = e._assignment(current["assignment_id"])
        if current is None:
            raise LookupError("Assignment disappeared during artifact capture")
        resumed = self._resume_unknown_mission(
            mission_id,
            allow_capture_assignment_id=current["assignment_id"] if current["state"] in {"capturing", "unknown"} else None,
            capture_run_evidence_ref=evidence_ids[0] if evidence_ids else None,
        )
        mission = e._read_one("SELECT state FROM missions WHERE mission_id=?", (mission_id,))
        recovering_original_capture = (
            not resumed and mission is not None and mission["state"] == "unknown"
            and current["state"] in {"capturing", "unknown"}
            and self._artifacts._has_reconcilable_capture(current["assignment_id"])
        )
        if not resumed and mission is not None and mission["state"] == "unknown" and not recovering_original_capture:
            return {"state": "unknown", "assignment_id": current["assignment_id"]}
        try:
            # Mission unknown 时不准创建新 Effect，只允许按原 Assignment 查已认领的捕获回执。
            if not recovering_original_capture:
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

        attention_id = (
            e._attention_id(current, current["goal_ref"], disposition.get("question", ""))
            if disposition.get("status") == "attention" else None
        )
        report_body = {
            "kind": "work_artifact_report", "assignment_id": current["assignment_id"],
            "run_id": current["run_id"], "artifact_id": artifact["artifact_id"],
            "manifest_hash": artifact["manifest_hash"], "tree_hash": artifact["tree_hash"],
            "commit_id": artifact["commit_id"], "output_ref": f"artifact:{artifact['artifact_id']}",
            "output_hash": artifact["manifest_hash"], "evidence_ids": list(evidence_ids),
            "run_output_hash": snapshot.get("output_hash"),
            "disposition": disposition.get("status", "completed"),
            "summary": disposition.get("summary", ""),
            "question": disposition.get("question", ""), "options": disposition.get("options", []),
            "handback_reason": disposition.get("handback_reason", ""),
            "handback_trigger_id": disposition.get("handback_trigger_id", ""),
            "attention_id": attention_id,
        }
        if checkpoint is not None:
            report_body.update(checkpoint=checkpoint, checkpoint_output=snapshot["output_text"])
        report_hash = _hash(_json(report_body))
        report = {**report_body, "report_hash": report_hash}
        assignment_facts = {"output_frozen_and_validated": True, "artifact_frozen": True}
        audit = None
        if current["state"] == "unknown":
            assignment_facts.update({"reconciliation_verified": True, "original_run_completed": True})
            audit = {
                "reconciliation_ref": f"assignment:{current['assignment_id']}:artifact-frozen",
                "run_id": current["run_id"], "run_evidence_ref": evidence_ids[0],
                "output_ref": report["output_ref"], "output_hash": report["output_hash"],
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

    def finalize_succeeded_review_assignment(self, assignment, report):
        e = self._execution
        current = e._assignment(assignment["assignment_id"])
        if current is None or current["state"] != "succeeded":
            return
        job = e._read_one("SELECT * FROM jobs WHERE job_id=?", (current["job_id"],))
        if job is None or job["role"] != "review" or job["state"] in {"done", "blocked", "cancelled"}:
            return
        # 只有修正轮次的新 Assignment 才能推进已 checkpointed 的 Review Job。
        latest = self._assignment_for_job(job["job_id"])
        if latest is None or latest["assignment_id"] != current["assignment_id"]:
            return
        if job["state"] == "checkpointed":
            mission = e._read_one("SELECT round_no FROM missions WHERE mission_id=?", (job["mission_id"],))
            if mission is None or current["attempt_no"] <= mission["round_no"]:
                return
        with e._store.unit_of_work(
            actor=_ACTOR, correlation_id=f"mission:{job['mission_id']}:review:{current['assignment_id']}",
        ) as uow:
            app = CoordinatorApplicationService(uow)
            current_job = uow.jobs.get(job["job_id"])
            current_assignment = uow.assignments.get(current["assignment_id"])
            if (current_job is None or current_assignment is None or current_assignment["state"] != "succeeded"
                    or not app._review_frozen_for_assignment(
                        job["mission_id"], current_job, current["assignment_id"], report,
                    )):
                raise HarnessError("Succeeded Reviewer Assignment has no valid frozen Review", code="superagent/review-corrupt")
            if current_job["state"] == "checkpointed":
                # Run 已成功并冻结结论时，允许补记未观察到的 running 阶段。
                current_job = app.transition(
                    "job", job["job_id"], expected_revision=current_job["aggregate_revision"],
                    target_state="running",
                    facts={"authorized": True, "new_assignment_created": True,
                           "checkpoint_and_capture_imported": True, "control_allows": True},
                )
            if current_job["state"] not in {"ready", "running", "unknown"}:
                return
            facts = {
                "authorized": True, "role_result_validated": True,
                "report_frozen": True, "review_verdict_and_evidence_frozen": True,
            }
            audit = None
            if current_job["state"] == "unknown":
                facts["reconciliation_verified"] = True
                audit = {"reconciliation_ref": f"job:{job['job_id']}:review-frozen",
                         "review_id": report["review_id"]}
            app.transition(
                "job", job["job_id"], expected_revision=current_job["aggregate_revision"],
                target_state="done", facts=facts,
                changes={"result_hash": report["report_hash"]}, audit_payload=audit,
            )
        self._advance_mission_state(job["mission_id"])

    def finalize_succeeded_assignment(self, assignment):
        e = self._execution
        current = e._assignment(assignment["assignment_id"])
        if current is None or current["state"] != "succeeded":
            return
        report = _object(current.get("report_json"))
        if report.get("kind") == "review_verdict_report":
            self.finalize_succeeded_review_assignment(current, report)
            return
        if report.get("kind") != "work_artifact_report":
            return
        job = e._read_one("SELECT * FROM jobs WHERE job_id=?", (current["job_id"],))
        if job is None or job["state"] == "done":
            return
        # 同一 Job 的旧 Assignment 只保留历史证据，不得结算新的执行轮次。
        latest = self._assignment_for_job(job["job_id"])
        if latest is None or latest["assignment_id"] != current["assignment_id"]:
            return
        if job["state"] == "checkpointed":
            if job["role"] == "work":
                if job["result_hash"] == report.get("report_hash"):
                    return
            else:
                mission = e._read_one("SELECT round_no FROM missions WHERE mission_id=?", (job["mission_id"],))
                if mission is None or current["attempt_no"] <= mission["round_no"]:
                    return
        artifact = self._artifacts.get(current["assignment_id"])
        if (artifact is None or artifact["artifact_id"] != report.get("artifact_id")
                or artifact["manifest_hash"] != report.get("manifest_hash")
                or artifact["tree_hash"] != report.get("tree_hash")):
            raise HarnessError("Succeeded Work Assignment lost its frozen Artifact", code="superagent/artifact-corrupt")
        mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (job["mission_id"],))
        if report.get("disposition") in {"attention", "handback"}:
            mandate_valid, control_allows = self._mission_authorization(mission)
            if mandate_valid and control_allows:
                disposition = report["disposition"]
                if disposition == "handback":
                    status = self._handback_status(
                        current, report.get("handback_trigger_id"), report.get("handback_reason", ""),
                    )
                    if status == "ready":
                        try:
                            e._authorization.handback(
                                current["ticket_id"], report["handback_trigger_id"],
                                report["handback_reason"], actor=_ACTOR,
                                assignment_id=current["assignment_id"],
                            )
                        except PermissionError:
                            ticket = e._authorization.ticket(current["ticket_id"])
                            disposition = "attention" if ticket and ticket.get("state") == "open" else "completed"
                    elif status != "applied":
                        ticket = e._authorization.ticket(current["ticket_id"])
                        disposition = "attention" if ticket and ticket.get("state") == "open" else "completed"
                if disposition == "attention":
                    question = report.get("question") or report.get("handback_reason", "")
                    attention_id = e._attention_id(current, current["goal_ref"], question)
                    if report.get("attention_id") not in {None, attention_id}:
                        raise HarnessError("Frozen DAG Attention identity does not match its question", code="superagent/attention-conflict")
                    e._create_attention(current, current["goal_ref"], question, report.get("options", []))
                    self._block_job(job, "attention", assignment=current)
                    self._advance_mission_state(job["mission_id"])
                    return
        _, nodes, _, _ = self._load_plan(mission)
        maximum = nodes[job["node_id"]].get("max_work_turns") if job["role"] == "work" else None
        checkpoint = None
        if maximum is not None and report.get("disposition") not in {"attention", "handback"}:
            with e._store.transaction() as db:
                checkpoint = frozen_work_checkpoint(db, current)
        if job["state"] == "checkpointed":
            # 已成功的原 Run 无需重放；仅以冻结 Artifact 和导入回执推进状态。
            e._transition(
                "job", job["job_id"], "running",
                facts={"new_assignment_created": True, "checkpoint_and_capture_imported": True,
                       "control_allows": True},
            )
            job = e._read_one("SELECT * FROM jobs WHERE job_id=?", (job["job_id"],))
        if job["state"] not in {"ready", "running", "unknown"}:
            raise HarnessError("Succeeded Artifact Assignment has a non-completable Job", code="superagent/job-state-conflict")
        if checkpoint is not None and checkpoint["status"] != "ready":
            if checkpoint["status"] == "blocked" or current["attempt_no"] >= maximum:
                self._block_job(job, "work-stage-budget-exhausted" if checkpoint["status"] == "continue" else "work-checkpoint-blocked")
                return
            previous_stages = e._read_all(
                "SELECT * FROM assignments WHERE job_id=? AND attempt_no<? AND state='succeeded' ORDER BY attempt_no",
                (job["job_id"], current["attempt_no"]),
            )
            for previous in previous_stages:
                with e._store.transaction() as db:
                    prior_checkpoint = frozen_work_checkpoint(db, previous)
                prior_artifact = self._artifacts.get(previous["assignment_id"])
                if prior_checkpoint == checkpoint and prior_artifact["tree_hash"] == artifact["tree_hash"]:
                    self._block_job(job, "work-checkpoint-stalled")
                    return
            e._transition(
                "job", job["job_id"], "checkpointed",
                facts={"checkpoint_and_capture_frozen": True},
                changes={"result_hash": report["report_hash"]},
            )
            return
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
        if job and job["state"] in {"ready", "running", "checkpointed", "unknown"}:
            facts = {"known_failure_or_human_decision": True}
            audit = None
            if job["state"] == "unknown":
                facts["reconciliation_verified"] = True
                audit = {"reconciliation_ref": f"job:{job['job_id']}:capture-failed",
                         "assignment_id": current["assignment_id"], "run_id": current["run_id"],
                         "failure_ref": reason}
            e._transition("job", job["job_id"], "blocked", facts=facts, audit_payload=audit)
        if job is not None:
            self._advance_mission_state(job["mission_id"])

    def _block_unstarted_assignment(self, job, assignment, reason):
        """原 Run 未提交时原子结算 Assignment、InputDelivery 和 Job。"""
        e = self._execution
        assignment_id = assignment["assignment_id"]
        with e._store.unit_of_work(
            actor=_ACTOR, correlation_id=f"mission:{job['mission_id']}:job:{job['job_id']}",
        ) as uow:
            app = CoordinatorApplicationService(uow)
            current = uow.assignments.get(assignment_id)
            current_job = uow.jobs.get(job["job_id"])
            if (current is None or current["state"] != "pending"
                    or current["job_id"] != job["job_id"] or current.get("run_id")):
                raise HarnessError("Unstarted Assignment changed before blocking",
                                   code="superagent/assignment-conflict")
            app.transition(
                "assignment", assignment_id,
                expected_revision=current["aggregate_revision"], target_state="cancelled",
                facts={"authorized": True, "no_external_side_effect_started": True},
                changes={"error_ref": reason},
            )
            rows = uow.db.execute(
                "SELECT * FROM input_deliveries WHERE assignment_id=? "
                "AND state IN ('pending','submitting')", (assignment_id,),
            ).fetchall()
            for row in rows:
                e._input_deliveries.transition_in_transaction(
                    uow.db, row["input_id"], assignment_id, "rejected",
                    expected_revision=row["aggregate_revision"],
                    receipt=e._input_deliveries.make_receipt(
                        dict(row), "rejected", reason_code=reason,
                    ), actor=_ACTOR,
                )
            owned = uow.db.execute(
                "SELECT input_id FROM ticket_inputs WHERE delivered_to_assignment_id=? "
                "AND state='delivered'", (assignment_id,),
            ).fetchall()
            for row in owned:
                e._mark_input_db(uow.db, row["input_id"], "waiting")
            if current_job and current_job["state"] in {"pending", "ready", "running", "checkpointed", "unknown"}:
                facts = {"authorized": True, "known_failure_or_human_decision": True}
                if current_job["state"] == "unknown":
                    facts["reconciliation_verified"] = True
                app.transition(
                    "job", current_job["job_id"],
                    expected_revision=current_job["aggregate_revision"],
                    target_state="blocked", facts=facts,
                )

    def _block_job(self, job, reason, *, assignment=None):
        e = self._execution
        current_assignment = assignment or self._assignment_for_job(job["job_id"])
        if current_assignment is not None and current_assignment["state"] == "pending":
            self._block_unstarted_assignment(job, current_assignment, reason)
            return
        current_job = e._read_one("SELECT * FROM jobs WHERE job_id=?", (job["job_id"],))
        if current_job and current_job["state"] in {"pending", "ready", "running", "checkpointed", "unknown"}:
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
            # 已封存的旧尝试既不能完成新轮次，也不能用旧终态阻塞新轮次。
            latest = self._assignment_for_job(job["job_id"])
            if latest is None or latest["assignment_id"] != assignment["assignment_id"]:
                continue
            if job["state"] == "checkpointed":
                if job["role"] == "work":
                    report = _object(assignment.get("report_json"))
                    if assignment["state"] == "succeeded" and job["result_hash"] == report.get("report_hash"):
                        continue
                else:
                    mission = e._read_one("SELECT round_no FROM missions WHERE mission_id=?", (mission_id,))
                    if (mission is None or assignment["state"] != "succeeded"
                            or assignment["attempt_no"] <= mission["round_no"]):
                        continue
            if assignment["state"] == "succeeded":
                if job["state"] in {"blocked", "cancelled"}:
                    continue
                report = _object(assignment.get("report_json"))
                if report.get("kind") == "single_worker_report":
                    # 共用 Scheduler 也恢复已冻结的单节点 Report 效果。
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
                if job["state"] in {"pending", "ready", "running", "checkpointed", "unknown"}:
                    e._transition("job", job["job_id"], "blocked", facts=facts)
            elif assignment["state"] == "cancelled":
                mission = e._read_one("SELECT state FROM missions WHERE mission_id=?", (mission_id,))
                if mission and mission["state"] in {"cancelling", "cancelled"} and job["state"] in {"pending", "ready", "running", "unknown"}:
                    facts = {"owned_operations_settled": True}
                    if job["state"] == "unknown":
                        facts["reconciliation_verified"] = True
                    e._transition("job", job["job_id"], "cancelled", facts=facts)

    def _required_deliveries_applied(self, mission_id, *, child_roles=None):
        e = self._execution
        params = [mission_id]
        role_filter = ""
        if child_roles is not None:
            roles = sorted(set(child_roles))
            if not roles:
                return True
            role_filter = " AND child.role IN (" + ",".join("?" for _ in roles) + ")"
            params.extend(roles)
        edges = e._read_all(
            "SELECT child.job_id AS child_job_id,parent.job_id AS parent_job_id "
            "FROM job_dependencies AS dependency "
            "JOIN jobs AS child ON child.job_id=dependency.job_id "
            "JOIN jobs AS parent ON parent.job_id=dependency.depends_on_job_id "
            "WHERE child.mission_id=? AND dependency.requires_delivery=1" + role_filter,
            tuple(params),
        )
        for edge in edges:
            target = e._read_one(
                "SELECT assignment_id FROM assignments WHERE job_id=? "
                "ORDER BY attempt_no DESC LIMIT 1", (edge["child_job_id"],),
            )
            source = e._read_one(
                "SELECT artifact.artifact_id FROM assignments AS assignment "
                "JOIN artifacts AS artifact ON artifact.assignment_id=assignment.assignment_id "
                "WHERE assignment.job_id=? AND assignment.state='succeeded' "
                "ORDER BY assignment.attempt_no DESC LIMIT 1", (edge["parent_job_id"],),
            )
            if target is None or source is None:
                return False
            delivery = e._read_one(
                "SELECT state FROM deliveries WHERE target_assignment_id=? AND artifact_id=?",
                (target["assignment_id"], source["artifact_id"]),
            )
            if delivery is None or delivery["state"] != "applied":
                return False
        return True

    def _block_mission(self, mission, reason):
        if mission is None or mission["state"] not in {
            "running", "integrating", "reviewing", "unknown",
        }:
            return
        facts = {"known_failure_or_human_decision": True}
        if mission["state"] == "unknown":
            facts["reconciliation_verified"] = True
        self._execution._transition(
            "mission", mission["mission_id"], "blocked",
            facts=facts,
            changes={"outcome": str(reason)[:128]},
            audit_payload={
                "failure_ref": f"mission:{mission['mission_id']}:{reason}",
                "plan_revision": mission["plan_revision"],
            },
        )

    def _has_attention(self, mission_id):
        return self._execution._read_one(
            "SELECT 1 AS present FROM attentions WHERE mission_id=? LIMIT 1",
            (mission_id,),
        ) is not None

    def _advance_mission_state(self, mission_id):
        e = self._execution
        mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        if mission is None:
            return
        if mission["state"] == "unknown":
            if not self._resume_unknown_mission(mission_id):
                return
            mission = e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        if mission is None or mission["state"] not in {"running", "integrating", "reviewing"}:
            return
        if self._active_assignments(mission_id):
            return
        if self._has_attention(mission_id):
            self._block_mission(mission, "attention")
            return

        jobs = self._load_jobs(mission_id, mission["plan_revision"])
        if not jobs:
            return
        if any(job["state"] == "unknown" for job in jobs):
            return
        if any(job["state"] in {"blocked", "cancelled"} for job in jobs):
            self._block_mission(mission, "job-failed")
            return

        work_jobs = [job for job in jobs if job["role"] == "work"]
        integrators = [job for job in jobs if job["role"] == "integrate"]
        reviewers = [job for job in jobs if job["role"] == "review"]
        if not work_jobs:
            return
        all_work_done = all(job["state"] == "done" for job in work_jobs)
        work_deliveries_applied = self._required_deliveries_applied(
            mission_id, child_roles={"work"},
        )

        if mission["state"] == "running":
            if not all_work_done or not work_deliveries_applied:
                return
            plan, _, _, _ = self._load_plan(mission)
            if plan["acceptance"].get("kind") == "single_worker_report":
                return
            if len(integrators) != 1 or len(reviewers) != 1:
                self._block_mission(mission, "quality-gate-plan-invalid")
                return
            mandate_valid, control_allows = self._mission_authorization(mission)
            if not mandate_valid or not control_allows:
                return
            e._transition(
                "mission", mission_id, "integrating",
                facts={
                    "all_work_jobs_done": True,
                    "required_deliveries_applied": True,
                    "mandate_valid": True, "control_allows": True,
                },
            )
            return

        if mission["state"] == "integrating":
            if not all_work_done or not work_deliveries_applied:
                return
            if len(integrators) != 1 or len(reviewers) != 1:
                self._block_mission(mission, "quality-gate-plan-invalid")
                return
            integrator = integrators[0]
            if integrator["state"] != "done":
                return
            mandate_valid, control_allows = self._mission_authorization(mission)
            if not mandate_valid or not control_allows:
                return
            with e._store.unit_of_work(
                actor=_ACTOR, correlation_id=f"mission:{mission_id}:integrator-frozen",
            ) as uow:
                app = CoordinatorApplicationService(uow)
                current_mission = uow.missions.get(mission_id)
                if current_mission is None:
                    return
                # 防止工作区准备后授权变更导致迟到的质量门推进。
                still_authorized, still_allowed = self._mission_authorization_in_transaction(
                    current_mission, uow.db,
                )
                if not still_authorized or not still_allowed:
                    return
                current_integrator = uow.jobs.get(integrator["job_id"])
                candidate = app._latest_integration_candidate(
                    mission_id, mission["plan_revision"],
                )
                if (current_mission is None or current_integrator is None
                        or current_integrator["state"] != "done" or candidate is None
                        or not app._job_deliveries_applied(
                            integrator["job_id"], candidate["assignment_id"],
                        )):
                    return
                app.transition(
                    "mission", mission_id,
                    expected_revision=current_mission["aggregate_revision"],
                    target_state="reviewing",
                    facts={
                        "authorized": True,
                        "integration_artifact_frozen": True,
                        "all_work_jobs_done": True,
                        "required_deliveries_applied": True,
                        "mandate_valid": True, "control_allows": True,
                    },
                    audit_payload={
                        "integration_ref": f"assignment:{candidate['assignment_id']}",
                        "candidate_artifact_id": candidate["artifact_id"],
                    },
                )
            return

        if mission["state"] == "reviewing":
            if len(integrators) != 1 or len(reviewers) != 1:
                self._block_mission(mission, "quality-gate-plan-invalid")
                return
            reviewer = reviewers[0]
            if reviewer["state"] != "done":
                return
            mandate_valid, control_allows = self._mission_authorization(mission)
            if not mandate_valid or not control_allows:
                return

            with e._store.unit_of_work(
                actor=_ACTOR, correlation_id=f"mission:{mission_id}:review-settlement",
            ) as uow:
                app = CoordinatorApplicationService(uow)
                current_mission = uow.missions.get(mission_id)
                if current_mission is None:
                    return
                # Review 结算、修正轮次和完成前再次核对持久授权与控制状态。
                still_authorized, still_allowed = self._mission_authorization_in_transaction(
                    current_mission, uow.db,
                )
                if not still_authorized or not still_allowed:
                    return
                latest = app._latest_review(mission_id, current_mission["plan_revision"])
                if (latest is None or latest["job_state"] != "done"
                        or latest["assignment_state"] != "succeeded"):
                    app.transition(
                        "mission", mission_id,
                        expected_revision=current_mission["aggregate_revision"],
                        target_state="blocked",
                        facts={"authorized": True, "known_failure_or_human_decision": True},
                        changes={"outcome": "review-record-missing"},
                        audit_payload={"failure_ref": f"mission:{mission_id}:review-record-missing"},
                    )
                    return
                report = _object(latest.get("report_json"))
                current_reviewer = uow.jobs.get(latest["job_id"])
                if (current_reviewer is None or not app._review_frozen_for_assignment(
                    mission_id, current_reviewer, latest["reviewer_assignment_id"], report,
                ) or not app._job_deliveries_applied(
                    latest["job_id"], latest["reviewer_assignment_id"],
                )):
                    app.transition(
                        "mission", mission_id,
                        expected_revision=current_mission["aggregate_revision"],
                        target_state="blocked",
                        facts={"authorized": True, "known_failure_or_human_decision": True},
                        changes={"outcome": "review-record-invalid"},
                        audit_payload={"failure_ref": f"mission:{mission_id}:review-record-invalid"},
                    )
                    return

                if latest["state"] == "passed":
                    if not app._review_passed(mission_id, current_mission):
                        app.transition(
                            "mission", mission_id,
                            expected_revision=current_mission["aggregate_revision"],
                            target_state="blocked",
                            facts={"authorized": True, "known_failure_or_human_decision": True},
                            changes={"outcome": "review-verification-failed"},
                            audit_payload={"failure_ref": f"mission:{mission_id}:review-verification-failed"},
                        )
                        return
                    app.transition(
                        "mission", mission_id,
                        expected_revision=current_mission["aggregate_revision"],
                        target_state="completed",
                        facts={
                            "authorized": True,
                            "acceptance_verified": True,
                            "original_integration_settled": True,
                            "integration_artifact_frozen": True,
                            "all_work_jobs_done": True,
                            "required_deliveries_applied": True,
                            "mandate_valid": True, "control_allows": True,
                        },
                        audit_payload={
                            "completion_ref": f"review:{latest['review_id']}",
                            "candidate_artifact_id": latest["candidate_artifact_id"],
                        },
                    )
                    return

                if latest["state"] == "failed":
                    plan = uow.plans.get(mission_id, current_mission["plan_revision"])
                    acceptance = (plan or {}).get("acceptance_json") or {}
                    maximum = acceptance.get("max_repair_rounds", 1)
                    if type(maximum) is not int or not 0 <= maximum <= 3:
                        app.transition(
                            "mission", mission_id,
                            expected_revision=current_mission["aggregate_revision"],
                            target_state="blocked",
                            facts={"authorized": True, "known_failure_or_human_decision": True},
                            changes={"outcome": "repair-budget-invalid"},
                            audit_payload={"failure_ref": f"mission:{mission_id}:repair-budget-invalid"},
                        )
                        return
                    if current_mission["round_no"] >= maximum:
                        app.transition(
                            "mission", mission_id,
                            expected_revision=current_mission["aggregate_revision"],
                            target_state="blocked",
                            facts={"authorized": True, "known_failure_or_human_decision": True},
                            changes={"outcome": "review-repair-budget-exhausted"},
                            audit_payload={
                                "failure_ref": f"mission:{mission_id}:review-repair-budget-exhausted",
                                "review_id": latest["review_id"],
                            },
                        )
                        return

                    system_jobs = uow.db.execute(
                        "SELECT job_id,role,state,aggregate_revision FROM jobs "
                        "WHERE mission_id=? AND plan_revision=? AND role IN ('integrate','review') "
                        "ORDER BY role",
                        (mission_id, current_mission["plan_revision"]),
                    ).fetchall()
                    if (len(system_jobs) != 2
                            or any(row["state"] != "done" for row in system_jobs)):
                        app.transition(
                            "mission", mission_id,
                            expected_revision=current_mission["aggregate_revision"],
                            target_state="blocked",
                            facts={"authorized": True, "known_failure_or_human_decision": True},
                            changes={"outcome": "repair-checkpoint-invalid"},
                            audit_payload={"failure_ref": f"mission:{mission_id}:repair-checkpoint-invalid"},
                        )
                        return
                    repair_facts = {
                        "authorized": True,
                        "review_failed": True, "repair_budget_available": True,
                        "mandate_valid": True, "control_allows": True,
                    }
                    for row in system_jobs:
                        app.transition(
                            "job", row["job_id"],
                            expected_revision=row["aggregate_revision"],
                            target_state="checkpointed",
                            facts=repair_facts,
                            audit_payload={
                                "repair_ref": f"review:{latest['review_id']}",
                                "repair_round": current_mission["round_no"] + 1,
                            },
                        )
                    app.transition(
                        "mission", mission_id,
                        expected_revision=current_mission["aggregate_revision"],
                        target_state="integrating",
                        facts={
                            "authorized": True,
                            "repair_authorized": True, "repair_budget_available": True,
                            "mandate_valid": True, "control_allows": True,
                        },
                        changes={"round_no": current_mission["round_no"] + 1},
                        audit_payload={
                            "repair_ref": f"review:{latest['review_id']}",
                            "repair_round": current_mission["round_no"] + 1,
                            "previous_candidate_artifact_id": latest["candidate_artifact_id"],
                        },
                    )
                    return

                app.transition(
                    "mission", mission_id,
                    expected_revision=current_mission["aggregate_revision"],
                    target_state="blocked",
                    facts={"authorized": True, "known_failure_or_human_decision": True},
                    changes={"outcome": "review-invalid"},
                    audit_payload={"failure_ref": f"mission:{mission_id}:review-invalid"},
                )

"""统一 Ticket／Mission 执行、原生证据、报告和人工问题生命周期。"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from harness.i18n import tr

from ..core.errors import HarnessError
from .superagent_coordinator_service import CoordinatorApplicationService
from .superagent_coordinator_store import CoordinatorStore, _hash, _json, _now, _text
from .superagent_executor import ExecutionAdapter, WorkerProfile
from .superagent_coordination import CoordinatorControl, CoordinatorInputDelivery
from .superagent_scheduler import CoordinatorScheduler
from .superagent_dag_scheduler import CoordinatorDagScheduler, _run_started_at


_ACTOR = {"kind": "host", "component": "superagent-v2-execution"}
_ACTIVE_ASSIGNMENTS = ("pending", "submitting", "accepted", "running", "capturing", "stopping", "unknown")
_TERMINAL_ASSIGNMENTS = ("succeeded", "failed", "interrupted", "rejected", "cancelled", "blocked")


class SingleNodePlanner:
    """默认确定性单节点 Planner；所有 Work Job 均由统一 Scheduler 推进。"""

    def plan(self, *, trigger_id: str, profile: WorkerProfile, prompt: str) -> dict:
        trigger_id = _text(trigger_id, "trigger_id")
        if not isinstance(profile, WorkerProfile):
            raise TypeError("SingleNodePlanner requires a host-owned WorkerProfile")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("single-node prompt must be non-empty")
        return {
            "plan": {
                "nodes": [{
                    "node_id": "work", "role": "work", "profile_id": profile.profile_id,
                    "instruction_ref": f"trigger:{trigger_id}",
                    "instruction_hash": _hash(prompt), "dependencies": [],
                }],
            },
            "acceptance": {"kind": "single_worker_report", "schema_version": 1},
        }


class CoordinatorExecutionService:
    """统一协调执行入口，调度、交付和恢复共用同一账本。"""

    def __init__(self, store, authorization, sources, executor: ExecutionAdapter, *,
                 instance_id: str, planner=None, system_profiles: Mapping[str, str] | None = None,
                 watchdog=None):
        if executor.instance_id != instance_id:
            raise ValueError("ExecutorAdapter instance_id must match CoordinatorHost")
        self._store = store
        self._authorization = authorization
        self._sources = sources
        self._executor = executor
        self.instance_id = instance_id
        self._profiles = dict(executor.profiles)
        if system_profiles is None:
            self._system_profiles = None
        else:
            if (not isinstance(system_profiles, Mapping)
                    or set(system_profiles) != {"integrate", "review"}):
                raise ValueError("system_profiles must define exactly integrate and review")
            selected = {
                role: _text(system_profiles[role], role + " profile")
                for role in ("integrate", "review")
            }
            if len(set(selected.values())) != 2:
                raise ValueError("Integrator and Reviewer must use distinct host-owned profiles")
            for role, profile_id in selected.items():
                if not isinstance(self._profiles.get(profile_id), WorkerProfile):
                    raise ValueError(f"{role} profile must be owned by this ExecutorAdapter")
            integrator = self._profiles[selected["integrate"]]
            reviewer = self._profiles[selected["review"]]
            integrator_effects = self._profile_tool_effects(integrator)
            if ("workspace_write" not in integrator_effects.values()
                    or any(effect not in {"read", "workspace_write", "execute"}
                           for effect in integrator_effects.values())):
                raise ValueError("Integrator profile requires workspace-write tools and cannot use external or unknown effects")
            if not reviewer.tools:
                raise ValueError("Reviewer profile must expose read-only inspection tools")
            reviewer_effects = self._profile_tool_effects(reviewer)
            if any(effect != "read" for effect in reviewer_effects.values()):
                raise ValueError("Reviewer tools must all declare the read effect")
            self._system_profiles = selected
        self._planner = planner if planner is not None else SingleNodePlanner()
        if not callable(getattr(self._planner, "plan", None)):
            raise TypeError("V2 planner must expose plan(trigger_id, profile, prompt)")
        if not self._profiles:
            raise ValueError("V2 execution requires at least one WorkerProfile")
        max_workers = getattr(executor, "max_workers", 32)
        policy = CoordinatorScheduler(
            max_active_assignments=min(4, max(1, max_workers)),
            max_active_per_mission=2,
        )
        self._dag_scheduler = CoordinatorDagScheduler(self, policy, watchdog=watchdog)
        self._locks: dict[str, asyncio.Lock] = {}
        self._closed = False
        self._admission_closed = False
        self._control = CoordinatorControl(store)
        self._input_deliveries = CoordinatorInputDelivery(store)
        executor.set_worker_lookup(self._worker_row)

    def _profile_tool_effects(self, profile: WorkerProfile) -> dict[str, str]:
        if not profile.tools:
            return {}
        resolver = getattr(self._executor, "tool_effects", None)
        if not callable(resolver):
            raise ValueError("ExecutorAdapter must expose trusted tool effects for non-empty Profiles")
        effects = resolver(profile)
        if (not isinstance(effects, Mapping) or set(effects) != set(profile.tools)
                or any(not isinstance(effect, str) for effect in effects.values())):
            raise ValueError("ExecutorAdapter returned incomplete or invalid tool effect metadata")
        return dict(effects)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.close()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        closer = getattr(self._executor, "aclose", None)
        if callable(closer):
            result = closer()
            if inspect.isawaitable(result):
                await result
        else:
            self._executor.close()

    async def schedule_dag_missions(self, *, limit: int = 100) -> dict:
        """周期性恢复并调度统一账本中的 DAG Mission。"""
        self._ensure_open()
        return await self._dag_scheduler.schedule_pending(limit=limit)

    async def schedule_dag_mission(self, mission_id: str) -> list[dict]:
        self._ensure_open()
        return await self._dag_scheduler.schedule_mission(mission_id)

    def control_mission(self, mission_id, action, *, request_id, actor):
        """可信宿主控制入口；继续前重新核验授权，重复请求只返回原回执。"""
        self._ensure_open()
        previous = self._control.receipt(request_id)
        if previous is None and action == "resume":
            mission = self._read_one("SELECT ticket_id FROM missions WHERE mission_id=?", (mission_id,))
            ticket = self._authorization.ticket(mission["ticket_id"]) if mission else None
            mandate_id = ((ticket or {}).get("mandate") or {}).get("mandate_id")
            if not mandate_id or self._authorization.revalidate_mandate(mandate_id, actor=actor).get("status") not in {"valid", "affected"}:
                raise HarnessError(tr("superagent.mandate.unavailable"), code="superagent/mandate-unavailable")
        return self._control.command(mission_id, action, request_id=request_id, actor=actor)

    def control_receipt(self, request_id):
        self._ensure_open()
        return self._control.receipt(request_id)

    def mission_control(self, mission_id):
        self._ensure_open()
        return self._control.mode(mission_id)

    def control_status(self, mission_id):
        """只读控制状态；不触发原生回查、恢复写入或资源创建。"""
        self._ensure_open()
        control = self._control.mode(mission_id)
        mission = self._read_one("SELECT state FROM missions WHERE mission_id=?", (mission_id,))
        if mission is None:
            raise LookupError("Mission not found")
        active = self._read_all(
            "SELECT assignment.assignment_id,assignment.state,assignment.error_ref "
            "FROM assignments AS assignment JOIN jobs AS job ON job.job_id=assignment.job_id "
            "WHERE job.mission_id=? AND assignment.state IN "
            "('pending','submitting','accepted','running','capturing','stopping','unknown') "
            "ORDER BY assignment.assignment_id",
            (mission_id,),
        )
        jobs = self._read_all("SELECT job_id,state FROM jobs WHERE mission_id=? ORDER BY job_id", (mission_id,))
        unknown_assignments = [
            item for item in active
            if item["state"] == "unknown" or str(item.get("error_ref") or "").startswith("stop-unknown:")
        ]
        unsettled_jobs = [item for item in jobs if item["state"] not in {"done", "blocked", "cancelled"}]
        stop_state = None
        if control["mode"] == "cancelled":
            if mission["state"] == "cancelled":
                stop_state = "settled"
            elif unknown_assignments or any(item["state"] == "unknown" for item in unsettled_jobs):
                stop_state = "unknown"
            else:
                stop_state = "pending"
        return {
            **control,
            "mission_state": mission["state"],
            "stop_state": stop_state,
            "active_assignments": len(active),
            "unknown_assignments": len(unknown_assignments),
            "unsettled_jobs": len(unsettled_jobs),
        }

    async def reconcile_controls(self, *, limit=100):
        """恢复已持久化的 Cancel 意图；未知停止结果只回查，不重放副作用。"""
        self._ensure_open()
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer between 1 and 200")
        missions = self._read_all(
            "SELECT control.mission_id FROM mission_controls AS control "
            "JOIN missions AS mission ON mission.mission_id=control.mission_id "
            "LEFT JOIN watchdog_snapshots AS watchdog ON watchdog.mission_id=mission.mission_id "
            "WHERE control.mode='cancelled' AND mission.state IN "
            "('planning','running','integrating','reviewing','cancelling','unknown') "
            "ORDER BY COALESCE(watchdog.observed_at,''),mission.updated_at,control.mission_id LIMIT ?",
            (limit,),
        )
        results = []
        for row in missions:
            results.append(await self._reconcile_cancelled_mission(row["mission_id"]))
        return {"processed": len(results), "missions": results}


    async def _reconcile_cancelled_mission(self, mission_id):
        control = self._control.mode(mission_id)
        if control["mode"] != "cancelled":
            return self.control_status(mission_id)
        mission = self._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        if mission is None:
            raise LookupError("Mission not found")
        if mission["state"] == "cancelled":
            return self.control_status(mission_id)
        if mission["state"] in {"completed", "blocked", "rejected", "suppressed"}:
            return self.control_status(mission_id)
        if mission["state"] != "cancelling":
            self._transition(
                "mission", mission_id, "cancelling",
                facts={"cancel_accepted": True},
                changes={"outcome": "cancellation_pending"},
            )

        assignments = self._read_all(
            "SELECT assignment.assignment_id,assignment.state,assignment.error_ref,assignment.run_id "
            "FROM assignments AS assignment JOIN jobs AS job ON job.job_id=assignment.job_id "
            "WHERE job.mission_id=? AND assignment.state IN "
            "('pending','submitting','accepted','running','capturing','stopping','unknown') "
            "ORDER BY assignment.assignment_id",
            (mission_id,),
        )
        for item in assignments:
            assignment = self._assignment(item["assignment_id"])
            if assignment is None:
                continue
            state = assignment["state"]
            if state == "pending" and not assignment.get("run_id"):
                self._transition(
                    "assignment", assignment["assignment_id"], "cancelled",
                    facts={"no_external_side_effect_started": True},
                    changes={"ended_at": _now(), "error_ref": "mission-cancelled-before-submit"},
                )
                continue
            if state in {"submitting", "unknown"}:
                await self.reconcile(assignment["assignment_id"])
                assignment = self._assignment(item["assignment_id"])
                if assignment is None:
                    continue
                state = assignment["state"]
                if state in {"accepted", "running", "capturing"}:
                    await self.stop(assignment["assignment_id"], reason="mission cancelled")
                elif state == "stopping":
                    await self.reconcile(assignment["assignment_id"])
                    assignment = self._assignment(item["assignment_id"])
                    if assignment and assignment["state"] == "capturing":
                        await self.stop(assignment["assignment_id"], reason="mission cancelled")
                continue
            if state == "stopping":
                await self.reconcile(assignment["assignment_id"])
                assignment = self._assignment(item["assignment_id"])
                if assignment and assignment["state"] == "capturing":
                    await self.stop(assignment["assignment_id"], reason="mission cancelled")
                continue
            if state in {"accepted", "running", "capturing"}:
                await self.stop(assignment["assignment_id"], reason="mission cancelled")

        jobs = self._read_all("SELECT * FROM jobs WHERE mission_id=? ORDER BY job_id", (mission_id,))
        for job in jobs:
            if job["state"] not in {"pending", "ready", "checkpointed"}:
                continue
            active_assignment = self._read_one(
                "SELECT 1 AS active FROM assignments WHERE job_id=? AND state IN "
                "('pending','submitting','accepted','running','capturing','stopping','unknown') LIMIT 1",
                (job["job_id"],),
            )
            if active_assignment is None:
                self._transition(
                    "job", job["job_id"], "cancelled",
                    facts={"no_external_side_effect_started": True},
                )

        active = self._read_one(
            "SELECT COUNT(*) AS count FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "WHERE job.mission_id=? AND assignment.state IN "
            "('pending','submitting','accepted','running','capturing','stopping','unknown')",
            (mission_id,),
        )
        jobs = self._read_all("SELECT state FROM jobs WHERE mission_id=?", (mission_id,))
        if active["count"] == 0 and all(job["state"] in {"done", "blocked", "cancelled"} for job in jobs):
            current = self._read_one("SELECT state FROM missions WHERE mission_id=?", (mission_id,))
            if current and current["state"] == "cancelling":
                self._transition(
                    "mission", mission_id, "cancelled",
                    facts={"owned_operations_settled": True},
                    changes={"outcome": "cancelled"},
                )
        return self.control_status(mission_id)

    def _ensure_open(self) -> None:
        if self._closed:
            raise HarnessError("V2 execution service is closed", code="superagent/executor-closed")

    def _lock(self, identity: str) -> asyncio.Lock:
        return self._locks.setdefault(identity, asyncio.Lock())

    def _read_one(self, sql: str, params: tuple = ()) -> dict | None:
        with self._store.transaction() as db:
            row = db.execute(sql, params).fetchone()
            return dict(row) if row is not None else None

    def _read_all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._store.transaction() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def _worker_row(self, worker_id: str) -> dict | None:
        if not isinstance(worker_id, str) or not worker_id:
            return None
        return self._read_one("SELECT * FROM workers WHERE worker_id=?", (worker_id,))

    def _assignment(self, assignment_id: str) -> dict | None:
        return self._read_one(
            "SELECT assignment.*,job.mission_id,job.role,job.state AS job_state,"
            "job.result_hash,mission.ticket_id,mission.state AS mission_state,"
            "mission.plan_revision,mission.repo,mission.base_commit,mission.goal_ref,mission.budgets_json "
            "FROM assignments AS assignment JOIN jobs AS job ON job.job_id=assignment.job_id "
            "JOIN missions AS mission ON mission.mission_id=job.mission_id "
            "WHERE assignment.assignment_id=?", (assignment_id,),
        )

    def _input(self, ticket_id: str, trigger_id: str) -> dict | None:
        return self._read_one(
            "SELECT * FROM ticket_inputs WHERE ticket_id=? AND trigger_id=?",
            (ticket_id, trigger_id),
        )

    def _transition(self, aggregate_type: str, identity: str, target: str, *,
                    facts: dict[str, bool], changes: dict | None = None,
                    audit_payload: object | None = None, correlation_id: str | None = None) -> dict:
        self._ensure_open()
        repo_name = {
            "ticket": "tickets", "mission": "missions", "job": "jobs",
            "assignment": "assignments", "worker": "workers", "attention": "attentions",
        }[aggregate_type]
        with self._store.unit_of_work(actor=_ACTOR, correlation_id=correlation_id or f"{aggregate_type}:{identity}") as uow:
            repo = getattr(uow, repo_name)
            current = repo.get(identity)
            if current is None:
                raise LookupError(f"{aggregate_type} {identity} not found")
            if current["state"] == target:
                return current
            return CoordinatorApplicationService(uow).transition(
                aggregate_type, identity,
                expected_revision=current["aggregate_revision"],
                target_state=target,
                facts={"authorized": True, **facts},
                changes=changes or {},
                audit_payload=audit_payload,
            )

    def _mandate_context(self, ticket: dict, trigger: dict, input_row: dict,
                         *, require_work: bool = True) -> tuple[dict, WorkerProfile, dict]:
        mandate = ticket.get("mandate")
        if not isinstance(mandate, dict) or not mandate.get("mandate_id"):
            raise PermissionError("Ticket has no active Mandate")
        check = self._authorization.revalidate_mandate(mandate["mandate_id"], actor=_ACTOR)
        if check.get("status") not in {"valid", "affected"}:
            raise PermissionError("Ticket Mandate is no longer valid")
        scope = mandate.get("scope") or {}
        source_id = trigger.get("source_instance_id")
        thread_id = trigger.get("primary_thread_id")
        if source_id not in scope.get("source_instance_ids", []):
            raise PermissionError("Trigger source is outside the Ticket Mandate")
        if thread_id not in scope.get("thread_ids", []):
            raise PermissionError("Trigger Thread is outside the Ticket Mandate")
        if require_work and input_row.get("context_only"):
            raise PermissionError("Context-only Ticket Input cannot start a Mission")
        if hasattr(self._executor, "profile_for_source"):
            profile = self._executor.profile_for_source(source_id)
        else:
            matches = [item for item in self._profiles.values() if source_id in item.source_instance_ids]
            profile = matches[0] if len(matches) == 1 else None
        if profile is None:
            raise PermissionError("No unique host-owned WorkerProfile maps this Trigger source")
        if not set(profile.actions) <= set(scope.get("actions", [])):

            raise PermissionError("WorkerProfile actions exceed the Ticket Mandate")
        if not set(profile.tools) <= set(scope.get("tools", [])):
            raise PermissionError("WorkerProfile tools exceed the Ticket Mandate")
        budget = scope.get("budget") or {}
        max_turns = budget.get("max_turns", profile.max_turns)
        max_seconds = budget.get("max_seconds", profile.max_seconds)
        if type(max_turns) is not int or type(max_seconds) is not int:
            raise PermissionError("Ticket Mandate execution budget is invalid")
        # Mission 的全局执行预算取自 Ticket Mandate；各角色的 WorkerProfile
        # 上限由 _profile_for_job 分别执行，不能让默认来源 Profile 限制整个 DAG。
        effective = {
            "max_turns": min(profile.max_turns, max_turns) if require_work else max_turns,
            "max_seconds": min(profile.max_seconds, max_seconds) if require_work else max_seconds,
        }
        if effective["max_turns"] < 1 or effective["max_seconds"] < 1:
            raise PermissionError("Ticket Mandate has no remaining execution budget")
        if ticket.get("state") not in {"open", "ended"}:
            raise PermissionError("Ticket is not eligible for execution")
        if ticket.get("state") == "ended":
            self._reopen_for_human_followup(ticket, trigger, input_row, mandate)
        return mandate, profile, effective

    def _reopen_for_human_followup(self, ticket: dict, trigger: dict, input_row: dict, mandate: dict) -> None:
        actor = trigger.get("actor") or {}
        if input_row.get("context_only") or actor.get("kind", actor.get("actor_kind")) != "human":
            raise PermissionError("Only a human work follow-up can resume a non-human-ended Ticket")
        current = self._read_one("SELECT * FROM tickets WHERE ticket_id=?", (ticket["ticket_id"],))
        if current is None or current["state"] != "ended" or current.get("ended_by") == "human":
            raise PermissionError("Human-ended Ticket cannot be reopened")
        self._transition(
            "ticket", ticket["ticket_id"], "open",
            facts={"human_followup": True, "mandate_valid": True},
            changes={"ended_by": None, "ended_at": None, "outcome": None},
            correlation_id=f"ticket:{ticket['ticket_id']}:human-followup",
        )

    def _trigger_payload(self, trigger: dict) -> dict:
        return {
            "trigger_id": trigger["trigger_id"],
            "source_instance_id": trigger["source_instance_id"],
            "thread_id": trigger.get("primary_thread_id"),
            "actor": trigger.get("actor"),
            "body": trigger.get("body"),
            "metadata": trigger.get("metadata"),
            "body_ref": trigger.get("body_ref"),
            "body_hash": trigger.get("body_hash"),
        }

    def _build_prompt(self, ticket: dict, trigger: dict, mandate: dict) -> tuple[str, list[str]]:
        event, input_ids = self._build_work_input(ticket, trigger, mandate)
        prompt = tr("superagent.worker_input", event=_json(event))
        prompt += "\n\n" + tr("superagent.worker_report_contract")
        return prompt, input_ids

    def _build_work_input(self, ticket: dict, trigger: dict, mandate: dict) -> tuple[dict, list[str]]:
        """冻结结构化任务数据，让新阶段按当前语言重新解析产品提示。"""
        inputs = self._read_all(
            "SELECT input_id,trigger_id,context_only,state,created_at FROM ticket_inputs "
            "WHERE ticket_id=? ORDER BY created_at,input_id", (ticket["ticket_id"],),
        )
        selected = [
            row for row in inputs
            if row["state"] != "handed_back"
            and (row["state"] == "read" or row["context_only"] or row["trigger_id"] == trigger["trigger_id"])
        ]
        context = []
        input_ids = []
        total = 0
        for row in selected:
            item_trigger = trigger if row["trigger_id"] == trigger["trigger_id"] else self._sources.get_trigger(row["trigger_id"])
            if item_trigger is None:
                raise HarnessError("Ticket context references a missing Trigger", code="superagent/context-corrupt")
            context.append({
                "input_id": row["input_id"], "context_only": bool(row["context_only"]),
                "state": row["state"], "trigger": self._trigger_payload(item_trigger),
            })
            encoded = _json(context[-1])
            total += len(encoded.encode("utf-8"))
            if total > 512 * 1024:
                raise HarnessError("Ticket context exceeds the bounded Worker prompt size", code="superagent/context-too-large")
            if row["state"] == "waiting" and (row["context_only"] or row["trigger_id"] == trigger["trigger_id"]):
                input_ids.append(row["input_id"])
        event = {
            "kind": "ticket_work", "ticket_id": ticket["ticket_id"],
            "origin_trigger_id": ticket["origin_trigger_id"],
            "current_trigger_id": trigger["trigger_id"],
            "current": self._trigger_payload(trigger),
            "ticket_context": context,
            "mandate": {
                "policy_version": mandate.get("policy_version"),
                "source_instance_ids": mandate["scope"].get("source_instance_ids", []),
                "thread_ids": mandate["scope"].get("thread_ids", []),
                "actions": mandate["scope"].get("actions", []),
                "tools": mandate["scope"].get("tools", []),
            },
        }
        event["attention_answers"] = self._read_all(
            "SELECT attention_id,question_text,options_json,answer_trigger_id FROM attentions "
            "WHERE ticket_id=? AND state='answered' AND answer_trigger_id=? ORDER BY created_at LIMIT 16",
            (ticket["ticket_id"], trigger["trigger_id"]),
        )
        return event, input_ids

    def route_attention_answer(self, ticket_id, trigger_id):
        """唯一已收尾问题的人工跟进建立新有界活动，不重放旧失败运行。"""
        input_row = self._input(ticket_id, trigger_id)
        if input_row is None or input_row["context_only"] or input_row["state"] != "waiting":
            return None
        rows = self._read_all(
            "SELECT attention.* FROM attentions attention JOIN missions mission ON mission.mission_id=attention.mission_id "
            "WHERE attention.ticket_id=? AND attention.state='open' AND mission.state='blocked' ORDER BY attention.created_at",
            (ticket_id,),
        )
        trigger = self._sources.get_trigger(trigger_id)
        selected = (trigger or {}).get("metadata", {}).get("attention_id")
        if not rows and selected is None:
            return None
        if selected is not None:
            rows = [row for row in rows if row["attention_id"] == selected]
        if len(rows) != 1:
            return {"state": "attention_required", "ticket_id": ticket_id, "trigger_id": trigger_id}
        if rows[0]["trigger_id"] == trigger_id:
            return {"state": "attention_required", "ticket_id": ticket_id, "trigger_id": trigger_id}
        self.resolve_attention(rows[0]["attention_id"], trigger_id)
        return None

    def _mark_input_db(self, db, input_id: str, target: str, *,
                       assignment_id: str | None = None, delivery_key: str | None = None) -> dict:
        row = db.execute("SELECT * FROM ticket_inputs WHERE input_id=?", (input_id,)).fetchone()
        if row is None:
            raise LookupError("Ticket Input not found")
        current = dict(row)
        if current["state"] == target:
            if target != "delivered" or (current.get("delivered_to_assignment_id") == assignment_id
                                         and current.get("delivery_key") == delivery_key):
                return current
        if target == "delivered":
            if current["state"] != "waiting" or not assignment_id or not delivery_key:
                raise PermissionError("Only waiting Ticket Inputs can be delivered")
            delivered_at, read_at = _now(), None
            owner, key = assignment_id, delivery_key
        elif target == "read":
            if current["state"] != "delivered" or not current.get("delivered_to_assignment_id"):
                raise PermissionError("Ticket Input can be read only after confirmed delivery")
            delivered_at, read_at = current.get("delivered_at"), _now()
            owner, key = current.get("delivered_to_assignment_id"), current.get("delivery_key")
        elif target == "waiting":
            if current["state"] != "delivered":
                return current
            delivered_at, read_at, owner, key = None, None, None, None
        else:
            raise ValueError("unsupported Ticket Input transition")
        changed = db.execute(
            "UPDATE ticket_inputs SET state=?,delivered_to_assignment_id=?,delivery_key=?,"
            "delivered_at=?,read_at=?,input_revision=input_revision+1 "
            "WHERE input_id=? AND input_revision=?",
            (target, owner, key, delivered_at, read_at, input_id, current["input_revision"]),
        ).rowcount
        if changed != 1:
            raise HarnessError("Ticket Input changed concurrently", code="superagent/revision-conflict")
        if target == "delivered":
            delivery = self._input_deliveries.prepare_in_transaction(db, input_id, assignment_id,
                delivery_key=delivery_key, initial_state="submitting", actor=_ACTOR)
            if delivery["state"] not in {"pending", "submitting", "applied"}:
                raise HarnessError(tr("superagent.conflict"), code="superagent/input-delivery-conflict")
        elif owner is not None and target == "read":
            delivery = db.execute("SELECT * FROM input_deliveries WHERE input_id=? AND assignment_id=?", (input_id, owner)).fetchone()
            if delivery is None:
                raise HarnessError(tr("superagent.corrupt"), code="superagent/input-delivery-missing")
            if delivery["state"] != "applied":
                self._input_deliveries.transition_in_transaction(db, input_id, owner, "applied",
                    expected_revision=delivery["aggregate_revision"],
                    receipt=self._input_deliveries.make_receipt(dict(delivery), "applied", native_receipt={"source": "original_run_input_receipt"}), actor=_ACTOR)
        CoordinatorStore.append_audit(
            db, aggregate_type="ticket_input", aggregate_id=input_id,
            event_type=f"ticket_input.{target}",
            payload={"ticket_id": current["ticket_id"], "trigger_id": current["trigger_id"],
                     "assignment_id": owner, "delivery_key": key, "input_revision": current["input_revision"] + 1},
            actor=_ACTOR, correlation_id=f"ticket:{current['ticket_id']}",
        )
        return dict(db.execute("SELECT * FROM ticket_inputs WHERE input_id=?", (input_id,)).fetchone())

    def _mark_input(self, input_id: str, target: str, *, assignment_id: str | None = None,
                    delivery_key: str | None = None) -> dict:
        with self._store.transaction(write=True) as db:
            return self._mark_input_db(db, input_id, target, assignment_id=assignment_id, delivery_key=delivery_key)

    def _worker_identity(self, ticket_id: str, profile: WorkerProfile, base_commit: str,
                         *, mission_id: str, job_id: str, stage: int) -> tuple[str, str]:
        worker_id = "worker_" + _hash(_json([self.instance_id, mission_id, job_id, stage]))[:32]
        identity_hash = _hash(_json({
            "instance_id": self.instance_id, "ticket_id": ticket_id,
            "mission_id": mission_id, "job_id": job_id, "stage": stage,
            "profile_id": profile.profile_id, "profile_fingerprint": profile.fingerprint,
            "repo": profile.repo, "base_commit": base_commit,
        }))
        return worker_id, identity_hash

    def stop_admission(self):
        """关闭时只撤销新执行准入，已受理操作仍沿原身份收尾。"""
        self._admission_closed = True

    def _admit_mission(self, mission_id: str) -> dict:
        """在每次新资源或写入边界重新核验持久控制与当前授权。"""
        if self._admission_closed:
            raise HarnessError(tr('superagent.closed'), code='superagent/closed')
        self._ensure_open()
        mission = self._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        if mission is None or mission["state"] not in {"planning", "running", "integrating", "reviewing"}:
            raise HarnessError(tr("superagent.mission.not-running"), code="superagent/mission-not-running")
        if not self._control.allows(mission_id):
            raise HarnessError(tr("superagent.mission.paused"), code="superagent/mission-paused")
        ticket = self._authorization.ticket(mission["ticket_id"])
        mandate_id = ((ticket or {}).get("mandate") or {}).get("mandate_id")
        if not mandate_id or self._authorization.revalidate_mandate(mandate_id, actor=_ACTOR).get("status") not in {"valid", "affected"}:
            raise HarnessError(tr("superagent.mandate.unavailable"), code="superagent/mandate-unavailable")
        return mission

    async def _ensure_worker(self, ticket_id: str, profile: WorkerProfile, mandate_valid: bool,
                             *, mission: dict, job: dict, stage: int = 1) -> dict:
        self._admit_mission(mission["mission_id"])
        reserve=getattr(self._executor,'reserve_mission',None)
        if callable(reserve):reserve(mission['mission_id'])

        base_commit = mission["base_commit"]
        worker_id, identity_hash = self._worker_identity(ticket_id, profile, base_commit,
            mission_id=mission["mission_id"], job_id=job["job_id"], stage=stage)
        existing = self._worker_row(worker_id)
        if existing is None:
            with self._store.unit_of_work(actor=_ACTOR, correlation_id=f"ticket:{ticket_id}") as uow:
                count = uow.db.execute("SELECT COUNT(*) FROM workers WHERE runtime_instance_id=?", (self.instance_id,)).fetchone()[0]
                if count >= getattr(self._executor, "max_workers", 32):
                    raise HarnessError(tr("superagent.worker-capacity"), code="superagent/worker-capacity")
                existing = uow.workers.create({
                    "worker_id": worker_id, "ticket_id": ticket_id,
                    "mission_id": mission["mission_id"], "job_id": job["job_id"], "stage": stage,
                    "profile_id": profile.profile_id, "repo": profile.repo,
                    "base_commit": base_commit, "runtime_instance_id": self.instance_id,
                    "identity_hash": identity_hash,
                })
        if existing["identity_hash"] != identity_hash or existing["repo"] != profile.repo:
            raise PermissionError("Persisted Worker identity does not match the current host-owned Profile")
        async with self._lock(worker_id):
            return await self._ensure_worker_locked(existing, profile, mandate_valid)

    async def _ensure_worker_locked(self, worker: dict, profile: WorkerProfile, mandate_valid: bool) -> dict:
        state = worker["state"]
        if state == "planned":
            self._admit_mission(worker["mission_id"])
            worker = self._transition("worker", worker["worker_id"], "workspace_creating", facts={"worker_profile_valid": True, "mandate_valid": mandate_valid, "control_allows": True})
            try:
                await self._executor.create_workspace(worker, profile)
            except Exception:
                found = self._executor.find_worker(worker, profile)
                if found is None:
                    self._worker_unknown(worker, "workspace")
                    raise
            found = self._executor.find_worker(worker, profile)
            if found is None or not found.get("workspace_id"):
                self._worker_unknown(worker, "workspace")
                raise HarnessError("Worker Worktree cannot be verified", code="superagent/worker-unknown")
            worker = self._transition("worker", worker["worker_id"], "workspace_ready", facts={"workspace_identity_verified": True}, changes={"workspace_id": found["workspace_id"]})
            state = "workspace_ready"
        elif state == "workspace_creating":
            found = self._executor.find_worker(worker, profile)
            if found is None or not found.get("workspace_id"):
                self._worker_unknown(worker, "workspace")
                raise HarnessError("Worker Worktree creation outcome is unknown", code="superagent/worker-unknown")
            worker = self._transition("worker", worker["worker_id"], "workspace_ready", facts={"workspace_identity_verified": True}, changes={"workspace_id": found["workspace_id"]})
            state = "workspace_ready"
        elif state == "unknown":
            found = self._executor.find_worker(worker, profile)
            if found is None or not found.get("workspace_id"):
                raise HarnessError("Unknown Worker resources cannot be reconciled", code="superagent/worker-unknown")
            if found.get("session_id"):
                worker = self._transition("worker", worker["worker_id"], "ready", facts={"reconciliation_verified": True, "session_identity_verified": True, "tools_restricted": True}, changes={"workspace_id": found["workspace_id"], "session_id": found["session_id"]}, audit_payload={"reconciliation_ref": f"worker:{worker['worker_id']}:session"})
                state = "ready"
            else:
                worker = self._transition("worker", worker["worker_id"], "workspace_ready", facts={"reconciliation_verified": True, "workspace_identity_verified": True}, changes={"workspace_id": found["workspace_id"]}, audit_payload={"reconciliation_ref": f"worker:{worker['worker_id']}:workspace"})
                state = "workspace_ready"
        if state == "workspace_ready":
            self._admit_mission(worker["mission_id"])
            worker = self._transition("worker", worker["worker_id"], "session_creating", facts={"workspace_identity_verified": True, "mandate_valid": mandate_valid, "control_allows": True})
            try:
                await self._executor.create_session(worker, profile)
            except Exception:
                found = self._executor.find_worker(worker, profile)
                if found is None or not found.get("session_id"):
                    self._worker_unknown(worker, "session")
                    raise
            found = self._executor.find_worker(worker, profile)
            if found is None or not found.get("session_id"):
                self._worker_unknown(worker, "session")
                raise HarnessError("Worker Session cannot be verified", code="superagent/worker-unknown")
            worker = self._transition("worker", worker["worker_id"], "ready", facts={"session_identity_verified": True, "tools_restricted": True}, changes={"session_id": found["session_id"]})
            state = "ready"
        elif state == "session_creating":
            found = self._executor.find_worker(worker, profile)
            if found is None or not found.get("session_id"):
                self._worker_unknown(worker, "session")
                raise HarnessError("Worker Session creation outcome is unknown", code="superagent/worker-unknown")
            worker = self._transition("worker", worker["worker_id"], "ready", facts={"session_identity_verified": True, "tools_restricted": True}, changes={"session_id": found["session_id"]})
            state = "ready"
        if state != "ready":
            raise HarnessError(f"Worker is not ready (state={state})", code="superagent/worker-unavailable")
        found = self._executor.find_worker(worker, profile)
        if found is None or found.get("session_id") != worker["session_id"]:
            self._worker_unknown(worker, "ready-check")
            raise HarnessError("Ready Worker resources do not match", code="superagent/worker-unknown")
        return worker

    def _worker_unknown(self, worker: dict, phase: str) -> None:
        self._transition("worker", worker["worker_id"], "unknown", facts={"operation_outcome_uncertain": True}, audit_payload={"reconciliation_ref": f"worker:{worker['worker_id']}:{phase}"})

    def _assignment_for_trigger(self, ticket_id: str, trigger_id: str) -> dict | None:
        return self._read_one(
            "SELECT assignment.*,job.mission_id,job.role,job.state AS job_state,"
            "mission.ticket_id,mission.state AS mission_state,mission.plan_revision "
            "FROM missions AS mission JOIN jobs AS job ON job.mission_id=mission.mission_id "
            "JOIN assignments AS assignment ON assignment.job_id=job.job_id "
            "WHERE mission.ticket_id=? AND mission.goal_ref=? ORDER BY assignment.attempt_no LIMIT 1",
            (ticket_id, trigger_id),
        )

    def _active_assignment(self, ticket_id: str) -> dict | None:
        placeholders = ",".join("?" for _ in _ACTIVE_ASSIGNMENTS)
        return self._read_one(
            "SELECT assignment.*,job.mission_id,job.role,job.state AS job_state,"
            "mission.ticket_id,mission.state AS mission_state,mission.plan_revision "
            "FROM missions AS mission JOIN jobs AS job ON job.mission_id=mission.mission_id "
            "JOIN assignments AS assignment ON assignment.job_id=job.job_id "
            f"WHERE mission.ticket_id=? AND mission.state IN ('planning','running','integrating','reviewing','cancelling','unknown') "
            f"AND assignment.state IN ({placeholders}) ORDER BY mission.sequence DESC,assignment.attempt_no DESC LIMIT 1",
            (ticket_id, *_ACTIVE_ASSIGNMENTS),
        )

    def _create_mission_plan(self, ticket, trigger, mandate, profile, budget, prompt, *, frozen_plan=None):
        """先建立冻结目标和控制记录，再创建任何执行资源。"""
        ticket_id, trigger_id = ticket["ticket_id"], trigger["trigger_id"]
        mission_id = "mission_" + _hash(f"{ticket_id}\0{trigger_id}")[:32]
        original = self._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        base_commit = original["base_commit"] if original else self._executor.base_commit(profile)
        if original is None and frozen_plan is None:
            frozen_plan = self._planner.plan(trigger_id=trigger_id, profile=profile, prompt=prompt)
            if inspect.isawaitable(frozen_plan):
                if inspect.iscoroutine(frozen_plan):
                    frozen_plan.close()
                raise HarnessError(tr("superagent.coordinator.output"), code="superagent/plan-invalid")
        with self._store.unit_of_work(actor=_ACTOR, correlation_id=f"ticket:{ticket_id}") as uow:
            mission = uow.missions.get(mission_id)
            if mission is None:
                sequence = uow.db.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM missions WHERE ticket_id=?", (ticket_id,),
                ).fetchone()[0]
                mission = uow.missions.create({
                    "mission_id": mission_id, "ticket_id": ticket_id, "sequence": sequence,
                    "goal_ref": trigger_id, "policy_version_at_start": mandate.get("policy_version"),
                    "source_revision_at_start": trigger.get("source_revision"),
                    "repo": profile.repo, "base_commit": base_commit, "budgets_json": budget,
                })
                if not isinstance(frozen_plan, dict):
                    raise HarnessError("Planner must return a structured frozen plan", code="superagent/plan-invalid")
                plan_value = frozen_plan.get("plan")
                planned_nodes = plan_value.get("nodes") if isinstance(plan_value, dict) else None
                acceptance = frozen_plan.get("acceptance")
                if not isinstance(planned_nodes, list) or not planned_nodes:
                    raise HarnessError("Planner must return a non-empty Work DAG", code="superagent/plan-invalid")
                scope = mandate.get("scope") or {}
                source_id = trigger.get("source_instance_id")
                node_profiles = []
                for node in planned_nodes:
                    if not isinstance(node, dict) or node.get("role") != "work":
                        raise HarnessError("Planner may propose Work nodes only", code="superagent/plan-invalid")
                    profile_id = node.get("profile_id")
                    if (not isinstance(profile_id, str) or not profile_id.strip()
                            or profile_id != profile_id.strip() or len(profile_id) > 128):
                        raise HarnessError("Planner Work node has an invalid Profile identity", code="superagent/plan-invalid")
                    selected_profile = self._profiles.get(profile_id)
                    if not isinstance(selected_profile, WorkerProfile):
                        raise PermissionError("Planner selected a WorkerProfile not owned by this Host")
                    if (selected_profile.repo != profile.repo or source_id not in selected_profile.source_instance_ids
                            or not set(selected_profile.actions) <= set(scope.get("actions", []))
                            or not set(selected_profile.tools) <= set(scope.get("tools", []))):
                        raise PermissionError("Planner Work profile exceeds the frozen Mission repository or Mandate scope")
                    node_profiles.append(selected_profile)
                report_only = (
                    isinstance(acceptance, dict) and acceptance.get("kind") == "single_worker_report"
                    and type(acceptance.get("schema_version")) is int
                    and acceptance.get("schema_version") == 1 and len(planned_nodes) == 1
                    and node_profiles[0].max_work_turns is None
                    and all(effect == "read" for effect in self._profile_tool_effects(node_profiles[0]).values())
                )

                system_profiles = None
                if not report_only:
                    if isinstance(acceptance, dict) and acceptance.get("kind") == "single_worker_report":
                        raise HarnessError("single_worker_report requires exactly one read-only Work node",
                                           code="superagent/acceptance-invalid")
                    if self._system_profiles is None:
                        raise HarnessError("Artifact-producing Mission requires host-owned Integrator and Reviewer profiles",
                                           code="superagent/quality-gate-unavailable")
                    if (not isinstance(acceptance, dict) or acceptance.get("kind") != "artifact_review"
                            or type(acceptance.get("schema_version", 1)) is not int
                            or acceptance.get("schema_version", 1) != 1):
                        raise HarnessError("Artifact-producing Mission requires versioned artifact_review acceptance",
                                           code="superagent/acceptance-invalid")
                    criteria = acceptance.get("criteria")
                    if not isinstance(criteria, list) or not criteria or any(
                        not isinstance(item, dict) or not isinstance(item.get("id"), str)
                        or not item["id"].strip() or item["id"] != item["id"].strip() or len(item["id"]) > 128
                        or not isinstance(item.get("kind"), str) or not item["kind"].strip()
                        or item["kind"] != item["kind"].strip() or len(item["kind"]) > 64
                        for item in criteria
                    ):
                        raise HarnessError("Artifact review acceptance requires explicit criteria",
                                           code="superagent/acceptance-invalid")
                    ids = [item["id"] for item in criteria]
                    if len(ids) != len(set(ids)):
                        raise HarnessError("Artifact review criteria ids must be unique",
                                           code="superagent/acceptance-invalid")
                    # 修复轮次由宿主设定上限，避免 Planner 将质量门禁扩展为无界重试。
                    max_repair_rounds = acceptance.get("max_repair_rounds", 1)
                    if type(max_repair_rounds) is not int or not 0 <= max_repair_rounds <= 3:
                        raise HarnessError("Artifact review max_repair_rounds must be between 0 and 3",
                                           code="superagent/acceptance-invalid")
                    acceptance = {
                        **acceptance, "schema_version": 1,
                        "max_repair_rounds": max_repair_rounds,
                    }
                    for role, profile_id in self._system_profiles.items():
                        system_profile = self._profiles[profile_id]
                        if (system_profile.repo != profile.repo or source_id not in system_profile.source_instance_ids
                                or not set(system_profile.actions) <= set(scope.get("actions", []))
                                or not set(system_profile.tools) <= set(scope.get("tools", []))):
                            raise PermissionError(f"Host-owned {role} profile exceeds the frozen Mission repository or Mandate scope")
                    system_profiles = self._system_profiles
                work_input = self._build_work_input(ticket, trigger, mandate)[0] if any(
                    selected.max_work_turns is not None for selected in node_profiles
                ) else None
                work_nodes = []
                for node, selected in zip(planned_nodes, node_profiles, strict=True):
                    if selected.max_work_turns is not None:
                        self._dag_scheduler._node_prompt(prompt, node)
                    work_nodes.append({
                        **node, "max_work_turns": selected.max_work_turns,
                        "work_instruction": ({"request": work_input, "node_instruction": node.get("instruction")}
                                             if selected.max_work_turns is not None else None),
                    })
                plan = uow.plans.create(
                    mission_id, expected_mission_revision=mission["aggregate_revision"],
                    plan={**frozen_plan["plan"], "nodes": work_nodes}, acceptance=acceptance,
                    created_by_kind="host", created_by_ref="host-planner",
                    system_profiles=system_profiles,
                )
                if plan is None:
                    raise HarnessError(tr("superagent.conflict"), code="superagent/revision-conflict")
                mission = uow.missions.get(mission_id)
            plan = uow.plans.get(mission_id, mission["plan_revision"])
            if plan is None or not plan["jobs"]:
                raise HarnessError(tr("superagent.corrupt"), code="superagent/mission-corrupt")
            return mission, plan

    def _create_mission_assignment(self, ticket, trigger, mandate, profile, budget, worker,
                                   prompt, input_ids):
        mission_id = worker["mission_id"]
        assignment_id = "assignment_" + _hash(f"{mission_id}\0work\0{1}")[:32]
        with self._store.unit_of_work(actor=_ACTOR, correlation_id=f"ticket:{ticket['ticket_id']}") as uow:
            mission = uow.missions.get(mission_id)
            job = uow.jobs.get(worker["job_id"])
            assignment = uow.assignments.get(assignment_id)
            if assignment is not None:
                return mission, job, {**assignment, "max_seconds": budget["max_seconds"]}
            control = uow.db.execute("SELECT mode FROM mission_controls WHERE mission_id=?", (mission_id,)).fetchone()
            if control is None or control["mode"] != "running":
                raise HarnessError(tr("superagent.mission.paused"), code="superagent/mission-paused")
            app = CoordinatorApplicationService(uow)
            if mission["state"] == "planning":
                mission = app.transition("mission", mission_id, expected_revision=mission["aggregate_revision"],
                    target_state="running", facts={"authorized": True, "plan_valid": True,
                    "base_commit_frozen": True, "mandate_valid": True, "control_allows": True})
            if job["state"] == "pending":
                job = app.transition("job", job["job_id"], expected_revision=job["aggregate_revision"],
                    target_state="ready", facts={"authorized": True, "dependencies_satisfied": True,
                    "mandate_valid": True, "control_allows": True})
            assignment = uow.assignments.create({"assignment_id": assignment_id, "job_id": job["job_id"],
                "attempt_no": 1, "worker_id": worker["worker_id"], "session_id": worker["session_id"],
                "workspace_id": worker["workspace_id"], "submission_key": f"superagent-v2:{assignment_id}",
                "report_json": {"pending_prompt": prompt, "pending_input_ids": input_ids}})
            assignment = app.transition("assignment", assignment_id, expected_revision=assignment["aggregate_revision"],
                target_state="submitting", facts={"authorized": True, "stable_submission_persisted": True,
                "mandate_valid": True, "control_allows": True})
            for input_id in input_ids:
                self._mark_input_db(uow.db, input_id, "delivered", assignment_id=assignment_id,
                    delivery_key=f"superagent-v2:input:{input_id}:{assignment_id}")
            return mission, job, {**assignment, "max_seconds": budget["max_seconds"]}

    async def route_trigger(self, ticket_id: str, trigger_id: str) -> dict:
        self._ensure_open()
        async with self._lock(f"ticket:{ticket_id}"):
            ticket = self._authorization.ticket(ticket_id)
            if ticket is None:
                raise LookupError("Ticket not found")
            trigger = self._sources.get_trigger(trigger_id)
            if trigger is None:
                raise LookupError("Trigger not found")
            input_row = self._input(ticket_id, trigger_id)
            if input_row is None:
                raise PermissionError("Trigger is not an authorized Ticket Input")
            if input_row["state"] == "handed_back":
                return {"state": "handed_back", "duplicate": True, "trigger_id": trigger_id}
            mandate, profile, budget = self._mandate_context(ticket, trigger, input_row, require_work=False)
            existing = self._assignment_for_trigger(ticket_id, trigger_id)
            if existing is not None:
                result = await self.reconcile(existing["assignment_id"])
                scheduled = await self._dag_scheduler.schedule_mission(existing["mission_id"])
                if any(item.get("state") == "queued" for item in scheduled):
                    raise HarnessError(
                        "Mission is paused; execution remains queued",
                        code="superagent/mission-paused",
                    )
                if scheduled:
                    result["scheduled_assignments"] = [
                        item.get("assignment_id") for item in scheduled if item.get("assignment_id")
                    ]
                return result
            if input_row["state"] == "delivered" and input_row.get("delivered_to_assignment_id"):
                await self.reconcile(input_row["delivered_to_assignment_id"])
                input_row = self._input(ticket_id, trigger_id)
                if input_row["state"] in {"read", "handed_back", "delivered"}:
                    return {"state": input_row["state"], "ticket_id": ticket_id, "trigger_id": trigger_id,
                            "assignment_id": input_row.get("delivered_to_assignment_id")}
            if input_row["state"] == "read":
                return {"state": "read", "ticket_id": ticket_id, "trigger_id": trigger_id}
            active = self._active_assignment(ticket_id)
            if active is not None:
                worker = self._worker_row(active["worker_id"])
                if worker is None or worker["profile_id"] != profile.profile_id:
                    raise PermissionError("Active Ticket Worker does not match the source's host-owned Profile")
                if active["state"] in {"accepted", "running"} and input_row["state"] == "waiting":
                    return await self._deliver_input(ticket, trigger, input_row, mandate, active, worker)
                return {"state": active["state"], "ticket_id": ticket_id,
                        "assignment_id": active["assignment_id"], "input_state": input_row["state"]}
            if input_row["context_only"]:
                return {"state": "queued_context", "ticket_id": ticket_id, "trigger_id": trigger_id}
            turns_used = self._read_one(
                "SELECT COUNT(*) AS total FROM assignments AS assignment "
                "JOIN jobs AS job ON job.job_id=assignment.job_id "
                "JOIN missions AS mission ON mission.mission_id=job.mission_id "
                "WHERE mission.ticket_id=? AND assignment.state<>'rejected'", (ticket_id,),
            )["total"]
            if turns_used >= budget["max_turns"]:
                raise PermissionError("Ticket Mandate execution turn budget is exhausted")
            prompt, _ = self._build_prompt(ticket, trigger, mandate)
            mission_id = "mission_" + _hash(f"{ticket_id}\0{trigger_id}")[:32]
            frozen_plan = None
            if self._read_one("SELECT mission_id FROM missions WHERE mission_id=?", (mission_id,)) is None:
                if self._admission_closed:
                    raise HarnessError(tr('superagent.closed'), code='superagent/closed')
                frozen_plan = self._planner.plan(trigger_id=trigger_id, profile=profile, prompt=prompt)
                if inspect.isawaitable(frozen_plan):
                    frozen_plan = await frozen_plan
                if self._admission_closed:
                    raise HarnessError(tr('superagent.closed'), code='superagent/closed')
                # 模型等待后重新核验来源、预算与授权；规划结果不能放宽权限。
                ticket = self._authorization.ticket(ticket_id)
                mandate, profile, budget = self._mandate_context(ticket, trigger, input_row, require_work=True)
            mission, _ = self._create_mission_plan(ticket, trigger, mandate, profile, budget, prompt, frozen_plan=frozen_plan)

            # Single-node reports and multi-node Work DAGs enter the same durable
            # Scheduler, Assignment creation, submission, and reconciliation path.
            scheduled = await self._dag_scheduler.schedule_mission(mission["mission_id"])
            if any(item.get("state") == "queued" for item in scheduled):
                raise HarnessError(
                    "Mission is paused; execution remains queued",
                    code="superagent/mission-paused",
                )
            if scheduled:
                receipt = dict(scheduled[0])
                receipt.setdefault("mission_id", mission["mission_id"])
                receipt["scheduled_assignments"] = [
                    item.get("assignment_id") for item in scheduled if item.get("assignment_id")
                ]
                return receipt
            current = self._read_one("SELECT state FROM missions WHERE mission_id=?", (mission["mission_id"],))
            return {
                "state": current["state"] if current is not None else "queued",
                "mission_id": mission["mission_id"],
                "scheduled_assignments": [],
            }

    def _assert_delivery_still_authorized(self, ticket_id: str, assignment_id: str,
                                          trigger: dict, mandate_id: str) -> None:
        assignment = self._assignment(assignment_id)
        if assignment is None:
            raise HarnessError(tr("superagent.conflict"), code="superagent/assignment-unavailable")
        self._admit_mission(assignment["mission_id"])
        ticket = self._authorization.ticket(ticket_id)
        if ticket is None or ticket["state"] != "open":
            raise PermissionError("Ticket stopped while delivering follow-up input")
        mandate = self._authorization.revalidate_mandate(mandate_id, actor=_ACTOR)
        if mandate.get("status") not in {"valid", "affected"}:
            raise PermissionError("Mandate was revoked while delivering follow-up input")
        active = self._active_assignment(ticket_id)
        if active is None or active["assignment_id"] != assignment_id or active["state"] not in {"accepted", "running"}:
            raise PermissionError("Assignment is no longer eligible for follow-up delivery")

    async def _deliver_input(self, ticket: dict, trigger: dict, input_row: dict,
                             mandate: dict, assignment: dict, worker: dict) -> dict:
        input_id = input_row["input_id"]
        delivery_key = f"superagent-v2:input:{input_id}:{assignment['assignment_id']}"
        self._mark_input(input_id, "delivered", assignment_id=assignment["assignment_id"], delivery_key=delivery_key)
        event = {
            "kind": "ticket_follow_up", "ticket_id": ticket["ticket_id"],
            "assignment_id": assignment["assignment_id"], "input_id": input_id,
            "context_only": bool(input_row["context_only"]), "trigger": self._trigger_payload(trigger),
        }
        prompt = tr("superagent.worker_input", event=_json(event)) + "\n\n" + tr("superagent.worker_report_contract")
        try:
            receipt = await self._executor.deliver_input(
                worker, assignment, prompt, delivery_key,
                lambda: self._assert_delivery_still_authorized(
                    ticket["ticket_id"], assignment["assignment_id"], trigger, mandate["mandate_id"],
                ),
            )
        except HarnessError as exc:
            if exc.code in {"run/stale-turn", "session/turn-stale", "runtime/stale-turn"}:
                self._mark_input(input_id, "waiting")
                return {"state": "queued", "ticket_id": ticket["ticket_id"], "trigger_id": trigger["trigger_id"]}
            raise
        if (not isinstance(receipt, dict) or type(receipt.get("accepted")) is not bool
                or type(receipt.get("deferred", False)) is not bool
                or receipt.get("accepted") and receipt.get("deferred")):
            raise HarnessError("Follow-up delivery receipt is invalid or uncertain", code="superagent/input-delivery-unknown")
        if receipt.get("deferred"):
            self._mark_input(input_id, "waiting")
            return {"state": "queued", "ticket_id": ticket["ticket_id"], "trigger_id": trigger["trigger_id"]}
        if not receipt["accepted"]:
            raise HarnessError("Follow-up input was not accepted by the original Run", code="superagent/input-delivery-unknown")
        return {"state": "delivered", "ticket_id": ticket["ticket_id"],
                "assignment_id": assignment["assignment_id"], "trigger_id": trigger["trigger_id"],
                "delivery_key": delivery_key}

    async def reconcile_ticket(self, ticket_id: str) -> list[dict]:
        self._ensure_open()
        rows = self._read_all(
            "SELECT assignment.assignment_id FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "JOIN missions AS mission ON mission.mission_id=job.mission_id "
            "WHERE mission.ticket_id=? AND assignment.state IN ('submitting','accepted','running','capturing','stopping','unknown') "
            "ORDER BY mission.sequence,assignment.attempt_no,assignment.assignment_id", (ticket_id,),
        )
        results = []
        for row in rows:
            results.append(await self.reconcile(row["assignment_id"]))
        return results

    def pending_routes(self, *, limit: int = 100) -> list[dict]:
        """列出已授权且仍等待执行的 TicketInput；不创建资源或改变输入状态。"""
        self._ensure_open()
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer between 1 and 200")
        return self._read_all(
            "SELECT input.ticket_id,input.trigger_id FROM ticket_inputs AS input "
            "JOIN tickets AS ticket ON ticket.ticket_id=input.ticket_id "
            "JOIN triggers AS trigger ON trigger.trigger_id=input.trigger_id "
            "WHERE ticket.state='open' AND input.context_only=0 AND input.state='waiting' "
            "AND COALESCE(json_extract(trigger.metadata_json,'$.instruction_only'),0)<>1 "
            "ORDER BY input.created_at,input.input_id LIMIT ?",
            (limit,),
        )

    async def reconcile_active(self, *, limit: int = 100) -> dict:
        """回查当前实例的活动 Assignment；不创建新资源或重放提交。"""
        self._ensure_open()
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer between 1 and 200")
        rows = self._read_all(
            "SELECT assignment.assignment_id FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "JOIN missions AS mission ON mission.mission_id=job.mission_id "
            "LEFT JOIN watchdog_snapshots AS watchdog ON watchdog.mission_id=mission.mission_id "
            "WHERE assignment.state IN "
            "('submitting','accepted','running','capturing','stopping','unknown') "
            "ORDER BY COALESCE(watchdog.observed_at,''),COALESCE(assignment.started_at,''),"
            "assignment.assignment_id LIMIT ?",
            (limit,),
        )
        results = []
        for row in rows:
            results.append(await self.reconcile(row["assignment_id"]))
        return {"processed": len(results), "assignments": results}

    def active_mission_states(self, *, limit: int = 100) -> list[dict]:
        """列出需要监督的 Mission 状态；读取不创建或修复快照。"""
        self._ensure_open()
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer between 1 and 200")
        rows = self._read_all(
            "SELECT mission.mission_id,mission.state FROM missions AS mission "
            "LEFT JOIN watchdog_snapshots AS watchdog ON watchdog.mission_id=mission.mission_id "
            "WHERE mission.state IN "

            "('planning','running','integrating','reviewing','cancelling','unknown') "
            "ORDER BY COALESCE(watchdog.observed_at,''),mission.updated_at,mission.mission_id LIMIT ?",
            (limit,),
        )
        return [{"mission_id": row["mission_id"], "state": row["state"]} for row in rows]

    def active_missions(self, *, limit: int = 100) -> list[str]:
        """列出需要监督的 Mission 身份。"""
        return [
            item["mission_id"] for item in self.active_mission_states(limit=limit)
        ]

    def watchdog_activity(self, mission_id: str) -> dict:
        """只读检查原生 Run 活动；只返回状态摘要，不把正文写入监督指纹。"""
        self._ensure_open()
        mission_id = _text(mission_id, "mission_id")
        rows = self._read_all(
            "SELECT assignment.assignment_id FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "WHERE job.mission_id=? AND assignment.state IN "
            "('submitting','accepted','running','capturing','stopping','unknown') "
            "ORDER BY assignment.assignment_id",
            (mission_id,),
        )
        activity = []
        for row in rows:
            assignment = self._assignment(row["assignment_id"])
            if assignment is None:
                continue
            worker = self._worker_row(assignment["worker_id"])
            if worker is None:
                activity.append({
                    "assignment_id": assignment["assignment_id"],
                    "inspection": "worker_unavailable",
                })
                continue
            try:
                snapshot = self._executor.inspect(worker, assignment)
            except Exception as exc:
                activity.append({
                    "assignment_id": assignment["assignment_id"],
                    "inspection": "unknown", "error_type": type(exc).__name__,
                })
                continue
            if not isinstance(snapshot, dict):
                activity.append({
                    "assignment_id": assignment["assignment_id"],
                    "inspection": "invalid",
                })
                continue
            reference = snapshot.get("run_reference")
            if not isinstance(reference, dict):
                reference = {}
            if snapshot.get("found") is False:
                activity.append({
                    "assignment_id": assignment["assignment_id"], "inspection": "not_found",
                })
                continue
            # 观察只读，但错误归属的 Run 不得成为 Watchdog 恢复质量门的进度凭据。
            if (snapshot.get("found") is not True
                    or not isinstance(assignment.get("run_id"), str) or not assignment["run_id"]
                    or not isinstance(worker.get("session_id"), str) or not worker["session_id"]
                    or assignment.get("session_id") != worker["session_id"]
                    or not isinstance(snapshot.get("status"), str) or not snapshot["status"]
                    or snapshot.get("status") != reference.get("status")
                    or type(snapshot.get("settled")) is not bool
                    or snapshot.get("settled") is not reference.get("settled")
                    or snapshot.get("run_id") != assignment["run_id"]
                    or reference.get("run_id") != assignment["run_id"]
                    or reference.get("session_id") != worker["session_id"]
                    or snapshot.get("session_id", worker["session_id"]) != worker["session_id"]):
                activity.append({
                    "assignment_id": assignment["assignment_id"], "inspection": "invalid",
                })
                continue
            last_activity = reference.get("last_activity")
            if isinstance(last_activity, dict) and type(last_activity.get("time")) is int:
                # Harness 原生日志使用 Unix 毫秒；监督游标使用规范 UTC 文本，不改写原证据。
                try:
                    stamp = last_activity["time"]
                    if stamp < 0:
                        raise ValueError
                    event_time = (datetime(1970, 1, 1, tzinfo=timezone.utc)
                                  + timedelta(milliseconds=stamp)).isoformat(timespec="milliseconds")
                except (ValueError, OverflowError):
                    activity.append({"assignment_id": assignment["assignment_id"], "inspection": "invalid"})
                    continue
                last_activity = {**last_activity, "time": event_time}
            activity.append({
                "assignment_id": assignment["assignment_id"],
                "run_id": snapshot.get("run_id"),
                "session_id": worker["session_id"],
                "status": snapshot.get("status"),
                "settled": snapshot.get("settled"),
                "turn": snapshot.get("turn", reference.get("turn")),
                "last_activity": last_activity,
                "unconfirmed_tools": snapshot.get("unconfirmed_tools", 0),
                "unsettled_background": snapshot.get("unsettled_background", 0),
                "inspection": "confirmed" if snapshot.get("found", True) else "not_found",
            })
        return {"assignments": activity}

    async def reconcile(self, assignment_id: str) -> dict:
        self._ensure_open()
        async with self._lock(f"assignment:{assignment_id}"):
            return await self._reconcile_locked(assignment_id)

    async def _reconcile_locked(self, assignment_id: str) -> dict:
        assignment = self._assignment(assignment_id)
        if assignment is None:
            raise LookupError("Assignment not found")
        worker = self._worker_row(assignment["worker_id"])
        if worker is None or worker["state"] != "ready":
            raise HarnessError("Assignment Worker is not ready", code="superagent/worker-unavailable")
        if assignment["state"] in _TERMINAL_ASSIGNMENTS:
            if assignment["state"] == "succeeded":
                if self._dag_scheduler.uses_artifact_workflow(assignment):
                    self._dag_scheduler.finalize_succeeded_assignment(assignment)
                else:
                    self._finalize_report_effects(assignment)
            return {"state": assignment["state"], "assignment_id": assignment_id}
        if assignment["state"] == "submitting" or assignment["state"] == "unknown" and not assignment.get("run_id"):
            try:
                snapshot = self._executor.reconcile(worker, assignment)
            except Exception:
                self._mark_unknown(assignment, "original submission reconciliation failed")
                return {"state": "unknown", "assignment_id": assignment_id}
            if not snapshot.get("found"):
                if snapshot.get("authoritative"):
                    self._reject_unstarted(assignment, "original submission key is absent from native Run storage")
                    return {"state": "rejected", "assignment_id": assignment_id}
                self._mark_unknown(assignment, "submission outcome cannot be reconciled")
                return {"state": "unknown", "assignment_id": assignment_id}
            run_id = snapshot.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                self._mark_unknown(assignment, "original submission returned no Run identity")
                return {"state": "unknown", "assignment_id": assignment_id}
            if assignment.get("run_id") and assignment["run_id"] != run_id:
                raise HarnessError("Submission key resolved to a different original Run", code="superagent/run-conflict")
            if assignment["state"] == "submitting":
                assignment = self._transition("assignment", assignment_id, "accepted",
                    facts={"harness_acceptance_confirmed": True}, changes={"run_id": run_id})
            elif assignment["state"] == "unknown":
                assignment = self._transition("assignment", assignment_id, "accepted",
                    facts={"reconciliation_verified": True, "harness_acceptance_confirmed": True},
                    changes={"run_id": run_id},
                    audit_payload={"reconciliation_ref": f"assignment:{assignment_id}:original-run", "run_id": run_id})
            assignment = self._assignment(assignment_id)
        else:
            try:
                snapshot = self._executor.inspect(worker, assignment)
            except Exception:
                self._mark_unknown(assignment, "original Run inspection failed")
                return {"state": "unknown", "assignment_id": assignment_id}
        if not isinstance(snapshot, dict):
            self._mark_unknown(assignment, "native Run snapshot is invalid")
            return {"state": "unknown", "assignment_id": assignment_id}
        if not snapshot.get("found", True) or snapshot.get("run_id") != assignment.get("run_id"):
            self._mark_unknown(assignment, "original Run identity could not be verified")
            return {"state": "unknown", "assignment_id": assignment_id}
        self._reconcile_inputs(assignment, worker, snapshot)
        evidence_ids = self._persist_evidence(assignment, snapshot)
        if snapshot.get("budget_failure"):
            self._mark_unknown(assignment, "Worker Run exceeded its time budget but native cancellation was not confirmed")
            return {"state": "unknown", "assignment_id": assignment_id, "budget_failure": True}
        status = snapshot.get("status")
        stop_outcome_unknown = str(assignment.get("error_ref") or "").startswith("stop-unknown:")
        if stop_outcome_unknown and status in {"queued", "pending", "running"}:
            return {
                "state": "unknown", "assignment_id": assignment_id,
                "native_state": status, "stop_outcome": "unknown",
            }
        if status in {"queued", "pending"}:
            if assignment["state"] == "unknown":
                assignment = self._transition("assignment", assignment_id, "accepted",
                    facts={"reconciliation_verified": True, "harness_acceptance_confirmed": True})
            return {"state": assignment["state"], "assignment_id": assignment_id, "run_id": assignment["run_id"]}
        if status == "running":
            if assignment["state"] in {"accepted", "unknown"}:
                # 这里只记录原运行事实；新的资源或写入另经持久控制准入。
                facts = {"exact_original_run_active": True, "control_allows": True}
                audit = None
                if assignment["state"] == "unknown":
                    facts["reconciliation_verified"] = True
                    audit = {"reconciliation_ref": f"assignment:{assignment_id}:original-run-active",
                             "run_id": assignment.get("run_id")}
                assignment = self._transition("assignment", assignment_id, "running", facts=facts,
                    changes={"started_at": assignment.get("started_at") or _now()}, audit_payload=audit)
            job = self._read_one("SELECT * FROM jobs WHERE job_id=?", (assignment["job_id"],))
            if job and job["state"] == "ready":
                self._transition("job", job["job_id"], "running", facts={"assignment_accepted": True, "control_allows": True})
            elif job and job["state"] == "checkpointed":
                self._transition(
                    "job", job["job_id"], "running",
                    facts={"new_assignment_created": True, "checkpoint_and_capture_imported": True,
                           "control_allows": True},
                )
            elif job and job["state"] == "unknown":
                self._transition(
                    "job", job["job_id"], "running",
                    facts={"reconciliation_verified": True, "exact_original_assignment_active": True, "control_allows": True},
                    audit_payload={"reconciliation_ref": f"job:{job['job_id']}:original-assignment-active",
                                   "assignment_id": assignment_id, "run_id": assignment.get("run_id")},
                )
            return {"state": assignment["state"], "assignment_id": assignment_id, "run_id": assignment["run_id"]}
        if status in {"failed", "interrupted"} and snapshot.get("settled"):
            self._finish_failure(assignment, snapshot, evidence_ids)
            return {"state": "failed" if status == "failed" else "interrupted", "assignment_id": assignment_id}
        if status == "completed" and snapshot.get("settled"):
            if snapshot.get("unconfirmed_tools", 0):
                self._mark_unknown(assignment, "one or more Tool results are not durably confirmed")
                return {"state": "unknown", "assignment_id": assignment_id}
            if snapshot.get("unsettled_background", 0):
                if stop_outcome_unknown:
                    return {
                        "state": "unknown", "assignment_id": assignment_id,
                        "native_state": "completed",
                        "unsettled_background": snapshot["unsettled_background"],
                        "stop_outcome": "unknown",
                    }
                if assignment["state"] in {"accepted", "running", "stopping", "unknown"}:
                    facts = {"original_run_settled": True}

                    audit = None
                    if assignment["state"] == "unknown":
                        facts["reconciliation_verified"] = True
                        audit = {"reconciliation_ref": f"assignment:{assignment_id}:capturing", "run_id": assignment.get("run_id")}
                    assignment = self._transition("assignment", assignment_id, "capturing", facts=facts, audit_payload=audit)
                return {"state": assignment["state"], "assignment_id": assignment_id,
                        "unsettled_background": snapshot["unsettled_background"]}
            if self._dag_scheduler.uses_artifact_workflow(assignment):
                return await self._dag_scheduler.finish_work_success(assignment, snapshot, evidence_ids)
            return self._finish_success(assignment, snapshot, evidence_ids)
        return {"state": assignment["state"], "assignment_id": assignment_id, "run_id": assignment.get("run_id")}

    def _reconcile_inputs(self, assignment: dict, worker: dict, snapshot: dict) -> None:
        rows = self._read_all(
            "SELECT * FROM ticket_inputs WHERE delivered_to_assignment_id=? AND state='delivered'",
            (assignment["assignment_id"],),
        )
        prompt_ids = set(snapshot.get("input_ids", []))
        for row in rows:
            if row["input_id"] in prompt_ids and snapshot.get("started"):
                self._mark_input(row["input_id"], "read")
                continue
            key = row.get("delivery_key")
            if not key:
                continue
            receipt = self._executor.reconcile_input(worker, assignment, key)
            if not isinstance(receipt, dict):
                continue
            state = receipt.get("status")
            if state in {"applied", "read"}:
                self._mark_input(row["input_id"], "read")
            elif state in {"unapplied", "not-applied"}:
                self._mark_input(row["input_id"], "waiting")

        # 初始 DAG Run 的输入快照为每个 Assignment 提供独立的消费证明。
        # 只在原运行确认已启动且确实包含该 Input 时标记 applied；其余状态保留待回查。
        if snapshot.get("started"):
            deliveries = self._read_all(
                "SELECT input_id FROM input_deliveries WHERE assignment_id=? "
                "AND state IN ('submitting','accepted','unknown')",
                (assignment["assignment_id"],),
            )
            for row in deliveries:
                input_id = row["input_id"]
                if input_id not in prompt_ids:
                    continue
                delivery = self._input_deliveries.get(input_id, assignment["assignment_id"])
                if delivery is None or delivery["state"] not in {"submitting", "accepted", "unknown"}:
                    continue
                self._input_deliveries.transition(
                    input_id, assignment["assignment_id"], "applied",
                    expected_revision=delivery["aggregate_revision"],
                    receipt=self._input_deliveries.make_receipt(
                        delivery, "applied", native_receipt={"source": "original_run_input_receipt"},
                    ), actor=_ACTOR,
                )

    def _persist_evidence(self, assignment: dict, snapshot: dict) -> list[str]:
        records = [{
            "evidence_type": "run", "reference": snapshot["run_reference"],
            "reference_hash": snapshot["run_reference_hash"], "payload_hash": snapshot["run_payload_hash"],
            "turn_id": str(snapshot["run_reference"].get("turn")) if snapshot["run_reference"].get("turn") is not None else None,
            "tool_call_id": None, "result_seq": None,
        }]
        for item in snapshot.get("tools", []):
            records.append({
                "evidence_type": "tool", "reference": item["reference"],
                "reference_hash": item["reference_hash"], "payload_hash": item["payload_hash"],
                "turn_id": str(item["reference"].get("turn")), "tool_call_id": item["reference"].get("call_id"),
                "result_seq": item.get("result_seq"),
            })
        for item in snapshot.get("backgrounds", []):
            records.append({
                "evidence_type": "background", "reference": item["reference"],
                "reference_hash": item["reference_hash"], "payload_hash": item["payload_hash"],
                "turn_id": None, "tool_call_id": item["reference"].get("call_id"), "result_seq": None,
            })
        evidence_ids = []
        with self._store.transaction(write=True) as db:
            for item in records:
                identity = "ev_" + hashlib.sha256(
                    f"{assignment['assignment_id']}\0{item['evidence_type']}\0{item['reference_hash']}".encode()
                ).hexdigest()[:32]
                values = {
                    "evidence_id": identity, "assignment_id": assignment["assignment_id"],
                    "harness_instance_id": self.instance_id, "session_id": assignment["session_id"],
                    "run_id": assignment["run_id"], "evidence_type": item["evidence_type"],
                    "turn_id": item["turn_id"], "tool_call_id": item["tool_call_id"],
                    "result_seq": item["result_seq"], "reference_hash": item["reference_hash"],
                    "payload_hash": item["payload_hash"], "reference_json": _json(item["reference"]),
                    "created_at": _now(),
                }
                old = db.execute("SELECT * FROM evidence WHERE evidence_id=?", (identity,)).fetchone()
                if old is None:
                    columns = tuple(values)
                    db.execute(
                        f"INSERT INTO evidence({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                        tuple(values[name] for name in columns),
                    )
                    CoordinatorStore.append_audit(
                        db, aggregate_type="evidence", aggregate_id=identity,
                        event_type="execution_evidence.recorded",
                        payload={"assignment_id": assignment["assignment_id"], "evidence_type": item["evidence_type"],
                                 "reference_hash": item["reference_hash"], "payload_hash": item["payload_hash"]},
                        actor=_ACTOR, correlation_id=f"assignment:{assignment['assignment_id']}",
                    )
                else:
                    for key in ("assignment_id", "harness_instance_id", "session_id", "run_id", "evidence_type",
                                "turn_id", "tool_call_id", "result_seq", "reference_hash", "payload_hash", "reference_json"):
                        if old[key] != values[key]:
                            raise HarnessError("Immutable execution evidence identity conflict", code="superagent/evidence-conflict")
                evidence_ids.append(identity)
        return evidence_ids

    def _mark_unknown(self, assignment: dict, reason: str, *, error_ref: str | None = None) -> None:
        if assignment["state"] != "unknown":
            # stopping 表示原生 Stop 已先持久化；观察失败不能丢失其未知副作用身份。
            if error_ref is None and assignment["state"] == "stopping":
                error_ref = f"stop-unknown:{assignment['assignment_id']}"
            self._transition(
                "assignment", assignment["assignment_id"], "unknown",
                facts={"operation_outcome_uncertain": True},
                changes={"error_ref": error_ref or f"harness-run:{assignment.get('run_id') or assignment['submission_key']}"},
                audit_payload={"reconciliation_ref": f"assignment:{assignment['assignment_id']}:unknown", "reason": reason},
            )
        job = self._read_one("SELECT * FROM jobs WHERE job_id=?", (assignment["job_id"],))
        if job and job["state"] in {"ready", "running"}:
            self._transition("job", job["job_id"], "unknown", facts={"operation_outcome_uncertain": True})
        mission_id = job.get("mission_id") if job else None
        mission = self._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,)) if mission_id else None
        if (mission and mission["state"] in {"running", "integrating", "reviewing"}
                and self._control.mode(mission_id)["mode"] != "cancelled"):
            self._transition("mission", mission_id, "unknown", facts={"operation_outcome_uncertain": True})

    def _reject_unstarted(self, assignment: dict, reason: str) -> None:
        target = "rejected"
        facts = {"original_submission_not_started": True, "explicit_preexecution_rejection": True}
        audit = None
        if assignment["state"] == "unknown":
            facts["reconciliation_verified"] = True
            audit = {"reconciliation_ref": f"assignment:{assignment['assignment_id']}:submission-absent"}
        self._transition(
            "assignment", assignment["assignment_id"], target, facts=facts,
            changes={"error_ref": f"submission-not-started:{assignment['submission_key']}"},
            audit_payload=audit,
        )
        if self._control.mode(assignment["mission_id"])["mode"] != "cancelled":
            self._requeue_inputs(assignment["assignment_id"])
        # 原提交键已被原生运行存储确认不存在，才能拒绝该 Assignment 尚未消费的输入。
        pending_inputs = self._read_all(
            "SELECT input_id FROM input_deliveries WHERE assignment_id=? AND state='submitting'",
            (assignment["assignment_id"],),
        )
        for row in pending_inputs:
            input_id = row["input_id"]
            delivery = self._input_deliveries.get(input_id, assignment["assignment_id"])
            if delivery is None or delivery["state"] != "submitting":
                continue
            self._input_deliveries.transition(
                input_id, assignment["assignment_id"], "rejected",
                expected_revision=delivery["aggregate_revision"],
                receipt=self._input_deliveries.make_receipt(
                    delivery, "rejected", reason_code="original-run-not-started",
                ), actor=_ACTOR,
            )
        job = self._read_one("SELECT * FROM jobs WHERE job_id=?", (assignment["job_id"],))
        if job and job["state"] in {"ready", "unknown"}:
            facts = {"known_failure_or_human_decision": True}
            audit = {"reconciliation_ref": f"job:{job['job_id']}:submission-absent"} if job["state"] == "unknown" else None
            if job["state"] == "unknown":
                facts["reconciliation_verified"] = True
            self._transition("job", job["job_id"], "blocked", facts=facts, audit_payload=audit)
        if self._dag_scheduler.uses_artifact_workflow(assignment):
            self._dag_scheduler._advance_mission_state(assignment["mission_id"])
            return
        mission = self._read_one("SELECT * FROM missions WHERE mission_id=?", (assignment["mission_id"],))
        if (mission and mission["state"] in {"running", "unknown"}
                and self._control.mode(assignment["mission_id"])["mode"] != "cancelled"):
            facts = {"known_failure_or_human_decision": True}
            audit = {"reconciliation_ref": f"mission:{mission['mission_id']}:submission-absent"} if mission["state"] == "unknown" else None
            if mission["state"] == "unknown":
                facts["reconciliation_verified"] = True
            self._transition("mission", mission["mission_id"], "blocked", facts=facts, audit_payload=audit)

    def _requeue_inputs(self, assignment_id: str) -> None:
        rows = self._read_all("SELECT input_id FROM ticket_inputs WHERE delivered_to_assignment_id=? AND state='delivered'", (assignment_id,))
        for row in rows:
            self._mark_input(row["input_id"], "waiting")

    def _parse_worker_report(self, output: str) -> dict:
        try:
            value = json.loads(output)
        except (TypeError, ValueError):
            value = None
        if not isinstance(value, dict) or value.get("status") not in {"completed", "handback", "attention"}:
            return {"status": "completed", "summary": output[:4096], "reply": ""}
        summary = value.get("summary", "")
        reply = value.get("reply", "")
        question = value.get("question", "")
        reason = value.get("handback_reason", "")
        handback_trigger_id = value.get("handback_trigger_id", "")
        options = value.get("options", [])
        if not isinstance(summary, str) or len(summary) > 4096:
            raise HarnessError("Worker Report summary is invalid", code="superagent/report-invalid")
        if not isinstance(reply, str) or len(reply) > 8192:
            raise HarnessError("Worker Report reply is invalid", code="superagent/report-invalid")
        if not isinstance(question, str) or len(question) > 8192:
            raise HarnessError("Worker Report question is invalid", code="superagent/report-invalid")
        if not isinstance(reason, str) or len(reason) > 4096:
            raise HarnessError("Worker Report handback reason is invalid", code="superagent/report-invalid")
        if not isinstance(handback_trigger_id, str) or len(handback_trigger_id) > 128:
            raise HarnessError("Worker Report handback Trigger identity is invalid", code="superagent/report-invalid")
        if not isinstance(options, list) or len(options) > 16 or any(not isinstance(item, str) or len(item) > 512 for item in options):
            raise HarnessError("Worker Report options are invalid", code="superagent/report-invalid")
        if value["status"] == "handback" and not reason.strip():
            return {"status": "completed", "summary": output[:4096], "reply": ""}
        if value["status"] == "attention" and not question.strip():

            return {"status": "completed", "summary": output[:4096], "reply": ""}
        return {"status": value["status"], "summary": summary, "reply": reply,
                "question": question, "handback_reason": reason,
                "handback_trigger_id": handback_trigger_id, "options": options}

    def _attention_id(self, assignment: dict, trigger_id: str, question: str) -> str:
        question_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
        return "attention_" + hashlib.sha256(
            f"{assignment['ticket_id']}\0{trigger_id}\0{assignment['assignment_id']}\0{question_hash}".encode()
        ).hexdigest()[:32]

    def _create_attention(self, assignment: dict, trigger_id: str, question: str, options: list[str]) -> str:
        question_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
        attention_id = self._attention_id(assignment, trigger_id, question)
        with self._store.unit_of_work(actor=_ACTOR, correlation_id=f"ticket:{assignment['ticket_id']}") as uow:
            existing = uow.attentions.get(attention_id)
            if existing is not None:
                if existing["question_text"] != question or existing["options_json"] != options:
                    raise HarnessError("Attention identity conflicts with its frozen question", code="superagent/attention-conflict")
                return attention_id
            uow.attentions.create({
                "attention_id": attention_id, "ticket_id": assignment["ticket_id"],
                "mission_id": assignment["mission_id"], "trigger_id": trigger_id,
                "assignment_id": assignment["assignment_id"],
                "question_ref": f"inline:{question_hash}", "question_text": question,
                "options_json": options,
            }, audit_payload={"question_hash": question_hash, "assignment_id": assignment["assignment_id"]})
        return attention_id

    def _finish_success(self, assignment: dict, snapshot: dict, evidence_ids: list[str]) -> dict:
        ticket = self._authorization.ticket(assignment["ticket_id"])
        if ticket is None:
            raise HarnessError("Completed Worker Run references a missing Ticket", code="superagent/ticket-corrupt")
        trigger = self._sources.get_trigger(assignment.get("goal_ref"))
        if trigger is None:
            raise HarnessError("Completed Worker Run references a missing Trigger", code="superagent/trigger-corrupt")
        parsed = self._parse_worker_report(snapshot.get("output_text", ""))
        disposition = parsed["status"]
        question = parsed.get("question", "")
        options = parsed.get("options", [])
        handback_trigger_id = parsed.get("handback_trigger_id", "")
        if disposition == "handback":
            handback_trigger_id = handback_trigger_id or trigger["trigger_id"]
            input_row = self._input(assignment["ticket_id"], handback_trigger_id)
            if (handback_trigger_id == ticket.get("origin_trigger_id") or input_row is None
                    or input_row["context_only"] or input_row.get("delivered_to_assignment_id") != assignment["assignment_id"]
                    or input_row["state"] == "handed_back") :
                disposition = "attention"
                question = parsed.get("handback_reason") or "This request is outside the authorized scope. A human decision is required."
                options = []
        attention_id = self._attention_id(assignment, trigger["trigger_id"], question) if disposition == "attention" else None
        mandate = ticket.get("mandate") or {}
        mandate_status = "missing"
        if mandate.get("mandate_id"):
            mandate_status = self._authorization.revalidate_mandate(mandate["mandate_id"], actor=_ACTOR).get("status", "unknown")
        mandate_valid = mandate_status in {"valid", "affected"}
        output_ref = f"harness-run:{assignment['session_id']}:{assignment['run_id']}"
        report = {
            "kind": "single_worker_report", "schema_version": 1,
            "ticket_id": assignment["ticket_id"], "mission_id": assignment["mission_id"],
            "job_id": assignment["job_id"], "assignment_id": assignment["assignment_id"],
            "trigger_id": trigger["trigger_id"], "run_id": assignment["run_id"],
            "output_ref": output_ref, "output_hash": snapshot["output_hash"],
            "disposition": disposition, "summary": parsed["summary"],
            "reply": parsed.get("reply", ""), "handback_reason": parsed.get("handback_reason", ""),
            "handback_trigger_id": handback_trigger_id,
            "question": question, "options": options, "attention_id": attention_id,
            "mandate_status": mandate_status, "mandate_valid": mandate_valid,
            "evidence_ids": evidence_ids, "created_at": _now(),
        }
        report["report_hash"] = _hash(_json(report))
        current = self._assignment(assignment["assignment_id"])
        if current["state"] in {"accepted", "running"}:
            current = self._transition("assignment", current["assignment_id"], "capturing",
                                       facts={"original_run_settled": True},
                                       changes={"started_at":current.get("started_at") or _run_started_at(snapshot)})
        if current["state"] == "unknown":
            current = self._transition(
                "assignment", current["assignment_id"], "succeeded",
                facts={"reconciliation_verified": True, "original_run_completed": True,
                       "output_frozen_and_validated": True, "report_frozen": True},
                changes={"report_json": report, "ended_at": _now(), "started_at":current.get("started_at") or _run_started_at(snapshot)},
                audit_payload={"reconciliation_ref": f"assignment:{current['assignment_id']}:report",
                               "run_id":current["run_id"], "run_evidence_ref": evidence_ids[0], "output_ref": output_ref,
                               "output_hash": snapshot["output_hash"]},
            )
        elif current["state"] == "capturing":
            current = self._transition("assignment", current["assignment_id"], "succeeded",
                facts={"output_frozen_and_validated": True, "report_frozen": True},
                changes={"report_json": report, "ended_at": _now(), "started_at":current.get("started_at") or _run_started_at(snapshot)})
        elif current["state"] == "stopping":
            current = self._transition("assignment", current["assignment_id"], "succeeded",
                facts={"original_run_completed": True, "output_frozen_and_validated": True, "report_frozen": True},
                changes={"report_json": report, "ended_at": _now(), "started_at":current.get("started_at") or _run_started_at(snapshot)})
        else:
            raise HarnessError("Assignment is not eligible to freeze a Worker Report", code="superagent/assignment-state-conflict")
        current = self._assignment(current["assignment_id"])
        if current is None:
            raise HarnessError("Frozen Assignment disappeared before finalization", code="superagent/assignment-corrupt")
        self._finalize_report_effects(current, report, trigger=trigger, ticket=ticket)
        return {"state": "succeeded", "assignment_id": current["assignment_id"],
                "mission_id": current["mission_id"], "report_hash": report["report_hash"],
                "disposition": disposition, "attention_id": attention_id}

    def _finalize_report_effects(self, assignment: dict, report: dict | None = None, *,
                                 trigger: dict | None = None, ticket: dict | None = None) -> None:
        assignment = self._assignment(assignment["assignment_id"])
        if assignment is None or assignment["state"] != "succeeded":
            return
        if report is None:
            try:
                report = json.loads(assignment["report_json"])
            except (TypeError, ValueError) as exc:
                raise HarnessError("Frozen Worker Report is corrupt", code="superagent/report-corrupt") from exc
        if report.get("kind") != "single_worker_report":
            return
        report_hash = report.get("report_hash")
        payload = {key: value for key, value in report.items() if key != "report_hash"}
        if not isinstance(report_hash, str) or _hash(_json(payload)) != report_hash:
            raise HarnessError("Frozen Worker Report hash does not match its payload", code="superagent/report-corrupt")
        trigger = trigger or self._sources.get_trigger(report.get("trigger_id"))
        ticket = ticket or self._authorization.ticket(assignment["ticket_id"])
        if trigger is None or ticket is None:
            raise HarnessError("Frozen Worker Report references a missing source aggregate", code="superagent/report-corrupt")
        if not self._control.allows(assignment["mission_id"]):
            # 暂停/取消期间只冻结原生结果与 Job 事实，不创建 Attention、Handback 或发布候选。
            self._complete_job_mission(assignment, report)
            return
        if report.get("disposition") == "attention" and report.get("attention_id"):
            expected = self._attention_id(assignment, trigger["trigger_id"], report.get("question", ""))
            if expected != report["attention_id"]:
                raise HarnessError("Frozen Attention identity does not match its question", code="superagent/attention-conflict")
            self._create_attention(assignment, trigger["trigger_id"], report.get("question", ""), report.get("options", []))
        elif report.get("disposition") == "handback":
            target_trigger_id = report.get("handback_trigger_id")
            input_row = self._input(assignment["ticket_id"], target_trigger_id) if target_trigger_id else None
            if (input_row is not None and not input_row["context_only"]
                    and input_row.get("delivered_to_assignment_id") == assignment["assignment_id"]
                    and target_trigger_id != ticket.get("origin_trigger_id")
                    and input_row["state"] != "handed_back"
                    and not (ticket.get("state") == "ended" and ticket.get("ended_by") == "human")):
                self._authorization.handback(assignment["ticket_id"], target_trigger_id,
                                             report.get("handback_reason", "Worker handed back this request"), actor=_ACTOR)
        self._complete_job_mission(assignment, report)
        if report.get("mandate_valid") is True:
            self._create_outbox_candidates(assignment, report, trigger, ticket)

    def _complete_job_mission(self, assignment: dict, report: dict | None = None,
                              snapshot: dict | None = None) -> None:
        assignment = self._assignment(assignment["assignment_id"])
        if assignment is None or assignment["state"] != "succeeded":
            return
        report = report or json.loads(assignment["report_json"])
        report_hash = report["report_hash"]
        job = self._read_one("SELECT * FROM jobs WHERE job_id=?", (assignment["job_id"],))
        if job and job["state"] != "done":
            if job["state"] not in {"ready", "running", "unknown"}:
                raise HarnessError("Succeeded Assignment has a non-completable Job state", code="superagent/job-state-conflict")
            facts = {"role_result_validated": True, "report_frozen": True}
            audit = None
            if job["state"] == "unknown":
                facts["reconciliation_verified"] = True
                audit = {"reconciliation_ref": f"job:{job['job_id']}:frozen-report", "report_hash": report_hash}
            self._transition("job", job["job_id"], "done", facts=facts,
                             changes={"result_hash": report_hash}, audit_payload=audit)
        mission = self._read_one("SELECT * FROM missions WHERE mission_id=?", (assignment["mission_id"],))
        if mission is None or mission["state"] in {"completed", "blocked", "cancelled", "suppressed"}:
            return
        if mission["state"] == "cancelling" or not self._control.allows(assignment["mission_id"]):
            return
        ticket = self._authorization.ticket(assignment["ticket_id"])
        mandate = (ticket or {}).get("mandate") or {}
        mandate_status = "missing"
        if mandate.get("mandate_id"):
            mandate_status = self._authorization.revalidate_mandate(mandate["mandate_id"], actor=_ACTOR).get("status", "unknown")
        mandate_valid = report.get("mandate_valid") is True and mandate_status in {"valid", "affected"}
        if not mandate_valid:
            audit = None
            if mandate_status == "revoked":
                target = "suppressed"
                facts = {"mandate_unavailable": True, "mandate_revoked": True}

                outcome = "suppressed"
            else:
                # Expiry is a known execution block, not a fabricated revocation event.
                target = "blocked"
                facts = {"known_failure_or_human_decision": True}
                outcome = "mandate_expired" if mandate_status == "expired" else "mandate_unavailable"
            if mission["state"] == "unknown":
                facts["reconciliation_verified"] = True
                audit = {"reconciliation_ref": f"mission:{mission['mission_id']}:mandate-unavailable",
                         "report_hash": report_hash, "mandate_status": mandate_status}
            self._transition("mission", mission["mission_id"], target, facts=facts,
                changes={"outcome": outcome}, audit_payload=audit)
            return
        if report.get("disposition") == "attention":
            facts = {"known_failure_or_human_decision": True}
            audit = None
            if mission["state"] == "unknown":
                facts["reconciliation_verified"] = True
                audit = {"reconciliation_ref": f"mission:{mission['mission_id']}:attention", "report_hash": report_hash}
            self._transition("mission", mission["mission_id"], "blocked", facts=facts,
                changes={"outcome": "attention"},
                audit_payload=audit)
            return
        if not self._control.allows(mission["mission_id"]):
            return
        facts = {"single_node_report_frozen": True, "all_work_jobs_done": True,
                 "original_run_settled": True, "mandate_valid": True, "control_allows": True}
        audit = None
        if mission["state"] == "unknown":
            facts["reconciliation_verified"] = True
            audit = {"reconciliation_ref": f"mission:{mission['mission_id']}:single-report",
                     "mission_id": mission["mission_id"], "plan_revision": mission["plan_revision"],
                     "assignment_id": assignment["assignment_id"], "run_id": assignment["run_id"],
                     "report_ref": f"report:{assignment['assignment_id']}:{report_hash}",
                     "report_hash": report_hash}
        self._transition("mission", mission["mission_id"], "completed", facts=facts,
            changes={"outcome": report.get("disposition", "completed")}, audit_payload=audit)

    def _finish_failure(self, assignment: dict, snapshot: dict, evidence_ids: list[str]) -> None:
        status = snapshot["status"]
        target = "failed" if status == "failed" else "interrupted"
        current = self._assignment(assignment["assignment_id"])
        if current["state"] == "unknown":
            facts = {"reconciliation_verified": True,
                     "original_run_failure_confirmed": status == "failed",
                     "original_run_interruption_confirmed": status == "interrupted"}
            audit = {"reconciliation_ref": f"assignment:{current['assignment_id']}:{status}",
                     "run_evidence_ref": evidence_ids[0]}
        else:
            facts = {"original_run_failure_confirmed": status == "failed",
                     "original_run_interruption_confirmed": status == "interrupted"}
            audit = None
        self._transition(
            "assignment", current["assignment_id"], target, facts=facts,
            changes={"ended_at": _now(), "started_at":current.get("started_at") or _run_started_at(snapshot), "error_ref": f"harness-run:{current['run_id']}:{status}",
                     "report_json": {"kind": "run_failure", "run_id": current["run_id"],
                                     "status": status, "evidence_ids": evidence_ids}},
            audit_payload=audit,
        )
        current = self._assignment(current["assignment_id"])
        job = self._read_one("SELECT * FROM jobs WHERE job_id=?", (current["job_id"],))
        if job and job["state"] in {"ready", "running", "unknown"}:
            facts = {"known_failure_or_human_decision": True}
            audit = None
            if job["state"] == "unknown":
                facts["reconciliation_verified"] = True
                audit = {"reconciliation_ref": f"job:{job['job_id']}:{status}"}
            self._transition("job", job["job_id"], "blocked", facts=facts, audit_payload=audit)
        mission = self._read_one("SELECT * FROM missions WHERE mission_id=?", (current["mission_id"],))
        if (mission and mission["state"] in {"running", "unknown"}
                and self._control.mode(assignment["mission_id"])["mode"] != "cancelled"):
            facts = {"known_failure_or_human_decision": True}
            audit = None
            if mission["state"] == "unknown":
                facts["reconciliation_verified"] = True
                audit = {"reconciliation_ref": f"mission:{mission['mission_id']}:{status}"}
            self._transition("mission", mission["mission_id"], "blocked", facts=facts, audit_payload=audit)

    def reply_triggers(self, mission_id):
        """每个已获准且实际参与本次活动的 Thread 仅保留最早原回复目标。"""
        mission = self._read_one("SELECT ticket_id,goal_ref FROM missions WHERE mission_id=?", (mission_id,))
        if mission is None:
            raise HarnessError(tr("superagent.corrupt"), code="superagent/mission-corrupt")
        rows = self._read_all(
            "SELECT i.trigger_id FROM ticket_inputs i WHERE i.ticket_id=? AND i.context_only=0 AND i.state<>'handed_back' "
            "AND (i.trigger_id=? OR EXISTS(SELECT 1 FROM input_deliveries d JOIN assignments a ON a.assignment_id=d.assignment_id "
            "JOIN jobs j ON j.job_id=a.job_id WHERE d.input_id=i.input_id AND d.state='applied' AND j.mission_id=?)) "
            "ORDER BY (i.trigger_id=?) DESC,i.created_at,i.input_id LIMIT 1000",
            (mission['ticket_id'], mission['goal_ref'], mission_id, mission['goal_ref']))
        targets = {}
        for row in rows:
            trigger = self._sources.get_trigger(row['trigger_id'])
            key = (trigger['source_instance_id'], trigger['primary_thread_id'])
            targets.setdefault(key, trigger)
        return list(targets.values())

    def _create_outbox_candidates(self, assignment: dict, report: dict, trigger: dict, ticket: dict) -> None:
        reply = report.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            return
        ticket = self._authorization.ticket(ticket["ticket_id"])
        if ticket is None or ticket.get("state") != "open":
            return
        if not self._control.allows(assignment["mission_id"]):
            return
        mandate = ticket.get("mandate") or {}
        if not mandate.get("mandate_id"):
            return
        check = self._authorization.revalidate_mandate(mandate["mandate_id"], actor=_ACTOR)
        if check.get("status") not in {"valid", "affected"}:
            return
        if "reply" not in (mandate.get("scope") or {}).get("actions", []):
            return
        targets = [(channel, target) for target in self.reply_triggers(assignment['mission_id'])
                   for channel in self._sources.reply_channels(target['trigger_id'])]
        for channel, target in targets:
            if not (channel.get("capabilities") or {}).get("reply") is True:
                continue
            message_id = "message_" + hashlib.sha256(
                f"{assignment['assignment_id']}\0{channel['channel_id']}\0reply".encode()
            ).hexdigest()[:32]
            body_hash = hashlib.sha256(reply.encode("utf-8")).hexdigest()
            body_ref = f"report:{assignment['assignment_id']}:{report['report_hash']}:reply"
            with self._store.unit_of_work(actor=_ACTOR, correlation_id=f"ticket:{ticket['ticket_id']}") as uow:
                old = uow.outbox.get(message_id)
                if old is not None:
                    if old["body_hash"] != body_hash or old["body_ref"] != body_ref:
                        raise HarnessError("Outbox candidate idempotency conflict", code="superagent/outbox-conflict")
                    continue
                uow.outbox.create({
                    "message_id": message_id, "ticket_id": ticket["ticket_id"],
                    "channel_id": channel["channel_id"], "kind": "reply",
                    "body_ref": body_ref, "body_hash": body_hash,
                    "idempotency_key": f"superagent-v2:{message_id}",
                })
                uow.db.execute("INSERT INTO outbox_reply_targets VALUES(?,?)", (message_id, target['trigger_id']))

    def report(self, assignment_id: str) -> dict | None:
        assignment = self._assignment(assignment_id)
        if assignment is None:
            raise LookupError("Assignment not found")
        try:
            report = json.loads(assignment["report_json"])
        except (TypeError, ValueError):
            return None
        if report.get("kind") not in {"single_worker_report", "work_artifact_report"}:
            return None
        report_hash = report.get("report_hash")
        payload = {key: value for key, value in report.items() if key != "report_hash"}
        if not isinstance(report_hash, str) or _hash(_json(payload)) != report_hash:
            raise HarnessError("Frozen Worker Report hash does not match its payload", code="superagent/report-corrupt")
        if report.get("kind") == "work_artifact_report":
            artifact = self._read_one("SELECT * FROM artifacts WHERE assignment_id=?", (assignment_id,))
            if (artifact is None or artifact["artifact_id"] != report.get("artifact_id")
                    or artifact["manifest_hash"] != report.get("manifest_hash")
                    or report.get("output_hash") != artifact["manifest_hash"]):
                raise HarnessError("Frozen Work Artifact Report does not match its Artifact", code="superagent/report-corrupt")
        evidence = self._read_all(
            "SELECT * FROM evidence WHERE assignment_id=? ORDER BY created_at,evidence_id",
            (assignment_id,),
        )
        return {"assignment_id": assignment_id, "state": assignment["state"],
                "report": report, "evidence": evidence}

    def list_workers(self, ticket_id: str) -> list[dict]:
        return self._read_all("SELECT * FROM workers WHERE ticket_id=? ORDER BY created_at,worker_id", (ticket_id,))

    def list_attentions(self, ticket_id: str, *, state: str | None = None) -> list[dict]:
        if state is None:
            return self._read_all("SELECT * FROM attentions WHERE ticket_id=? ORDER BY created_at,attention_id", (ticket_id,))
        if state not in {"open", "answered", "dismissed"}:
            raise ValueError("invalid Attention state")
        return self._read_all(
            "SELECT * FROM attentions WHERE ticket_id=? AND state=? ORDER BY created_at,attention_id",
            (ticket_id, state),
        )

    def resolve_attention(self, attention_id: str, answer_trigger_id: str) -> dict:
        attention = self._read_one("SELECT * FROM attentions WHERE attention_id=?", (attention_id,))
        if attention is None:
            raise LookupError("Attention not found")
        ticket = self._authorization.ticket(attention["ticket_id"])
        if ticket is None or ticket.get("state") != "open":
            raise PermissionError("Attention Ticket is not open")
        mandate = ticket.get("mandate") or {}
        check = self._authorization.revalidate_mandate(mandate.get("mandate_id"), actor=_ACTOR)
        if check.get("status") not in {"valid", "affected"}:
            raise PermissionError("Attention cannot be answered under the current Mandate")
        trigger = self._sources.get_trigger(answer_trigger_id)
        actor = (trigger or {}).get("actor") or {}
        if trigger is None or actor.get("kind", actor.get("actor_kind")) != "human":
            raise PermissionError("Attention answer must be a verified human Trigger")
        input_row = self._input(attention["ticket_id"], answer_trigger_id)
        if input_row is None or input_row["context_only"] or input_row["state"] == "handed_back":
            raise PermissionError("Attention answer must be a work input on the same Ticket")
        return self._transition(
            "attention", attention_id, "answered",
            facts={"human_answer_trigger_verified": True},

            changes={"answer_trigger_id": answer_trigger_id, "resolved_at": _now()},
        )

    def outbox_body(self, message_id: str) -> str:
        message = self._read_one("SELECT * FROM outbox_messages WHERE message_id=?", (message_id,))
        if message is None:
            raise LookupError("Outbox message not found")
        prefix = "report:"
        if not message["body_ref"].startswith(prefix) or not message["body_ref"].endswith(":reply"):
            raise PermissionError("Outbox body reference is not a V2 Report reply")
        parts = message["body_ref"].split(":")
        if len(parts) != 4 or parts[0] != "report" or parts[3] != "reply":
            raise PermissionError("Outbox Report body reference is malformed")
        assignment_id, expected_report_hash = parts[1], parts[2]
        report = self.report(assignment_id)
        if report is None or report["report"].get("report_hash") != expected_report_hash:
            raise HarnessError("Outbox Report body is unavailable or changed", code="superagent/outbox-body-missing")
        body = report["report"].get("reply", "")
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != message["body_hash"]:
            raise HarnessError("Outbox body hash does not match its frozen Report", code="superagent/outbox-body-conflict")
        return body

    async def stop(self, assignment_id: str, *, reason: str) -> dict:
        self._ensure_open()
        if not isinstance(reason, str):
            raise ValueError("stop reason must be a string")
        reason = reason.strip()
        if not reason or len(reason) > 4096:
            raise ValueError("stop reason must be between 1 and 4096 characters")
        async with self._lock(f"assignment:{assignment_id}"):
            assignment = self._assignment(assignment_id)
            if assignment is None:
                raise LookupError("Assignment not found")

            if assignment["state"] in {"submitting", "unknown"}:
                if str(assignment.get("error_ref") or "").startswith("stop-unknown:"):
                    return await self._reconcile_locked(assignment_id)
                await self._reconcile_locked(assignment_id)
                assignment = self._assignment(assignment_id)
                if assignment is None:
                    raise LookupError("Assignment not found")
                if assignment["state"] in _TERMINAL_ASSIGNMENTS or assignment["state"] == "unknown":
                    return {"state": assignment["state"], "stopped": False}

            if assignment["state"] == "stopping":
                result = await self._reconcile_locked(assignment_id)
                assignment = self._assignment(assignment_id)
                if assignment is None:
                    raise LookupError("Assignment not found")
                if assignment["state"] != "capturing":
                    return result

            if assignment["state"] not in {"accepted", "running", "capturing"}:
                return {"state": assignment["state"], "stopped": False}

            control = self._control.mode(assignment["mission_id"])
            ticket = self._authorization.ticket(assignment["ticket_id"])
            if ticket is None:
                raise PermissionError("Ticket not found")
            mandate = ticket.get("mandate") or {}
            mandate_status = self._authorization.revalidate_mandate(
                mandate.get("mandate_id"), actor=_ACTOR,
            ).get("status", "unknown")
            mandate_valid = mandate_status in {"valid", "affected"}
            # Cancel 是已持久化的宿主安全控制；即使 Mandate 已撤销，也必须允许停止原 Run。
            if control["mode"] != "cancelled" and not mandate_valid:
                raise PermissionError("Current Mandate does not authorize stopping this Assignment")
            worker = self._worker_row(assignment["worker_id"])
            if worker is None:
                raise HarnessError("Assignment Worker is missing", code="superagent/worker-unavailable")

            stop_facts = {
                "stop_requested": True,
                "mandate_valid": mandate_valid,
                "control_allows": True,
            }
            if assignment["state"] == "capturing":
                stop_facts["original_run_settled"] = True
            assignment = self._transition(
                "assignment", assignment_id, "stopping",
                facts=stop_facts,
                changes={"error_ref": f"stop-request:{hashlib.sha256(reason.encode()).hexdigest()}"},
            )
            try:
                receipt = await self._executor.stop(worker, assignment)
            except Exception:
                self._mark_unknown(
                    assignment, "native stop outcome cannot be confirmed",
                    error_ref=f"stop-unknown:{assignment_id}",
                )
                return {"state": "unknown", "stopped": False}
            if not isinstance(receipt, dict) or type(receipt.get("stopped")) is not bool:
                self._mark_unknown(
                    assignment, "native stop receipt is invalid",
                    error_ref=f"stop-unknown:{assignment_id}",
                )
                return {"state": "unknown", "stopped": False}
            if not receipt["stopped"]:
                result = await self._reconcile_locked(assignment_id)
                current = self._assignment(assignment_id)
                if current and (current["state"] in _TERMINAL_ASSIGNMENTS or current["state"] == "capturing"):
                    return result
                if current is not None:
                    self._mark_unknown(
                        current, "native stop could not be confirmed",
                        error_ref=f"stop-unknown:{assignment_id}",
                    )
                return {"state": "unknown", "stopped": False, "receipt": receipt}
            return await self._reconcile_locked(assignment_id)

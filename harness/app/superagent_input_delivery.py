"""按 Input / Assignment 独立消费输入，并只回查未知的原生投递。"""
from __future__ import annotations

import asyncio
import weakref
from typing import Callable

from harness.i18n import tr

from ..core.errors import HarnessError
from .superagent_coordination import CoordinatorInputDelivery
from .superagent_coordinator_store import _json, _text


_ACTOR = {"kind": "host", "component": "superagent-input-delivery"}
_ACTIVE_ASSIGNMENTS = {"accepted", "running"}
_ACTIVE_JOB_STATES = {"ready", "running", "integrating", "reviewing"}
_TERMINAL_DELIVERIES = {"applied", "unapplied", "rejected"}


class CoordinatorAssignmentInputDelivery:
    """为每个 Assignment 建立独立、稳定且不可重放的原生输入投递。"""

    def __init__(self, store, authorization, sources, executor, *,
                 admit_mission: Callable[[str], dict]):
        self._store = store
        self._authorization = authorization
        self._sources = sources
        self._executor = executor
        self._profiles = dict(getattr(executor, "profiles", {}))
        self._admit_mission = admit_mission
        self._deliveries = CoordinatorInputDelivery(store)
        self._locks = weakref.WeakValueDictionary()

    def _lock(self, input_id: str, assignment_id: str) -> asyncio.Lock:
        key = (input_id, assignment_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _read_one(self, sql: str, params: tuple = ()) -> dict | None:
        with self._store.transaction() as db:
            row = db.execute(sql, params).fetchone()
            return dict(row) if row is not None else None

    def _input(self, input_id: str) -> dict | None:
        return self._read_one("SELECT * FROM ticket_inputs WHERE input_id=?", (input_id,))

    def _assignment(self, assignment_id: str) -> dict | None:
        return self._read_one(
            "SELECT assignment.*,job.mission_id,job.role,job.profile_id,job.state AS job_state,"
            "mission.ticket_id,mission.state AS mission_state,mission.repo,mission.base_commit "
            "FROM assignments AS assignment JOIN jobs AS job ON job.job_id=assignment.job_id "
            "JOIN missions AS mission ON mission.mission_id=job.mission_id "
            "WHERE assignment.assignment_id=?",
            (assignment_id,),
        )

    def _worker(self, worker_id: str) -> dict | None:
        return self._read_one("SELECT * FROM workers WHERE worker_id=?", (worker_id,))

    @staticmethod
    def _native_status(receipt: object, delivery_key: str) -> str | None:
        if not isinstance(receipt, dict) or receipt.get("request_id") != delivery_key:
            return None
        if type(receipt.get("turn")) is not int or receipt["turn"] < 1:
            return None
        if type(receipt.get("accepted_seq")) is not int or receipt["accepted_seq"] < 1:
            return None
        status = receipt.get("status")
        if not isinstance(status, str) or status not in {"pending", "applied", "unapplied"}:
            return None
        applied_seq = receipt.get("applied_seq")
        if status == "applied":
            if type(applied_seq) is not int or applied_seq <= receipt["accepted_seq"]:
                return None
        elif applied_seq is not None:
            return None
        return status

    @staticmethod
    def _native_evidence(receipt: dict) -> dict:
        return {
            "request_id": receipt["request_id"],
            "turn": receipt["turn"],
            "status": receipt["status"],
            "accepted_seq": receipt["accepted_seq"],
            "applied_seq": receipt.get("applied_seq"),
        }

    def _context(self, input_id: str, assignment_id: str, *, authorize: bool) -> dict:
        input_row = self._input(input_id)
        assignment = self._assignment(assignment_id)
        if input_row is None or assignment is None:
            raise LookupError(tr("superagent.corrupt"))
        if input_row["ticket_id"] != assignment["ticket_id"]:
            raise PermissionError(tr("superagent.conflict"))
        if authorize and input_row["state"] == "handed_back":
            raise PermissionError(tr("superagent.conflict"))
        if not isinstance(assignment.get("run_id"), str) or not assignment["run_id"]:
            raise HarnessError(tr("superagent.conflict"), code="superagent/input-run-missing")

        worker = self._worker(assignment["worker_id"])
        if worker is None:
            raise HarnessError(tr("superagent.worker-unavailable"), code="superagent/worker-unavailable")
        if (
            worker["ticket_id"] != assignment["ticket_id"]
            or worker.get("mission_id") != assignment["mission_id"]
            or worker.get("job_id") != assignment["job_id"]
            or worker.get("profile_id") != assignment["profile_id"]
            or worker.get("workspace_id") != assignment.get("workspace_id")
            or worker.get("session_id") != assignment.get("session_id")
            or worker.get("repo") != assignment["repo"]
            or worker.get("base_commit") != assignment["base_commit"]
        ):
            raise HarnessError(tr("superagent.worker-unavailable"),
                               code="superagent/input-scope")

        context = {
            "input": input_row,
            "assignment": assignment,
            "worker": worker,
            "trigger": None,
            "ticket": None,
            "mandate": None,
            "profile": self._profiles.get(worker["profile_id"]),
        }
        if not authorize:
            return context

        if assignment["state"] not in _ACTIVE_ASSIGNMENTS or assignment["job_state"] not in _ACTIVE_JOB_STATES:
            raise HarnessError(tr("superagent.conflict"),
                               code="superagent/input-assignment-inactive")
        if worker["state"] != "ready" or context["profile"] is None:
            raise HarnessError(tr("superagent.worker-unavailable"),
                               code="superagent/worker-unavailable")

        ticket = self._authorization.ticket(input_row["ticket_id"])
        if ticket is None or ticket.get("state") != "open":
            raise PermissionError(tr("superagent.conflict"))
        trigger = self._sources.get_trigger(input_row["trigger_id"])
        if trigger is None:
            raise HarnessError(tr("superagent.corrupt"), code="superagent/context-corrupt")
        mandate = ticket.get("mandate")
        if not isinstance(mandate, dict) or not mandate.get("mandate_id"):
            raise PermissionError(tr("superagent.mandate.unavailable"))
        check = self._authorization.revalidate_mandate(mandate["mandate_id"], actor=_ACTOR)
        if check.get("status") not in {"valid", "affected"}:
            raise PermissionError(tr("superagent.mandate.unavailable"))

        scope = mandate.get("scope") or {}
        source_id = trigger.get("source_instance_id")
        thread_id = trigger.get("primary_thread_id")
        profile = context["profile"]
        if source_id not in scope.get("source_instance_ids", []) or thread_id not in scope.get("thread_ids", []):
            raise PermissionError(tr("superagent.mandate.unavailable"))
        if source_id not in getattr(profile, "source_instance_ids", ()):
            raise PermissionError(tr("superagent.mandate.unavailable"))
        if not set(getattr(profile, "actions", ())) <= set(scope.get("actions", [])):
            raise PermissionError(tr("superagent.mandate.unavailable"))
        if not set(getattr(profile, "tools", ())) <= set(scope.get("tools", [])):
            raise PermissionError(tr("superagent.mandate.unavailable"))

        self._admit_mission(assignment["mission_id"])
        found = self._executor.find_worker(worker, profile)
        if (
            not isinstance(found, dict)
            or found.get("workspace_id") != worker["workspace_id"]
            or found.get("session_id") != worker["session_id"]
        ):
            raise HarnessError("Input delivery Worker resources cannot be verified",
                               code="superagent/worker-conflict")
        context.update(ticket=ticket, trigger=trigger, mandate=mandate)
        return context

    @staticmethod
    def _trigger_payload(trigger: dict) -> dict:
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

    def _transition(self, input_id: str, assignment_id: str, target: str, *,
                    native_receipt: dict | None = None, reason_code: str | None = None) -> dict:
        for attempt in range(2):
            current = self._deliveries.get(input_id, assignment_id)
            if current is None:
                raise HarnessError(tr("superagent.corrupt"), code="superagent/input-delivery-missing")
            if current["state"] == target:
                return current
            if target not in self._deliveries._TRANSITIONS[current["state"]]:
                if current["state"] in {"accepted", *_TERMINAL_DELIVERIES}:
                    return current
                raise HarnessError(tr("superagent.conflict"), code="superagent/conflict")
            receipt = self._deliveries.make_receipt(
                current, target, native_receipt=native_receipt, reason_code=reason_code,
            )
            try:
                return self._deliveries.transition(
                    input_id, assignment_id, target,
                    expected_revision=current["aggregate_revision"], receipt=receipt, actor=_ACTOR,
                )
            except HarnessError as exc:
                if exc.code != "superagent/conflict" or attempt:
                    raise
        raise HarnessError(tr("superagent.conflict"), code="superagent/conflict")

    @staticmethod
    def _result(delivery: dict) -> dict:
        return {
            "state": delivery["state"],
            "input_id": delivery["input_id"],
            "assignment_id": delivery["assignment_id"],
            "delivery_id": delivery["delivery_id"],
            "delivery_key": delivery["delivery_key"],
            "receipt": delivery["receipt"],
        }

    async def deliver(self, input_id: str, assignment_id: str) -> dict:
        """首次调用最多提交一次；所有既有投递都只回查原 request_id。"""
        _text(input_id, "input_id")
        _text(assignment_id, "assignment_id")
        async with self._lock(input_id, assignment_id):
            existing = self._deliveries.get(input_id, assignment_id)
            if existing is not None and existing["state"] != "pending":
                if existing["state"] in {"submitting", "accepted", "unknown"}:
                    context = self._context(input_id, assignment_id, authorize=False)
                    return await self._reconcile_locked(input_id, assignment_id, context, existing)
                return self._result(existing)
            context = self._context(input_id, assignment_id, authorize=True)
            delivery_key = f"superagent-v2:input:{input_id}:{assignment_id}"
            claimed = False
            with self._store.transaction(write=True) as db:
                delivery = self._deliveries.prepare_in_transaction(
                    db, input_id, assignment_id, actor=_ACTOR,
                    delivery_key=delivery_key, initial_state="pending",
                )
                if delivery["state"] == "pending":
                    self._deliveries.transition_in_transaction(
                        db, input_id, assignment_id, "submitting",
                        expected_revision=delivery["aggregate_revision"], receipt=None, actor=_ACTOR,
                    )
                    claimed = True
            delivery = self._deliveries.get(input_id, assignment_id)
            if delivery is None:
                raise HarnessError(tr("superagent.corrupt"), code="superagent/input-delivery-missing")
            if not claimed:
                if delivery["state"] in {"submitting", "accepted", "unknown"}:
                    return await self._reconcile_locked(input_id, assignment_id, context, delivery)
                return self._result(delivery)

            event = {
                "kind": "ticket_input_delivery",
                "ticket_id": context["assignment"]["ticket_id"],
                "mission_id": context["assignment"]["mission_id"],
                "job_id": context["assignment"]["job_id"],
                "assignment_id": assignment_id,
                "run_id": context["assignment"]["run_id"],
                "input_id": input_id,
                "context_only": bool(context["input"]["context_only"]),
                "trigger": self._trigger_payload(context["trigger"]),
            }
            prompt = tr("superagent.worker_input", event=_json(event)) + "\n\n" + tr("superagent.worker_report_contract")
            original_run_id = context["assignment"]["run_id"]
            original_session_id = context["assignment"]["session_id"]

            def before_accept() -> None:
                try:
                    latest = self._context(input_id, assignment_id, authorize=True)
                except PermissionError as exc:
                    raise HarnessError(tr("superagent.mandate.unavailable"),
                                       code="superagent/mandate-unavailable") from exc
                except LookupError as exc:
                    raise HarnessError(tr("superagent.corrupt"),
                                       code="superagent/input-scope") from exc
                if (
                    latest["assignment"]["run_id"] != original_run_id
                    or latest["assignment"]["session_id"] != original_session_id
                ):
                    raise HarnessError(tr("superagent.conflict"),
                                       code="superagent/run-conflict")

            try:
                response = await self._executor.deliver_input(
                    context["worker"], context["assignment"], prompt, delivery_key, before_accept,
                )
            except HarnessError as exc:
                if exc.code == "run/stale-turn":
                    delivery = self._transition(input_id, assignment_id, "unapplied",
                                                 reason_code="run/stale-turn")
                    return self._result(delivery)
                if exc.code in {
                    "superagent/run-conflict", "superagent/mission-paused",
                    "superagent/mandate-unavailable", "superagent/mission-not-running",
                    "superagent/input-assignment-inactive", "superagent/input-scope", "superagent/worker-conflict",
                    "superagent/input-run-missing", "superagent/worker-unavailable", "superagent/context-corrupt",
                }:
                    delivery = self._transition(input_id, assignment_id, "rejected", reason_code=exc.code)
                    return self._result(delivery)
                delivery = self._transition(input_id, assignment_id, "unknown", reason_code=exc.code)
                raise
            except BaseException as exc:
                self._transition(input_id, assignment_id, "unknown",
                                 reason_code=type(exc).__name__[:96] or "operation-unknown")
                raise

            if (
                not isinstance(response, dict)
                or type(response.get("accepted")) is not bool
                or type(response.get("deferred", False)) is not bool
                or response.get("accepted") and response.get("deferred")
            ):
                delivery = self._transition(input_id, assignment_id, "unknown",
                                             reason_code="invalid-native-response")
                return self._result(delivery)
            if response.get("deferred"):
                delivery = self._transition(input_id, assignment_id, "unapplied",
                                             reason_code="native-deferred")
                return self._result(delivery)
            if response.get("accepted") is not True:
                delivery = self._transition(input_id, assignment_id, "unknown",
                                             reason_code="acceptance-unconfirmed")
                return self._result(delivery)

            native = response.get("receipt")
            status = self._native_status(native, delivery_key)
            if (
                response.get("request_id") != delivery_key
                or not isinstance(native, dict)
                or type(response.get("turn")) is not int
                or response.get("turn") != native.get("turn")
                or status is None
            ):
                delivery = self._transition(input_id, assignment_id, "unknown",
                                             native_receipt=None,
                                             reason_code="native-receipt-invalid")
                return self._result(delivery)
            target = {"pending": "accepted", "applied": "applied", "unapplied": "unapplied"}[status]
            delivery = self._transition(
                input_id, assignment_id, target, native_receipt=self._native_evidence(native),
            )
            return self._result(delivery)

    async def reconcile(self, input_id: str, assignment_id: str) -> dict | None:
        """只查询原生 request_id 并推进该 Delivery 的回执，不执行新投递。"""
        _text(input_id, "input_id")
        _text(assignment_id, "assignment_id")
        async with self._lock(input_id, assignment_id):
            delivery = self._deliveries.get(input_id, assignment_id)
            if delivery is None:
                return None
            if delivery["state"] in _TERMINAL_DELIVERIES:
                return self._result(delivery)
            context = self._context(input_id, assignment_id, authorize=False)
            return await self._reconcile_locked(input_id, assignment_id, context, delivery)

    async def _reconcile_locked(self, input_id: str, assignment_id: str,
                                context: dict, delivery: dict) -> dict:
        current = self._deliveries.get(input_id, assignment_id)
        if current is None:
            raise HarnessError(tr("superagent.corrupt"), code="superagent/input-delivery-missing")
        delivery = current
        if delivery["state"] in _TERMINAL_DELIVERIES:
            return self._result(delivery)
        try:
            native = self._executor.reconcile_input(
                context["worker"], context["assignment"], delivery["delivery_key"],
            )
        except Exception as exc:
            if delivery["state"] == "submitting":
                delivery = self._transition(
                    input_id, assignment_id, "unknown",
                    reason_code=type(exc).__name__[:96] or "reconciliation-unknown",
                )
            else:
                delivery = self._deliveries.get(input_id, assignment_id) or delivery
            return self._result(delivery)

        if native is None:
            if delivery["state"] == "submitting":
                delivery = self._transition(
                    input_id, assignment_id, "unknown", reason_code="native-receipt-missing",
                )
            else:
                delivery = self._deliveries.get(input_id, assignment_id) or delivery
            return self._result(delivery)

        status = self._native_status(native, delivery["delivery_key"])
        if status is None:
            if delivery["state"] == "submitting":
                delivery = self._transition(
                    input_id, assignment_id, "unknown", native_receipt=None,
                    reason_code="native-receipt-invalid",
                )
            else:
                delivery = self._deliveries.get(input_id, assignment_id) or delivery
            return self._result(delivery)
        target = {"pending": "accepted", "applied": "applied", "unapplied": "unapplied"}[status]
        delivery = self._transition(
            input_id, assignment_id, target, native_receipt=self._native_evidence(native),
        )
        return self._result(delivery)

"""统一协调账本中的节点指令受理与原生应用回执。"""
from __future__ import annotations

import asyncio
import weakref
from collections.abc import Mapping
from typing import Callable

from harness.i18n import tr

from ..core.errors import HarnessError
from .superagent_common import _json as strict_loads
from .superagent_coordinator_store import _hash, _json, _now, _text


_ACTOR = {"kind": "host", "component": "superagent-mission-instruction"}
_ACTIVE_ASSIGNMENTS = {"accepted", "running"}
_ACTIVE_JOBS = {"ready", "running", "integrating", "reviewing"}
_STATES = {"pending", "submitting", "accepted", "unknown", "rejected"}
_TRANSITIONS = {
    "pending": {"submitting", "rejected"},
    "submitting": {"accepted", "unknown", "rejected"},
    "unknown": {"accepted", "rejected"},
    "accepted": {"accepted"},
    "rejected": set(),
}


def _conflict() -> HarnessError:
    return HarnessError(tr("superagent.conflict"), code="superagent/conflict")


class CoordinatorMissionInstruction:
    """将一次节点指令绑定到原 Assignment / Run / Turn，未知写入只回查。"""

    def __init__(self, store, authorization, sources, executor, *,
                 admit_mission: Callable[[str], dict]):
        self._store = store
        self._authorization = authorization
        self._sources = sources
        self._executor = executor
        self._profiles = dict(getattr(executor, "profiles", {}))
        self._admit_mission = admit_mission
        self._locks = weakref.WeakValueDictionary()
        self._reconcile_cursor = 0

    def _lock(self, request_id: str) -> asyncio.Lock:
        lock = self._locks.get(request_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[request_id] = lock
        return lock

    def _read_one(self, sql: str, params: tuple = ()) -> dict | None:
        with self._store.transaction() as db:
            row = db.execute(sql, params).fetchone()
            return dict(row) if row is not None else None

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
    def _operation_key(request_id: str, assignment_id: str) -> str:
        key = f"superagent-v2:instruction:{request_id}:{assignment_id}"
        if len(key) > 200:
            raise ValueError(tr("superagent.invalid", field="mission_instruction.request_id"))
        return key

    @staticmethod
    def _payload(trigger: dict, text: str) -> dict:
        actor = trigger.get("actor")
        if (
            not isinstance(actor, Mapping)
            or set(actor) != {"source_instance_id", "actor_id", "actor_kind"}
            or actor.get("actor_kind") != "human"
        ):
            raise HarnessError(tr("superagent.mandate.unavailable"), code="superagent/instruction-unauthorized")
        return {
            "version": 1,
            "trigger_id": trigger["trigger_id"],
            "source_instance_id": trigger["source_instance_id"],
            "source_revision": trigger["source_revision"],
            "thread_id": trigger["primary_thread_id"],
            "actor": dict(actor),
            "text": text,
        }

    @staticmethod
    def _validate_payload(value: object) -> dict:
        keys = {
            "version", "trigger_id", "source_instance_id", "source_revision",
            "thread_id", "actor", "text",
        }
        if (
            not isinstance(value, dict) or set(value) != keys
            or type(value.get("version")) is not int or value["version"] != 1
        ):
            raise ValueError("invalid mission instruction payload")
        for name in ("trigger_id", "source_instance_id", "thread_id"):
            _text(value[name], name)
        if type(value["source_revision"]) is not int or not 1 <= value["source_revision"] <= 2**53 - 1:
            raise ValueError("invalid mission instruction source revision")
        actor = value["actor"]
        if (
            not isinstance(actor, dict)
            or set(actor) != {"source_instance_id", "actor_id", "actor_kind"}
            or actor.get("actor_kind") != "human"
            or actor.get("source_instance_id") != value["source_instance_id"]
        ):
            raise ValueError("invalid mission instruction actor")
        _text(actor["actor_id"], "actor_id")
        text = value["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 65536 or "\x00" in text:
            raise ValueError("invalid mission instruction text")
        return value

    @staticmethod
    def _native_status(receipt: object, operation_key: str, turn_id: int, text: str) -> str | None:
        keys = {"request_id", "turn", "text", "status", "accepted_seq", "applied_seq"}
        if (
            not isinstance(receipt, dict) or set(receipt) != keys
            or type(turn_id) is not int
            or type(receipt.get("request_id")) is not str or receipt["request_id"] != operation_key
            or type(receipt.get("turn")) is not int or receipt["turn"] != turn_id
            or type(receipt.get("text")) is not str or receipt["text"] != text
            or type(receipt.get("accepted_seq")) is not int or receipt["accepted_seq"] < 1
        ):
            return None
        status = receipt.get("status")
        if type(status) is not str or status not in {"pending", "applied", "unapplied"}:
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

    @staticmethod
    def _envelope(state: str, *, native_receipt: dict | None = None,
                  reason_code: str | None = None) -> dict:
        if state not in {"accepted", "unknown", "rejected"}:
            raise ValueError("invalid mission instruction receipt state")
        if state == "accepted":
            if not isinstance(native_receipt, dict) or reason_code is not None:
                raise ValueError("accepted mission instruction requires native evidence")
        elif native_receipt is not None or not isinstance(reason_code, str) or not reason_code:
            raise ValueError("unconfirmed mission instruction requires a reason")
        return {
            "version": 1,
            "state": state,
            "reason_code": reason_code,
            "native_receipt": native_receipt,
        }

    def _decode(self, row) -> dict:
        if row is None:
            raise HarnessError(tr("superagent.corrupt"), code="superagent/instruction-corrupt")
        result = dict(row)
        try:
            payload = self._validate_payload(strict_loads(result["payload_json"]))
            if (
                _hash(result["payload_json"]) != result["payload_hash"]
                or result["state"] not in _STATES
                or type(result["turn_id"]) is not int or result["turn_id"] < 1
                or type(result["aggregate_revision"]) is not int or result["aggregate_revision"] < 1
            ):
                raise ValueError("mission instruction identity or revision is invalid")
            for name in ("request_id", "mission_id", "job_id", "assignment_id", "run_id"):
                _text(result[name], name)
            receipt = strict_loads(result["receipt_json"])
            if not isinstance(receipt, dict):
                raise ValueError("mission instruction receipt is not an object")
            if result["state"] in {"pending", "submitting"}:
                if receipt:
                    raise ValueError("unsubmitted mission instruction has a receipt")
            else:
                expected = {"version", "state", "reason_code", "native_receipt"}
                if set(receipt) != expected or receipt.get("version") != 1 or receipt.get("state") != result["state"]:
                    raise ValueError("mission instruction receipt envelope is invalid")
                reason = receipt.get("reason_code")
                native = receipt.get("native_receipt")
                if result["state"] == "accepted":
                    if reason is not None or not isinstance(native, dict):
                        raise ValueError("accepted mission instruction receipt is invalid")
                    if set(native) != {"request_id", "turn", "status", "accepted_seq", "applied_seq"}:
                        raise ValueError("mission instruction native receipt shape is invalid")
                    if (
                        type(native.get("request_id")) is not str
                        or native["request_id"] != self._operation_key(result["request_id"], result["assignment_id"])
                        or type(native.get("turn")) is not int or native["turn"] != result["turn_id"]
                        or type(native.get("status")) is not str
                        or native["status"] not in {"pending", "applied", "unapplied"}
                        or type(native.get("accepted_seq")) is not int or native["accepted_seq"] < 1
                    ):
                        raise ValueError("mission instruction native receipt identity is invalid")
                    applied_seq = native.get("applied_seq")
                    if native["status"] == "applied":
                        if type(applied_seq) is not int or applied_seq <= native["accepted_seq"]:
                            raise ValueError("mission instruction applied sequence is invalid")
                    elif applied_seq is not None:
                        raise ValueError("mission instruction has an unexpected applied sequence")
                elif native is not None or not isinstance(reason, str) or not reason or len(reason) > 96:
                    raise ValueError("mission instruction reason receipt is invalid")
        except (KeyError, TypeError, ValueError, RecursionError, HarnessError) as exc:
            raise HarnessError(tr("superagent.corrupt"), code="superagent/instruction-corrupt") from exc
        result["payload"] = payload
        result["receipt"] = receipt
        return result

    def get(self, request_id: str) -> dict | None:
        _text(request_id, "request_id")
        with self._store.transaction() as db:
            row = db.execute("SELECT * FROM mission_instructions WHERE request_id=?", (request_id,)).fetchone()
            return None if row is None else self._decode(row)

    @staticmethod
    def _public(row: dict) -> dict:
        envelope = row["receipt"]
        native = envelope.get("native_receipt") if isinstance(envelope, dict) else None
        return {
            "request_id": row["request_id"],
            "mission_id": row["mission_id"],
            "job_id": row["job_id"],
            "assignment_id": row["assignment_id"],
            "run_id": row["run_id"],
            "turn_id": row["turn_id"],
            "state": row["state"],
            "application": native.get("status") if native else None,
            "accepted_seq": native.get("accepted_seq") if native else None,
            "applied_seq": native.get("applied_seq") if native else None,
            "reason_code": envelope.get("reason_code") if isinstance(envelope, dict) else None,
            "payload_hash": row["payload_hash"],
            "aggregate_revision": row["aggregate_revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _context(self, mission_id: str, job_id: str, assignment_id: str, *,
                 trigger_id: str | None = None, authorize: bool) -> dict:
        assignment = self._assignment(assignment_id)
        if (
            assignment is None or assignment["mission_id"] != mission_id
            or assignment["job_id"] != job_id
        ):
            raise HarnessError(tr("superagent.conflict"), code="superagent/instruction-scope")
        if not isinstance(assignment.get("run_id"), str) or not assignment["run_id"]:
            raise HarnessError(tr("superagent.conflict"), code="superagent/run-conflict")
        worker = self._worker(assignment["worker_id"])
        if worker is None:
            raise HarnessError(tr("superagent.worker-unavailable"), code="superagent/worker-unavailable")
        profile = self._profiles.get(worker["profile_id"])
        if (
            worker["ticket_id"] != assignment["ticket_id"]
            or worker.get("mission_id") != mission_id
            or worker.get("job_id") != job_id
            or worker.get("profile_id") != assignment["profile_id"]
            or worker.get("workspace_id") != assignment.get("workspace_id")
            or worker.get("session_id") != assignment.get("session_id")
            or worker.get("repo") != assignment["repo"]
            or worker.get("base_commit") != assignment["base_commit"]
        ):
            raise HarnessError(tr("superagent.worker-unavailable"), code="superagent/instruction-scope")
        context = {
            "assignment": assignment, "worker": worker, "profile": profile,
            "trigger": None, "source": None, "thread": None, "ticket": None, "mandate": None,
        }
        if not authorize:
            return context
        if (
            assignment["state"] not in _ACTIVE_ASSIGNMENTS
            or assignment["job_state"] not in _ACTIVE_JOBS
            or assignment["role"] not in {"work", "integrate"}
            or assignment["mission_state"] not in {"running", "integrating", "reviewing"}
        ):
            raise HarnessError(tr("superagent.conflict"), code="superagent/instruction-unavailable")
        if worker["state"] != "ready" or profile is None:
            raise HarnessError(tr("superagent.worker-unavailable"), code="superagent/worker-unavailable")
        ticket = self._authorization.ticket(assignment["ticket_id"])
        if ticket is None or ticket.get("state") != "open":
            raise HarnessError(tr("superagent.mandate.unavailable"), code="superagent/instruction-unauthorized")
        mandate = ticket.get("mandate")
        mandate_id = mandate.get("mandate_id") if isinstance(mandate, dict) else None
        if not mandate_id or self._authorization.revalidate_mandate(mandate_id, actor=_ACTOR).get("status") not in {"valid", "affected"}:
            raise HarnessError(tr("superagent.mandate.unavailable"), code="superagent/mandate-unavailable")
        trigger = self._sources.get_trigger(_text(trigger_id, "trigger_id"))
        if trigger is None:
            raise HarnessError(tr("superagent.corrupt"), code="superagent/context-corrupt")
        ticket_input = self._read_one(
            "SELECT context_only,state FROM ticket_inputs WHERE ticket_id=? AND trigger_id=?",
            (assignment["ticket_id"], trigger_id),
        )
        if ticket_input is None or ticket_input["context_only"] or ticket_input["state"] == "handed_back":
            raise HarnessError(tr("superagent.mandate.unavailable"), code="superagent/instruction-unauthorized")
        source_id = trigger.get("source_instance_id")
        thread_id = trigger.get("primary_thread_id")
        actor = trigger.get("actor")
        source = self._sources.get_instance(source_id)
        thread = self._sources.get_thread(thread_id)
        scope = mandate.get("scope")
        if source is None or not isinstance(scope, Mapping):
            raise HarnessError(tr("superagent.mandate.unavailable"), code="superagent/instruction-unauthorized")
        if (
            not source.enabled or not source.installed
            or source.config_revision != trigger.get("source_revision")
            or thread is None or thread.get("source_instance_id") != source_id
            or thread.get("source_state") == "closed"
            or not isinstance(actor, Mapping)
            or actor.get("source_instance_id") != source_id
            or actor.get("actor_kind") != "human"
            or source_id not in scope.get("source_instance_ids", [])
            or thread_id not in scope.get("thread_ids", [])
            or source_id not in getattr(profile, "source_instance_ids", ())
            or not set(getattr(profile, "actions", ())) <= set(scope.get("actions", []))
            or not set(getattr(profile, "tools", ())) <= set(scope.get("tools", []))
        ):
            raise HarnessError(tr("superagent.mandate.unavailable"), code="superagent/instruction-unauthorized")
        self._admit_mission(mission_id)
        found = self._executor.find_worker(worker, profile)
        if (
            not isinstance(found, dict)
            or found.get("workspace_id") != worker.get("workspace_id")
            or found.get("session_id") != worker.get("session_id")
        ):
            raise HarnessError(tr("superagent.worker-unavailable"), code="superagent/worker-conflict")
        context.update(trigger=trigger, source=source, thread=thread, ticket=ticket, mandate=mandate)
        return context

    def _prepare(self, *, request_id: str, mission_id: str, job_id: str,
                 assignment_id: str, run_id: str, turn_id: int, payload: dict) -> tuple[dict, bool]:
        payload_json = _json(payload)
        payload_hash = _hash(payload_json)
        now = _now()
        with self._store.transaction(write=True) as db:
            old = db.execute("SELECT * FROM mission_instructions WHERE request_id=?", (request_id,)).fetchone()
            if old is not None:
                row = self._decode(old)
                if (
                    row["mission_id"] != mission_id or row["job_id"] != job_id
                    or row["assignment_id"] != assignment_id or row["run_id"] != run_id
                    or row["turn_id"] != turn_id or row["payload_hash"] != payload_hash
                ):
                    raise _conflict()
                return row, False
            if db.execute("SELECT count(*) FROM mission_instructions").fetchone()[0] >= 4096:
                raise HarnessError(tr("superagent.worker-capacity"), code="superagent/instruction-capacity")
            db.execute(
                "INSERT INTO mission_instructions(request_id,mission_id,job_id,assignment_id,run_id,turn_id,"
                "payload_json,payload_hash,state,receipt_json,aggregate_revision,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,'pending','{}',1,?,?)",
                (request_id, mission_id, job_id, assignment_id, run_id, turn_id,
                 payload_json, payload_hash, now, now),
            )
            self._store.append_audit(
                db, aggregate_type="mission_instruction", aggregate_id=request_id,
                event_type="mission_instruction.created",
                payload={"mission_id": mission_id, "job_id": job_id, "assignment_id": assignment_id,
                         "run_id": run_id, "turn_id": turn_id, "payload_hash": payload_hash,
                         "revision": 1},
                actor=_ACTOR, correlation_id="mission-instruction:" + request_id,
            )
            changed = db.execute(
                "UPDATE mission_instructions SET state='submitting',aggregate_revision=2,updated_at=? "
                "WHERE request_id=? AND state='pending' AND aggregate_revision=1",
                (now, request_id),
            ).rowcount
            if changed != 1:
                raise _conflict()
            self._store.append_audit(
                db, aggregate_type="mission_instruction", aggregate_id=request_id,
                event_type="mission_instruction.submitting",
                payload={"payload_hash": payload_hash, "revision": 2},
                actor=_ACTOR, correlation_id="mission-instruction:" + request_id,
            )
            row = db.execute("SELECT * FROM mission_instructions WHERE request_id=?", (request_id,)).fetchone()
            return self._decode(row), True

    def _transition(self, request_id: str, target: str, *,
                    native_receipt: dict | None = None, reason_code: str | None = None) -> dict:
        for attempt in range(2):
            current = self.get(request_id)
            if current is None:
                raise HarnessError(tr("superagent.corrupt"), code="superagent/instruction-corrupt")
            old_state = current["state"]
            if old_state == "rejected":
                return current
            if old_state == "accepted":
                if target != "accepted" or native_receipt is None:
                    return current
                old_native = current["receipt"].get("native_receipt") or {}
                old_status = old_native.get("status")
                new_status = native_receipt.get("status")
                if old_status != "pending" or new_status not in {"applied", "unapplied"}:
                    return current
            elif target not in _TRANSITIONS[old_state]:
                if old_state == target:
                    return current
                raise _conflict()
            if target == "accepted":
                envelope = self._envelope(
                    "accepted", native_receipt=self._native_evidence(native_receipt),
                )
            else:
                envelope = self._envelope(target, reason_code=reason_code)
            encoded = _json(envelope)
            if current["state"] == target and current["receipt"] == envelope:
                return current
            with self._store.transaction(write=True) as db:
                old = db.execute(
                    "SELECT * FROM mission_instructions WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if old is None:
                    raise HarnessError(tr("superagent.corrupt"), code="superagent/instruction-corrupt")
                if old["aggregate_revision"] != current["aggregate_revision"]:
                    if attempt:
                        raise _conflict()
                    continue
                changed = db.execute(
                    "UPDATE mission_instructions SET state=?,receipt_json=?,aggregate_revision=aggregate_revision+1,updated_at=? "
                    "WHERE request_id=? AND aggregate_revision=?",
                    (target, encoded, _now(), request_id, current["aggregate_revision"]),
                ).rowcount
                if changed != 1:
                    if attempt:
                        raise _conflict()
                    continue
                self._store.append_audit(
                    db, aggregate_type="mission_instruction", aggregate_id=request_id,
                    event_type="mission_instruction." + target,
                    payload={"revision": current["aggregate_revision"] + 1,
                             "receipt_hash": _hash(encoded),
                             "application": native_receipt.get("status") if native_receipt else None,
                             "reason_code": reason_code},
                    actor=_ACTOR, correlation_id="mission-instruction:" + request_id,
                )
            result = self.get(request_id)
            if result is None:
                raise HarnessError(tr("superagent.corrupt"), code="superagent/instruction-corrupt")
            return result
        raise _conflict()

    @staticmethod
    def _matches(row: dict, *, mission_id: str, job_id: str, assignment_id: str,
                 run_id: str, payload_hash: str) -> None:
        if (
            row["mission_id"] != mission_id or row["job_id"] != job_id
            or row["assignment_id"] != assignment_id or row["run_id"] != run_id
            or row["payload_hash"] != payload_hash
        ):
            raise _conflict()

    async def deliver(self, mission_id: str, job_id: str, assignment_id: str,
                      request_id: str, trigger_id: str, text: str, *, expected_run_id=None, expected_turn_id=None) -> dict:
        """首次调用持久化后最多受理一次；其余调用只回查原生回执。"""
        for value, name in (
            (mission_id, "mission_id"), (job_id, "job_id"),
            (assignment_id, "assignment_id"), (request_id, "request_id"),
            (trigger_id, "trigger_id"),
        ):
            _text(value, name)
        if len(request_id) > 96 or not isinstance(text, str) or not text.strip() or len(text) > 65536 or "\x00" in text:
            raise ValueError(tr("superagent.invalid", field="mission_instruction"))
        operation_key = self._operation_key(request_id, assignment_id)
        async with self._lock(request_id):
            existing = self.get(request_id)
            if existing is not None:
                context = self._context(mission_id, job_id, assignment_id, authorize=False)
                trigger = self._sources.get_trigger(trigger_id)
                if trigger is None:
                    raise HarnessError(tr("superagent.corrupt"), code="superagent/context-corrupt")
                payload = self._payload(trigger, text)
                self._matches(
                    existing, mission_id=mission_id, job_id=job_id, assignment_id=assignment_id,
                    run_id=context["assignment"]["run_id"], payload_hash=_hash(_json(payload)),
                )
                if existing["state"] in {"submitting", "unknown", "accepted"}:
                    return self._public(await self._reconcile_locked(existing, context))
                return self._public(existing)

            context = self._context(
                mission_id, job_id, assignment_id, trigger_id=trigger_id, authorize=True,
            )
            payload = self._payload(context["trigger"], text)
            turn_id = self._executor.instruction_turn(
                context["worker"], context["assignment"], operation_key,
            )
            if (expected_run_id is not None and context["assignment"]["run_id"] != expected_run_id
                    or expected_turn_id is not None and turn_id != expected_turn_id):
                raise HarnessError(tr("superagent.conflict"), code="superagent/run-conflict")
            if type(turn_id) is not int or not 1 <= turn_id <= 2**53 - 1:
                raise HarnessError(tr("superagent.conflict"), code="superagent/run-conflict")
            row, claimed = self._prepare(
                request_id=request_id, mission_id=mission_id, job_id=job_id,
                assignment_id=assignment_id, run_id=context["assignment"]["run_id"],
                turn_id=turn_id, payload=payload,
            )
            if not claimed:
                if row["state"] in {"submitting", "unknown", "accepted"}:
                    return self._public(await self._reconcile_locked(row, context))
                return self._public(row)
            if row["state"] != "submitting":
                return self._public(row)

            original_run_id = row["run_id"]
            original_session_id = context["assignment"]["session_id"]

            def before_accept() -> None:
                latest = self._context(
                    mission_id, job_id, assignment_id, trigger_id=trigger_id, authorize=True,
                )
                if (
                    latest["assignment"]["run_id"] != original_run_id
                    or latest["assignment"]["session_id"] != original_session_id
                    or _hash(_json(self._payload(latest["trigger"], text))) != row["payload_hash"]
                ):
                    raise HarnessError(tr("superagent.conflict"), code="superagent/run-conflict")

            try:
                response = await self._executor.deliver_instruction(
                    context["worker"], context["assignment"], text, operation_key, turn_id, before_accept,
                )
            except HarnessError as exc:
                definite = {
                    "run/stale-turn", "run/steering-capacity", "run/steering-unsupported",
                    "superagent/run-conflict", "superagent/mission-paused",
                    "superagent/mission-not-running", "superagent/mandate-unavailable",
                    "superagent/instruction-unauthorized", "superagent/instruction-unavailable",
                    "superagent/worker-unavailable", "superagent/worker-conflict",
                    "superagent/context-corrupt", "superagent/instruction-scope",
                }
                if exc.code in definite:
                    row = self._transition(request_id, "rejected", reason_code=exc.code)
                    return self._public(row)
                self._transition(request_id, "unknown", reason_code=exc.code or "submission-unconfirmed")
                raise
            except BaseException as exc:
                self._transition(
                    request_id, "unknown",
                    reason_code=type(exc).__name__[:96] or "submission-unconfirmed",
                )
                raise

            if (
                not isinstance(response, dict)
                or type(response.get("accepted")) is not bool
                or type(response.get("deferred", False)) is not bool
                or response.get("accepted") and response.get("deferred")
            ):
                row = self._transition(request_id, "unknown", reason_code="invalid-native-response")
                return self._public(row)
            if response.get("deferred"):
                row = self._transition(request_id, "rejected", reason_code="native-deferred")
                return self._public(row)
            if response.get("accepted") is not True:
                row = self._transition(request_id, "unknown", reason_code="acceptance-unconfirmed")
                return self._public(row)
            native = response.get("receipt")
            status = self._native_status(native, operation_key, turn_id, text)
            if (
                type(response.get("request_id")) is not str or response["request_id"] != operation_key
                or type(response.get("turn")) is not int or response["turn"] != turn_id
                or status is None
            ):
                row = self._transition(request_id, "unknown", reason_code="native-receipt-invalid")
                return self._public(row)
            row = self._transition(request_id, "accepted", native_receipt=native)
            return self._public(row)

    def receipt(self, request_id: str) -> dict | None:
        row = self.get(request_id)
        return None if row is None else self._public(row)

    def applied_context(self, mission_id: str, *, job_id: str | None = None) -> dict:
        """只投影当前计划中已应用的原指令，优先保留最近内容并显式报告截断。"""
        _text(mission_id, "mission_id")
        if job_id is not None:
            _text(job_id, "job_id")
        with self._store.transaction() as db:
            mission = db.execute("SELECT plan_revision FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
            if mission is None:
                raise LookupError(tr("superagent.corrupt"))
            if job_id is not None and db.execute(
                "SELECT 1 FROM jobs WHERE job_id=? AND mission_id=? AND plan_revision=?",
                (job_id, mission_id, mission["plan_revision"]),
            ).fetchone() is None:
                raise HarnessError(tr("superagent.conflict"), code="superagent/instruction-scope")
            where = ("instruction.mission_id=? AND instruction.state='accepted' "
                     "AND job.plan_revision=?")
            params = [mission_id, mission["plan_revision"]]
            if job_id is not None:
                where += " AND instruction.job_id=?"
                params.append(job_id)
            source = (" FROM mission_instructions instruction JOIN jobs job ON job.job_id=instruction.job_id WHERE " + where)
            invalid = db.execute(
                "SELECT instruction.*" + source
                + " AND (json_type(instruction.receipt_json,'$.native_receipt.status') IS NOT 'text' "
                "OR json_extract(instruction.receipt_json,'$.native_receipt.status') NOT IN ('pending','applied','unapplied')) LIMIT 1",
                params,
            ).fetchone()
            if invalid is not None:
                self._decode(invalid)
            applied = source + " AND json_extract(instruction.receipt_json,'$.native_receipt.status')='applied'"
            total = db.execute("SELECT count(*)" + applied, params).fetchone()[0]
            rows = db.execute("SELECT instruction.*" + applied + " ORDER BY instruction.rowid DESC LIMIT 16", params).fetchall()
            items = []
            for raw in rows:
                row = self._decode(raw)
                assignment = db.execute(
                    "SELECT assignment.*,job.mission_id FROM assignments assignment "
                    "JOIN jobs job ON job.job_id=assignment.job_id WHERE assignment.assignment_id=?",
                    (row["assignment_id"],),
                ).fetchone()
                if (assignment is None or assignment["mission_id"] != mission_id
                        or assignment["job_id"] != row["job_id"] or assignment["run_id"] != row["run_id"]):
                    raise HarnessError(tr("superagent.corrupt"), code="superagent/instruction-corrupt")
                native = row["receipt"]["native_receipt"]
                text = row["payload"]["text"]
                shown = text[:2048]
                item = {
                    "request_id": row["request_id"], "job_id": row["job_id"],
                    "assignment_id": row["assignment_id"], "run_id": row["run_id"],
                    "turn_id": row["turn_id"], "applied_seq": native["applied_seq"],
                    "text": shown, "text_truncated": len(shown) < len(text),
                }
                # 整体预算包含序列化开销；不切断 UTF-8 字节，超长新指令仍保留有标记的摘要。
                while shown and len(_json([*items, item]).encode("utf-8")) > 15000:
                    shown = shown[:len(shown) // 2]
                    item.update(text=shown, text_truncated=True)
                if not shown:
                    break
                items.append(item)
            items.reverse()
            return {"items": items, "total": total, "has_more": total > len(items),
                    "text_truncated": any(item["text_truncated"] for item in items)}

    async def reconcile(self, request_id: str) -> dict | None:
        """只查询原 request_id，不创建新投递。"""
        _text(request_id, "request_id")
        async with self._lock(request_id):
            row = self.get(request_id)
            if row is None:
                return None
            if row["state"] == "rejected":
                return self._public(row)
            context = self._context(
                row["mission_id"], row["job_id"], row["assignment_id"], authorize=False,
            )
            return self._public(await self._reconcile_locked(row, context))

    async def reconcile_pending(self, *, limit=100):
        """有界轮转回查原指令；受理结果未知时从不重新调用原生投递。"""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError(tr("superagent.invalid", field="limit"))
        with self._store.transaction() as db:
            rows = db.execute(
                "SELECT rowid AS cursor,request_id FROM mission_instructions WHERE "
                "(state IN ('submitting','unknown') OR (state='accepted' "
                "AND json_extract(receipt_json,'$.native_receipt.status')='pending')) "
                "ORDER BY rowid<=?,rowid LIMIT ?",
                (self._reconcile_cursor, limit + 1),
            ).fetchall()
        results, errors = [], []
        for row in rows[:limit]:
            try:
                results.append(await self.reconcile(row["request_id"]))
            except Exception as exc:
                errors.append({"request_id": row["request_id"], "code": getattr(exc, "code", type(exc).__name__)})
            self._reconcile_cursor = row["cursor"]
        return {"processed": min(limit, len(rows)), "results": results, "errors": errors, "backlog": len(rows) > limit}

    async def _reconcile_locked(self, row: dict, context: dict) -> dict:
        current = self.get(row["request_id"])
        if current is None:
            raise HarnessError(tr("superagent.corrupt"), code="superagent/instruction-corrupt")
        if current["state"] == "rejected":
            return current
        operation_key = self._operation_key(current["request_id"], current["assignment_id"])
        try:
            native = self._executor.reconcile_instruction(
                context["worker"], context["assignment"], operation_key,
                current["turn_id"], current["payload"]["text"],
            )
        except Exception as exc:
            if current["state"] == "submitting":
                return self._transition(
                    current["request_id"], "unknown",
                    reason_code=type(exc).__name__[:96] or "reconciliation-unavailable",
                )
            return self.get(current["request_id"]) or current
        status = self._native_status(
            native, operation_key, current["turn_id"], current["payload"]["text"],
        ) if native is not None else None
        if status is None:
            if current["state"] == "submitting":
                return self._transition(
                    current["request_id"], "unknown",
                    reason_code="native-receipt-missing" if native is None else "native-receipt-invalid",
                )
            return self.get(current["request_id"]) or current
        return self._transition(current["request_id"], "accepted", native_receipt=native)

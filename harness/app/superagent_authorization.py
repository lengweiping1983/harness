"""下一代 Superagent 的 Gate、Evaluation、Decision、Mandate 与 Ticket 授权闭环。"""
from __future__ import annotations

from datetime import datetime, timedelta
import inspect
import json
import re
from typing import Any, Mapping
from uuid import uuid4

from .superagent_coordinator_store import CoordinatorStore, _hash, _json, _now, _text, _time
from .superagent_source_registry import NormalizedThread, NormalizedTrigger, _bounded_text


_DEFAULT_ADMISSION = {
    "listed_unaddressed": "inferred",
    "unlisted_addressed": "ignore",
    "unlisted_unaddressed": "ignore",
}
_ADMISSION_CELLS = set(_DEFAULT_ADMISSION)
_STRENGTH = {"ignore": 0, "inferred": 1, "explicit": 2}
_MAX_EVAL_TRIGGERS = 32
_MAX_POLICY_RULES = 64


def _identity(prefix: str) -> str:
    return prefix + "_" + uuid4().hex


def _string_list(value: object, name: str, *, maximum: int = 128, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum or (not allow_empty and not value):
        raise ValueError(f"{name} must be a bounded list")
    result = [_bounded_text(item, name, limit=512) for item in value]
    if len(set(result)) != len(result):
        raise ValueError(f"{name} contains duplicates")
    return sorted(result)


def _actor(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"source_instance_id", "actor_id", "actor_kind"}:
        raise ValueError("actor identity has an invalid shape")
    kind = value["actor_kind"]
    if kind not in {"human", "bot", "system"}:
        raise ValueError("actor_kind is invalid")
    return {
        "source_instance_id": _text(value["source_instance_id"], "source_instance_id"),
        "actor_id": _text(value["actor_id"], "actor_id"),
        "actor_kind": kind,
    }


def _validate_scope(value: object, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"source_instance_ids", "thread_ids", "actions", "tools", "budget"}:
        raise ValueError(f"{name} must contain source_instance_ids, thread_ids, actions, tools and budget")
    budget = value["budget"]
    if not isinstance(budget, Mapping) or set(budget) != {"max_turns", "max_seconds"}:
        raise ValueError(f"{name}.budget is invalid")
    max_turns, max_seconds = budget["max_turns"], budget["max_seconds"]
    if type(max_turns) is not int or not 1 <= max_turns <= 1000:
        raise ValueError(f"{name}.budget.max_turns is invalid")
    if type(max_seconds) is not int or not 1 <= max_seconds <= 31_536_000:
        raise ValueError(f"{name}.budget.max_seconds is invalid")
    return {
        "source_instance_ids": _string_list(value["source_instance_ids"], f"{name}.source_instance_ids", allow_empty=False),
        "thread_ids": _string_list(value["thread_ids"], f"{name}.thread_ids", allow_empty=False),
        "actions": _string_list(value["actions"], f"{name}.actions", allow_empty=False),
        "tools": _string_list(value["tools"], f"{name}.tools"),
        "budget": {"max_turns": max_turns, "max_seconds": max_seconds},
    }


def _scope_subset(requested: dict[str, Any], allowed: dict[str, Any]) -> bool:
    return (
        set(requested["source_instance_ids"]) <= set(allowed["source_instance_ids"])
        and set(requested["thread_ids"]) <= set(allowed["thread_ids"])
        and set(requested["actions"]) <= set(allowed["actions"])
        and set(requested["tools"]) <= set(allowed["tools"])
        and requested["budget"]["max_turns"] <= allowed["budget"]["max_turns"]
        and requested["budget"]["max_seconds"] <= allowed["budget"]["max_seconds"]
    )


def validate_policy(value: object) -> dict[str, Any]:
    """验证授权策略快照；未知字段拒绝，避免宽松解析扩大授权。"""
    if not isinstance(value, Mapping) or set(value) - {"mode", "timeout_seconds", "rules", "sources"}:
        raise ValueError("policy has an invalid shape")
    mode = value.get("mode", "observe")
    if mode not in {"off", "observe", "auto"}:
        raise ValueError("policy mode is invalid")
    timeout = value.get("timeout_seconds", 120)
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError("policy timeout_seconds is invalid")
    rules = value.get("rules", [])
    if not isinstance(rules, list) or len(rules) > _MAX_POLICY_RULES:
        raise ValueError("policy rules are invalid")
    parsed_rules = []
    seen_rules = set()
    for rule in rules:
        if not isinstance(rule, Mapping) or set(rule) != {"id", "name", "enabled", "instruction"}:
            raise ValueError("policy rule has an invalid shape")
        rule_id = _bounded_text(rule["id"], "rule.id", limit=80)
        if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", rule_id) is None or rule_id in seen_rules:
            raise ValueError("policy rule ID is invalid or duplicated")
        seen_rules.add(rule_id)
        if type(rule["enabled"]) is not bool:
            raise ValueError("policy rule enabled must be boolean")
        parsed_rules.append({
            "id": rule_id,
            "name": _bounded_text(rule["name"], "rule.name", limit=256),
            "enabled": rule["enabled"],
            "instruction": _bounded_text(rule["instruction"], "rule.instruction", limit=4000),
        })
    sources = value.get("sources", {})
    if not isinstance(sources, Mapping) or len(sources) > 256:
        raise ValueError("policy sources are invalid")
    parsed_sources = {}
    for source_id, part in sources.items():
        source_id = _bounded_text(source_id, "source_instance_id", limit=128)
        if not isinstance(part, Mapping) or set(part) - {"allowed", "bots", "admission", "scope"}:
            raise ValueError(f"policy source {source_id} has an invalid shape")
        identities = {}
        for field in ("allowed", "bots"):
            entries = part.get(field, [])
            if not isinstance(entries, list) or len(entries) > 256:
                raise ValueError(f"policy source {source_id}.{field} is invalid")
            parsed = []
            seen = set()
            for entry in entries:
                if not isinstance(entry, Mapping) or set(entry) - {"id", "name"} or "id" not in entry:
                    raise ValueError(f"policy source {source_id}.{field} entry is invalid")
                identity = _bounded_text(entry["id"], f"{field}.id", limit=512)
                if identity in seen:
                    raise ValueError(f"policy source {source_id}.{field} contains duplicate IDs")
                seen.add(identity)
                item = {"id": identity}
                if "name" in entry:
                    item["name"] = _bounded_text(entry["name"], f"{field}.name", limit=256)
                parsed.append(item)
            identities[field] = sorted(parsed, key=lambda item: item["id"])
        admission = part.get("admission", {})
        if not isinstance(admission, Mapping) or len(admission) > 64:
            raise ValueError(f"policy source {source_id}.admission is invalid")
        parsed_admission = {}
        for row, cells in admission.items():
            row = _bounded_text(row, "admission row", limit=128)
            if not isinstance(cells, Mapping) or set(cells) - _ADMISSION_CELLS:
                raise ValueError(f"policy source {source_id}.admission row is invalid")
            parsed_cells = {}
            for cell, result in cells.items():
                if result not in ({"inferred", "ignore"} if cell != "unlisted_addressed" else {"explicit", "inferred", "ignore"}):
                    raise ValueError(f"policy source {source_id}.admission result is invalid")
                parsed_cells[cell] = result
            parsed_admission[row] = parsed_cells
        parsed_sources[source_id] = {
            **identities, "admission": parsed_admission,
            **({"scope": _validate_scope(part["scope"], name=f"sources.{source_id}.scope")} if "scope" in part else {}),
        }
    return {
        "mode": mode, "timeout_seconds": timeout,
        "rules": parsed_rules, "sources": parsed_sources,
    }


class Gate:
    """确定性准入门；模型只能评估 Gate 放行的候选，不能提高其授权强度。"""

    @staticmethod
    def evaluate(
        trigger: Mapping[str, Any], source: Mapping[str, Any], policy: Mapping[str, Any],
        *, thread_state: str,
    ) -> dict[str, Any]:
        source_id = trigger["source_instance_id"]
        actor = trigger["actor"]
        if not source["enabled"]:
            return {"admissible": False, "reason": "source_disabled"}
        metadata = trigger.get("metadata")
        if not isinstance(metadata, Mapping):
            return {"admissible": False, "reason": "invalid_metadata"}
        if metadata.get("lifecycle_only") is True:
            return {"admissible": False, "reason": "lifecycle_only_event"}
        if thread_state == "closed":
            return {"admissible": False, "reason": "thread_closed"}
        if policy["mode"] == "off":
            return {"admissible": False, "reason": "policy_off"}
        part = policy["sources"].get(source_id, {})
        allowed_scope = part.get("scope")
        if allowed_scope is not None and (
            source_id not in allowed_scope["source_instance_ids"]
            or (allowed_scope["thread_ids"] != ["$current"] and trigger.get("primary_thread_id") not in allowed_scope["thread_ids"])
        ):
            return {"admissible": False, "reason": "outside_policy_scope"}
        actor_id = actor["actor_id"]
        actor_kind = actor["actor_kind"]
        if metadata.get('context_event') is True:
            listed = {item['id'] for item in part.get('bots' if actor_kind=='bot' else 'allowed',[])}
            return {'admissible':actor_kind in {'human','bot'} and actor_id in listed,'strength':'inferred',
                'reason':'context_event','basis':{'source_instance_id':source_id,'source_revision':trigger['source_revision'],'via':'context_event'}}
        addressed = trigger["addressed"] in {"mention", "direct"}
        row = trigger["metadata"].get("scope_row", "default")
        if not isinstance(row, str) or not row:
            return {"admissible": False, "reason": "invalid_scope_row"}
        basis = {
            "source_instance_id": source_id,
            "source_revision": trigger["source_revision"],
            "scope_row": row,
            "addressed": trigger["addressed"],
            "policy_mode": policy["mode"],
        }
        if actor_kind == "bot":
            listed = {item["id"] for item in part.get("bots", [])}
            if actor_id not in listed:
                return {"admissible": False, "reason": "bot_not_listed"}
            return {"admissible": True, "strength": "inferred", "basis": {**basis, "via": "bot"}}
        if actor_kind != "human":
            return {"admissible": False, "reason": "non_human_actor"}
        allowed = {item["id"]: item for item in part.get("allowed", [])}
        listed = actor_id in allowed
        if listed and addressed:
            return {
                "admissible": True, "strength": "explicit",
                "basis": {**basis, "via": "addressed", "sender": allowed[actor_id].get("name", actor_id)},
            }
        cell = "listed_unaddressed" if listed else (
            "unlisted_addressed" if addressed else "unlisted_unaddressed"
        )
        decision = part.get("admission", {}).get(row, {}).get(cell, _DEFAULT_ADMISSION[cell])
        if decision == "ignore":
            return {"admissible": False, "reason": f"admission_{cell}_ignored", "basis": {**basis, "cell": cell}}
        if decision == "explicit" and not addressed:
            return {"admissible": False, "reason": "explicit_requires_addressing", "basis": {**basis, "cell": cell}}
        return {
            "admissible": True, "strength": decision,
            "basis": {**basis, "via": "admission", "cell": cell,
                      **({"sender": allowed[actor_id].get("name", actor_id)} if listed else {})},
        }


class CoordinatorAuthorizationEngine:
    """持久化 Gate/Evaluation/Decision；不调用模型、不启动 Worker，也不执行副作用。"""

    def __init__(self, store: CoordinatorStore):
        self._store = store

    @staticmethod
    def _append_disposition(
        db, *, trigger_id: str, state: str, event_type: str, payload: Mapping[str, Any],
        actor: object, evaluation_id: str | None = None, decision_id: str | None = None,
        fanout: list[str] | None = None, context: list[str] | None = None,
    ) -> str:
        now = _now()
        old = db.execute("SELECT fanout_json,context_json FROM trigger_dispositions WHERE trigger_id=?", (trigger_id,)).fetchone()
        fanout_json = _json(sorted(set(fanout))) if fanout is not None else old["fanout_json"] if old else "[]"
        context_json = _json(sorted(set(context))) if context is not None else old["context_json"] if old else "[]"
        db.execute(
            """INSERT INTO trigger_dispositions(trigger_id,state,evaluation_id,decision_id,fanout_json,context_json,updated_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(trigger_id) DO UPDATE SET state=excluded.state,
                 evaluation_id=excluded.evaluation_id,decision_id=excluded.decision_id,
                 fanout_json=excluded.fanout_json,context_json=excluded.context_json,updated_at=excluded.updated_at""",
            (trigger_id, state, evaluation_id, decision_id, fanout_json, context_json, now),
        )
        audit_id = CoordinatorStore.append_audit(
            db, aggregate_type="trigger", aggregate_id=trigger_id,
            event_type=f"trigger.{event_type}", payload=dict(payload), actor=actor,
        )
        db.execute(
            """INSERT INTO trigger_disposition_events(event_id,trigger_id,event_type,payload_json,occurred_at,audit_event_id)
               VALUES(?,?,?,?,?,?)""",
            (_identity("disp"), trigger_id, event_type, _json(dict(payload)), now, audit_id),
        )
        return audit_id

    @staticmethod
    def _mandate_revoked(db, mandate_id: str) -> bool:
        return db.execute(
            "SELECT 1 FROM mandate_events WHERE mandate_id=? AND event_type IN ('revoked','expired') LIMIT 1",
            (mandate_id,),
        ).fetchone() is not None

    @staticmethod
    def _append_mandate_event(db, *, mandate_id: str, event_type: str, payload: Mapping[str, Any], actor: object) -> None:
        if db.execute(
            "SELECT 1 FROM mandate_events WHERE mandate_id=? AND event_type=? AND payload_json=? LIMIT 1",
            (mandate_id, event_type, _json(dict(payload))),
        ).fetchone():
            return
        db.execute(
            "INSERT INTO mandate_events(event_id,mandate_id,event_type,payload_json,occurred_at,actor_json) VALUES(?,?,?,?,?,?)",
            (_identity("mde"), mandate_id, event_type, _json(dict(payload)), _now(), _json(actor)),
        )
        CoordinatorStore.append_audit(
            db, aggregate_type="mandate", aggregate_id=mandate_id,
            event_type=f"mandate.{event_type}", payload=dict(payload), actor=actor,
        )

    @staticmethod
    def _ticket_update(db, ticket, changes: Mapping[str, Any], *, event_type: str, payload: Mapping[str, Any], actor: object):
        expected = ticket["aggregate_revision"]
        if not CoordinatorStore.compare_and_swap(db, "ticket", ticket["ticket_id"], expected):
            raise RuntimeError("ticket revision conflict inside a write transaction")
        fields = dict(changes)
        fields["updated_at"] = _now()
        fields["aggregate_revision"] = expected + 1
        cursor = db.execute(
            "UPDATE tickets SET " + ",".join(f"{field}=?" for field in fields)
            + " WHERE ticket_id=? AND aggregate_revision=?",
            (*fields.values(), ticket["ticket_id"], expected),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("ticket revision ledger diverged from ticket record")
        CoordinatorStore.append_audit(
            db, aggregate_type="ticket", aggregate_id=ticket["ticket_id"],
            event_type=event_type, payload={"revision": expected + 1, **dict(payload)}, actor=actor,
        )
        return db.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket["ticket_id"],)).fetchone()

    @staticmethod
    def _source_policy_scope(policy: Mapping[str, Any], source_id: str, thread_id: str) -> dict[str, Any]:
        part = policy["sources"].get(source_id, {})
        default = {
            "source_instance_ids": [source_id],
            "thread_ids": [thread_id],
            "actions": ["read"],
            "tools": [],
            "budget": {"max_turns": 1, "max_seconds": 600},
        }
        scope = dict(part.get("scope", default))
        if scope.get("thread_ids") == ["$current"]:
            scope["thread_ids"] = [thread_id]
        return _validate_scope(scope, name="mandate_scope")

    @staticmethod
    def _mandate_status(db, mandate, *, actor: object, now: str, record: bool = True) -> str:
        if CoordinatorAuthorizationEngine._mandate_revoked(db, mandate["mandate_id"]):
            return "revoked"
        if mandate["expires_at"] is not None and mandate["expires_at"] <= now:
            if record:
                CoordinatorAuthorizationEngine._append_mandate_event(
                    db, mandate_id=mandate["mandate_id"], event_type="expired",
                    payload={"expired_at": mandate["expires_at"]}, actor=actor,
                )
            return "expired"
        source = db.execute(
            "SELECT * FROM source_instances WHERE source_instance_id=?",
            (mandate["source_instance_id"],),
        ).fetchone()
        current = db.execute(
            """SELECT v.* FROM policy_current c JOIN policy_versions v ON v.policy_version=c.policy_version
               WHERE c.singleton=1"""
        ).fetchone()
        if source is None or not source["enabled"] or current is None:
            if record:
                reason = (
                    "source_unavailable" if source is None else
                    "source_disabled" if not source["enabled"] else
                    "policy_unavailable"
                )
                CoordinatorAuthorizationEngine._append_mandate_event(
                    db, mandate_id=mandate["mandate_id"], event_type="revoked",
                    payload={"reason": reason}, actor=actor,
                )
            return "revoked"
        trigger_row = db.execute(
            "SELECT * FROM triggers WHERE trigger_id=?", (mandate["trigger_id"],),
        ).fetchone()
        if trigger_row is None:
            if record:
                CoordinatorAuthorizationEngine._append_mandate_event(
                    db, mandate_id=mandate["mandate_id"], event_type="revoked",
                    payload={"reason": "origin_trigger_unavailable"}, actor=actor,
                )
            return "revoked"
        if source["config_revision"] != trigger_row["source_revision"]:
            if record:
                CoordinatorAuthorizationEngine._append_mandate_event(
                    db, mandate_id=mandate["mandate_id"], event_type="revoked",
                    payload={
                        "reason": "source_config_changed",
                        "trigger_source_revision": trigger_row["source_revision"],
                        "current_source_revision": source["config_revision"],
                    },
                    actor=actor,
                )
            return "revoked"
        policy = json.loads(current["policy_json"])
        trigger = CoordinatorAuthorizationEngine._trigger_view(trigger_row)
        thread = db.execute(
            "SELECT source_state FROM threads WHERE thread_id=?",
            (trigger["primary_thread_id"],),
        ).fetchone()
        if thread is None:
            if record:
                CoordinatorAuthorizationEngine._append_mandate_event(
                    db, mandate_id=mandate["mandate_id"], event_type="revoked",
                    payload={"reason": "origin_thread_unavailable"}, actor=actor,
                )
            return "revoked"
        gate = Gate.evaluate(trigger, source, policy, thread_state=thread["source_state"])
        if not gate.get("admissible") or gate.get("strength") != mandate["strength"]:
            if record:
                reason = (
                    "origin_thread_closed"
                    if gate.get("reason") == "thread_closed" else "current_gate_denied"
                )
                CoordinatorAuthorizationEngine._append_mandate_event(
                    db, mandate_id=mandate["mandate_id"], event_type="revoked",
                    payload={"reason": reason, "policy_version": current["policy_version"]},
                    actor=actor,
                )
            return "revoked"
        if mandate["strength"] == "inferred" and policy["mode"] != "auto":
            if record:
                CoordinatorAuthorizationEngine._append_mandate_event(
                    db, mandate_id=mandate["mandate_id"], event_type="revoked",
                    payload={
                        "reason": "inferred_admission_disabled",
                        "policy_version": current["policy_version"],
                    },
                    actor=actor,
                )
            return "revoked"
        mandate_scope = json.loads(mandate["scope_json"])
        current_scope = CoordinatorAuthorizationEngine._source_policy_scope(
            policy, mandate["source_instance_id"], trigger["primary_thread_id"],
        )
        if not _scope_subset(mandate_scope, current_scope):
            if record:
                CoordinatorAuthorizationEngine._append_mandate_event(
                    db, mandate_id=mandate["mandate_id"], event_type="revoked",
                    payload={"reason": "current_policy_scope_narrowed",
                             "policy_version": current["policy_version"]},
                    actor=actor,
                )
            return "revoked"
        if current["policy_version"] != mandate["policy_version"]:
            if not record:
                return "affected"
            CoordinatorAuthorizationEngine._append_mandate_event(
                db, mandate_id=mandate["mandate_id"], event_type="revalidated",
                payload={
                    "policy_version": current["policy_version"],
                    "previous_policy_version": mandate["policy_version"],
                },
                actor=actor,
            )
        return "valid"

    @staticmethod
    def _trigger_view(row) -> dict[str, Any]:
        actor = json.loads(row["actor_json"])
        metadata = json.loads(row["metadata_json"])
        body = json.loads(row["body_json"])
        return {
            "trigger_id": row["trigger_id"], "source_instance_id": row["source_instance_id"],
            "source_revision": row["source_revision"], "actor": actor,
            "addressed": row["addressed"], "metadata": metadata, "body": body,
            "event_kind": row["event_kind"], "occurred_at": row["occurred_at"],
            "external_event_id": row["external_event_id"], "primary_thread_id": row["primary_thread_id"],
        }

    def publish_policy(self, policy: object, *, actor: object, expected_revision: int | None = None) -> dict[str, Any]:
        with self._store.transaction(write=True) as db:
            return self.publish_policy_in_transaction(db, policy, actor=actor, expected_revision=expected_revision)

    def publish_policy_in_transaction(self, db, policy: object, *, actor: object, expected_revision: int | None = None):
        """在所属操作事务中发布策略，修订、授权影响和回执可原子提交。"""
        parsed = validate_policy(policy)
        encoded = _json(parsed)
        if len(encoded.encode("utf-8")) > 262_144:
            raise ValueError("policy snapshot is too large")
        digest = _hash(encoded)
        source_ids = {row[0] for row in db.execute("SELECT source_instance_id FROM source_instances")}
        unknown = set(parsed["sources"]) - source_ids
        if unknown:
            raise LookupError(f"policy references unknown SourceInstances: {sorted(unknown)}")
        current = db.execute(
            """SELECT c.revision AS current_revision,v.* FROM policy_current c
               JOIN policy_versions v ON v.policy_version=c.policy_version WHERE c.singleton=1"""
        ).fetchone()
        current_revision = 0 if current is None else current["current_revision"]
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision != current_revision
        ):
            raise RuntimeError("policy revision conflict")
        if current is not None and current["policy_hash"] == digest:
            return self._policy_view(current)
        revision = current_revision + 1
        version = f"policy_{revision}_{digest}"
        db.execute(
            """INSERT INTO policy_versions(policy_version,revision,mode,policy_json,policy_hash,created_at,created_by_json)
               VALUES(?,?,?,?,?,?,?)""",
            (version, revision, parsed["mode"], encoded, digest, _now(), _json(actor)),
        )
        for rule in parsed["rules"]:
            db.execute(
                "INSERT INTO policy_rules(policy_version,rule_id,rule_json) VALUES(?,?,?)",
                (version, rule["id"], _json(rule)),
            )
        for source_id, source_policy in parsed["sources"].items():
            db.execute(
                "INSERT INTO source_policies(policy_version,source_instance_id,policy_json) VALUES(?,?,?)",
                (version, source_id, _json(source_policy)),
            )
        db.execute(
            """INSERT INTO policy_current(singleton,policy_version,revision) VALUES(1,?,?)
               ON CONFLICT(singleton) DO UPDATE SET policy_version=excluded.policy_version,revision=excluded.revision""",
            (version, revision),
        )
        CoordinatorStore.append_audit(
            db, aggregate_type="policy", aggregate_id=version, event_type="policy_revision.created",
            payload={"revision": revision, "mode": parsed["mode"], "policy_hash": digest}, actor=actor,
        )
        if current is not None:
            active = db.execute(
                """SELECT m.mandate_id FROM mandates m JOIN tickets t ON t.ticket_id=m.ticket_id
                   WHERE t.state='open' AND m.policy_version<>?
                     AND NOT EXISTS (
                       SELECT 1 FROM mandate_events e
                       WHERE e.mandate_id=m.mandate_id AND e.event_type IN ('revoked','expired')
                     )""",
                (version,)
            ).fetchall()
            for mandate in active:
                self._append_mandate_event(
                    db, mandate_id=mandate["mandate_id"], event_type="affected",
                    payload={"policy_version": version, "reason": "policy_revision_changed"}, actor=actor,
                )
        row = db.execute("SELECT * FROM policy_versions WHERE policy_version=?", (version,)).fetchone()
        return self._policy_view(row)

    @staticmethod
    def _policy_view(row) -> dict[str, Any]:
        return {
            "policy_version": row["policy_version"], "revision": row["revision"],
            "mode": row["mode"], "policy": json.loads(row["policy_json"]),
            "policy_hash": row["policy_hash"], "created_at": row["created_at"],
            "created_by": json.loads(row["created_by_json"]),
        }

    def current_policy(self) -> dict[str, Any] | None:
        with self._store.transaction() as db:
            row = db.execute(
                """SELECT v.* FROM policy_current c JOIN policy_versions v ON v.policy_version=c.policy_version
                   WHERE c.singleton=1"""
            ).fetchone()
        return None if row is None else self._policy_view(row)

    def receive_normalized(
        self, *, source_instance_id: str, expected_source_revision: int, kind: str,
        adapter_version: str, trigger: NormalizedTrigger,
    ) -> dict[str, Any]:
        source_instance_id = _bounded_text(source_instance_id, "source_instance_id", limit=128)
        if type(expected_source_revision) is not int or expected_source_revision < 1:
            raise ValueError("expected_source_revision must be positive")
        if not isinstance(trigger, NormalizedTrigger):
            raise TypeError("trigger must be NormalizedTrigger")
        event_id = _bounded_text(trigger.external_event_id, "external_event_id", limit=512)
        event_kind = _bounded_text(trigger.event_kind, "event_kind", limit=128)
        actor_kind = trigger.actor_kind
        if actor_kind not in {"human", "bot", "system"}:
            raise ValueError("actor_kind is invalid")
        actor = {
            "source_instance_id": source_instance_id,
            "actor_id": _bounded_text(trigger.actor_id, "actor_id", limit=512),
            "actor_kind": actor_kind,
        }
        if trigger.addressed not in {"mention", "direct", "none", "unknown"}:
            raise ValueError("addressed is invalid")
        occurred_at = _time(trigger.occurred_at, "occurred_at")
        if not isinstance(trigger.threads, tuple) or not 1 <= len(trigger.threads) <= 16:
            raise ValueError("trigger must reference one to sixteen threads")
        if any(not isinstance(item, NormalizedThread) for item in trigger.threads):
            raise TypeError("trigger threads must be NormalizedThread values")
        thread_ids = [
            _bounded_text(item.external_thread_id, "external_thread_id", limit=1024)
            for item in trigger.threads
        ]
        primary_external_thread_id = _bounded_text(
            trigger.primary_thread_id, "primary_thread_id", limit=1024,
        )
        if len(set(thread_ids)) != len(thread_ids) or primary_external_thread_id not in thread_ids:
            raise ValueError("trigger thread identities are invalid")
        if sum(item.relation == "primary" for item in trigger.threads) != 1:
            raise ValueError("exactly one primary thread is required")
        if next(item for item in trigger.threads if item.relation == "primary").external_thread_id != primary_external_thread_id:
            raise ValueError("primary thread reference is inconsistent")
        for item in trigger.threads:
            if item.relation not in {"primary", "related"}:
                raise ValueError("thread relation is invalid")
        if not isinstance(trigger.body, Mapping) or not isinstance(trigger.metadata, Mapping):
            raise ValueError("trigger body and metadata must be objects")
        body_json, metadata_json = _json(dict(trigger.body)), _json(dict(trigger.metadata))
        if len(body_json.encode("utf-8")) > 131_072 or len(metadata_json.encode("utf-8")) > 65_536:
            raise ValueError("normalized trigger is too large")
        body_hash, meta_hash = _hash(body_json), _hash(metadata_json)
        if trigger.verification_state not in {"verified", "local_attested"}:
            raise ValueError("verification_state is invalid")
        if trigger.lifecycle is not None:
            if trigger.lifecycle.state not in {"open", "closed", "unknown", "unsupported"}:
                raise ValueError("thread lifecycle state is invalid")
            if (trigger.lifecycle.state == "closed" and trigger.lifecycle.outcome not in {"merged", "closed"}) or (
                trigger.lifecycle.state != "closed" and trigger.lifecycle.outcome is not None
            ):
                raise ValueError("thread lifecycle outcome is invalid")
            if trigger.lifecycle.provider_revision is not None:
                _bounded_text(trigger.lifecycle.provider_revision, "provider_revision", limit=256)
            if trigger.lifecycle.observed_at is not None:
                _time(trigger.lifecycle.observed_at, "observed_at")
        adapter_version = _bounded_text(adapter_version, "adapter_version", limit=128)
        received_at = _now()
        body_ref, meta_ref = f"inline-sha256:{body_hash}", f"inline-sha256:{meta_hash}"
        thread_refs = []
        thread_metadata_bytes = 0
        for item in trigger.threads:
            if item.metadata is None:
                thread_metadata = {}
            elif isinstance(item.metadata, Mapping):
                thread_metadata = item.metadata
            else:
                raise ValueError("thread metadata must be a JSON object")
            if any(not isinstance(key, str) for key in thread_metadata):
                raise ValueError("thread metadata must be a JSON object with string keys")
            thread_metadata_json = _json(dict(thread_metadata))
            thread_metadata_bytes += len(thread_metadata_json.encode("utf-8"))
            if thread_metadata_bytes > 65_536:
                raise ValueError("normalized thread metadata is too large")
            thread_refs.append({
                "external_thread_id": item.external_thread_id,
                "relation": item.relation,
                "metadata": dict(thread_metadata),
            })
        channel_address = None
        if trigger.channel_address is not None:
            if not isinstance(trigger.channel_address, Mapping) or not 1 <= len(trigger.channel_address) <= 16:
                raise ValueError("channel address is invalid")
            channel_address = {}
            for key, value in trigger.channel_address.items():
                if not isinstance(key, str) or not key.strip() or key != key.strip():
                    raise ValueError("channel address key is invalid")
                channel_address[key] = _text(value, "channel address")
            channel_address_json = _json(channel_address)
            if len(channel_address_json.encode("utf-8")) > 16_384:
                raise ValueError("channel address is too large")
        fingerprint_value = {
            "event_kind": event_kind, "actor": actor, "occurred_at": occurred_at,
            "addressed": trigger.addressed, "threads": thread_refs,
            "primary_thread_id": primary_external_thread_id, "body_hash": body_hash,
            "meta_hash": meta_hash, "source_revision": expected_source_revision,
            "adapter_version": adapter_version, "verification_state": trigger.verification_state,
            "channel_address": channel_address,
            "lifecycle": None if trigger.lifecycle is None else {
                "state": trigger.lifecycle.state, "outcome": trigger.lifecycle.outcome,
                "provider_revision": trigger.lifecycle.provider_revision,
                "observed_at": trigger.lifecycle.observed_at,
            },
        }
        fingerprint = _hash(_json(fingerprint_value))
        trigger_id = "trg_" + _hash(_json([source_instance_id, event_id]))
        thread_ids_by_external = {
            external: "thr_" + _hash(_json([source_instance_id, external])) for external in thread_ids
        }
        with self._store.transaction(write=True) as db:
            source = db.execute(
                "SELECT * FROM source_instances WHERE source_instance_id=?", (source_instance_id,)
            ).fetchone()
            if source is None or source["kind"] != kind:
                raise LookupError("SourceInstance does not match the registered SourceKind")
            if not source["enabled"]:
                raise PermissionError("SourceInstance is disabled")
            if source["config_revision"] != expected_source_revision:
                raise RuntimeError("SourceInstance changed while the event was being verified")
            old = db.execute(
                "SELECT trigger_id,fingerprint FROM triggers WHERE source_instance_id=? AND external_event_id=?",
                (source_instance_id, event_id),
            ).fetchone()
            if old is not None:
                if old["fingerprint"] != fingerprint:
                    raise ValueError("source event identity was reused with different verified facts")
                disposition = db.execute(
                    "SELECT state,fanout_json,context_json FROM trigger_dispositions WHERE trigger_id=?",
                    (old["trigger_id"],),
                ).fetchone()
                return {
                    "state": "duplicate", "trigger_id": old["trigger_id"],
                    "source_instance_id": source_instance_id, "external_event_id": event_id,
                    "disposition": None if disposition is None else disposition["state"],
                    "fanout_ticket_ids": [] if disposition is None else json.loads(disposition["fanout_json"]),
                    "context_ticket_ids": [] if disposition is None else json.loads(disposition["context_json"]),
                }
            now = received_at
            closed_thread_ids = []
            for item in trigger.threads:
                external = item.external_thread_id
                thread_id = thread_ids_by_external[external]
                existing = db.execute(
                    "SELECT * FROM threads WHERE source_instance_id=? AND external_thread_id=?",
                    (source_instance_id, external),
                ).fetchone()
                item_metadata = _json(dict(item.metadata or {}))
                if existing is None:
                    initial_state = "unknown" if kind == "github" else "unsupported"
                    initial_outcome = None
                    if item.relation == "primary" and trigger.lifecycle is not None:
                        initial_state, initial_outcome = trigger.lifecycle.state, trigger.lifecycle.outcome
                    db.execute(
                        """INSERT INTO threads(
                            thread_id,source_instance_id,external_thread_id,metadata_json,source_state,source_outcome,
                            provider_revision,source_observed_at,last_event_at,aggregate_revision,lifecycle_revision,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,1,1,?,?)""",
                        (thread_id, source_instance_id, external, item_metadata, initial_state, initial_outcome,
                         None if trigger.lifecycle is None else trigger.lifecycle.provider_revision,
                         None if trigger.lifecycle is None else _time(trigger.lifecycle.observed_at or now, "observed_at"),
                         occurred_at, now, now),
                    )
                    CoordinatorStore.compare_and_swap(db, "thread", thread_id, 0)
                    CoordinatorStore.append_audit(
                        db, aggregate_type="thread", aggregate_id=thread_id, event_type="thread.created",
                        payload={"source_instance_id": source_instance_id, "external_thread_id": external,
                                 "source_state": initial_state}, actor={"kind": "source", "source_instance_id": source_instance_id},
                    )
                else:
                    if existing["thread_id"] != thread_id:
                        raise RuntimeError("thread identity hash does not match its stored identity")
                    last_event = max(existing["last_event_at"] or occurred_at, occurred_at)
                    update_lifecycle = False
                    lifecycle_fields = {}
                    if item.relation == "primary" and trigger.lifecycle is not None:
                        observed = _time(trigger.lifecycle.observed_at or now, "observed_at")
                        current_order = {
                            "unsupported": (0, 0), "unknown": (1, 0), "open": (2, 0),
                            "closed": (3, 1 if existing["source_outcome"] == "merged" else 0),
                        }[existing["source_state"]]
                        incoming_order = {
                            "unsupported": (0, 0), "unknown": (1, 0), "open": (2, 0),
                            "closed": (3, 1 if trigger.lifecycle.outcome == "merged" else 0),
                        }[trigger.lifecycle.state]
                        if (
                            existing["source_observed_at"] is None
                            or observed > existing["source_observed_at"]
                            or (observed == existing["source_observed_at"] and incoming_order > current_order)
                        ):
                            state = trigger.lifecycle.state
                            outcome = trigger.lifecycle.outcome
                            lifecycle_fields = {
                                "source_state": state, "source_outcome": outcome,
                                "provider_revision": trigger.lifecycle.provider_revision,
                                "source_observed_at": observed,
                            }
                            update_lifecycle = any(existing[key] != value for key, value in lifecycle_fields.items())
                    if not CoordinatorStore.compare_and_swap(
                        db, "thread", thread_id, existing["aggregate_revision"]
                    ):
                        raise RuntimeError("thread revision conflict inside a write transaction")
                    changes = {"last_event_at": last_event, "updated_at": now,
                               "aggregate_revision": existing["aggregate_revision"] + 1}
                    if update_lifecycle:
                        changes.update(lifecycle_fields)
                        changes["lifecycle_revision"] = existing["lifecycle_revision"] + 1
                        if lifecycle_fields["source_state"] == "closed":
                            closed_thread_ids.append(thread_id)
                    db.execute(
                        "UPDATE threads SET " + ",".join(f"{key}=?" for key in changes)
                        + " WHERE thread_id=?",
                        (*changes.values(), thread_id),
                    )
                    if update_lifecycle:
                        CoordinatorStore.append_audit(
                            db, aggregate_type="thread", aggregate_id=thread_id,
                            event_type="thread.lifecycle_observed",
                            payload={"source_state": lifecycle_fields["source_state"],
                                     "source_outcome": lifecycle_fields["source_outcome"],
                                     "provider_revision": lifecycle_fields["provider_revision"]},
                            actor={"kind": "source", "source_instance_id": source_instance_id},
                        )
            for closed_thread_id in closed_thread_ids:
                mandates = db.execute(
                    """SELECT DISTINCT m.* FROM mandates m
                       JOIN triggers origin ON origin.trigger_id=m.trigger_id
                       WHERE origin.primary_thread_id=? AND m.source_instance_id=?""",
                    (closed_thread_id, source_instance_id),
                ).fetchall()
                for mandate in mandates:
                    self._mandate_status(
                        db, mandate, actor={"kind": "source", "source_instance_id": source_instance_id},
                        now=now,
                    )
            primary_thread_id = thread_ids_by_external[primary_external_thread_id]
            db.execute(
                """INSERT INTO triggers(
                    trigger_id,source_instance_id,external_event_id,event_kind,actor_json,occurred_at,received_at,
                    addressed,primary_thread_id,body_json,body_ref,body_hash,metadata_json,meta_ref,meta_hash,
                    source_revision,adapter_version,verification_state,fingerprint
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (trigger_id, source_instance_id, event_id, event_kind, _json(actor), occurred_at, received_at,
                 trigger.addressed, primary_thread_id, body_json, body_ref, body_hash, metadata_json,
                 meta_ref, meta_hash, expected_source_revision, adapter_version,
                 trigger.verification_state, fingerprint),
            )
            for item in trigger.threads:
                db.execute(
                    "INSERT INTO trigger_threads(trigger_id,source_instance_id,thread_id,relation) VALUES(?,?,?,?)",
                    (trigger_id, source_instance_id, thread_ids_by_external[item.external_thread_id], item.relation),
                )
            if channel_address is not None:
                address = channel_address
                address_json = _json(address)
                address_hash = _hash(address_json)
                channel_id = "chn_" + _hash(_json([source_instance_id, primary_thread_id, address_hash]))
                db.execute(
                    """INSERT OR IGNORE INTO channels(
                        channel_id,source_instance_id,thread_id,address_json,address_hash,capabilities_json,created_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (channel_id, source_instance_id, primary_thread_id, address_json, address_hash,
                     _json({"reply": False, "read_context": False}), now),
                )
            db.execute(
                "INSERT INTO trigger_dispositions(trigger_id,state,evaluation_id,decision_id,fanout_json,context_json,updated_at) VALUES(?,?,?,?,?,?,?)",
                (trigger_id, "evaluation.queued", None, None, "[]", "[]", now),
            )
            CoordinatorStore.append_audit(
                db, aggregate_type="trigger", aggregate_id=trigger_id, event_type="trigger.received",
                payload={"source_instance_id": source_instance_id, "external_event_id": event_id,
                         "event_kind": event_kind, "actor": actor, "addressed": trigger.addressed,
                         "source_revision": expected_source_revision, "adapter_version": adapter_version,
                         "body_hash": body_hash, "meta_hash": meta_hash},
                actor={"kind": "source", "source_instance_id": source_instance_id},
            )
            current_policy_row = db.execute(
                """SELECT v.policy_version,v.policy_json FROM policy_current c
                   JOIN policy_versions v ON v.policy_version=c.policy_version WHERE c.singleton=1"""
            ).fetchone()
            followup_gate = None
            followup_fanout_allowed = False
            if current_policy_row is not None:
                followup_policy = json.loads(current_policy_row["policy_json"])
                primary_thread = db.execute(
                    "SELECT source_state FROM threads WHERE thread_id=?",
                    (thread_ids_by_external[primary_external_thread_id],),
                ).fetchone()
                if primary_thread is None:
                    raise RuntimeError("Trigger references a missing primary Thread")
                followup_gate = Gate.evaluate(
                    {
                        "source_instance_id": source_instance_id,
                        "actor": actor,
                        "addressed": trigger.addressed,
                        "metadata": dict(trigger.metadata),
                        "primary_thread_id": thread_ids_by_external[primary_external_thread_id],
                        "source_revision": expected_source_revision,
                    },
                    source,
                    followup_policy,
                    thread_state=primary_thread["source_state"],
                )
                if followup_gate.get("admissible"):
                    followup_fanout_allowed = (
                        trigger.metadata.get('context_event') is True or followup_gate.get("strength") != "inferred" or followup_policy["mode"] == "auto"
                    )
            fanout: dict[str, bool] = {}
            mandate_statuses: dict[str, str] = {}
            if followup_fanout_allowed:
                for thread_id in thread_ids_by_external.values():
                    rows = db.execute(
                        """SELECT s.ticket_id,s.mode,t.state AS ticket_state,t.ended_by,m.*
                           FROM ticket_subscriptions s
                           JOIN tickets t ON t.ticket_id=s.ticket_id
                           JOIN mandates m ON m.ticket_id=t.ticket_id
                           WHERE s.thread_id=? AND s.state='active'""",
                        (thread_id,),
                    ).fetchall()
                    for subscription in rows:
                        ticket_id = subscription["ticket_id"]
                        if subscription["ticket_state"] == "ended" and subscription["ended_by"] == "human":
                            continue
                        status = self._mandate_status(
                            db, subscription, actor={"kind": "host"}, now=now
                        )
                        if status not in {"valid", "affected"}:
                            continue
                        work = (
                            subscription["mode"] == "work"
                            and actor_kind == "human"
                            and trigger.metadata.get('context_event') is not True
                            and thread_id == thread_ids_by_external[primary_external_thread_id]
                        )
                        if subscription["ticket_state"] == "ended" and not work:
                            continue
                        fanout[ticket_id] = fanout.get(ticket_id, False) or work
                        mandate_statuses[ticket_id] = status
            fanout_ids = sorted(ticket for ticket, work in fanout.items() if work)
            context_ids = sorted(ticket for ticket, work in fanout.items() if not work)
            for ticket_id, work in fanout.items():
                ticket = db.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
                if ticket is None:
                    continue
                if ticket["state"] == "ended":
                    status = mandate_statuses.get(ticket_id, "revoked")
                    if not work or status not in {"valid", "affected"}:
                        continue
                    self._ticket_update(
                        db, ticket, {"state": "open", "outcome": None, "ended_by": None, "ended_at": None},
                        event_type="ticket.resumed",
                        payload={"trigger_id": trigger_id, "reason": "human_followup", "mandate_status": status},
                        actor={"kind": "source", "source_instance_id": source_instance_id},
                    )
                input_id = "input_" + _hash(_json([ticket_id, trigger_id]))
                db.execute(
                    """INSERT OR IGNORE INTO ticket_inputs(
                        input_id,ticket_id,trigger_id,kind,context_only,state,created_at
                    ) VALUES(?,?,?,'trigger',?,'waiting',?)""",
                    (input_id, ticket_id, trigger_id, int(not work), now),
                )
                CoordinatorStore.append_audit(
                    db, aggregate_type="ticket", aggregate_id=ticket_id, event_type="ticket.input_received",
                    payload={"trigger_id": trigger_id, "context_only": not work}, actor={"kind": "source", "source_instance_id": source_instance_id},
                )
            has_work_owner = any(fanout.values())
            context_event = trigger.metadata.get('context_event') is True
            disposition_state = ('ticket.context_only' if context_ids else 'filtered') if context_event else "ticket.fanout" if has_work_owner else "evaluation.queued"
            self._append_disposition(
                db, trigger_id=trigger_id, state=disposition_state,
                event_type=(
                    disposition_state if context_event else "ticket.fanout" if has_work_owner else "ticket.context_only" if context_ids else "evaluation.queued"
                ),
                payload={
                    "fanout_ticket_ids": fanout_ids,
                    "context_ticket_ids": context_ids,
                    "evaluation_queued": not has_work_owner and not context_event,
                    "gate": followup_gate,
                    "policy_version": None if current_policy_row is None else current_policy_row["policy_version"],
                },
                actor={"kind": "source", "source_instance_id": source_instance_id},
                fanout=fanout_ids, context=context_ids,
            )
            return {
                "state": "stored", "trigger_id": trigger_id,
                "source_instance_id": source_instance_id, "external_event_id": event_id,
                "thread_ids": list(thread_ids_by_external.values()),
                "disposition": disposition_state,
                "fanout_ticket_ids": fanout_ids, "context_ticket_ids": context_ids,
                "gate": followup_gate,
                "policy_version": None if current_policy_row is None else current_policy_row["policy_version"],
            }

    def begin_evaluation(self, thread_id: str, *, actor: object, now: str | None = None) -> dict[str, Any]:
        thread_id = _text(thread_id, "thread_id")
        started_at = _now() if now is None else _time(now, "now")
        with self._store.transaction(write=True) as db:
            thread = db.execute("SELECT * FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
            if thread is None:
                raise LookupError("thread does not exist")
            active = db.execute(
                "SELECT * FROM evaluations WHERE thread_id=? AND state='running'", (thread_id,)
            ).fetchone()
            if active is not None:
                stale_reason = None
                if started_at > active["deadline_at"]:
                    stale_reason = "evaluation_deadline_exceeded"
                else:
                    current_policy = db.execute(
                        "SELECT policy_version FROM policy_current WHERE singleton=1",
                    ).fetchone()
                    if current_policy is None or current_policy["policy_version"] != active["policy_version"]:
                        stale_reason = "policy_changed"
                    elif thread["lifecycle_revision"] != active["thread_lifecycle_revision"]:
                        stale_reason = "thread_lifecycle_changed"
                    else:
                        changed_source = db.execute(
                            """SELECT 1 FROM evaluation_triggers et
                               JOIN triggers t ON t.trigger_id=et.trigger_id
                               JOIN source_instances s ON s.source_instance_id=t.source_instance_id
                               WHERE et.evaluation_id=? AND s.config_revision<>t.source_revision LIMIT 1""",
                            (active["evaluation_id"],),
                        ).fetchone()
                        if changed_source is not None:
                            stale_reason = "source_config_changed"
                if stale_reason is not None:
                    self._stale_evaluation(
                        db, active, reason=stale_reason, actor=actor, now=started_at,
                    )
                else:
                    return self._candidate(db, active)
            policy_row = db.execute(
                """SELECT v.* FROM policy_current c JOIN policy_versions v ON v.policy_version=c.policy_version
                   WHERE c.singleton=1"""
            ).fetchone()
            if policy_row is None:
                return {"state": "blocked", "reason": "policy_unconfigured", "thread_id": thread_id}
            policy = json.loads(policy_row["policy_json"])
            trigger_rows = db.execute(
                """SELECT t.* FROM triggers t JOIN trigger_dispositions d ON d.trigger_id=t.trigger_id
                   WHERE t.primary_thread_id=? AND d.state='evaluation.queued'
                   ORDER BY t.occurred_at,t.received_at,t.trigger_id LIMIT ?""",
                (thread_id, _MAX_EVAL_TRIGGERS),
            ).fetchall()
            if not trigger_rows:
                return {"state": "empty", "thread_id": thread_id}
            unavailable = db.execute("SELECT * FROM evaluations WHERE thread_id=? AND state='unavailable' ORDER BY started_at DESC,evaluation_id DESC LIMIT 1", (thread_id,)).fetchone()
            if (unavailable is not None and unavailable['retry_count'] == 0
                    and unavailable['policy_version'] == policy_row['policy_version']
                    and unavailable['thread_lifecycle_revision'] == thread['lifecycle_revision']):
                original_ids = {row[0] for row in db.execute("SELECT trigger_id FROM evaluation_triggers WHERE evaluation_id=?", (unavailable['evaluation_id'],))}
                unchanged_sources = all(db.execute("SELECT config_revision FROM source_instances WHERE source_instance_id=?", (row['source_instance_id'],)).fetchone()[0] == row['source_revision'] for row in trigger_rows)
                if original_ids == {row['trigger_id'] for row in trigger_rows} and unchanged_sources:
                    return {'state': 'unavailable', 'evaluation_id': unavailable['evaluation_id'], 'thread_id': thread_id}
            gate_results = []
            admissible = []
            for row in trigger_rows:
                trigger = self._trigger_view(row)
                source = db.execute(
                    "SELECT * FROM source_instances WHERE source_instance_id=?",
                    (row["source_instance_id"],),
                ).fetchone()
                if source is not None and source["config_revision"] != row["source_revision"]:
                    gate = {
                        "admissible": False,
                        "reason": "source_config_changed",
                        "source_revision": row["source_revision"],
                        "current_source_revision": source["config_revision"],
                    }
                else:
                    gate = Gate.evaluate(
                        trigger, source or {"enabled": False}, policy,
                        thread_state=thread["source_state"],
                    )
                gate_results.append((row, gate))
                if gate.get("admissible"):
                    admissible.append((row, gate, trigger))
            selected = None
            if admissible:
                selected = sorted(
                    admissible,
                    key=lambda item: (_STRENGTH[item[1]["strength"]], item[0]["occurred_at"], item[0]["trigger_id"]),
                    reverse=True,
                )[0]
            evaluation_id = _identity("eval")
            trigger_ids = [row["trigger_id"] for row in trigger_rows]
            batch_hash = _hash(_json([
                {"trigger_id": row["trigger_id"], "fingerprint": row["fingerprint"]} for row in trigger_rows
            ]))
            if selected is None:
                primary = trigger_rows[0]
                actor_json = primary["actor_json"]
                basis_json = "{}"
                strength = "inferred"
                db.execute(
                    """INSERT INTO evaluations(
                        evaluation_id,thread_id,policy_version,state,trigger_batch_hash,primary_trigger_id,
                        strength,actor_json,mandate_basis_json,thread_lifecycle_revision,started_at,deadline_at,
                        completed_at,retry_count,outcome_json
                    ) VALUES(?,?,?,'decided',?,?,?,?,?,?,?, ?,?,0,?)""",
                    (evaluation_id, thread_id, policy_row["policy_version"], batch_hash,
                     primary["trigger_id"], strength, actor_json, basis_json,
                     thread["lifecycle_revision"], started_at, started_at, started_at,
                     _json({"reason": "gate_denied"})),
                )
                for ordinal, (row, gate) in enumerate(gate_results):
                    db.execute(
                        "INSERT INTO evaluation_triggers(evaluation_id,ordinal,trigger_id,gate_json) VALUES(?,?,?,?)",
                        (evaluation_id, ordinal, row["trigger_id"], _json(gate)),
                    )
                decision_id = _identity("decision")
                reasons = [{"trigger_id": row["trigger_id"], "reason": gate.get("reason", "not_admissible")}
                           for row, gate in gate_results]
                db.execute(
                    """INSERT INTO decisions(decision_id,evaluation_id,thread_id,policy_version,kind,reason_json,
                       audit_json,ticket_id,created_at) VALUES(?,?,?,?, 'ignore',?,?,NULL,?)""",
                    (decision_id, evaluation_id, thread_id, policy_row["policy_version"],
                     _json(reasons), _json({"gate_denied": reasons}), started_at),
                )
                for row, gate in gate_results:
                    self._append_disposition(
                        db, trigger_id=row["trigger_id"], state="filtered",
                        event_type="filtered",
                        payload={"evaluation_id": evaluation_id, "decision_id": decision_id,
                                 "reason": gate.get("reason", "gate_denied")},
                        actor=actor, evaluation_id=evaluation_id, decision_id=decision_id,
                    )
                CoordinatorStore.append_audit(
                    db, aggregate_type="evaluation", aggregate_id=evaluation_id,
                    event_type="evaluation.gate_denied",
                    payload={"thread_id": thread_id, "trigger_ids": trigger_ids, "reasons": reasons},
                    actor=actor,
                )
                return {"state": "decided", "evaluation_id": evaluation_id, "decision_id": decision_id, "kind": "ignore"}
            selected_row, selected_gate, selected_trigger = selected
            deadline = (
                datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                + timedelta(seconds=policy["timeout_seconds"])
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            db.execute(
                """INSERT INTO evaluations(
                    evaluation_id,thread_id,policy_version,state,trigger_batch_hash,primary_trigger_id,
                    strength,actor_json,mandate_basis_json,thread_lifecycle_revision,started_at,deadline_at,
                    completed_at,retry_count,outcome_json
                ) VALUES(?,?,?,'running',?,?,?,?,?,?,?,?,NULL,0,NULL)""",
                (evaluation_id, thread_id, policy_row["policy_version"], batch_hash,
                 selected_row["trigger_id"], selected_gate["strength"], selected_row["actor_json"],
                 _json(selected_gate["basis"]), thread["lifecycle_revision"], started_at, deadline),
            )
            for ordinal, (row, gate) in enumerate(gate_results):
                db.execute(
                    "INSERT INTO evaluation_triggers(evaluation_id,ordinal,trigger_id,gate_json) VALUES(?,?,?,?)",
                    (evaluation_id, ordinal, row["trigger_id"], _json(gate)),
                )
                self._append_disposition(
                    db, trigger_id=row["trigger_id"], state="evaluation.started",
                    event_type="evaluation.started",
                    payload={"evaluation_id": evaluation_id, "policy_version": policy_row["policy_version"],
                             "gate": gate},
                    actor=actor, evaluation_id=evaluation_id,
                )
            CoordinatorStore.append_audit(
                db, aggregate_type="evaluation", aggregate_id=evaluation_id,
                event_type="evaluation.started",
                payload={"thread_id": thread_id, "trigger_ids": trigger_ids,
                         "primary_trigger_id": selected_row["trigger_id"],
                         "strength": selected_gate["strength"], "policy_version": policy_row["policy_version"],
                         "trigger_batch_hash": batch_hash},
                actor=actor,
            )
            evaluation = db.execute("SELECT * FROM evaluations WHERE evaluation_id=?", (evaluation_id,)).fetchone()
            return self._candidate(db, evaluation)

    def _candidate(self, db, evaluation) -> dict[str, Any]:
        policy = db.execute(
            "SELECT policy_json FROM policy_versions WHERE policy_version=?", (evaluation["policy_version"],)
        ).fetchone()
        rows = db.execute(
            """SELECT t.*,et.gate_json,et.ordinal FROM evaluation_triggers et
               JOIN triggers t ON t.trigger_id=et.trigger_id
               WHERE et.evaluation_id=? ORDER BY et.ordinal""",
            (evaluation["evaluation_id"],),
        ).fetchall()
        gate_results = [
            {
                "trigger_id": row["trigger_id"],
                "ordinal": row["ordinal"],
                "gate": json.loads(row["gate_json"]),
            }
            for row in rows
        ]
        return {
            "state": evaluation["state"], "evaluation_id": evaluation["evaluation_id"],
            "thread_id": evaluation["thread_id"], "policy_version": evaluation["policy_version"],
            "primary_trigger_id": evaluation["primary_trigger_id"], "strength": evaluation["strength"],
            "actor": json.loads(evaluation["actor_json"]),
            "mandate_basis": json.loads(evaluation["mandate_basis_json"]),
            "deadline_at": evaluation["deadline_at"],
            "policy": json.loads(policy["policy_json"]),
            "triggers": [
                {**self._trigger_view(row), "gate": json.loads(row["gate_json"])}
                for row in rows if json.loads(row["gate_json"]).get("admissible")
            ],
            "gate_results": gate_results,
        }

    @staticmethod
    def _proposal(value: object, policy: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"kind", "reason", "rule_assessments", "direction", "title", "scope",
                   "intent_evidence", "ticket_id"}
        if not isinstance(value, Mapping) or set(value) - allowed or not {"kind", "reason"} <= set(value):
            raise ValueError("assessment proposal has an invalid shape")
        kind = value["kind"]
        if kind not in {"accept", "ignore", "unavailable", "reopen", "stop"}:
            raise ValueError("assessment decision kind is invalid")
        proposal = dict(value)
        proposal["reason"] = _bounded_text(proposal["reason"], "decision.reason", limit=4000)
        if "title" in proposal:
            proposal["title"] = _bounded_text(proposal["title"], "ticket.title", limit=512)
        if "ticket_id" in proposal:
            proposal["ticket_id"] = _bounded_text(proposal["ticket_id"], "ticket_id", limit=128)
        if "intent_evidence" in proposal:
            proposal["intent_evidence"] = _bounded_text(
                proposal["intent_evidence"], "intent_evidence", limit=4000,
            )
        if "direction" in proposal:
            direction = proposal["direction"]
            if not isinstance(direction, Mapping) or set(direction) != {
                "implementation_requested", "target_basis", "requires_design_decision", "evidence",
            }:
                raise ValueError("direction assessment has an invalid shape")
            proposal["direction"] = {
                "implementation_requested": direction["implementation_requested"],
                "target_basis": direction["target_basis"],
                "requires_design_decision": direction["requires_design_decision"],
                "evidence": _bounded_text(direction["evidence"], "direction.evidence", limit=4000),
            }
        if "scope" in proposal:
            proposal["scope"] = _validate_scope(proposal["scope"], name="requested_scope")
        enabled = {rule["id"] for rule in policy["rules"] if rule["enabled"]}
        assessments = proposal.get("rule_assessments", [])
        if not isinstance(assessments, list) or len(assessments) > _MAX_POLICY_RULES:
            raise ValueError("rule_assessments is invalid")
        if kind == "unavailable" and not assessments:
            return {"kind": kind, "reason": proposal["reason"], "rule_assessments": []}
        parsed = {}
        for item in assessments:
            if not isinstance(item, Mapping) or set(item) != {"rule_id", "matched", "reason"}:
                raise ValueError("rule assessment has an invalid shape")
            rule_id = _bounded_text(item["rule_id"], "rule_id", limit=80)
            if rule_id in parsed or rule_id not in enabled or type(item["matched"]) is not bool:
                raise ValueError("rule assessment IDs or matched values are invalid")
            parsed[rule_id] = {
                "rule_id": rule_id, "matched": item["matched"],
                "reason": _bounded_text(item["reason"], "rule reason", limit=2000),
            }
        if set(parsed) != enabled:
            raise ValueError("exactly one assessment for every enabled rule is required")
        proposal["rule_assessments"] = [parsed[key] for key in sorted(parsed)]
        if kind == "unavailable":
            return {"kind": kind, "reason": proposal["reason"],
                    "rule_assessments": proposal["rule_assessments"]}
        return proposal

    def _stale_evaluation(self, db, evaluation, *, reason: str, actor: object, now: str) -> dict[str, Any]:
        db.execute(
            "UPDATE evaluations SET state='cancelled',completed_at=?,outcome_json=? WHERE evaluation_id=? AND state='running'",
            (now, _json({"reason": reason}), evaluation["evaluation_id"]),
        )
        rows = db.execute(
            "SELECT trigger_id FROM evaluation_triggers WHERE evaluation_id=? ORDER BY ordinal",
            (evaluation["evaluation_id"],),
        ).fetchall()
        for row in rows:
            self._append_disposition(
                db, trigger_id=row["trigger_id"], state="evaluation.queued",
                event_type="evaluation.requeued",
                payload={"evaluation_id": evaluation["evaluation_id"], "reason": reason},
                actor=actor,
            )
        CoordinatorStore.append_audit(
            db, aggregate_type="evaluation", aggregate_id=evaluation["evaluation_id"],
            event_type="evaluation.cancelled", payload={"reason": reason}, actor=actor,
        )
        return {"state": "stale", "evaluation_id": evaluation["evaluation_id"], "reason": reason}

    def complete_evaluation(
        self, evaluation_id: str, proposal: object, *, actor: object,
        now: str | None = None,
    ) -> dict[str, Any]:
        evaluation_id = _text(evaluation_id, "evaluation_id")
        completed_at = _now() if now is None else _time(now, "now")
        with self._store.transaction(write=True) as db:
            evaluation = db.execute("SELECT * FROM evaluations WHERE evaluation_id=?", (evaluation_id,)).fetchone()
            if evaluation is None:
                raise LookupError("evaluation does not exist")
            if evaluation["state"] != "running":
                raise RuntimeError("evaluation is no longer running")
            if completed_at > evaluation["deadline_at"]:
                return self._stale_evaluation(
                    db, evaluation, reason="evaluation_deadline_exceeded", actor=actor, now=completed_at,
                )
            current = db.execute(
                "SELECT policy_version FROM policy_current WHERE singleton=1"
            ).fetchone()
            if current is None or current["policy_version"] != evaluation["policy_version"]:
                return self._stale_evaluation(db, evaluation, reason="policy_changed", actor=actor, now=completed_at)
            thread = db.execute("SELECT * FROM threads WHERE thread_id=?", (evaluation["thread_id"],)).fetchone()
            if thread is None or thread["lifecycle_revision"] != evaluation["thread_lifecycle_revision"]:
                return self._stale_evaluation(db, evaluation, reason="thread_lifecycle_changed", actor=actor, now=completed_at)
            rows = db.execute(
                """SELECT t.*,et.gate_json FROM evaluation_triggers et JOIN triggers t ON t.trigger_id=et.trigger_id
                   WHERE et.evaluation_id=? ORDER BY et.ordinal""", (evaluation_id,)
            ).fetchall()
            if any(
                db.execute("SELECT config_revision FROM source_instances WHERE source_instance_id=?",
                           (row["source_instance_id"],)).fetchone()[0] != row["source_revision"]
                for row in rows
            ):
                return self._stale_evaluation(db, evaluation, reason="source_config_changed", actor=actor, now=completed_at)
            policy_row = db.execute("SELECT policy_json FROM policy_versions WHERE policy_version=?",
                                    (evaluation["policy_version"],)).fetchone()
            policy = json.loads(policy_row["policy_json"])
            parsed = self._proposal(proposal, policy)
            audit = {
                "strength": evaluation["strength"], "policy_version": evaluation["policy_version"],
                "rule_assessments": parsed["rule_assessments"],
                "direction": parsed.get("direction"), "intent_evidence": parsed.get("intent_evidence"),
                "reason": parsed["reason"],
            }
            kind = parsed["kind"]
            final_kind = kind
            ticket_id = None
            mandate_id = None
            selected = next(row for row in rows if row["trigger_id"] == evaluation["primary_trigger_id"])
            selected_trigger = self._trigger_view(selected)
            enabled_rules = {rule["id"] for rule in policy["rules"] if rule["enabled"]}
            matched = [item for item in parsed["rule_assessments"] if item["matched"]]
            direction = parsed.get("direction")
            direction_allowed = (
                isinstance(direction, Mapping)
                and set(direction) == {"implementation_requested", "target_basis", "requires_design_decision", "evidence"}
                and direction["implementation_requested"] is True
                and direction["target_basis"] in {"specified", "existing-standard"}
                and direction["requires_design_decision"] is False
                and isinstance(direction["evidence"], str) and bool(direction["evidence"].strip())
            )
            if kind == "accept":
                if not matched or not direction_allowed:
                    final_kind = "ignore"
                    audit["rejected_accept"] = "direction_unsettled" if not direction_allowed else "no_rule_matched"
                elif not isinstance(parsed.get("intent_evidence"), str) or not parsed["intent_evidence"].strip():
                    raise ValueError("acceptance requires non-empty intent_evidence")
                elif not any(
                    parsed["direction"]["evidence"] in source_text
                    and parsed["intent_evidence"] in source_text
                    for row in rows if json.loads(row["gate_json"]).get("admissible")
                    for source_text in (
                        json.loads(row["body_json"]).get("text", ""),
                        json.loads(row["body_json"]).get("title", ""),
                    )
                    if isinstance(source_text, str) and source_text
                ):
                    final_kind = "ignore"
                    audit["rejected_accept"] = "evidence_not_grounded"
                elif evaluation["strength"] == "inferred" and policy["mode"] != "auto":
                    final_kind = "ignore"
                    audit["would_accept"] = True
                    audit["observed_proposal"] = {
                        "title": parsed.get("title"), "reason": parsed["reason"],
                    }
                else:
                    title = _text(parsed.get("title"), "ticket.title")
                    if len(parsed["intent_evidence"].encode("utf-8")) > 4000:
                        raise ValueError("intent_evidence is too large")
                    parsed["intent_evidence"] = _text(parsed["intent_evidence"], "intent_evidence")
                    allowed_scope = self._source_policy_scope(
                        policy, selected["source_instance_id"], evaluation["thread_id"]
                    )
                    requested_scope = allowed_scope if parsed.get("scope") is None else _validate_scope(parsed["scope"], name="requested_scope")
                    if not _scope_subset(requested_scope, allowed_scope):
                        raise PermissionError("assessment scope exceeds the authorized policy scope")
                    if selected["source_instance_id"] not in requested_scope["source_instance_ids"]:
                        raise PermissionError("Mandate scope must include the originating SourceInstance")
                    if evaluation["thread_id"] not in requested_scope["thread_ids"]:
                        raise PermissionError("Mandate scope must include the originating Thread")
                    ticket_id, mandate_id = _identity("ticket"), _identity("mandate")
                    if not CoordinatorStore.compare_and_swap(db, "ticket", ticket_id, 0):
                        raise RuntimeError("new ticket identity unexpectedly exists")
                    db.execute(
                        """INSERT INTO tickets(
                            ticket_id,mandate_id,origin_trigger_id,title,state,outcome,ended_by,ended_at,
                            created_at,updated_at,aggregate_revision
                        ) VALUES(?,?,?,?, 'open',NULL,NULL,NULL,?,?,1)""",
                        (ticket_id, mandate_id, selected["trigger_id"], title, completed_at, completed_at),
                    )
                    decision_id = _identity("decision")
                    db.execute(
                        """INSERT INTO decisions(decision_id,evaluation_id,thread_id,policy_version,kind,
                           reason_json,audit_json,ticket_id,created_at) VALUES(?,?,?,?, 'accept',?,?,?,?)""",
                        (decision_id, evaluation_id, evaluation["thread_id"], evaluation["policy_version"],
                         _json([parsed["reason"]]), _json(audit), ticket_id, completed_at),
                    )
                    db.execute(
                        """INSERT INTO mandates(
                            mandate_id,decision_id,ticket_id,strength,policy_version,trigger_id,source_instance_id,
                            actor_json,basis_json,scope_json,granted_at,expires_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                        (mandate_id, decision_id, ticket_id, evaluation["strength"], evaluation["policy_version"],
                         selected["trigger_id"], selected["source_instance_id"], evaluation["actor_json"],
                         evaluation["mandate_basis_json"], _json(requested_scope), completed_at),
                    )
                    CoordinatorStore.append_audit(
                        db, aggregate_type="mandate", aggregate_id=mandate_id, event_type="mandate.granted",
                        payload={"ticket_id": ticket_id, "decision_id": decision_id,
                                 "policy_version": evaluation["policy_version"],
                                 "strength": evaluation["strength"], "scope": requested_scope},
                        actor=actor,
                    )
                    trigger_ids = [row["trigger_id"] for row in rows]
                    all_threads = {}
                    for row in rows:
                        if not json.loads(row["gate_json"]).get("admissible"):
                            continue
                        links = db.execute(
                            "SELECT thread_id,relation FROM trigger_threads WHERE trigger_id=? ORDER BY relation,thread_id",
                            (row["trigger_id"],),
                        ).fetchall()
                        for link in links:
                            all_threads[link["thread_id"]] = (
                                "work" if link["thread_id"] == evaluation["thread_id"] else "context"
                            )
                    for linked_thread_id, mode in all_threads.items():
                        thread_row = db.execute(
                            "SELECT source_instance_id FROM threads WHERE thread_id=?", (linked_thread_id,)
                        ).fetchone()
                        if thread_row is None:
                            raise RuntimeError("Trigger references a missing Thread")
                        source_id = thread_row["source_instance_id"]
                        if (
                            linked_thread_id not in requested_scope["thread_ids"]
                            or source_id not in requested_scope["source_instance_ids"]
                        ):
                            continue
                        db.execute(
                            """INSERT OR IGNORE INTO ticket_subscriptions(
                                ticket_id,thread_id,source_instance_id,mode,owns_thread,state,created_at,updated_at
                            ) VALUES(?,?,?,?,0,'active',?,?)""",
                            (ticket_id, linked_thread_id, source_id, mode, completed_at, completed_at),
                        )
                    for row in rows:
                        gate = json.loads(row["gate_json"])
                        if not gate.get("admissible"):
                            self._append_disposition(
                                db, trigger_id=row["trigger_id"], state="filtered",
                                event_type="filtered",
                                payload={"evaluation_id": evaluation_id, "decision_id": decision_id,
                                         "reason": gate.get("reason", "not_admissible")},
                                actor=actor, evaluation_id=evaluation_id, decision_id=decision_id,
                            )
                            continue
                        trigger_view = self._trigger_view(row)
                        work = row["primary_thread_id"] == evaluation["thread_id"] and trigger_view["actor"]["actor_kind"] == "human"
                        input_id = "input_" + _hash(_json([ticket_id, row["trigger_id"]]))
                        db.execute(
                            """INSERT OR IGNORE INTO ticket_inputs(
                                input_id,ticket_id,trigger_id,kind,context_only,state,created_at
                            ) VALUES(?,?,?,'trigger',?,'waiting',?)""",
                            (input_id, ticket_id, row["trigger_id"], int(not work), completed_at),
                        )
                        self._append_disposition(
                            db, trigger_id=row["trigger_id"],
                            state="ticket.fanout" if work else "ticket.context_only",
                            event_type="ticket.created",
                            payload={"ticket_id": ticket_id, "mandate_id": mandate_id,
                                     "decision_id": decision_id, "context_only": not work},
                            actor=actor, evaluation_id=evaluation_id, decision_id=decision_id,
                            fanout=[ticket_id] if work else [],
                            context=[ticket_id] if not work else [],
                        )
                    db.execute(
                        "UPDATE evaluations SET state='decided',completed_at=?,outcome_json=? WHERE evaluation_id=?",
                        (completed_at, _json({"kind": "accept", "decision_id": decision_id,
                                             "ticket_id": ticket_id, "mandate_id": mandate_id}), evaluation_id),
                    )
                    return {
                        "state": "decided", "kind": "accept", "decision_id": decision_id,
                        "ticket_id": ticket_id, "mandate_id": mandate_id,
                    }
            if kind in {"stop", "reopen"}:
                decision_id = _identity("decision")
                target_id = _text(parsed.get("ticket_id"), "ticket_id")
                target = db.execute("SELECT * FROM tickets WHERE ticket_id=?", (target_id,)).fetchone()
                if target is None:
                    raise LookupError("target ticket does not exist")
                mandate = db.execute("SELECT * FROM mandates WHERE ticket_id=?", (target_id,)).fetchone()
                if mandate is None or self._mandate_status(db, mandate, actor=actor, now=completed_at) not in {"valid", "affected"}:
                    raise PermissionError("target ticket mandate is not valid")
                subscription = db.execute(
                    "SELECT mode FROM ticket_subscriptions WHERE ticket_id=? AND thread_id=? AND state='active'",
                    (target_id, evaluation["thread_id"]),
                ).fetchone()
                input_row = db.execute(
                    "SELECT * FROM ticket_inputs WHERE ticket_id=? AND trigger_id=?",
                    (target_id, evaluation["primary_trigger_id"]),
                ).fetchone()
                if subscription is None or subscription["mode"] != "work" or input_row is None:
                    raise PermissionError("decision is not bound to a work follow-up of the target ticket")
                if kind == "stop":
                    if evaluation["strength"] != "explicit" or selected_trigger["actor"]["actor_kind"] != "human":
                        raise PermissionError("stopping a ticket requires an explicit human request")
                    if input_row["state"] != "handed_back":
                        raise PermissionError("a work follow-up must be handed back before the main assessor may stop it")
                    if target["state"] != "open":
                        raise PermissionError("only an open ticket can be stopped")
                    self._ticket_update(
                        db, target, {"state": "ended", "outcome": "stopped", "ended_by": "human", "ended_at": completed_at},
                        event_type="ticket.stopped", payload={"trigger_id": evaluation["primary_trigger_id"],
                        "decision_reason": parsed["reason"]}, actor=actor,
                    )
                    self._append_mandate_event(
                        db, mandate_id=mandate["mandate_id"], event_type="revoked",
                        payload={"reason": "human_stop", "decision_id": decision_id}, actor=actor,
                    )
                    ticket_id = target_id
                else:
                    if input_row["context_only"] or selected_trigger["actor"]["actor_kind"] != "human":
                        raise PermissionError("context-only or non-human follow-up cannot reopen a ticket")
                    if target["state"] != "ended" or target["ended_by"] == "human":
                        raise PermissionError("only a non-human-ended ticket may be reopened")
                    self._ticket_update(
                        db, target, {"state": "open", "outcome": None, "ended_by": None, "ended_at": None},
                        event_type="ticket.reopened",
                        payload={"trigger_id": evaluation["primary_trigger_id"], "decision_reason": parsed["reason"]},
                        actor=actor,
                    )
                    db.execute(
                        "UPDATE ticket_inputs SET state='waiting',handback_reason=NULL WHERE input_id=?",
                        (input_row["input_id"],),
                    )
                    ticket_id = target_id
                db.execute(
                    """INSERT INTO decisions(decision_id,evaluation_id,thread_id,policy_version,kind,
                       reason_json,audit_json,ticket_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (decision_id, evaluation_id, evaluation["thread_id"], evaluation["policy_version"],
                     kind, _json([parsed["reason"]]), _json(audit), ticket_id, completed_at),
                )
                final_kind = kind
            else:
                decision_id = _identity("decision")
                db.execute(
                    """INSERT INTO decisions(decision_id,evaluation_id,thread_id,policy_version,kind,
                       reason_json,audit_json,ticket_id,created_at) VALUES(?,?,?,?,?,?,?,NULL,?)""",
                    (decision_id, evaluation_id, evaluation["thread_id"], evaluation["policy_version"],
                     final_kind, _json([parsed["reason"]]), _json(audit), completed_at),
                )
            state = "unavailable" if final_kind == "unavailable" else "decided"
            db.execute(
                "UPDATE evaluations SET state=?,completed_at=?,outcome_json=? WHERE evaluation_id=?",
                (state, completed_at, _json({"kind": final_kind, "decision_id": decision_id}), evaluation_id),
            )
            for row in rows:
                gate = json.loads(row["gate_json"])
                if not gate.get("admissible"):
                    self._append_disposition(
                        db, trigger_id=row["trigger_id"], state="filtered",
                        event_type="filtered",
                        payload={"evaluation_id": evaluation_id, "decision_id": decision_id,
                                 "reason": gate.get("reason", "not_admissible")},
                        actor=actor, evaluation_id=evaluation_id, decision_id=decision_id,
                    )
                    continue
                self._append_disposition(
                    db, trigger_id=row["trigger_id"],
                    state="evaluation.queued" if final_kind == "unavailable" else "decision.recorded",
                    event_type="decision.recorded",
                    payload={"evaluation_id": evaluation_id, "decision_id": decision_id,
                             "kind": final_kind, "reason": parsed["reason"]},
                    actor=actor, evaluation_id=evaluation_id,
                    decision_id=None if final_kind == "unavailable" else decision_id,
                )
            CoordinatorStore.append_audit(
                db, aggregate_type="evaluation", aggregate_id=evaluation_id,
                event_type=f"evaluation.{final_kind}",
                payload={"decision_id": decision_id, "kind": final_kind, "audit": audit,
                         "ticket_id": ticket_id},
                actor=actor,
            )
            return {"state": state, "kind": final_kind, "decision_id": decision_id, "ticket_id": ticket_id}

    def assess_next(
        self, thread_id: str, assessor: object | None, *, actor: object,
        assessor_actor: object | None = None, now: str | None = None,
    ) -> dict[str, Any]:
        """冻结候选后调用注入的 Assessor；模型异常只形成 unavailable，不产生授权。"""
        candidate = self.begin_evaluation(thread_id, actor=actor, now=now)
        if candidate.get("state") != "running":
            return candidate
        evaluation_id = candidate["evaluation_id"]
        audit_actor = {"kind": "assessor"} if assessor_actor is None else assessor_actor
        if assessor is None:
            return self.complete_evaluation(
                evaluation_id, {"kind": "unavailable", "reason": "assessor_unavailable"},
                actor=audit_actor, now=now,
            )
        try:
            method = getattr(assessor, "assess", None)
            proposal = method(candidate) if callable(method) else assessor(candidate) if callable(assessor) else None
            if proposal is None:
                raise TypeError("Assessor must return a DecisionProposal")
        except Exception:
            return self.complete_evaluation(
                evaluation_id, {"kind": "unavailable", "reason": "assessor_failed"},
                actor=audit_actor, now=now,
            )
        try:
            return self.complete_evaluation(evaluation_id, proposal, actor=audit_actor, now=now)
        except (ValueError, TypeError, PermissionError, LookupError):
            return self.complete_evaluation(
                evaluation_id, {"kind": "unavailable", "reason": "assessor_invalid_proposal"},
                actor=audit_actor, now=now,
            )

    async def assess_next_async(
        self, thread_id: str, assessor: object | None, *, actor: object,
        assessor_actor: object | None = None, now: str | None = None,
    ) -> dict[str, Any]:
        """冻结候选后等待原生异步 Assessor；异常和无效提案只形成 unavailable。"""
        candidate = self.begin_evaluation(thread_id, actor=actor, now=now)
        if candidate.get("state") != "running":
            return candidate
        evaluation_id = candidate["evaluation_id"]
        audit_actor = {"kind": "assessor"} if assessor_actor is None else assessor_actor
        if assessor is None:
            return self.complete_evaluation(
                evaluation_id, {"kind": "unavailable", "reason": "assessor_unavailable"},
                actor=audit_actor, now=now,
            )
        try:
            method = getattr(assessor, "assess", None)
            proposal = method(candidate) if callable(method) else assessor(candidate) if callable(assessor) else None
            if inspect.isawaitable(proposal):
                proposal = await proposal
            if proposal is None:
                raise TypeError("Assessor must return a DecisionProposal")
        except Exception:
            return self.complete_evaluation(
                evaluation_id, {"kind": "unavailable", "reason": "assessor_failed"},
                actor=audit_actor, now=now,
            )
        try:
            return self.complete_evaluation(evaluation_id, proposal, actor=audit_actor, now=now)
        except (ValueError, TypeError, PermissionError, LookupError):
            return self.complete_evaluation(
                evaluation_id, {"kind": "unavailable", "reason": "assessor_invalid_proposal"},
                actor=audit_actor, now=now,
            )

    def requeue_unavailable(self, evaluation_id: str, *, actor: object) -> bool:
        with self._store.transaction(write=True) as db:
            return self.requeue_unavailable_in_transaction(db, evaluation_id, actor=actor)

    def requeue_unavailable_in_transaction(self, db, evaluation_id: str, *, actor: object) -> bool:
        """显式重新排队与操作者回执共用事务，不自动重试未知原生调用。"""
        evaluation_id = _text(evaluation_id, 'evaluation_id')
        row = db.execute("SELECT * FROM evaluations WHERE evaluation_id=?", (evaluation_id,)).fetchone()
        if row is None:
            raise LookupError("evaluation does not exist")
        if row["state"] != "unavailable" or row["retry_count"] >= 2:
            return False
        current = db.execute("SELECT policy_version FROM policy_current WHERE singleton=1").fetchone()
        thread = db.execute("SELECT lifecycle_revision FROM threads WHERE thread_id=?", (row["thread_id"],)).fetchone()
        if current is None or current["policy_version"] != row["policy_version"] or thread is None or thread["lifecycle_revision"] != row["thread_lifecycle_revision"]:
            return False
        trigger_rows = db.execute(
            "SELECT trigger_id,gate_json FROM evaluation_triggers WHERE evaluation_id=? ORDER BY ordinal",
            (evaluation_id,),
        ).fetchall()
        for item in trigger_rows:
            if not json.loads(item["gate_json"]).get("admissible"):
                disposition = db.execute(
                    "SELECT state FROM trigger_dispositions WHERE trigger_id=?", (item["trigger_id"],)
                ).fetchone()
                if disposition is None or disposition["state"] != "filtered":
                    self._append_disposition(
                        db, trigger_id=item["trigger_id"], state="filtered",
                        event_type="filtered",
                        payload={"evaluation_id": evaluation_id, "reason": "gate_denied"},
                        actor=actor,
                    )
                continue
            disposition = db.execute(
                "SELECT state FROM trigger_dispositions WHERE trigger_id=?", (item["trigger_id"],)
            ).fetchone()
            if disposition is not None and disposition["state"] == "evaluation.queued":
                continue
            self._append_disposition(
                db, trigger_id=item["trigger_id"], state="evaluation.queued",
                event_type="evaluation.requeued", payload={"evaluation_id": evaluation_id},
                actor=actor,
            )
        # 不复用已经结束的 Evaluation 身份；调用方再次 begin_evaluation 会冻结新尝试。
        db.execute(
            "UPDATE evaluations SET retry_count=retry_count+1 WHERE evaluation_id=?",
            (evaluation_id,),
        )
        CoordinatorStore.append_audit(
            db, aggregate_type="evaluation", aggregate_id=evaluation_id,
            event_type="evaluation.retry_queued", payload={"retry_count": row["retry_count"] + 1},
            actor=actor,
        )
        return True

    def subscribe(
        self, ticket_id: str, thread_id: str, *, mode: str, owns_thread: bool = False, actor: object,
    ) -> dict[str, Any]:
        ticket_id, thread_id = _text(ticket_id, "ticket_id"), _text(thread_id, "thread_id")
        if mode not in {"work", "context"} or type(owns_thread) is not bool:
            raise ValueError("subscription mode or owns_thread is invalid")
        with self._store.transaction(write=True) as db:
            ticket = db.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
            thread = db.execute("SELECT * FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
            mandate = db.execute("SELECT * FROM mandates WHERE ticket_id=?", (ticket_id,)).fetchone()
            if ticket is None or thread is None or mandate is None or ticket["state"] != "open":
                raise LookupError("open ticket, mandate and thread are required")
            scope = json.loads(mandate["scope_json"])
            if thread["source_instance_id"] not in scope["source_instance_ids"] or thread_id not in scope["thread_ids"]:
                raise PermissionError("subscription is outside the Mandate scope")
            mandate_status = self._mandate_status(
                db, mandate, actor=actor, now=_now(),
            )
            if mandate_status not in {"valid", "affected"}:
                raise PermissionError("ticket Mandate is no longer valid")
            existing = db.execute(
                "SELECT * FROM ticket_subscriptions WHERE ticket_id=? AND thread_id=?",
                (ticket_id, thread_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["source_instance_id"] != thread["source_instance_id"]
                    or existing["mode"] != mode
                    or bool(existing["owns_thread"]) != owns_thread
                ):
                    raise PermissionError("subscription identity is immutable")
                if existing["state"] == "active":
                    return {
                        "ticket_id": ticket_id, "thread_id": thread_id,
                        "mode": mode, "owns_thread": owns_thread, "state": "duplicate",
                    }
                db.execute(
                    "UPDATE ticket_subscriptions SET state='active',updated_at=? WHERE ticket_id=? AND thread_id=?",
                    (_now(), ticket_id, thread_id),
                )
            else:
                now = _now()
                db.execute(
                    """INSERT INTO ticket_subscriptions(
                        ticket_id,thread_id,source_instance_id,mode,owns_thread,state,created_at,updated_at
                    ) VALUES(?,?,?,?,?,'active',?,?)""",
                    (ticket_id, thread_id, thread["source_instance_id"], mode, int(owns_thread), now, now),
                )
            CoordinatorStore.append_audit(
                db, aggregate_type="ticket", aggregate_id=ticket_id, event_type="ticket.thread_subscribed",
                payload={"thread_id": thread_id, "mode": mode, "owns_thread": owns_thread}, actor=actor,
            )
            return {
                "ticket_id": ticket_id, "thread_id": thread_id,
                "mode": mode, "owns_thread": owns_thread, "state": "active",
            }

    def unsubscribe(self, ticket_id: str, thread_id: str, *, actor: object) -> bool:
        with self._store.transaction(write=True) as db:
            row = db.execute(
                "SELECT * FROM ticket_subscriptions WHERE ticket_id=? AND thread_id=?",
                (_text(ticket_id, "ticket_id"), _text(thread_id, "thread_id")),
            ).fetchone()
            if row is None or row["state"] == "dropped":
                return False
            db.execute(
                "UPDATE ticket_subscriptions SET state='dropped',updated_at=? WHERE ticket_id=? AND thread_id=?",
                (_now(), ticket_id, thread_id),
            )
            CoordinatorStore.append_audit(
                db, aggregate_type="ticket", aggregate_id=ticket_id,
                event_type="ticket.thread_unsubscribed",
                payload={"thread_id": thread_id, "existing_inputs_retained": True}, actor=actor,
            )
            return True

    def handback(self, ticket_id: str, trigger_id: str, reason: str, *, actor: object,
                 assignment_id: str | None = None) -> dict[str, Any]:
        ticket_id = _text(ticket_id, "ticket_id")
        trigger_id = _text(trigger_id, "trigger_id")
        reason = _text(reason, "reason")
        with self._store.transaction(write=True) as db:
            ticket = db.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
            trigger = db.execute("SELECT * FROM triggers WHERE trigger_id=?", (trigger_id,)).fetchone()
            input_row = db.execute(
                "SELECT * FROM ticket_inputs WHERE ticket_id=? AND trigger_id=?",
                (ticket_id, trigger_id),
            ).fetchone()
            if ticket is None or trigger is None or input_row is None:
                raise LookupError("ticket input does not exist")
            if ticket["origin_trigger_id"] == trigger_id:
                raise PermissionError("a Ticket cannot hand back its origin Trigger")
            if input_row["context_only"]:
                raise PermissionError("context-only input cannot be handed back")
            if assignment_id is not None:
                assignment_id = _text(assignment_id, "assignment_id")
                delivery = db.execute(
                    "SELECT delivery.state,job.role,mission.ticket_id,mission.state AS mission_state,"
                    "control.mode AS control_mode FROM input_deliveries AS delivery "
                    "JOIN assignments AS assignment ON assignment.assignment_id=delivery.assignment_id "
                    "JOIN jobs AS job ON job.job_id=assignment.job_id "
                    "JOIN missions AS mission ON mission.mission_id=job.mission_id "
                    "LEFT JOIN mission_controls AS control ON control.mission_id=mission.mission_id "
                    "WHERE delivery.input_id=? AND delivery.assignment_id=?",
                    (input_row["input_id"], assignment_id),
                ).fetchone()
                if (delivery is None or delivery["state"] != "applied"
                        or delivery["role"] != "work" or delivery["ticket_id"] != ticket_id
                        or delivery["mission_state"] not in {"running", "integrating", "reviewing", "unknown"}
                        or delivery["control_mode"] != "running" or ticket["state"] != "open"):
                    raise PermissionError("only an active authorized Work Assignment may hand back this input")
                mandate = db.execute(
                    "SELECT * FROM mandates WHERE mandate_id=?", (ticket["mandate_id"],),
                ).fetchone()
                if mandate is None or self._mandate_status(db, mandate, actor=actor, now=_now()) not in {"valid", "affected"}:
                    raise PermissionError("Ticket Mandate no longer authorizes this handback")
                other = db.execute(
                    "SELECT COUNT(*) AS total FROM input_deliveries WHERE input_id=? "
                    "AND assignment_id<>? AND state NOT IN ('rejected','unapplied')",
                    (input_row["input_id"], assignment_id),
                ).fetchone()
                if other["total"]:
                    raise PermissionError("a shared input cannot be handed back by one Assignment")
            if input_row["state"] == "handed_back":
                return {"state": "duplicate", "requeued": False}
            if ticket["state"] == "ended" and ticket["ended_by"] == "human":
                raise PermissionError("a human-stopped Ticket no longer owns follow-ups")
            now = _now()
            db.execute(
                "UPDATE ticket_inputs SET state='handed_back',handback_reason=?,input_revision=input_revision+1 WHERE input_id=?",
                (reason, input_row["input_id"]),
            )
            owner_rows = db.execute(
                """SELECT i.ticket_id,m.* FROM ticket_inputs i
                   JOIN tickets t ON t.ticket_id=i.ticket_id
                   JOIN mandates m ON m.ticket_id=t.ticket_id
                   WHERE i.trigger_id=? AND i.context_only=0 AND i.state<>'handed_back'
                     AND (t.state='open' OR (t.state='ended' AND t.ended_by<>'human'))""",
                (trigger_id,),
            ).fetchall()
            owners = 0
            for owner in owner_rows:
                if self._mandate_status(db, owner, actor=actor, now=_now()) in {"valid", "affected"}:
                    owners += 1
            disposition = db.execute(
                "SELECT fanout_json,context_json FROM trigger_dispositions WHERE trigger_id=?",
                (trigger_id,),
            ).fetchone()
            fanout = [] if disposition is None else json.loads(disposition["fanout_json"])
            context = [] if disposition is None else json.loads(disposition["context_json"])
            if ticket_id not in fanout:
                fanout.append(ticket_id)
            requeued = owners == 0
            self._append_disposition(
                db, trigger_id=trigger_id,
                state="evaluation.queued" if requeued else "ticket.fanout",
                event_type="ticket.handback",
                payload={"ticket_id": ticket_id, "reason": reason, "requeued": requeued},
                actor=actor, fanout=fanout, context=context,
            )
            CoordinatorStore.append_audit(
                db, aggregate_type="ticket", aggregate_id=ticket_id, event_type="ticket.input_handed_back",
                payload={"trigger_id": trigger_id, "reason": reason, "requeued": requeued}, actor=actor,
            )
            return {"state": "handed_back", "requeued": requeued}

    def revalidate_source_mandates_in_transaction(
        self, db, source_instance_id: str, *, actor: object,
    ) -> None:
        """在来源配置事务内重新核验该来源的全部 Mandate。"""
        source_instance_id = _text(source_instance_id, "source_instance_id")
        now = _now()
        mandates = db.execute(
            "SELECT * FROM mandates WHERE source_instance_id=? ORDER BY mandate_id",
            (source_instance_id,),
        ).fetchall()
        for mandate in mandates:
            self._mandate_status(db, mandate, actor=actor, now=now)

    def revalidate_source_kind_mandates_in_transaction(
        self, db, kind_id: str, installed_api_version: int | None, *, actor: object,
    ) -> None:
        """插件不可用或 API 版本漂移时撤销授权并封存尚未评估的来源事件。"""
        kind_id = _text(kind_id, "kind_id")
        if installed_api_version is not None and (
            type(installed_api_version) is not int or installed_api_version < 1
        ):
            raise ValueError("installed SourceKind API version is invalid")
        sources = db.execute(
            "SELECT source_instance_id,kind_api_version FROM source_instances WHERE kind=? ORDER BY source_instance_id",
            (kind_id,),
        ).fetchall()
        if not sources:
            return
        now = _now()
        for source in sources:
            if installed_api_version == source["kind_api_version"]:
                continue
            reason = (
                "source_kind_unavailable" if installed_api_version is None
                else "source_kind_api_version_mismatch"
            )
            source_id = source["source_instance_id"]
            evaluations = db.execute(
                """SELECT DISTINCT e.* FROM evaluations e
                   JOIN evaluation_triggers et ON et.evaluation_id=e.evaluation_id
                   JOIN triggers t ON t.trigger_id=et.trigger_id
                   WHERE t.source_instance_id=? AND e.state='running'
                   ORDER BY e.started_at,e.evaluation_id""",
                (source_id,),
            ).fetchall()
            for evaluation in evaluations:
                self._stale_evaluation(
                    db, evaluation, reason=reason, actor=actor, now=now,
                )
            pending = db.execute(
                """SELECT t.trigger_id FROM triggers t
                   JOIN trigger_dispositions d ON d.trigger_id=t.trigger_id
                   WHERE t.source_instance_id=? AND d.state IN ('evaluation.queued','evaluation.running')
                   ORDER BY t.trigger_id""",
                (source_id,),
            ).fetchall()
            for trigger in pending:
                self._append_disposition(
                    db, trigger_id=trigger["trigger_id"], state="filtered",
                    event_type="filtered",
                    payload={
                        "reason": reason, "source_kind": kind_id,
                        "installed_api_version": installed_api_version,
                    },
                    actor=actor,
                )
            mandates = db.execute(
                "SELECT * FROM mandates WHERE source_instance_id=? ORDER BY mandate_id",
                (source_id,),
            ).fetchall()
            for mandate in mandates:
                terminal = db.execute(
                    """SELECT 1 FROM mandate_events WHERE mandate_id=?
                       AND event_type IN ('revoked','expired') LIMIT 1""",
                    (mandate["mandate_id"],),
                ).fetchone()
                if terminal or (
                    mandate["expires_at"] is not None and mandate["expires_at"] <= now
                ):
                    continue
                self._append_mandate_event(
                    db, mandate_id=mandate["mandate_id"], event_type="revoked",
                    payload={
                        "reason": reason, "source_kind": kind_id,
                        "expected_api_version": source["kind_api_version"],
                        "installed_api_version": installed_api_version,
                    },
                    actor=actor,
                )

    def revalidate_mandate(self, mandate_id: str, *, actor: object) -> dict[str, Any]:
        mandate_id = _text(mandate_id, "mandate_id")
        with self._store.transaction(write=True) as db:
            mandate = db.execute("SELECT * FROM mandates WHERE mandate_id=?", (mandate_id,)).fetchone()
            if mandate is None:
                raise LookupError("mandate does not exist")
            current = db.execute(
                "SELECT policy_version FROM policy_current WHERE singleton=1",
            ).fetchone()
            current_policy_version = None if current is None else current["policy_version"]
            status = self._mandate_status(db, mandate, actor=actor, now=_now())
            return {
                "mandate_id": mandate_id,
                "status": status,
                "policy_version": mandate["policy_version"],
                "current_policy_version": current_policy_version,
                "revalidated": status == "valid" and current_policy_version != mandate["policy_version"],
            }

    def policy_revision(self, policy_version: str) -> dict[str, Any] | None:
        """读取指定的不可变策略快照，不改变当前生效版本。"""
        policy_version = _text(policy_version, "policy_version")
        with self._store.transaction() as db:
            row = db.execute(
                "SELECT * FROM policy_versions WHERE policy_version=?", (policy_version,),
            ).fetchone()
        return None if row is None else self._policy_view(row)

    @staticmethod
    def _policy_summary(row) -> dict[str, Any]:
        return {
            "policy_version": row["policy_version"],
            "revision": row["revision"],
            "mode": row["mode"],
            "policy_hash": row["policy_hash"],
            "created_at": row["created_at"],
            "created_by": json.loads(row["created_by_json"]),
        }

    def list_policy_revisions(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """按发布时间倒序列出有界的策略修订摘要。"""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._store.transaction() as db:
            rows = db.execute(
                "SELECT * FROM policy_versions ORDER BY revision DESC LIMIT ?", (limit,),
            ).fetchall()
        return [self._policy_summary(row) for row in rows]

    @staticmethod
    def _evaluation_summary(row) -> dict[str, Any]:
        return {
            "evaluation_id": row["evaluation_id"],
            "thread_id": row["thread_id"],
            "policy_version": row["policy_version"],
            "state": row["state"],
            "trigger_batch_hash": row["trigger_batch_hash"],
            "primary_trigger_id": row["primary_trigger_id"],
            "strength": row["strength"],
            "actor": json.loads(row["actor_json"]),
            "mandate_basis": json.loads(row["mandate_basis_json"]),
            "thread_lifecycle_revision": row["thread_lifecycle_revision"],
            "started_at": row["started_at"],
            "deadline_at": row["deadline_at"],
            "completed_at": row["completed_at"],
            "retry_count": row["retry_count"],
            "outcome": None if row["outcome_json"] is None else json.loads(row["outcome_json"]),
        }

    @staticmethod
    def _evaluation_detail(db, row) -> dict[str, Any]:
        policy = db.execute(
            "SELECT * FROM policy_versions WHERE policy_version=?", (row["policy_version"],),
        ).fetchone()
        if policy is None:
            raise RuntimeError("evaluation references a missing PolicyRevision")
        triggers = db.execute(
            """SELECT t.*,et.gate_json,et.ordinal FROM evaluation_triggers et
               JOIN triggers t ON t.trigger_id=et.trigger_id
               WHERE et.evaluation_id=? ORDER BY et.ordinal""",
            (row["evaluation_id"],),
        ).fetchall()
        return {
            **CoordinatorAuthorizationEngine._evaluation_summary(row),
            "policy": CoordinatorAuthorizationEngine._policy_view(policy),
            "triggers": [
                {**CoordinatorAuthorizationEngine._trigger_view(trigger), "ordinal": trigger["ordinal"],
                 "gate": json.loads(trigger["gate_json"])}
                for trigger in triggers
            ],
        }

    def evaluation(self, evaluation_id: str) -> dict[str, Any] | None:
        """读取 Evaluation 的冻结输入、Gate 结果、策略和最终回执。"""
        evaluation_id = _text(evaluation_id, "evaluation_id")
        with self._store.transaction() as db:
            row = db.execute(
                "SELECT * FROM evaluations WHERE evaluation_id=?", (evaluation_id,),
            ).fetchone()
            return None if row is None else self._evaluation_detail(db, row)

    def list_evaluations(
        self, *, thread_id: str | None = None, state: str | None = None, limit: int = 200,
    ) -> list[dict[str, Any]]:
        """按时间倒序列出 Evaluation 摘要；详情通过 evaluation() 读取。"""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        states = {"running", "decided", "unavailable", "cancelled"}
        if state is not None and (not isinstance(state, str) or state not in states):
            raise ValueError("evaluation state is invalid")
        conditions, params = [], []
        if thread_id is not None:
            conditions.append("thread_id=?")
            params.append(_text(thread_id, "thread_id"))
        if state is not None:
            conditions.append("state=?")
            params.append(state)
        sql = "SELECT * FROM evaluations"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY started_at DESC,evaluation_id DESC LIMIT ?"
        params.append(limit)
        with self._store.transaction() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._evaluation_summary(row) for row in rows]

    @staticmethod
    def _decision_view(row) -> dict[str, Any]:
        return {
            "decision_id": row["decision_id"],
            "evaluation_id": row["evaluation_id"],
            "thread_id": row["thread_id"],
            "policy_version": row["policy_version"],
            "kind": row["kind"],
            "reason": json.loads(row["reason_json"]),
            "audit": json.loads(row["audit_json"]),
            "ticket_id": row["ticket_id"],
            "created_at": row["created_at"],
        }

    def decision(self, decision_id: str) -> dict[str, Any] | None:
        """读取不可变 Decision 及宿主核验时保存的审计依据。"""
        decision_id = _text(decision_id, "decision_id")
        with self._store.transaction() as db:
            row = db.execute(
                "SELECT * FROM decisions WHERE decision_id=?", (decision_id,),
            ).fetchone()
        return None if row is None else self._decision_view(row)

    def list_decisions(
        self, *, thread_id: str | None = None, kind: str | None = None, limit: int = 200,
    ) -> list[dict[str, Any]]:
        """按时间倒序列出不可变 Decision。"""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        kinds = {"accept", "reopen", "stop", "ignore", "unavailable"}
        if kind is not None and (not isinstance(kind, str) or kind not in kinds):
            raise ValueError("decision kind is invalid")
        conditions, params = [], []
        if thread_id is not None:
            conditions.append("thread_id=?")
            params.append(_text(thread_id, "thread_id"))
        if kind is not None:
            conditions.append("kind=?")
            params.append(kind)
        sql = "SELECT * FROM decisions"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY created_at DESC,decision_id DESC LIMIT ?"
        params.append(limit)
        with self._store.transaction() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._decision_view(row) for row in rows]

    def mandate(
        self, mandate_id: str, *, event_limit: int = 200,
    ) -> dict[str, Any] | None:
        """读取不可变 Mandate 快照及有界状态事件，不触发重新核验或写入。"""
        mandate_id = _text(mandate_id, "mandate_id")
        if type(event_limit) is not int or not 1 <= event_limit <= 1000:
            raise ValueError("event_limit must be between 1 and 1000")
        with self._store.transaction() as db:
            row = db.execute(
                "SELECT * FROM mandates WHERE mandate_id=?", (mandate_id,),
            ).fetchone()
            if row is None:
                return None
            event_count = db.execute(
                "SELECT count(*) FROM mandate_events WHERE mandate_id=?", (mandate_id,),
            ).fetchone()[0]
            events = db.execute(
                """SELECT * FROM mandate_events WHERE mandate_id=?
                   ORDER BY occurred_at DESC,event_id DESC LIMIT ?""",
                (mandate_id, event_limit),
            ).fetchall()
        return {
            "mandate_id": row["mandate_id"],
            "decision_id": row["decision_id"],
            "ticket_id": row["ticket_id"],
            "strength": row["strength"],
            "policy_version": row["policy_version"],
            "trigger_id": row["trigger_id"],
            "source_instance_id": row["source_instance_id"],
            "actor": json.loads(row["actor_json"]),
            "basis": json.loads(row["basis_json"]),
            "scope": json.loads(row["scope_json"]),
            "granted_at": row["granted_at"],
            "expires_at": row["expires_at"],
            "events": [
                {
                    "event_id": event["event_id"],
                    "event_type": event["event_type"],
                    "payload": json.loads(event["payload_json"]),
                    "occurred_at": event["occurred_at"],
                    "actor": json.loads(event["actor_json"]),
                }
                for event in reversed(events)
            ],
            "events_truncated": event_count > len(events),
        }

    def list_mandates(self, *, ticket_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """按签发时间倒序列出 Mandate 摘要。"""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        sql = "SELECT * FROM mandates"
        params: list[Any] = []
        if ticket_id is not None:
            sql += " WHERE ticket_id=?"
            params.append(_text(ticket_id, "ticket_id"))
        sql += " ORDER BY granted_at DESC,mandate_id DESC LIMIT ?"
        params.append(limit)
        with self._store.transaction() as db:
            rows = db.execute(sql, params).fetchall()
        return [
            {
                "mandate_id": row["mandate_id"], "decision_id": row["decision_id"],
                "ticket_id": row["ticket_id"], "strength": row["strength"],
                "policy_version": row["policy_version"], "trigger_id": row["trigger_id"],
                "source_instance_id": row["source_instance_id"], "granted_at": row["granted_at"],
                "expires_at": row["expires_at"],
            }
            for row in rows
        ]

    def ticket(self, ticket_id: str) -> dict[str, Any] | None:
        with self._store.transaction() as db:
            row = db.execute("SELECT * FROM tickets WHERE ticket_id=?", (_text(ticket_id, "ticket_id"),)).fetchone()
            if row is None:
                return None
            mandate = db.execute("SELECT * FROM mandates WHERE ticket_id=?", (ticket_id,)).fetchone()
            subscriptions = db.execute(
                "SELECT thread_id,mode,owns_thread,state FROM ticket_subscriptions WHERE ticket_id=? ORDER BY thread_id",
                (ticket_id,),
            ).fetchall()
            inputs = db.execute(
                "SELECT input_id,trigger_id,context_only,state,handback_reason,created_at,delivered_at,read_at,"
            "delivered_to_assignment_id,delivery_key,input_revision FROM ticket_inputs WHERE ticket_id=? ORDER BY created_at",
                (ticket_id,),
            ).fetchall()
        return {
            **dict(row),
            "mandate": None if mandate is None else {
                "mandate_id": mandate["mandate_id"], "strength": mandate["strength"],
                "policy_version": mandate["policy_version"], "basis": json.loads(mandate["basis_json"]),
                "scope": json.loads(mandate["scope_json"]),
            },
            "subscriptions": [dict(item) for item in subscriptions],
            "inputs": [dict(item) for item in inputs],
        }

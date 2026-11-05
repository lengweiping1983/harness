"""只读对照 v1 Superagent Inbox/Router 与 v2 Source Registry/Decision 的 Shadow 审计。"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from .superagent_coordinator_store import CoordinatorStore


class ShadowComparisonService:
    """只读比较两套账本中可映射的来源事实和研判结果；绝不双写或触发执行。"""

    def __init__(self, store: CoordinatorStore, legacy_state_dir: str | Path):
        self._store = store
        self._legacy_state_dir = Path(legacy_state_dir).expanduser().resolve()

    @staticmethod
    def _readonly(path: Path):
        if not path.is_file() or path.is_symlink():
            return None
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db

    @staticmethod
    def _tables(db) -> set[str]:
        return {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    def compare(self, *, limit: int = 200) -> dict:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        inbox_path = self._legacy_state_dir / "inbox.sqlite3"
        router_path = inbox_path.with_name(inbox_path.stem + ".router.sqlite3")
        legacy_inbox = self._readonly(inbox_path)
        legacy_router = self._readonly(router_path)
        try:
            if legacy_inbox is None:
                return {
                    "mode": "shadow-read-only", "available": False,
                    "reason": "legacy_inbox_unavailable",
                    "legacy_inbox": str(inbox_path), "legacy_router": str(router_path),
                    "side_effects": {"legacy_writes": False, "coordinator_writes": False, "execution": False},
                }
            inbox_tables = self._tables(legacy_inbox)
            if not {"triggers", "sources"} <= inbox_tables:
                return {
                    "mode": "shadow-read-only", "available": False,
                    "reason": "legacy_inbox_schema_unavailable",
                    "legacy_inbox": str(inbox_path), "legacy_router": str(router_path),
                    "side_effects": {"legacy_writes": False, "coordinator_writes": False, "execution": False},
                }
            with self._store.transaction() as db:
                source_rows = db.execute(
                    """SELECT source_instance_id,legacy_source_id FROM source_instances
                       WHERE legacy_source_id IS NOT NULL ORDER BY source_instance_id"""
                ).fetchall()
                new_rows = db.execute(
                    """SELECT t.*,s.legacy_source_id,d.state AS disposition_state,d.decision_id
                       FROM triggers t JOIN source_instances s ON s.source_instance_id=t.source_instance_id
                       LEFT JOIN trigger_dispositions d ON d.trigger_id=t.trigger_id
                       WHERE s.legacy_source_id IS NOT NULL
                       ORDER BY t.received_at DESC,t.trigger_id DESC LIMIT ?""",
                    (limit + 1,),
                ).fetchall()
                new_rows_truncated = len(new_rows) > limit
                new_rows = new_rows[:limit]
                new_decisions = db.execute(
                    """SELECT d.kind,d.ticket_id,d.decision_id,e.primary_trigger_id,t.trigger_id,
                              t.source_instance_id,t.external_event_id,s.legacy_source_id,et.gate_json
                       FROM decisions d JOIN evaluations e ON e.evaluation_id=d.evaluation_id
                       JOIN evaluation_triggers et ON et.evaluation_id=e.evaluation_id
                       JOIN triggers t ON t.trigger_id=et.trigger_id
                       JOIN source_instances s ON s.source_instance_id=t.source_instance_id
                       WHERE s.legacy_source_id IS NOT NULL
                       ORDER BY d.created_at DESC,d.decision_id DESC LIMIT ?""",
                    (limit + 1,),
                ).fetchall()
                new_decisions_truncated = len(new_decisions) > limit
                new_decisions = new_decisions[:limit]
                new_threads_by_trigger = {}
                for new in new_rows:
                    new_threads_by_trigger[new["trigger_id"]] = [
                        row[0] for row in db.execute(
                            """SELECT th.external_thread_id FROM trigger_threads tt
                               JOIN threads th ON th.thread_id=tt.thread_id
                               WHERE tt.trigger_id=? ORDER BY th.external_thread_id""",
                            (new["trigger_id"],),
                        ).fetchall()
                    ]
            mapping = {row["source_instance_id"]: row["legacy_source_id"] for row in source_rows}
            old_rows = legacy_inbox.execute(
                """SELECT id,source,remote_id,actor,text,threads,human,explicit,disposition,policy_revision
                   FROM triggers WHERE instance=? ORDER BY seq DESC LIMIT ?""",
                (self._store.instance_id, limit + 1),
            ).fetchall()
            old_rows_truncated = len(old_rows) > limit
            old_rows = old_rows[:limit]
            old_by_key = {(row["source"], row["remote_id"]): row for row in old_rows}
            new_by_key = {}
            for row in new_rows:
                key = (row["legacy_source_id"], row["external_event_id"])
                new_by_key[key] = row
            fact_matches, fact_mismatches = [], []
            for key, new in new_by_key.items():
                old = old_by_key.get(key)
                if old is None:
                    continue
                actor = json.loads(new["actor_json"])
                body = json.loads(new["body_json"])
                new_threads = sorted(new_threads_by_trigger.get(new["trigger_id"], []))
                old_threads = sorted(json.loads(old["threads"]))
                facts = {
                    "actor_id": (old["actor"], actor["actor_id"]),
                    "human": (bool(old["human"]), actor["actor_kind"] == "human"),
                    "text": (old["text"], body.get("text", "")),
                    "threads": (old_threads, new_threads),
                    "explicit": (bool(old["explicit"]), actor["actor_kind"] == "human" and new["addressed"] in {"mention", "direct"}),
                }
                differences = {name: {"legacy": pair[0], "coordinator": pair[1]}
                               for name, pair in facts.items() if pair[0] != pair[1]}
                entry = {
                    "legacy_source_id": key[0], "external_event_id": key[1],
                    "legacy_trigger_id": old["id"], "coordinator_trigger_id": new["trigger_id"],
                    "differences": differences,
                }
                (fact_mismatches if differences else fact_matches).append(entry)
            unmatched_new = [
                {"legacy_source_id": key[0], "external_event_id": key[1], "trigger_id": row["trigger_id"]}
                for key, row in new_by_key.items() if key not in old_by_key
            ]
            unmatched_legacy = [
                {"legacy_source_id": key[0], "external_event_id": key[1], "trigger_id": row["id"]}
                for key, row in old_by_key.items() if key not in new_by_key
            ]

            legacy_decision_map = {}
            legacy_decision_truncated = False
            router_available = False
            if legacy_router is not None and {"inputs", "batches"} <= self._tables(legacy_router):
                router_available = True
                old_inputs = legacy_router.execute(
                    """SELECT i.trigger_id,i.source_id,i.event_id,i.target,i.state,i.batch_id,
                              b.state AS batch_state,b.decision,b.reason
                       FROM inputs i LEFT JOIN batches b ON b.id=i.batch_id
                       WHERE i.instance=? ORDER BY i.seq DESC LIMIT ?""",
                    (self._store.instance_id, limit + 1),
                ).fetchall()
                legacy_decision_truncated = len(old_inputs) > limit
                old_inputs = old_inputs[:limit]
                for row in old_inputs:
                    if row["batch_state"] != "decided" or not row["decision"]:
                        continue
                    try:
                        decision = json.loads(row["decision"])
                        if not isinstance(decision, dict) or row["trigger_id"] not in decision:
                            continue
                        target = decision[row["trigger_id"]]
                        accepted = target is not None
                        key = (row["source_id"], row["event_id"])
                        if key not in legacy_decision_map:
                            legacy_decision_map[key] = "accept" if accepted else "ignore"
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
            new_decision_map = {}
            for row in new_decisions:
                key = (row["legacy_source_id"], row["external_event_id"])
                if key not in new_decision_map:
                    gate = json.loads(row["gate_json"])
                    new_decision_map[key] = (
                        "ignore" if not gate.get("admissible") else
                        "accept" if row["kind"] == "accept" else
                        "ignore" if row["kind"] == "ignore" else row["kind"]
                    )
            decision_matches, decision_mismatches = [], []
            for key, new_kind in new_decision_map.items():
                old_kind = legacy_decision_map.get(key)
                if old_kind is None:
                    continue
                item = {
                    "legacy_source_id": key[0], "external_event_id": key[1],
                    "legacy": old_kind, "coordinator": new_kind,
                }
                (decision_matches if old_kind == new_kind else decision_mismatches).append(item)
            unmatched_new_decisions = [
                {"legacy_source_id": key[0], "external_event_id": key[1], "coordinator": kind}
                for key, kind in new_decision_map.items() if key not in legacy_decision_map
            ]
            unmatched_legacy_decisions = [
                {"legacy_source_id": key[0], "external_event_id": key[1], "legacy": kind}
                for key, kind in legacy_decision_map.items() if key not in new_decision_map
            ]
            return {
                "mode": "shadow-read-only",
                "available": True,
                "legacy_inbox": str(inbox_path),
                "legacy_router": str(router_path),
                "source_mapping": mapping,
                "sample": {
                    "limit": limit,
                    "source_facts": {
                        "legacy_truncated": old_rows_truncated,
                        "coordinator_truncated": new_rows_truncated,
                    },
                    "decisions": {
                        "legacy_truncated": legacy_decision_truncated,
                        "coordinator_truncated": new_decisions_truncated,
                    },
                },
                "source_facts": {
                    "compared": len(fact_matches) + len(fact_mismatches),
                    "matched": len(fact_matches), "mismatched": len(fact_mismatches),
                    "mismatches": fact_mismatches,
                    "unmatched_coordinator": unmatched_new,
                    "unmatched_legacy": unmatched_legacy,
                },
                "decisions": {
                    "router_available": router_available,
                    "compared": len(decision_matches) + len(decision_mismatches),
                    "matched": len(decision_matches), "mismatched": len(decision_mismatches),
                    "mismatches": decision_mismatches,
                    "unmatched_coordinator": unmatched_new_decisions,
                    "unmatched_legacy": unmatched_legacy_decisions,
                },
                "side_effects": {"legacy_writes": False, "coordinator_writes": False, "execution": False},
            }
        finally:
            if legacy_inbox is not None:
                legacy_inbox.close()
            if legacy_router is not None:
                legacy_router.close()

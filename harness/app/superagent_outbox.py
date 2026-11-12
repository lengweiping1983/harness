"""Outbox 消息与 Durable Effect 的原子状态协调。"""
from __future__ import annotations

import json
from typing import Any

from .superagent_coordinator_store import CoordinatorStore, _json, _now, _text, _time


_OUTBOX_TO_EFFECT = {
    "completed": "sent",
    "retry_wait": "retry_wait",
    "failed": "dropped",
    "unknown": "unknown",
}


class OutboxEffectService:
    """在同一 SQLite 事务内推进 Outbox 与其唯一 Effect。"""

    def __init__(self, store: CoordinatorStore):
        self._store = store

    @staticmethod
    def _linked(db, effect_id: str):
        return db.execute(
            "SELECT message.*,link.effect_id FROM outbox_effect_links AS link "
            "JOIN outbox_messages AS message ON message.message_id=link.message_id "
            "WHERE link.effect_id=?",
            (effect_id,),
        ).fetchone()

    @staticmethod
    def _validate_linked_effect(effect, message) -> None:
        if effect is None:
            raise RuntimeError("linked Outbox Effect record is missing")
        try:
            payload = json.loads(effect["payload_json"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("linked Outbox Effect payload is corrupt") from exc
        expected = {
            "schema_version": 1,
            "message_id": message["message_id"],
            "ticket_id": message["ticket_id"],
            "channel_id": message["channel_id"],
            "message_kind": message["kind"],
            "body_ref": message["body_ref"],
            "body_hash": message["body_hash"],
            "idempotency_key": message["idempotency_key"],
        }
        if (
            effect["kind"] != "outbox.send"
            or effect["aggregate_id"] != f"outbox:{message['message_id']}"
            or effect["idempotency_key"] != message["idempotency_key"]
            or payload != expected
        ):
            raise RuntimeError("linked Outbox Effect identity does not match its business message")

    @staticmethod
    def _message_update(db, message, *, target: str, changes: dict[str, Any],
                        event_type: str, actor: object, correlation_id: str | None,
                        causation_id: str | None, effect_id: str) -> dict:
        """使用 Outbox 自身 Revision CAS 更新业务投影并追加审计。"""
        expected = message["aggregate_revision"]
        if not CoordinatorStore.compare_and_swap(
            db, "outbox_message", message["message_id"], expected
        ):
            raise RuntimeError("outbox aggregate revision changed inside a write transaction")
        encoded = dict(changes)
        effect_generation = encoded.pop("_effect_generation", None)
        assignments = [f"{field}=?" for field in encoded]
        assignments.append("aggregate_revision=aggregate_revision+1")
        cursor = db.execute(
            f"UPDATE outbox_messages SET {','.join(assignments)} "
            "WHERE message_id=? AND aggregate_revision=?",
            (*encoded.values(), message["message_id"], expected),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("outbox revision ledger diverged from message revision")
        event_id = CoordinatorStore.append_audit(
            db, aggregate_type="outbox_message", aggregate_id=message["message_id"],
            event_type=event_type,
            payload={
                "from": message["state"], "to": target,
                "effect_id": effect_id,
                "effect_generation": effect_generation,
                "changes": encoded,
            },
            actor=actor, correlation_id=correlation_id, causation_id=causation_id,
        )
        db.execute(
            "UPDATE effects SET last_event_id=? WHERE effect_id=?",
            (event_id, effect_id),
        )
        row = db.execute(
            "SELECT * FROM outbox_messages WHERE message_id=?", (message["message_id"],)
        ).fetchone()
        return dict(row)

    @staticmethod
    def _receipt_key(receipt: object, idempotency_key: str) -> dict:
        if not isinstance(receipt, dict):
            raise ValueError("provider receipt must be an object")
        key = receipt.get("idempotency_key", receipt.get("provider_key"))
        if key != idempotency_key:
            raise ValueError("provider receipt must confirm the original idempotency key")
        return receipt

    def claim(self, *, owner: str, limit: int = 100, lease_until: str, message_id: str | None = None) -> list[dict]:
        """领取 Outbox Effect，并原子地把对应消息推进到 sending。"""
        owner = _text(owner, "owner")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        lease = _time(lease_until, "lease_until")
        if lease <= _now():
            raise ValueError("lease_until must be in the future")
        claimed = []
        with self._store.transaction(write=True) as db:
            rows = db.execute(
                "SELECT effect.effect_id FROM effects AS effect "
                "JOIN outbox_effect_links AS link ON link.effect_id=effect.effect_id "
                "JOIN outbox_messages AS message ON message.message_id=link.message_id "
                "WHERE effect.kind='outbox.send' AND effect.state IN ('pending','retry_wait') "
                "AND effect.due_at<=? AND message.state=effect.state AND (? IS NULL OR message.message_id=?) "
                "ORDER BY effect.due_at,effect.created_at LIMIT ?",
                (_now(), message_id, message_id, limit),
            ).fetchall()
            for candidate in rows:
                effect_id = candidate["effect_id"]
                effect_before = db.execute(
                    "SELECT * FROM effects WHERE effect_id=?", (effect_id,)
                ).fetchone()
                message = self._linked(db, effect_id)
                if message is None or message["state"] not in {"pending", "retry_wait"}:
                    raise RuntimeError("outbox effect link or message state is inconsistent")
                self._validate_linked_effect(effect_before, message)
                changed = db.execute(
                    "UPDATE effects SET state='leased',lease_owner=?,lease_until=?,"
                    "generation=generation+1,attempts=attempts+1 "
                    "WHERE effect_id=? AND state IN ('pending','retry_wait')",
                    (owner, lease, effect_id),
                ).rowcount
                if not changed:
                    continue
                effect = db.execute(
                    "SELECT * FROM effects WHERE effect_id=?", (effect_id,)
                ).fetchone()
                generation = effect["generation"]
                effect_event_id = CoordinatorStore._append_effect_event(
                    db, effect, event_type="effect.leased",
                    payload={"generation": generation, "attempts": effect["attempts"],
                             "lease_until": lease},
                    actor={"kind": "system", "component": "outbox-effect-queue", "owner": owner},
                    causation_id=effect_before["last_event_id"],
                )
                self._message_update(
                    db, message, target="sending",
                    changes={
                        "state": "sending",
                        "attempts": message["attempts"] + 1,
                        "provider_key": message["idempotency_key"],
                        "_effect_generation": generation,
                    },
                    event_type="outbox.sending",
                    actor={"kind": "system", "component": "outbox-effect-queue"},
                    correlation_id=effect["correlation_id"], causation_id=effect_event_id,
                    effect_id=effect_id,
                )
                claimed.append(dict(db.execute(
                    "SELECT * FROM effects WHERE effect_id=?", (effect_id,)
                ).fetchone()))
        return claimed

    def _finish(
        self, db, *, effect_id: str, generation: int, target_effect: str,
        receipt: object | None, error: str | None, due_at: str | None,
        owner: str | None,
    ) -> bool:
        message = self._linked(db, effect_id)
        if message is None:
            raise LookupError("outbox effect link does not exist")
        effect = db.execute(
            "SELECT * FROM effects WHERE effect_id=?", (effect_id,)
        ).fetchone()
        if type(generation) is not int or generation < 1:
            raise ValueError("generation must be a positive integer")
        self._validate_linked_effect(effect, message)
        expected_state = "leased" if owner is not None else "unknown"
        if (
            effect is None or effect["state"] != expected_state
            or effect["generation"] != generation
            or (owner is not None and effect["lease_owner"] != owner)
        ):
            return False
        if message["state"] != ("sending" if owner is not None else "unknown"):
            raise RuntimeError("outbox message and Effect states diverged")
        target_message = _OUTBOX_TO_EFFECT[target_effect]
        changes: dict[str, Any] = {"state": target_message, "_effect_generation": generation}
        if target_effect == "completed":
            verified = self._receipt_key(receipt, message["idempotency_key"])
            provider_id = _text(verified.get("provider_id"), "provider_id")
            sent_at = _time(verified.get("sent_at"), "sent_at")
            changes.update({
                "provider_id": provider_id,
                "provider_key": message["idempotency_key"],
                "sent_at": sent_at,
            })
            if error is not None or due_at is not None:
                raise ValueError("completed send cannot carry error or due_at")
        elif target_effect == "retry_wait":
            verified = self._receipt_key(receipt, message["idempotency_key"])
            if verified.get("retry_safe") is not True:
                raise ValueError("retry_wait requires provider proof that retry is safe")
            _text(error, "error")
            if due_at is None:
                raise ValueError("retry_wait requires due_at")
            changes["provider_key"] = message["idempotency_key"]
        elif target_effect == "failed":
            verified = self._receipt_key(receipt, message["idempotency_key"])
            if verified.get("permanent_rejection") is not True:
                raise ValueError("dropped requires a provider-confirmed permanent rejection")
            _text(error, "error")
            if due_at is not None:
                raise ValueError("dropped cannot carry due_at")
            changes["provider_key"] = message["idempotency_key"]
            if verified.get("provider_id") is not None:
                changes["provider_id"] = _text(verified["provider_id"], "provider_id")
        else:
            _text(error, "error")
            if due_at is not None:
                raise ValueError("unknown cannot carry due_at")

        due = None if due_at is None else _time(due_at, "due_at")
        receipt_json = None if receipt is None else _json(receipt)
        if owner is not None:
            cursor = db.execute(
                "UPDATE effects SET state=?,receipt_json=?,last_error=?,due_at=COALESCE(?,due_at),"
                "lease_owner=NULL,lease_until=NULL WHERE effect_id=? AND state='leased' "
                "AND lease_owner=? AND generation=?",
                (target_effect, receipt_json, error, due, effect_id, owner, generation),
            )
        else:
            cursor = db.execute(
                "UPDATE effects SET state=?,receipt_json=?,last_error=?,due_at=COALESCE(?,due_at) "
                "WHERE effect_id=? AND state='unknown' AND generation=?",
                (target_effect, receipt_json, error, due, effect_id, generation),
            )
        if cursor.rowcount != 1:
            return False
        effect_after = db.execute(
            "SELECT * FROM effects WHERE effect_id=?", (effect_id,)
        ).fetchone()
        effect_event_id = CoordinatorStore._append_effect_event(
            db, effect_after, event_type=f"effect.{target_effect}",
            payload={"generation": generation, "receipt_present": receipt is not None},
            actor={"kind": "system", "component": "outbox-effect-receipt"},
            causation_id=effect["last_event_id"],
        )
        self._message_update(
            db, message, target=target_message, changes=changes,
            event_type=f"outbox.{target_message}",
            actor={"kind": "system", "component": "outbox-effect-receipt"},
            correlation_id=effect_after["correlation_id"],
            causation_id=effect_event_id, effect_id=effect_id,
        )
        return True

    def settle(
        self, *, effect_id: str, owner: str, generation: int, state: str,
        receipt: object | None = None, error: str | None = None,
        due_at: str | None = None,
    ) -> bool:
        """将发送 Worker 的原始结算与 Outbox 业务回执原子提交。"""
        if state not in {"completed", "retry_wait", "failed", "unknown"}:
            raise ValueError("invalid Effect settlement state")
        with self._store.transaction(write=True) as db:
            return self._finish(
                db, effect_id=_text(effect_id, "effect_id"), generation=generation,
                target_effect=state, receipt=receipt, error=error, due_at=due_at,
                owner=_text(owner, "owner"),
            )

    def reconcile(
        self, *, effect_id: str, generation: int, state: str,
        receipt: object, error: str | None = None, due_at: str | None = None,
    ) -> bool:
        """只用原 Provider 回执核实 unknown；不执行新的发送。"""
        if state not in {"completed", "retry_wait", "failed"}:
            raise ValueError("invalid Outbox reconciliation state")
        with self._store.transaction(write=True) as db:
            return self._finish(
                db, effect_id=_text(effect_id, "effect_id"), generation=generation,
                target_effect=state, receipt=receipt, error=error, due_at=due_at,
                owner=None,
            )

    def withdraw(
        self, message_id: str, *, expected_revision: int, actor: object,
    ) -> bool:
        """仅在 Effect 尚未领取时撤回消息与发送意图。"""
        message_id = _text(message_id, "message_id")
        with self._store.transaction(write=True) as db:
            message = db.execute(
                "SELECT * FROM outbox_messages WHERE message_id=?", (message_id,)
            ).fetchone()
            if message is None:
                raise LookupError("outbox message does not exist")
            if type(expected_revision) is not int or expected_revision < 1:
                raise ValueError("expected_revision must be a positive integer")
            if message["aggregate_revision"] != expected_revision:
                return False
            if message["state"] not in {"pending", "retry_wait"}:
                raise ValueError("only an unclaimed Outbox message can be withdrawn")
            link = db.execute(
                "SELECT effect_id FROM outbox_effect_links WHERE message_id=?", (message_id,)
            ).fetchone()
            if link is None:
                raise RuntimeError("outbox message has no durable Effect")
            effect = db.execute(
                "SELECT * FROM effects WHERE effect_id=?", (link["effect_id"],)
            ).fetchone()
            self._validate_linked_effect(effect, message)
            if effect["state"] != message["state"]:
                raise RuntimeError("Outbox message and queued Effect states diverged")
            changed = db.execute(
                "UPDATE effects SET state='cancelled' WHERE effect_id=? AND state IN ('pending','retry_wait')",
                (link["effect_id"],),
            ).rowcount
            if changed != 1:
                return False
            effect_event_id = CoordinatorStore._append_effect_event(
                db, effect, event_type="effect.cancelled",
                payload={"generation": effect["generation"]},
                actor=actor,
            )
            self._message_update(
                db, message, target="withdrawn",
                changes={"state": "withdrawn", "_effect_generation": effect["generation"]},
                event_type="outbox.withdrawn", actor=actor,
                correlation_id=effect["correlation_id"],
                causation_id=effect_event_id, effect_id=link["effect_id"],
            )
            return True

    @staticmethod
    def mark_expired_unknown(
        db, *, effect_id: str, generation: int, causation_id: str,
    ) -> bool:
        """在租约过期事务内同步消息状态；Effect 的 unknown 事件由 Store 先行追加。"""
        if type(generation) is not int or generation < 1:
            raise ValueError("generation must be a positive integer")
        effect = db.execute(
            "SELECT * FROM effects WHERE effect_id=?", (effect_id,)
        ).fetchone()
        message = OutboxEffectService._linked(db, effect_id)
        if effect is None or message is None:
            raise RuntimeError("expired Outbox Effect link is incomplete")
        OutboxEffectService._validate_linked_effect(effect, message)
        if effect["state"] != "unknown" or effect["generation"] != generation:
            return False
        if message["state"] != "sending":
            raise RuntimeError("expired Outbox message must still be sending")
        OutboxEffectService._message_update(
            db, message, target="unknown",
            changes={"state": "unknown", "_effect_generation": generation},
            event_type="outbox.unknown",
            actor={"kind": "system", "component": "outbox-effect-recovery"},
            correlation_id=effect["correlation_id"],
            causation_id=_text(causation_id, "causation_id"),
            effect_id=effect_id,
        )
        return True

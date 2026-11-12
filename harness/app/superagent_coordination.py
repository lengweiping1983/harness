"""同一协调事务中的目标控制和逐执行输入投递，不执行原生副作用。"""
from __future__ import annotations

from ..core.errors import HarnessError
from ..i18n import tr
from .superagent_common import _json as strict_loads, _text
from .superagent_coordinator_store import _hash, _json, _now


def _conflict():
    return HarnessError(tr("superagent.conflict"), code="superagent/conflict")


class CoordinatorControl:
    """保存操作意图；模式变化不能充当原运行和后台作业已停止的证明。"""

    def __init__(self, store):
        self.store = store

    def mode(self, mission_id):
        _text(mission_id, "mission_id")
        with self.store.transaction() as db:
            return self._mode(db, mission_id)

    @staticmethod
    def _decode(row):
        result = dict(row)
        expected = _hash(_json([result["mission_id"], result["action"]]))
        if result["payload_hash"] != expected:
            raise _conflict()
        try:
            receipt = strict_loads(result.pop("receipt_json"))
        except (TypeError, ValueError, RecursionError) as exc:
            raise _conflict() from exc
        if (not isinstance(receipt, dict) or set(receipt) != {"mission_id", "action", "mode", "generation"}
                or receipt["mission_id"] != result["mission_id"] or receipt["action"] != result["action"]):
            raise _conflict()
        if result["state"] == "applied":
            if (receipt["mode"] != {"pause": "paused", "resume": "running", "cancel": "cancelled"}[result["action"]]
                    or type(receipt["generation"]) is not int or receipt["generation"] < 2):
                raise _conflict()
        elif result["state"] != "rejected" or receipt["mode"] is not None or receipt["generation"] is not None:
            raise _conflict()
        result["receipt"] = receipt
        return result

    def _mode(self, db, mission_id):
        row = db.execute("SELECT * FROM mission_controls WHERE mission_id=?", (mission_id,)).fetchone()
        if row is None:
            raise _conflict()
        count = db.execute("SELECT COUNT(*) FROM control_commands WHERE mission_id=? AND state='applied'", (mission_id,)).fetchone()[0]
        last = db.execute("SELECT * FROM control_commands WHERE mission_id=? AND state='applied' ORDER BY sequence DESC LIMIT 1",
                          (mission_id,)).fetchone()
        expected = self._decode(last)["receipt"] if last else {"mode": "running", "generation": 1}
        if (row["mode"] != expected["mode"] or row["generation"] != expected["generation"]
                or row["generation"] != count + 1):
            raise _conflict()
        return dict(row)

    def allows(self, mission_id):
        return self.mode(mission_id)["mode"] == "running"

    def receipt(self, request_id):
        _text(request_id, "request_id")
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM control_commands WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                return None
            return self._decode(row)

    def command(self, mission_id, action, *, request_id, actor):
        for value, field in ((mission_id, "mission_id"), (request_id, "request_id")):
            _text(value, field)
        if action not in {"pause", "resume", "cancel"}:
            raise ValueError(tr("superagent.invalid", field="action"))
        payload_hash = _hash(_json([mission_id, action]))
        with self.store.transaction(write=True) as db:
            old = db.execute("SELECT * FROM control_commands WHERE request_id=?", (request_id,)).fetchone()
            if old is not None:
                self._decode(old)
                if old["payload_hash"] != payload_hash:
                    raise _conflict()
            else:
                if db.execute("SELECT COUNT(*) FROM control_commands").fetchone()[0] >= 1024:
                    raise HarnessError(tr("superagent.control.capacity"), code="superagent/control-capacity")
                now = _now()
                current = db.execute("SELECT * FROM mission_controls WHERE mission_id=?", (mission_id,)).fetchone()
                if current is not None:
                    current = self._mode(db, mission_id)
                mission = db.execute("SELECT state FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
                if mission is not None and current is None:
                    raise _conflict()
                applicable = (current is not None and mission is not None
                    and mission["state"] not in {"completed", "rejected", "blocked", "suppressed", "cancelled"}
                    and (current["mode"] != "cancelled" or action == "cancel"))
                mode = {"pause": "paused", "resume": "running", "cancel": "cancelled"}[action]
                receipt = {"mission_id": mission_id, "action": action,
                           "mode": mode if applicable else None,
                           "generation": current["generation"] + 1 if applicable else None}
                if applicable:
                    db.execute("UPDATE mission_controls SET mode=?,generation=generation+1,updated_at=? WHERE mission_id=?",
                               (mode, now, mission_id))
                db.execute("INSERT INTO control_commands(request_id,mission_id,action,payload_hash,state,receipt_json,created_at,updated_at) "
                           "VALUES(?,?,?,?,?,?,?,?)", (request_id, mission_id, action, payload_hash,
                           "applied" if applicable else "rejected", _json(receipt), now, now))
                self.store.append_audit(db, aggregate_type="mission", aggregate_id=mission_id,
                    event_type="control.applied" if applicable else "control.rejected", payload=receipt,
                    actor=actor, correlation_id="control:" + request_id)
        return self.receipt(request_id)


class CoordinatorInputDelivery:
    """输入事实与每次原生投递分离；未知投递不能重新认领。"""

    _TRANSITIONS = {
        "pending": {"submitting", "rejected"},
        "submitting": {"accepted", "applied", "unapplied", "unknown", "rejected"},
        "accepted": {"applied", "unapplied"},
        "unknown": {"accepted", "applied", "unapplied", "rejected"},
        "applied": set(), "unapplied": set(), "rejected": set(),
    }
    _RECEIPT_STATES = {"accepted", "applied", "unapplied", "unknown", "rejected"}

    def __init__(self, store):
        self.store = store

    @staticmethod
    def _identity(input_id, assignment_id):
        return "input_delivery_" + _hash(_json([input_id, assignment_id]))[:32]

    @staticmethod
    def make_receipt(delivery, status, *, native_receipt=None, reason_code=None):
        if not isinstance(status, str) or status not in CoordinatorInputDelivery._RECEIPT_STATES:
            raise ValueError(tr("superagent.invalid", field="input_delivery.receipt_status"))
        if native_receipt is not None and not isinstance(native_receipt, dict):
            raise ValueError(tr("superagent.invalid", field="input_delivery.native_receipt"))
        if reason_code is not None:
            reason_code = _text(reason_code, "reason_code", limit=96)
        return {
            "status": status,
            "delivery_id": delivery["delivery_id"],
            "input_id": delivery["input_id"],
            "assignment_id": delivery["assignment_id"],
            "delivery_key": delivery["delivery_key"],
            "native_receipt": native_receipt,
            "reason_code": reason_code,
        }

    @staticmethod
    def _validate_scope(db, input_id, assignment_id):
        row = db.execute(
            "SELECT input.ticket_id AS input_ticket_id,mission.ticket_id AS mission_ticket_id "
            "FROM ticket_inputs AS input JOIN assignments AS assignment ON assignment.assignment_id=? "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "JOIN missions AS mission ON mission.mission_id=job.mission_id "
            "WHERE input.input_id=?",
            (assignment_id, input_id),
        ).fetchone()
        if row is None or row["input_ticket_id"] != row["mission_ticket_id"]:
            raise _conflict()

    @classmethod
    def _validate_envelope(cls, row, receipt, state):
        if not isinstance(receipt, dict) or receipt.get("status") != state:
            raise _conflict()
        expected = {
            "delivery_id": row["delivery_id"],
            "input_id": row["input_id"],
            "assignment_id": row["assignment_id"],
            "delivery_key": row["delivery_key"],
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise _conflict()
        if set(receipt) - {
            "status", "delivery_id", "input_id", "assignment_id",
            "delivery_key", "native_receipt", "reason_code",
        }:
            raise _conflict()
        native = receipt.get("native_receipt")
        reason = receipt.get("reason_code")
        if native is not None and not isinstance(native, dict):
            raise _conflict()
        if reason is not None and (not isinstance(reason, str) or not reason or len(reason) > 96):
            raise _conflict()
        if native is not None:
            if native.get("source") == "original_run_input_receipt":
                if state != "applied" or set(native) != {"source"}:
                    raise _conflict()
            else:
                if native.get("request_id") != row["delivery_key"]:
                    raise _conflict()
                native_status = native.get("status")
                if not isinstance(native_status, str) or native_status not in {"pending", "applied", "unapplied"}:
                    raise _conflict()
                if type(native.get("turn")) is not int or native["turn"] < 1:
                    raise _conflict()
                if type(native.get("accepted_seq")) is not int or native["accepted_seq"] < 1:
                    raise _conflict()
                applied_seq = native.get("applied_seq")
                if native_status == "applied":
                    if type(applied_seq) is not int or applied_seq <= native["accepted_seq"]:
                        raise _conflict()
                elif applied_seq is not None:
                    raise _conflict()
                expected_native = {
                    "accepted": {"pending"},
                    "applied": {"applied"},
                    "unapplied": {"unapplied"},
                    "unknown": {"pending", "applied", "unapplied"},
                    "rejected": set(),
                }[state]
                if native_status not in expected_native:
                    raise _conflict()
        elif state in {"unapplied", "unknown", "rejected"} and not reason:
            raise _conflict()

    @classmethod
    def _decode(cls, row):
        result = dict(row)
        expected_id = cls._identity(result["input_id"], result["assignment_id"])
        if result["delivery_id"] != expected_id or not isinstance(result["delivery_key"], str) or not result["delivery_key"]:
            raise _conflict()
        try:
            receipt = strict_loads(result.pop("receipt_json"))
        except (TypeError, ValueError, RecursionError) as exc:
            raise _conflict() from exc
        if not isinstance(receipt, dict):
            raise _conflict()
        if result["state"] == "applied" and receipt == {
            "status": "applied", "source": "original_run_input_receipt",
        }:
            receipt = cls.make_receipt(
                result, "applied", native_receipt={"source": "original_run_input_receipt"},
            )
            cls._validate_envelope(result, receipt, "applied")
        elif result["state"] in {"pending", "submitting"}:
            if receipt:
                raise _conflict()
        elif result["state"] == "unknown" and receipt == {}:
            # 兼容早期 V2 账本：空回执只允许保守停留在 unknown，绝不重新认领。
            pass
        elif result["state"] in cls._RECEIPT_STATES:
            cls._validate_envelope(result, receipt, result["state"])
        else:
            raise _conflict()
        result["receipt"] = receipt
        return result

    def get(self, input_id, assignment_id):
        _text(input_id, "input_id")
        _text(assignment_id, "assignment_id")
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM input_deliveries WHERE input_id=? AND assignment_id=?",
                             (input_id, assignment_id)).fetchone()
            if row is None:
                return None
            self._validate_scope(db, input_id, assignment_id)
            return self._decode(row)

    def prepare_in_transaction(self, db, input_id, assignment_id, *, actor,
                               delivery_key=None, initial_state="pending"):
        """与 Assignment 提交意图共用事务，已有投递只读取原状态。"""
        self.store._require_transaction(db)
        for value, field in ((input_id, "input_id"), (assignment_id, "assignment_id")):
            _text(value, field)
        if initial_state not in {"pending", "submitting"}:
            raise ValueError(tr("superagent.invalid", field="input_delivery.state"))
        self._validate_scope(db, input_id, assignment_id)
        identity = self._identity(input_id, assignment_id)
        key = delivery_key or "superagent:input:" + identity
        _text(key, "delivery_key", limit=1024)
        old = db.execute("SELECT * FROM input_deliveries WHERE delivery_id=?", (identity,)).fetchone()
        if old is None:
            now = _now()
            db.execute("INSERT INTO input_deliveries(delivery_id,input_id,assignment_id,delivery_key,state,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?)", (identity, input_id, assignment_id, key, initial_state, now, now))
            self.store.append_audit(db, aggregate_type="input_delivery", aggregate_id=identity,
                event_type="input_delivery.created", payload={"input_id": input_id, "assignment_id": assignment_id,
                    "state": initial_state}, actor=actor, correlation_id=identity)
            old = db.execute("SELECT * FROM input_deliveries WHERE delivery_id=?", (identity,)).fetchone()
        else:
            decoded = self._decode(old)
            if old["delivery_key"] != key:
                raise _conflict()
            old = db.execute("SELECT * FROM input_deliveries WHERE delivery_id=?", (identity,)).fetchone()
            if decoded["delivery_id"] != identity:
                raise _conflict()
        return self._decode(old)

    def prepare(self, input_id, assignment_id, *, actor):
        with self.store.transaction(write=True) as db:
            self.prepare_in_transaction(db, input_id, assignment_id, actor=actor)
        return self.get(input_id, assignment_id)

    def transition_in_transaction(self, db, input_id, assignment_id, target, *,
                                  expected_revision, receipt, actor):
        """消费原生回执的调用方可把投递、输入状态和审计一次提交。"""
        self.store._require_transaction(db)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError(tr("superagent.invalid", field="expected_revision"))
        self._validate_scope(db, input_id, assignment_id)
        old = db.execute("SELECT * FROM input_deliveries WHERE input_id=? AND assignment_id=?",
                         (input_id, assignment_id)).fetchone()
        if old is None or old["aggregate_revision"] != expected_revision:
            raise _conflict()
        decoded = self._decode(old)
        if target not in self._TRANSITIONS[old["state"]]:
            raise _conflict()
        if target in {"pending", "submitting"}:
            if receipt not in (None, {}):
                raise _conflict()
            normalized_receipt = {}
        else:
            self._validate_envelope(decoded, receipt, target)
            normalized_receipt = receipt
        changed = db.execute(
            "UPDATE input_deliveries SET state=?,receipt_json=?,aggregate_revision=aggregate_revision+1,updated_at=? "
            "WHERE delivery_id=? AND aggregate_revision=?",
            (target, _json(normalized_receipt), _now(), old["delivery_id"], expected_revision),
        ).rowcount
        if changed != 1:
            raise _conflict()
        self.store.append_audit(db, aggregate_type="input_delivery", aggregate_id=old["delivery_id"],
            event_type="input_delivery." + target,
            payload={"input_id": input_id, "assignment_id": assignment_id,
                     "receipt_hash": _hash(_json(normalized_receipt)), "revision": expected_revision + 1},
            actor=actor, correlation_id=old["delivery_id"])

    def transition(self, input_id, assignment_id, target, *, expected_revision, receipt, actor):
        with self.store.transaction(write=True) as db:
            self.transition_in_transaction(db, input_id, assignment_id, target,
                expected_revision=expected_revision, receipt=receipt, actor=actor)
        return self.get(input_id, assignment_id)

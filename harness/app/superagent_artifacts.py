"""统一协调账本上的冻结文件产物与一次性依赖导入。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from ..core.errors import HarnessError
from ..core.cleanup import raise_cleanup_errors
from ..i18n import tr
from .superagent_common import _json as strict_loads
from .superagent_coordinator_store import _hash, _json, _now, _time


class CoordinatorArtifacts:
    """原生 Git 操作发生在事务外，未知操作只核对所属原工作树回执。"""

    def __init__(self, store, executor, admit, *, admit_effect=None):
        self.store, self.executor, self.admit = store, executor, admit
        self.admit_effect = admit_effect
        self._locks = {}

    def get(self, assignment_id):
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM artifacts WHERE assignment_id=?", (assignment_id,)).fetchone()
        if row is None:
            return None
        artifact = dict(row)
        if _hash(_json(strict_loads(artifact["manifest_json"]))) != artifact["manifest_hash"]:
            raise HarnessError(tr("superagent.corrupt"), code="superagent/artifact-corrupt")
        return artifact

    def _verify_assignment(self, assignment, worker, *, capture):
        """以持久身份和原生 Run 回执核验调用，不能只相信传入字典。"""
        with self.store.transaction() as db:
            row = db.execute("SELECT assignment.*,job.mission_id,mission.repo,mission.base_commit "
                "FROM assignments AS assignment JOIN jobs AS job ON job.job_id=assignment.job_id "
                "JOIN missions AS mission ON mission.mission_id=job.mission_id WHERE assignment.assignment_id=?",
                (assignment["assignment_id"],)).fetchone()
        if (row is None or any(row[field] != worker.get(field) for field in
                ("worker_id", "workspace_id", "session_id", "repo", "base_commit", "mission_id"))
                or any(row[field] != assignment.get(field) for field in
                       ("assignment_id", "job_id", "mission_id", "worker_id", "workspace_id", "session_id", "run_id"))):
            raise HarnessError(tr("superagent.artifact.scope"), code="superagent/artifact-scope")
        if capture:
            if row["state"] not in {"capturing", "unknown"} or not row["run_id"]:
                raise HarnessError(tr("superagent.artifact.run-unsettled"), code="superagent/artifact-run-unsettled")
            snapshot = self.executor.reconcile(worker, dict(row))
            if (snapshot.get("run_id") != row["run_id"] or snapshot.get("status") != "completed"
                    or snapshot.get("settled") is not True or snapshot.get("unconfirmed_tools", 0)
                    or snapshot.get("unsettled_background", 0)):
                raise HarnessError(tr("superagent.artifact.run-unsettled"), code="superagent/artifact-run-unsettled")
        elif row["state"] != "pending" or row["run_id"] is not None:
            raise HarnessError(tr("superagent.delivery.started"), code="superagent/delivery-already-started")

    def _begin(self, kind, key, payload):
        """原子认领确切操作；已有 leased 或 unknown 从不返回新的写入许可。"""
        with self.store.transaction(write=True) as db:
            effect_id = self.store.enqueue_effect(db, kind=kind, aggregate_id=payload["assignment_id"],
                idempotency_key=key, payload=payload, actor={"kind": "host"}, correlation_id=key)
            row = db.execute("SELECT * FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
            fresh = row["state"] == "pending"
            if fresh:
                lease = _time((datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(), "lease_until")
                db.execute("UPDATE effects SET state='leased',lease_owner=?,lease_until=?,generation=generation+1,attempts=attempts+1 "
                           "WHERE effect_id=? AND state='pending'", (key, lease, effect_id))
                row = db.execute("SELECT * FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
                self.store._append_effect_event(db, row, event_type="effect.leased",
                    payload={"generation": row["generation"]}, actor={"kind": "host"})
            return dict(row), fresh

    def _settle(self, effect, state, receipt=None):
        with self.store.transaction(write=True) as db:
            current = db.execute("SELECT * FROM effects WHERE effect_id=?", (effect["effect_id"],)).fetchone()
            if current["generation"] != effect["generation"]:
                raise HarnessError(tr("superagent.conflict"), code="superagent/effect-fenced")
            if current["state"] == "completed":
                if receipt is not None and current["receipt_json"] != _json(receipt):
                    raise HarnessError(tr("superagent.conflict"), code="superagent/effect-conflict")
                return
            if current["state"] == "leased":
                changed = self.store.settle_effect(db, effect_id=effect["effect_id"], owner=current["lease_owner"],
                    generation=current["generation"], state=state, receipt=receipt,
                    error="operation_outcome_uncertain" if state == "unknown" else None)
            elif current["state"] == "unknown" and state == "completed":
                changed = self.store.reconcile_effect(db, effect_id=effect["effect_id"], generation=current["generation"],
                    state="completed", receipt=receipt)
            elif current["state"] == "unknown" and state == "unknown":
                return
            else:
                raise HarnessError(tr("superagent.conflict"), code="superagent/effect-conflict")
            if not changed:
                raise HarnessError(tr("superagent.conflict"), code="superagent/effect-fenced")

    def _existing_effect(self, kind, key):
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM effects WHERE kind=? AND idempotency_key=?", (kind, key)).fetchone()
            return dict(row) if row else None

    def _latest_effect(self, kind, assignment_id):
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT * FROM effects WHERE kind=? AND aggregate_id=? ORDER BY rowid DESC LIMIT 1",
                (kind, assignment_id),
            ).fetchone()
        return dict(row) if row else None

    def _proves_no_effect(self, effect):
        """只有授权闸门在原生调用前写入的失败回执才允许创建新尝试。"""
        if effect is None or effect["state"] != "failed" or not effect.get("receipt_json"):
            return False
        try:
            proof = strict_loads(effect["receipt_json"])
        except (TypeError, ValueError):
            return False
        if (not isinstance(proof, dict) or proof.get("outcome") != "not_started"
                or proof.get("generation") != effect["generation"]
                or not isinstance(proof.get("reason"), str) or not proof["reason"]):
            return False
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT payload_json FROM audit_events WHERE aggregate_type='effect' AND aggregate_id=? "
                "AND event_type='effect.failed' ORDER BY seq DESC LIMIT 1",
                (effect["effect_id"],),
            ).fetchone()
        if row is None:
            return False
        try:
            event = strict_loads(row["payload_json"])
        except (TypeError, ValueError):
            return False
        return (
            isinstance(event, dict) and event.get("not_started") is True
            and event.get("generation") == effect["generation"]
            and event.get("reason") == proof["reason"]
        )

    @staticmethod
    def _attempt_key(root, attempt_no):
        return root if attempt_no == 1 else f"{root}:attempt:{attempt_no}"

    def _next_attempt_no(self, effect):
        payload = strict_loads(effect["payload_json"])
        current = payload.get("attempt_no", 1)
        if type(current) is not int or current < 1:
            raise HarnessError(tr("superagent.conflict"), code="superagent/effect-conflict")
        return current + 1

    def _authorize_and_mark_started(self, effect, assignment, operation):
        """在外部写入前用同一事务复核授权，并持久标记不可重放的开始边界。"""
        denied = None
        with self.store.transaction(write=True) as db:
            current = db.execute(
                "SELECT * FROM effects WHERE effect_id=?", (effect["effect_id"],),
            ).fetchone()
            if (current is None or current["state"] != "leased"
                    or current["lease_owner"] != effect["lease_owner"]
                    or current["generation"] != effect["generation"]):
                raise HarnessError(tr("superagent.conflict"), code="superagent/effect-fenced")
            try:
                if self.admit_effect is None:
                    self.admit(assignment["mission_id"])
                else:
                    self.admit_effect(db, assignment, operation)
            except BaseException as original:
                reason = getattr(original, "code", None) or type(original).__name__
                proof = {
                    "outcome": "not_started", "reason": str(reason),
                    "generation": current["generation"],
                }
                changed = db.execute(
                    "UPDATE effects SET state='failed',receipt_json=?,last_error=?,"
                    "lease_owner=NULL,lease_until=NULL "
                    "WHERE effect_id=? AND state='leased' AND lease_owner=? AND generation=?",
                    (_json(proof), str(reason), effect["effect_id"],
                     current["lease_owner"], current["generation"]),
                ).rowcount
                if changed != 1:
                    raise HarnessError(tr("superagent.conflict"), code="superagent/effect-fenced")
                failed = db.execute(
                    "SELECT * FROM effects WHERE effect_id=?", (effect["effect_id"],),
                ).fetchone()
                self.store._append_effect_event(
                    db, failed, event_type="effect.failed",
                    payload={"generation": current["generation"], "not_started": True,
                             "reason": str(reason)},
                    actor={"kind": "host", "component": "artifact-admission"},
                )
                denied = original
            else:
                self.store._append_effect_event(
                    db, current, event_type="effect.started",
                    payload={"generation": current["generation"], "operation": operation},
                    actor={"kind": "host", "component": "artifact-admission"},
                )
        if denied is not None:
            raise denied

    def _has_reconcilable_capture(self, assignment_id):
        """原操作已经持久认领时只读回查；不能因此授予新的 Git 捕获权限。"""
        if self.get(assignment_id) is not None:
            return True
        effect = self._latest_effect("artifact.capture", assignment_id)
        return effect is not None and effect["state"] in {"leased", "unknown", "completed"}

    async def capture(self, assignment, worker):
        async with self._locks.setdefault(assignment["assignment_id"], asyncio.Lock()):
            return await self._capture(assignment, worker)

    async def _capture(self, assignment, worker):
        self._verify_assignment(assignment, worker, capture=True)
        original = self.get(assignment["assignment_id"])
        if original is not None:
            return original
        root_key = "superagent:capture:" + assignment["assignment_id"]
        previous = self._latest_effect("artifact.capture", assignment["assignment_id"])
        retrying_no_effect = self._proves_no_effect(previous)
        if previous is not None and not retrying_no_effect:
            if previous["state"] not in {"pending", "leased", "unknown", "completed"}:
                raise HarnessError(tr("superagent.conflict"), code="superagent/effect-conflict")
            key = previous["idempotency_key"]
            payload = strict_loads(previous["payload_json"])
        else:
            self.admit(assignment["mission_id"])
            attempt_no = 1 if previous is None else self._next_attempt_no(previous)
            view = self.executor.snapshot_workspace(worker)
            payload = {
                "assignment_id": assignment["assignment_id"], "workspace_id": worker["workspace_id"],
                "expected_tree": view["tree"],
                "previous_ref": (view["workspace"].get("delivery") or {}).get("ref"),
                "attempt_no": attempt_no,
            }
            key = self._attempt_key(root_key, attempt_no)
        # leased / unknown / completed 只读取原工作树回执；只有新租约进入事务内授权闸门。
        effect, fresh = self._begin("artifact.capture", key, payload)
        if fresh:
            self._authorize_and_mark_started(effect, assignment, "artifact.capture")
            try:
                await self.executor.capture_workspace(worker)
            except BaseException as original:
                try:
                    self._settle(effect, "unknown")
                except BaseException as failure:
                    raise_cleanup_errors([original, failure])
                raise
        view = self.executor.snapshot_workspace(worker)
        receipt = view["workspace"].get("delivery")
        if (not isinstance(receipt, dict) or receipt.get("tree") != payload["expected_tree"]
                or receipt.get("ref") == payload["previous_ref"] or view["tree"] != payload["expected_tree"]):
            self._settle(effect, "unknown")
            raise HarnessError(tr("superagent.artifact.unknown"), code="superagent/artifact-unknown")
        self._settle(effect, "completed", receipt)
        manifest = self.executor.artifact_manifest(worker, receipt)
        artifact_id = "artifact_" + _hash(assignment["assignment_id"])[:32]
        with self.store.transaction(write=True) as db:
            db.execute("INSERT INTO artifacts(artifact_id,assignment_id,workspace_id,repo,base_commit,commit_id,tree_hash,"
                "manifest_json,manifest_hash,receipt_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (artifact_id, assignment["assignment_id"], worker["workspace_id"], worker["repo"], worker["base_commit"],
                 receipt["commit"], receipt["tree"], _json(manifest), _hash(_json(manifest)), _json(receipt), _now()))
            self.store.append_audit(db, aggregate_type="artifact", aggregate_id=artifact_id,
                event_type="artifact.frozen", payload={"assignment_id": assignment["assignment_id"],
                    "tree": receipt["tree"], "manifest_hash": _hash(_json(manifest))}, actor={"kind": "host"}, correlation_id=key)
        return self.get(assignment["assignment_id"])

    async def deliver(self, assignment, worker, artifacts):
        async with self._locks.setdefault(assignment["assignment_id"], asyncio.Lock()):
            return await self._deliver(assignment, worker, artifacts)

    async def _deliver(self, assignment, worker, artifacts):
        if not artifacts:
            return True
        self._verify_assignment(assignment, worker, capture=False)
        with self.store.transaction() as db:
            for artifact in artifacts:
                stored = db.execute("SELECT artifact.*,job.mission_id FROM artifacts AS artifact "
                    "JOIN assignments AS source ON source.assignment_id=artifact.assignment_id "
                    "JOIN jobs AS job ON job.job_id=source.job_id WHERE artifact.artifact_id=?",
                    (artifact["artifact_id"],)).fetchone()
                if (stored is None or stored["mission_id"] != assignment["mission_id"]
                        or any(artifact.get(key) != stored[key] for key in stored.keys() if key != "mission_id")):
                    raise HarnessError(tr("superagent.delivery.scope"), code="superagent/delivery-scope")
        if any(a["repo"] != worker["repo"] or a["base_commit"] != worker["base_commit"] for a in artifacts):
            raise HarnessError(tr("superagent.delivery.scope"), code="superagent/delivery-scope")
        root_key = "superagent:delivery:" + assignment["assignment_id"]
        previous = self._latest_effect("artifact.import", assignment["assignment_id"])
        artifact_ids = [a["artifact_id"] for a in artifacts]
        retrying_no_effect = self._proves_no_effect(previous)
        if previous is not None:
            old_payload = strict_loads(previous["payload_json"])
            if old_payload.get("artifact_ids") != artifact_ids:
                raise HarnessError(tr("superagent.delivery.conflict"), code="superagent/delivery-conflict")
            if previous["state"] == "completed":
                receipt = strict_loads(previous["receipt_json"])
                self._record_applied(assignment, artifacts, old_payload["plan"], receipt)
                return True
            if retrying_no_effect:
                self.admit(assignment["mission_id"])
                self._verify_assignment(assignment, worker, capture=False)
                plan = await self.executor.plan_artifact_delivery(worker, artifacts)
                if plan != old_payload.get("plan"):
                    raise HarnessError(tr("superagent.delivery.conflict"), code="superagent/delivery-conflict")
                attempt_no = self._next_attempt_no(previous)
                key = self._attempt_key(root_key, attempt_no)
                payload = {**old_payload, "attempt_no": attempt_no}
            else:
                if previous["state"] not in {"pending", "leased", "unknown"}:
                    raise HarnessError(tr("superagent.conflict"), code="superagent/effect-conflict")
                key = previous["idempotency_key"]
                payload = old_payload
                plan = payload["plan"]
        else:
            plan = await self.executor.plan_artifact_delivery(worker, artifacts)
            self.admit(assignment["mission_id"])
            self._verify_assignment(assignment, worker, capture=False)
            key = root_key
            payload = {
                "assignment_id": assignment["assignment_id"], "workspace_id": worker["workspace_id"],
                "artifact_ids": artifact_ids, "plan": plan, "attempt_no": 1,
            }
        if payload["artifact_ids"] != artifact_ids:
            raise HarnessError(tr("superagent.delivery.conflict"), code="superagent/delivery-conflict")
        now = _now()
        with self.store.transaction(write=True) as db:
            for artifact in artifacts:
                identity = "delivery_" + _hash(_json([artifact["artifact_id"], assignment["assignment_id"]]))[:32]
                existing = db.execute("SELECT * FROM deliveries WHERE delivery_id=?", (identity,)).fetchone()
                if existing is None:
                    db.execute("INSERT INTO deliveries(delivery_id,artifact_id,target_assignment_id,target_workspace_id,idempotency_key,"
                        "state,import_plan_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (identity, artifact["artifact_id"], assignment["assignment_id"], worker["workspace_id"], identity,
                         "pending" if plan["clean"] else "conflict", _json(plan), now, now))
                elif existing["import_plan_json"] != _json(plan):
                    raise HarnessError(tr("superagent.delivery.conflict"), code="superagent/delivery-conflict")
        if not plan["clean"]:
            return False
        effect, fresh = self._begin("artifact.import", key, payload)
        if fresh:
            # 授权、控制代次与 PlanRevision 在同一事务复核后，才允许开始原生导入。
            self._authorize_and_mark_started(effect, assignment, "artifact.import")
            with self.store.transaction(write=True) as db:
                changed = db.execute(
                    "UPDATE deliveries SET state='applying',aggregate_revision=aggregate_revision+1,updated_at=? "
                    "WHERE target_assignment_id=? AND state='pending'",
                    (_now(), assignment["assignment_id"]),
                ).rowcount
                if changed != len(artifacts):
                    raise HarnessError(tr("superagent.delivery.conflict"), code="superagent/delivery-conflict")
            try:
                await self.executor.apply_artifact_delivery(worker, artifacts, plan)
            except BaseException as original:
                try:
                    self._settle(effect, "unknown")
                    with self.store.transaction(write=True) as db:
                        db.execute("UPDATE deliveries SET state='unknown',aggregate_revision=aggregate_revision+1,updated_at=? "
                                   "WHERE target_assignment_id=? AND state='applying'", (_now(), assignment["assignment_id"]))
                except BaseException as failure:
                    raise_cleanup_errors([original, failure])
                raise
        view = self.executor.snapshot_workspace(worker)
        receipt = view["workspace"].get("delivery_inputs")
        if (not isinstance(receipt, dict) or any(receipt.get(field) != plan[field] for field in ("tree", "target_tree", "sources"))
                or view["tree"] != plan["tree"]):
            self._settle(effect, "unknown")
            raise HarnessError(tr("superagent.delivery.unknown"), code="superagent/delivery-unknown")
        self._settle(effect, "completed", receipt)
        self._record_applied(assignment, artifacts, plan, receipt)
        return True

    def _record_applied(self, assignment, artifacts, plan, receipt):
        """同一已确认原回执可恢复协调投影，不能因此再次导入文件。"""
        if (not isinstance(receipt, dict)
                or any(receipt.get(field) != plan[field] for field in ("tree", "target_tree", "sources"))):
            raise HarnessError(tr("superagent.corrupt"), code="superagent/delivery-corrupt")
        with self.store.transaction(write=True) as db:
            rows = db.execute("SELECT * FROM deliveries WHERE target_assignment_id=?", (assignment["assignment_id"],)).fetchall()
            if {row["artifact_id"] for row in rows} != {artifact["artifact_id"] for artifact in artifacts}:
                raise HarnessError(tr("superagent.corrupt"), code="superagent/delivery-corrupt")
            for row in rows:
                if row["state"] == "applied":
                    if strict_loads(row["receipt_json"]) != receipt:
                        raise HarnessError(tr("superagent.corrupt"), code="superagent/delivery-corrupt")
                elif row["state"] not in {"pending", "applying", "unknown"}:
                    raise HarnessError(tr("superagent.corrupt"), code="superagent/delivery-corrupt")
                else:
                    db.execute("UPDATE deliveries SET state='applied',receipt_json=?,aggregate_revision=aggregate_revision+1,updated_at=? "
                        "WHERE delivery_id=? AND aggregate_revision=?", (_json(receipt), _now(), row["delivery_id"], row["aggregate_revision"]))

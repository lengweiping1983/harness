"""统一资源归属和持久控制在真实执行边界的定向回归。"""
from __future__ import annotations

import asyncio
import copy
import unittest
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from harness.app.superagent_executor import HarnessExecutorAdapter
from harness.app.superagent_service import SuperagentService
from harness.core.errors import HarnessError
from harness.core.language import current_language
from tests import test_superagent_execution as fixtures


class UnifiedExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixtures.SuperagentExecutionTests()
        await self.fixture.asyncSetUp()
        self.addAsyncCleanup(self.fixture.asyncTearDown)

    async def test_pause_during_workspace_creation_prevents_session_and_submission(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event()
        create = f.adapter.create_workspace

        async def pause(worker, profile):
            await create(worker, profile)
            self.mission_id = worker["mission_id"]
            self.receipt = f.host.execution.control_mission(self.mission_id, "pause", request_id="pause", actor={"kind": "human"})

        f.adapter.create_workspace = pause
        with self.assertRaises(HarnessError) as caught:
            await f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        self.assertEqual(caught.exception.code, "superagent/mission-paused")
        self.assertEqual(f.shared["workspace_calls"], 1)
        self.assertEqual(f.shared["session_calls"], 0)
        self.assertEqual(f.shared["submit_calls"], [])
        self.assertEqual(self.receipt["state"], "applied")
        self.assertEqual(f.host.execution.mission_control(self.mission_id)["mode"], "paused")
        f.host.execution.control_mission(self.mission_id, "resume", request_id="resume", actor={"kind": "human"})
        f.adapter.create_workspace = create
        result = await f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        self.assertEqual(result["state"], "running")
        self.assertEqual(f.shared["workspace_calls"], 1)
        self.assertEqual(f.shared["session_calls"], 1)
        self.assertEqual(len(f.shared["submit_calls"]), 1)

    async def test_worker_scope_and_input_receipt_bind_the_original_assignment(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event()
        started = await f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        worker = f.host.execution.list_workers(accepted["ticket_id"])[0]
        assignment = f.host._execution_service._assignment(started["assignment_id"])
        self.assertEqual((worker["mission_id"], worker["job_id"], worker["stage"]),
                         (assignment["mission_id"], assignment["job_id"], 1))
        with f.host._store.transaction() as db:
            receipt = dict(db.execute("SELECT * FROM input_deliveries WHERE assignment_id=?", (started["assignment_id"],)).fetchone())
        self.assertEqual(receipt["state"], "applied")
        self.assertEqual(f.shared["submit_calls"], [started["assignment_id"]])

    async def test_missing_control_never_defaults_to_allowed_and_reads_do_not_recreate_it(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event()
        create = f.adapter.create_workspace

        async def remove(worker, profile):
            await create(worker, profile)
            self.mission_id = worker["mission_id"]
            with f.host._store.transaction(write=True) as db:
                db.execute("DROP TRIGGER mission_controls_immutable_delete")
                db.execute("DELETE FROM mission_controls WHERE mission_id=?", (self.mission_id,))

        f.adapter.create_workspace = remove
        with self.assertRaises(HarnessError):
            await f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        with self.assertRaises(HarnessError):
            f.host.execution.mission_control(self.mission_id)
        self.assertEqual(f.shared["session_calls"], 0)
        self.assertEqual(f.shared["submit_calls"], [])
        with f.host._store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM mission_controls").fetchone()[0], 0)

    async def test_control_request_is_stable_and_conflicting_reuse_is_rejected(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event()
        started = await f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        mission = f.host._execution_service._assignment(started["assignment_id"])["mission_id"]
        first = f.host.execution.control_mission(mission, "pause", request_id="stable", actor={})
        second = f.host.execution.control_mission(mission, "pause", request_id="stable", actor={})
        self.assertEqual(first, second)
        self.assertEqual(f.host.execution.mission_control(mission)["generation"], 2)
        with self.assertRaises(HarnessError):
            f.host.execution.control_mission(mission, "cancel", request_id="stable", actor={})
        self.assertEqual(f.host.execution.control_receipt("stable"), first)
        self.assertIsNone(f.host.execution.control_receipt("missing"))

    async def test_pause_blocks_completion_and_reply_until_explicit_resume(self):
        f = self.fixture
        origin, accepted, thread = f.accept_event()
        f.add_reply_channel(thread)
        started = await f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        mission = f.host._execution_service._assignment(started["assignment_id"])["mission_id"]
        f.host.execution.control_mission(mission, "pause", request_id="pause", actor={})
        f.adapter.finish(started["assignment_id"], {"status": "completed", "summary": "Done", "reply": "Done"})
        await f.host.execution.reconcile(started["assignment_id"])
        self.assertEqual(f.mission_state(mission), "running")
        self.assertEqual(f.outbox_count(), 0)
        f.host.execution.control_mission(mission, "resume", request_id="resume", actor={})
        await f.host.execution.reconcile(started["assignment_id"])
        self.assertEqual(f.mission_state(mission), "completed")
        self.assertEqual(f.outbox_count(), 1)

    async def test_corrupt_control_mode_and_receipt_are_rejected_without_repair(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event()
        started = await f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        mission = f.host._execution_service._assignment(started["assignment_id"])["mission_id"]
        f.host.execution.control_mission(mission, "pause", request_id="pause", actor={})
        with f.host._store.transaction(write=True) as db:
            db.execute("DROP TRIGGER mission_controls_transition_guard")
            db.execute("DROP TRIGGER control_commands_terminal_immutable")
            db.execute("UPDATE mission_controls SET mode='running' WHERE mission_id=?", (mission,))
        with self.assertRaises(HarnessError):
            f.host.execution.mission_control(mission)
        with f.host._store.transaction(write=True) as db:
            db.execute("UPDATE control_commands SET receipt_json='{}' WHERE request_id='pause'")
        with self.assertRaises(HarnessError):
            f.host.execution.control_receipt("pause")
        with self.assertRaises(HarnessError):
            f.host.execution.control_mission(mission, "pause", request_id="pause", actor={})
        with f.host._store.transaction() as db:
            self.assertEqual(db.execute("SELECT receipt_json FROM control_commands").fetchone()[0], "{}")

    async def test_pause_error_consumes_english_chinese_english_in_the_same_instance(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event()
        started = await f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        mission = f.host._execution_service._assignment(started["assignment_id"])["mission_id"]
        f.host.execution.control_mission(mission, "pause", request_id="pause", actor={})
        messages = []
        for language in ("en", "zh-CN", "en"):
            token = current_language.set(language)
            try:
                with self.assertRaises(HarnessError) as caught:
                    f.host._execution_service._admit_mission(mission)
                messages.append(str(caught.exception))
            finally:
                current_language.reset(token)
        self.assertEqual(messages[0], messages[2])
        self.assertNotEqual(messages[0], messages[1])
        self.assertIn("paused", messages[0])
        self.assertIn("暂停", messages[1])
        self.assertEqual(f.shared["submit_calls"], [started["assignment_id"]])

    async def test_concurrent_control_requests_share_one_original_generation_and_receipt(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event()
        started = await f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        mission = f.host._execution_service._assignment(started["assignment_id"])["mission_id"]

        def command(_):
            return f.host.execution.control_mission(mission, "pause", request_id="concurrent", actor={})

        with ThreadPoolExecutor(max_workers=4) as executor:
            receipts = list(executor.map(command, range(4)))
        self.assertTrue(all(receipt == receipts[0] for receipt in receipts))
        self.assertEqual(f.host.execution.mission_control(mission)["generation"], 2)
        with f.host._store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM control_commands").fetchone()[0], 1)


    async def _create_multi_node_input_assignments(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event()
        ticket_id = accepted["ticket_id"]
        service = f.host._execution_service
        mission_id = "mission-input-fanout"
        with f.host._store.unit_of_work(actor={"kind": "test"}) as uow:
            mission = uow.missions.create({
                "mission_id": mission_id, "ticket_id": ticket_id, "sequence": 1,
                "repo": f.profile.repo, "base_commit": f.adapter.base_commit(f.profile),
                "budgets_json": {"max_turns": 20, "max_seconds": 120},
            })
            plan = uow.plans.create(
                mission_id, expected_mission_revision=mission["aggregate_revision"],
                plan={"nodes": [
                    {"node_id": "work-a", "role": "work", "profile_id": f.profile.profile_id, "dependencies": []},
                    {"node_id": "work-b", "role": "work", "profile_id": f.profile.profile_id, "dependencies": []},
                ]},
                acceptance={"kind": "artifact_review"}, created_by_kind="host",
                created_by_ref="multi-node-input-test",
            )
        service._transition("mission", mission_id, "running", facts={
            "plan_valid": True, "base_commit_frozen": True, "mandate_valid": True, "control_allows": True,
        })
        assignment_ids = []
        for index, planned_job in enumerate(plan["jobs"]):
            job_id = planned_job["job_id"]
            service._transition("job", job_id, "ready", facts={
                "dependencies_satisfied": True, "mandate_valid": True, "control_allows": True,
            })
            mission = service._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
            job = service._read_one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
            worker = await service._ensure_worker(ticket_id, f.profile, True, mission=mission, job=job, stage=1)
            assignment_id = f"assignment-input-fanout-{index}"
            with f.host._store.unit_of_work(actor={"kind": "test"}) as uow:
                uow.assignments.create({
                    "assignment_id": assignment_id, "job_id": job_id, "attempt_no": 1,
                    "worker_id": worker["worker_id"], "session_id": worker["session_id"],
                    "workspace_id": worker["workspace_id"], "submission_key": "test:" + assignment_id,
                })
            service._transition("assignment", assignment_id, "submitting", facts={
                "stable_submission_persisted": True, "mandate_valid": True, "control_allows": True,
            })
            assignment = service._assignment(assignment_id)
            await f.adapter.submit(worker, assignment, "initial assignment prompt", [])
            started = await f.host.execution.reconcile(assignment_id)
            self.assertEqual(started["state"], "running")
            assignment_ids.append(assignment_id)
        with f.host._store.transaction() as db:
            input_row = db.execute(
                "SELECT input_id FROM ticket_inputs WHERE ticket_id=? AND trigger_id=?",
                (ticket_id, origin["trigger_id"]),
            ).fetchone()
        return input_row["input_id"], assignment_ids

    async def test_route_trigger_fans_out_followup_to_each_active_work_assignment(self):
        f = self.fixture
        _, assignment_ids = await self._create_multi_node_input_assignments()
        followup = f.receive_followup("followup-multi-node")
        with f.host._store.transaction() as db:
            input_row = db.execute(
                "SELECT input_id,ticket_id FROM ticket_inputs WHERE trigger_id=?",
                (followup["trigger_id"],),
            ).fetchone()
        result = await f.host.execution.route_trigger(input_row["ticket_id"], followup["trigger_id"])
        self.assertEqual(result["state"], "delivered")
        self.assertEqual(result["assignment_ids"], assignment_ids)
        self.assertEqual([item["state"] for item in result["deliveries"]], ["applied", "applied"])
        self.assertEqual(len(f.shared["input_calls"]), 2)
        repeated = await f.host.execution.route_trigger(input_row["ticket_id"], followup["trigger_id"])
        self.assertEqual(repeated, result)
        self.assertEqual(len(f.shared["input_calls"]), 2)

    async def test_route_trigger_reconciles_partial_fanout_without_replaying_unknown_delivery(self):
        f = self.fixture
        _, assignment_ids = await self._create_multi_node_input_assignments()
        followup = f.receive_followup("followup-multi-node-lost-ack")
        with f.host._store.transaction() as db:
            input_row = db.execute(
                "SELECT input_id,ticket_id FROM ticket_inputs WHERE trigger_id=?",
                (followup["trigger_id"],),
            ).fetchone()
        f.shared["fail_input_ack"] = True
        with self.assertRaises(RuntimeError):
            await f.host.execution.route_trigger(input_row["ticket_id"], followup["trigger_id"])
        self.assertEqual(len(f.shared["input_calls"]), 1)
        # 首个原 Run 已结束也不能把其未知投递从固定目标集合中移除。
        f.adapter.finish(assignment_ids[0], {"status": "completed", "summary": "Done"})
        f.host._execution_service._transition("assignment", assignment_ids[0], "failed",
                                             facts={"original_run_failure_confirmed": True})
        recovered = await f.host.execution.route_trigger(input_row["ticket_id"], followup["trigger_id"])
        self.assertEqual(recovered["state"], "delivered")
        self.assertEqual(recovered["assignment_ids"], assignment_ids)
        self.assertEqual([item["state"] for item in recovered["deliveries"]], ["applied", "applied"])
        self.assertEqual(len(f.shared["input_calls"]), 2)

    async def test_one_ticket_input_is_consumed_independently_by_multiple_assignments(self):
        f = self.fixture
        input_id, assignment_ids = await self._create_multi_node_input_assignments()
        first = await f.host.execution.deliver_input_to_assignment(input_id, assignment_ids[0])
        second = await f.host.execution.deliver_input_to_assignment(input_id, assignment_ids[1])
        self.assertEqual((first["state"], second["state"]), ("applied", "applied"))
        self.assertNotEqual(first["delivery_id"], second["delivery_id"])
        self.assertEqual(len(f.shared["input_calls"]), 2)
        repeated = await f.host.execution.deliver_input_to_assignment(input_id, assignment_ids[0])
        self.assertEqual(repeated, first)
        self.assertEqual(len(f.shared["input_calls"]), 2)
        with f.host._store.transaction() as db:
            input_row = db.execute("SELECT * FROM ticket_inputs WHERE input_id=?", (input_id,)).fetchone()
            deliveries = db.execute(
                "SELECT assignment_id,state FROM input_deliveries WHERE input_id=? ORDER BY assignment_id",
                (input_id,),
            ).fetchall()
        self.assertEqual(input_row["state"], "waiting")
        self.assertIsNone(input_row["delivered_to_assignment_id"])
        self.assertEqual([(row["assignment_id"], row["state"]) for row in deliveries],
                         sorted((assignment_id, "applied") for assignment_id in assignment_ids))

    async def test_unknown_input_delivery_reconciles_the_original_receipt_without_resubmission(self):
        f = self.fixture
        input_id, assignment_ids = await self._create_multi_node_input_assignments()
        assignment_id = assignment_ids[0]
        f.shared["fail_input_ack"] = True
        with self.assertRaisesRegex(RuntimeError, "acknowledgement lost"):
            await f.host.execution.deliver_input_to_assignment(input_id, assignment_id)
        with f.host._store.transaction() as db:
            state = db.execute(
                "SELECT state FROM input_deliveries WHERE input_id=? AND assignment_id=?",
                (input_id, assignment_id),
            ).fetchone()["state"]
        self.assertEqual(state, "unknown")
        reconciled = await f.host.execution.reconcile_input_delivery(input_id, assignment_id)
        self.assertEqual(reconciled["state"], "applied")
        self.assertEqual(len(f.shared["input_calls"]), 1)
        repeated = await f.host.execution.deliver_input_to_assignment(input_id, assignment_id)
        self.assertEqual(repeated["state"], "applied")
        self.assertEqual(len(f.shared["input_calls"]), 1)

    async def test_input_delivery_rejects_a_different_active_run_for_the_assignment(self):
        f = self.fixture
        input_id, assignment_ids = await self._create_multi_node_input_assignments()
        assignment_id = assignment_ids[0]
        f.shared["runs"][assignment_id]["run_id"] = "different-run"
        result = await f.host.execution.deliver_input_to_assignment(input_id, assignment_id)
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(f.shared["input_calls"], [])

    async def test_executor_adapter_rejects_steering_a_new_run_for_an_old_assignment(self):
        calls = []
        runtime = SimpleNamespace(
            closed=False,
            active={"worker-session": "new-run"},
            _executions={"new-run": SimpleNamespace(id="new-run", session_id="worker-session")},
        )
        async def steer(session_id, text, **kwargs):
            kwargs["before_accept"]()
            calls.append((session_id, text, kwargs["request_id"]))
            receipt = {"request_id": kwargs["request_id"], "turn": kwargs["expected_turn"],
                       "status": "pending", "accepted_seq": 1, "applied_seq": None}
            return {"accepted": True, "deferred": False, "request_id": kwargs["request_id"],
                    "turn": kwargs["expected_turn"], "receipt": receipt}
        harness = SimpleNamespace(
            runtime=runtime,
            _close_task=None,
            worktrees=object(),
            sessions=SimpleNamespace(root="persistent-sessions"),
            config=SimpleNamespace(
                enable_agents=False, enable_skills=False, enable_goal=False,
                enable_plan_mode=False, enable_subagent=False, enable_ralph=False,
            ),
            agent_mount=None,
            _agents={"worker-session": SimpleNamespace(run_id="new-run")},
            get_session=lambda session_id: SimpleNamespace(open_turn=7),
            steer=steer,
            steering_receipt=lambda session_id, key, summary=False: {
                "request_id": key, "turn": 7, "status": "pending", "accepted_seq": 1, "applied_seq": None,
            },
        )
        adapter = object.__new__(HarnessExecutorAdapter)
        adapter.harness = harness
        adapter.closed = False
        worker = {"worker_id": "worker-1", "session_id": "worker-session", "workspace_id": "workspace"}
        assignment = {
            "assignment_id": "assignment-1", "worker_id": "worker-1",
            "session_id": "worker-session", "workspace_id": "workspace", "run_id": "old-run",
        }
        delivery_key = "superagent-v2:input:input-1:assignment-1"
        with self.assertRaises(HarnessError) as raised:
            await adapter.deliver_input(
                worker, assignment, "follow-up input", delivery_key,
                lambda: calls.append("before_accept"),
            )
        self.assertEqual(raised.exception.code, "superagent/run-conflict")

        wrong_worker = {**worker, "worker_id": "worker-2"}
        new_assignment = {**assignment, "run_id": "new-run"}
        with self.assertRaises(HarnessError) as raised:
            await adapter.deliver_input(
                wrong_worker, new_assignment, "follow-up input", delivery_key,
                lambda: calls.append("before_accept"),
            )
        self.assertEqual(raised.exception.code, "superagent/run-conflict")
        with self.assertRaises(HarnessError) as raised:
            adapter.reconcile_input(wrong_worker, new_assignment, delivery_key)
        self.assertEqual(raised.exception.code, "superagent/run-conflict")
        wrong_key = "superagent-v2:input:input-1:assignment-2"
        with self.assertRaises(HarnessError) as raised:
            await adapter.deliver_input(
                worker, new_assignment, "follow-up input", wrong_key,
                lambda: calls.append("before_accept"),
            )
        self.assertEqual(raised.exception.code, "superagent/run-conflict")
        with self.assertRaises(HarnessError) as raised:
            adapter.reconcile_input(worker, new_assignment, wrong_key)
        self.assertEqual(raised.exception.code, "superagent/run-conflict")
        self.assertEqual(calls, [])

        runtime.active["worker-session"] = "old-run"
        runtime._executions["old-run"] = SimpleNamespace(id="old-run", session_id="worker-session")
        harness._agents["worker-session"].run_id = "old-run"
        before_accept_calls = []
        result = await adapter.deliver_input(
            worker, assignment, "follow-up input", delivery_key,
            lambda: before_accept_calls.append("accepted"),
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(before_accept_calls, ["accepted"])
        self.assertEqual(calls, [("worker-session", "follow-up input", delivery_key)])
        self.assertEqual(adapter.reconcile_input(worker, assignment, delivery_key)["request_id"], delivery_key)

    async def test_malformed_native_receipts_remain_unknown_and_are_never_replayed(self):
        f = self.fixture
        input_id, assignment_ids = await self._create_multi_node_input_assignments()
        malformed = [[], {"request_id": "wrong-key", "turn": 1, "status": [], "accepted_seq": 1}]
        for index, (assignment_id, native) in enumerate(zip(assignment_ids, malformed)):
            async def deliver(worker, assignment, prompt, key, before_accept):
                before_accept()
                f.shared["input_calls"].append(key)
                return {"accepted": True, "deferred": False, "request_id": key, "turn": 1, "receipt": native}
            f.adapter.deliver_input = deliver
            result = await f.host.execution.deliver_input_to_assignment(input_id, assignment_id)
            self.assertEqual(result["state"], "unknown")
            repeated = await f.host.execution.deliver_input_to_assignment(input_id, assignment_id)
            self.assertEqual(repeated["state"], "unknown")
            self.assertEqual(len(f.shared["input_calls"]), index + 1)

    async def test_unknown_delivery_can_be_reconciled_after_input_is_handed_back(self):
        f = self.fixture
        input_id, assignment_ids = await self._create_multi_node_input_assignments()
        f.shared["fail_input_ack"] = True
        with self.assertRaisesRegex(RuntimeError, "acknowledgement lost"):
            await f.host.execution.deliver_input_to_assignment(input_id, assignment_ids[0])
        with f.host._store.transaction(write=True) as db:
            db.execute("UPDATE ticket_inputs SET state='handed_back',handback_reason='test-handback',input_revision=input_revision+1 WHERE input_id=?", (input_id,))
        result = await f.host.execution.reconcile_input_delivery(input_id, assignment_ids[0])
        self.assertEqual(result["state"], "applied")
        with f.host._store.transaction() as db:
            state = db.execute("SELECT state FROM ticket_inputs WHERE input_id=?", (input_id,)).fetchone()["state"]
        self.assertEqual(state, "handed_back")
        self.assertEqual(len(f.shared["input_calls"]), 1)

    async def test_receipt_lookup_failure_does_not_downgrade_confirmed_acceptance(self):
        f = self.fixture
        input_id, assignment_ids = await self._create_multi_node_input_assignments()
        assignment_id = assignment_ids[0]

        async def accepted_pending(worker, assignment, prompt, key, before_accept):
            before_accept()
            f.shared["input_calls"].append(key)
            receipt = {"request_id": key, "turn": 1, "status": "pending", "accepted_seq": 1, "applied_seq": None}
            f.shared["input_receipts"][key] = receipt
            return {"accepted": True, "deferred": False, "request_id": key, "turn": 1, "receipt": receipt}
        f.adapter.deliver_input = accepted_pending
        first = await f.host.execution.deliver_input_to_assignment(input_id, assignment_id)
        self.assertEqual(first["state"], "accepted")

        def unavailable(worker, assignment, key):
            raise RuntimeError("receipt query unavailable")
        f.adapter.reconcile_input = unavailable
        second = await f.host.execution.reconcile_input_delivery(input_id, assignment_id)
        self.assertEqual(second["state"], "accepted")
        self.assertEqual(len(f.shared["input_calls"]), 1)

    async def test_concurrent_receipt_reconciliation_cannot_downgrade_applied_delivery(self):
        f = self.fixture
        input_id, assignment_ids = await self._create_multi_node_input_assignments()
        assignment_id = assignment_ids[0]
        f.shared["fail_input_ack"] = True
        ledger = f.host._input_delivery_service._deliveries
        transition = ledger.transition

        def race(input_id, assignment_id, target, *, expected_revision, receipt, actor):
            if target == "unknown":
                current = ledger.get(input_id, assignment_id)
                native = f.shared["input_receipts"][current["delivery_key"]]
                applied = ledger.make_receipt(current, "applied", native_receipt=native)
                transition(input_id, assignment_id, "applied",
                           expected_revision=current["aggregate_revision"], receipt=applied, actor=actor)
            return transition(input_id, assignment_id, target,
                              expected_revision=expected_revision, receipt=receipt, actor=actor)
        ledger.transition = race
        with self.assertRaisesRegex(RuntimeError, "acknowledgement lost"):
            await f.host.execution.deliver_input_to_assignment(input_id, assignment_id)
        result = await f.host.execution.reconcile_input_delivery(input_id, assignment_id)
        self.assertEqual(result["state"], "applied")
        self.assertEqual(len(f.shared["input_calls"]), 1)

    async def test_reconcile_returns_latest_terminal_state_after_native_lookup_race(self):
        f = self.fixture
        input_id, assignment_ids = await self._create_multi_node_input_assignments()
        assignment_id = assignment_ids[0]

        async def accepted_pending(worker, assignment, prompt, key, before_accept):
            before_accept()
            f.shared["input_calls"].append(key)
            receipt = {"request_id": key, "turn": 1, "status": "pending", "accepted_seq": 1, "applied_seq": None}
            f.shared["input_receipts"][key] = receipt
            return {"accepted": True, "deferred": False, "request_id": key, "turn": 1, "receipt": receipt}
        f.adapter.deliver_input = accepted_pending
        self.assertEqual((await f.host.execution.deliver_input_to_assignment(input_id, assignment_id))["state"], "accepted")

        ledger = f.host._input_delivery_service._deliveries
        def raced_lookup(worker, assignment, key):
            current = ledger.get(input_id, assignment_id)
            native = {"request_id": key, "turn": 1, "status": "applied", "accepted_seq": 1, "applied_seq": 2}
            receipt = ledger.make_receipt(current, "applied", native_receipt=native)
            ledger.transition(input_id, assignment_id, "applied",
                              expected_revision=current["aggregate_revision"], receipt=receipt,
                              actor={"kind": "host", "component": "superagent-input-delivery"})
            return None
        f.adapter.reconcile_input = raced_lookup
        result = await f.host.execution.reconcile_input_delivery(input_id, assignment_id)
        self.assertEqual(result["state"], "applied")

    async def test_native_receipt_storage_keeps_only_canonical_evidence_fields(self):
        f = self.fixture
        input_id, assignment_ids = await self._create_multi_node_input_assignments()
        async def delivery(worker, assignment, prompt, key, before_accept):
            before_accept()
            f.shared["input_calls"].append(key)
            receipt = {"request_id": key, "turn": 1, "status": "pending", "accepted_seq": 1, "untrusted": object()}
            return {"accepted": True, "deferred": False, "request_id": key, "turn": 1, "receipt": receipt}
        f.adapter.deliver_input = delivery
        result = await f.host.execution.deliver_input_to_assignment(input_id, assignment_ids[0])
        self.assertEqual(result["state"], "accepted")
        self.assertEqual(set(result["receipt"]["native_receipt"]),
                         {"request_id", "turn", "status", "accepted_seq", "applied_seq"})
        self.assertIsNone(result["receipt"]["native_receipt"]["applied_seq"])

    async def test_input_delivery_revalidates_mission_authorization_before_native_acceptance(self):
        f = self.fixture
        input_id, assignment_ids = await self._create_multi_node_input_assignments()
        service = f.host._input_delivery_service
        original = service._admit_mission
        calls = 0

        def pause_before_accept(mission_id):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise HarnessError("mission paused before input acceptance", code="superagent/mission-paused")
            return original(mission_id)
        service._admit_mission = pause_before_accept
        result = await f.host.execution.deliver_input_to_assignment(input_id, assignment_ids[0])
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(calls, 2)
        self.assertEqual(f.shared["input_calls"], [])

    async def _create_instruction_target(self):
        f = self.fixture
        input_id, assignment_ids = await self._create_multi_node_input_assignments()
        with f.host._store.transaction() as db:
            row = db.execute(
                "SELECT trigger_id FROM ticket_inputs WHERE input_id=?", (input_id,),
            ).fetchone()
        assignment = f.host._execution_service._assignment(assignment_ids[0])
        return (
            assignment["mission_id"], assignment["job_id"], assignment_ids[0],
            row["trigger_id"], assignment_ids,
        )

    async def test_mission_instruction_is_assignment_bound_and_tracks_native_application(self):
        f = self.fixture
        mission_id, job_id, assignment_id, trigger_id, _ = await self._create_instruction_target()
        first = await f.host.execution.deliver_instruction(
            mission_id, job_id, assignment_id, "instruction-apply-1", trigger_id,
            "Please inspect the related regression test.",
        )
        self.assertEqual(first["state"], "accepted")
        self.assertEqual(first["application"], "pending")
        self.assertEqual(first["aggregate_revision"], 3)
        operation_key = f"superagent-v2:instruction:instruction-apply-1:{assignment_id}"
        self.assertEqual(f.shared["instruction_calls"], [operation_key])

        duplicate = await f.host.execution.deliver_instruction(
            mission_id, job_id, assignment_id, "instruction-apply-1", trigger_id,
            "Please inspect the related regression test.",
        )
        self.assertEqual(duplicate["state"], "accepted")
        self.assertEqual(f.shared["instruction_calls"], [operation_key])

        f.shared["instruction_receipts"][operation_key].update(status="applied", applied_seq=2)
        applied = await f.host.execution.reconcile_instruction("instruction-apply-1")
        self.assertEqual(applied["state"], "accepted")
        self.assertEqual(applied["application"], "applied")
        self.assertEqual(applied["accepted_seq"], 1)
        self.assertEqual(applied["applied_seq"], 2)
        self.assertEqual(applied["aggregate_revision"], 4)
        self.assertEqual(f.host.execution.instruction_receipt("instruction-apply-1"), applied)

    async def test_unknown_mission_instruction_reconciles_original_receipt_without_replay(self):
        f = self.fixture
        mission_id, job_id, assignment_id, trigger_id, _ = await self._create_instruction_target()
        f.shared["fail_instruction_ack"] = True
        with self.assertRaisesRegex(RuntimeError, "instruction acknowledgement lost"):
            await f.host.execution.deliver_instruction(
                mission_id, job_id, assignment_id, "instruction-unknown-1", trigger_id,
                "Please verify the generated artifact.",
            )
        unknown = f.host.execution.instruction_receipt("instruction-unknown-1")
        self.assertEqual(unknown["state"], "unknown")
        self.assertEqual(len(f.shared["instruction_calls"]), 1)
        recovered = await f.host.execution.reconcile_instruction("instruction-unknown-1")
        self.assertEqual(recovered["state"], "accepted")
        self.assertEqual(recovered["application"], "pending")
        self.assertEqual(len(f.shared["instruction_calls"]), 1)

    async def test_mission_instruction_request_id_cannot_be_reused_for_other_payload_or_assignment(self):
        f = self.fixture
        mission_id, job_id, assignment_id, trigger_id, assignment_ids = await self._create_instruction_target()
        await f.host.execution.deliver_instruction(
            mission_id, job_id, assignment_id, "instruction-identity-1", trigger_id,
            "Inspect the original implementation.",
        )
        with self.assertRaises(HarnessError) as changed_payload:
            await f.host.execution.deliver_instruction(
                mission_id, job_id, assignment_id, "instruction-identity-1", trigger_id,
                "Change the implementation instead.",
            )
        self.assertEqual(changed_payload.exception.code, "superagent/conflict")
        other = f.host._execution_service._assignment(assignment_ids[1])
        with self.assertRaises(HarnessError) as changed_target:
            await f.host.execution.deliver_instruction(
                other["mission_id"], other["job_id"], assignment_ids[1],
                "instruction-identity-1", trigger_id, "Inspect the original implementation.",
            )
        self.assertEqual(changed_target.exception.code, "superagent/conflict")
        self.assertEqual(len(f.shared["instruction_calls"]), 1)

    async def test_mission_instruction_revalidates_mission_authorization_inside_native_accept_lock(self):
        f = self.fixture
        mission_id, job_id, assignment_id, trigger_id, _ = await self._create_instruction_target()
        service = f.host._mission_instruction_service
        original = service._admit_mission
        calls = 0

        def pause_before_accept(identity):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise HarnessError("mission paused before instruction acceptance", code="superagent/mission-paused")
            return original(identity)

        service._admit_mission = pause_before_accept
        result = await f.host.execution.deliver_instruction(
            mission_id, job_id, assignment_id, "instruction-pause-1", trigger_id,
            "Do not continue after the mission is paused.",
        )
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(result["reason_code"], "superagent/mission-paused")
        self.assertEqual(calls, 2)
        self.assertEqual(f.shared["instruction_calls"], [])

    async def test_mission_instruction_rejects_context_only_ticket_input(self):
        f = self.fixture
        mission_id, job_id, assignment_id, trigger_id, _ = await self._create_instruction_target()
        service = f.host._mission_instruction_service
        original = service._read_one

        def context_only(sql, params=()):
            if sql.startswith("SELECT context_only,state FROM ticket_inputs"):
                return {"context_only": 1, "state": "read"}
            return original(sql, params)

        service._read_one = context_only
        with self.assertRaises(HarnessError) as rejected:
            await f.host.execution.deliver_instruction(
                mission_id, job_id, assignment_id, "instruction-context-only-1", trigger_id,
                "This context-only event must not become a node instruction.",
            )
        self.assertEqual(rejected.exception.code, "superagent/instruction-unauthorized")
        self.assertEqual(f.shared["instruction_calls"], [])
        self.assertIsNone(f.host.execution.instruction_receipt("instruction-context-only-1"))

    async def test_concurrent_mission_instruction_claim_across_hosts_never_submits_twice(self):
        f = self.fixture
        mission_id, job_id, assignment_id, trigger_id, _ = await self._create_instruction_target()
        first_service = f.host._mission_instruction_service
        second_service = type(first_service)(
            f.host._store, f.host._authorization_service, f.host._source_service, f.adapter,
            admit_mission=f.host._execution_service._admit_mission,
        )
        original_get = second_service.get
        reads = 0

        def miss_the_initial_read(request_id):
            nonlocal reads
            reads += 1
            if reads == 1:
                return None
            return original_get(request_id)

        second_service.get = miss_the_initial_read
        entered = asyncio.Event()
        release = asyncio.Event()
        key = f"superagent-v2:instruction:instruction-race-1:{assignment_id}"

        async def blocked_delivery(worker, assignment, text, operation_key, turn_id, before_accept):
            before_accept()
            f.shared["instruction_calls"].append(operation_key)
            entered.set()
            await release.wait()
            receipt = {
                "request_id": operation_key, "turn": turn_id, "text": text,
                "status": "pending", "accepted_seq": 1, "applied_seq": None,
            }
            f.shared["instruction_receipts"][operation_key] = receipt
            return {
                "accepted": True, "deferred": False, "request_id": operation_key,
                "turn": turn_id, "receipt": copy.deepcopy(receipt),
            }

        f.adapter.deliver_instruction = blocked_delivery
        first_task = asyncio.create_task(first_service.deliver(
            mission_id, job_id, assignment_id, "instruction-race-1", trigger_id,
            "Keep this instruction single-submission across host instances.",
        ))
        await entered.wait()
        concurrent = await second_service.deliver(
            mission_id, job_id, assignment_id, "instruction-race-1", trigger_id,
            "Keep this instruction single-submission across host instances.",
        )
        self.assertEqual(concurrent["state"], "unknown")
        self.assertEqual(f.shared["instruction_calls"], [key])
        release.set()
        first = await first_task
        self.assertEqual(first["state"], "accepted")
        self.assertEqual(f.shared["instruction_calls"], [key])

    def test_mission_instruction_native_status_rejects_noncanonical_types(self):
        service = type(self.fixture.host._mission_instruction_service)
        key = "superagent-v2:instruction:instruction-type-1:assignment-1"
        valid = {
            "request_id": key, "turn": 1, "text": "Inspect this change.",
            "status": "pending", "accepted_seq": 1, "applied_seq": None,
        }
        self.assertEqual(service._native_status(valid, key, 1, valid["text"]), "pending")
        for field, invalid in (
            ("turn", True), ("turn", 1.0), ("status", []), ("status", {}),
            ("request_id", 7), ("accepted_seq", True),
        ):
            candidate = dict(valid)
            candidate[field] = invalid
            self.assertIsNone(service._native_status(candidate, key, 1, valid["text"]), field)

    async def test_executor_adapter_reconciles_instruction_from_original_run_events_only(self):
        key = "superagent-v2:instruction:instruction-1:assignment-1"
        expected_text = "Inspect the frozen artifact."
        worker = {"worker_id": "worker-1", "session_id": "worker-session", "workspace_id": "workspace"}
        assignment = {
            "assignment_id": "assignment-1", "worker_id": "worker-1",
            "session_id": "worker-session", "workspace_id": "workspace", "run_id": "run-1",
        }
        events = [
            SimpleNamespace(seq=1, type="turn/start", data={"turn": 1, "run_id": "run-1"}),
            SimpleNamespace(seq=2, type="note", data={
                "kind": "steering/accepted", "request_id": key, "turn": 1,
                "run_id": "run-1", "text": expected_text,
            }),
            SimpleNamespace(seq=3, type="user/message", data={
                "source": "steer", "request_id": key, "turn": 1,
                "run_id": "run-1", "text": expected_text,
            }),
        ]
        session = SimpleNamespace(events=events)
        native = {
            "request_id": key, "turn": 1, "text": expected_text,
            "status": "applied", "accepted_seq": 2, "applied_seq": 3,
        }
        harness = SimpleNamespace(
            runtime=SimpleNamespace(closed=False),
            _close_task=None,
            worktrees=object(),
            sessions=SimpleNamespace(root="sessions"),
            config=SimpleNamespace(
                enable_agents=False, enable_skills=False, enable_goal=False,
                enable_plan_mode=False, enable_subagent=False, enable_ralph=False,
            ),
            agent_mount=None,
            get_session=lambda session_id: session,
            steering_receipt=lambda session_id, request_id, summary=False: native,
        )
        adapter = object.__new__(HarnessExecutorAdapter)
        adapter.harness = harness
        adapter.closed = False
        self.assertEqual(
            adapter.reconcile_instruction(worker, assignment, key, 1, expected_text),
            native,
        )

        events[1] = SimpleNamespace(seq=2, type="note", data={
            "kind": "steering/accepted", "request_id": key, "turn": 1,
            "run_id": "run-2", "text": expected_text,
        })
        with self.assertRaises(HarnessError) as rejected:
            adapter.reconcile_instruction(worker, assignment, key, 1, expected_text)
        self.assertEqual(rejected.exception.code, "superagent/instruction-receipt-invalid")

        events[1] = SimpleNamespace(seq=2, type="note", data={
            "kind": "steering/accepted", "request_id": key, "turn": 1,
            "run_id": "run-1", "text": expected_text,
        })
        native["status"] = []
        with self.assertRaises(HarnessError) as malformed:
            adapter.reconcile_instruction(worker, assignment, key, 1, expected_text)
        self.assertEqual(malformed.exception.code, "superagent/instruction-receipt-invalid")

    async def test_cancel_stops_original_run_and_settles_persisted_mission(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event()
        started = await f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        mission_id = started["mission_id"]
        receipt = f.host.execution.control_mission(
            mission_id, "cancel", request_id="cancel-mission-1", actor={"kind": "human"},
        )
        self.assertEqual(receipt["state"], "applied")
        self.assertEqual(f.host.execution.control_status(mission_id)["stop_state"], "pending")

        reconciled = await f.host.execution.reconcile_controls()
        self.assertEqual(reconciled["processed"], 1)
        assignment = f.host._execution_service._assignment(started["assignment_id"])
        self.assertEqual(assignment["state"], "interrupted")
        self.assertEqual(f.mission_state(mission_id), "cancelled")
        self.assertEqual(f.host.execution.control_status(mission_id)["stop_state"], "settled")

        settled = await f.host.execution.reconcile_controls()
        self.assertEqual(settled["processed"], 0)
        self.assertEqual(f.shared["stop_calls"], [started["assignment_id"]])

    async def test_unknown_cancel_stop_is_not_replayed_and_can_be_reconciled(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event()
        started = await f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        mission_id = started["mission_id"]
        f.shared["fail_stop"] = True
        f.host.execution.control_mission(
            mission_id, "cancel", request_id="cancel-mission-unknown", actor={"kind": "human"},
        )

        await f.host.execution.reconcile_controls()
        assignment = f.host._execution_service._assignment(started["assignment_id"])
        self.assertEqual(assignment["state"], "unknown")
        self.assertTrue(assignment["error_ref"].startswith("stop-unknown:"))
        self.assertEqual(f.host.execution.control_status(mission_id)["stop_state"], "unknown")

        await f.host.execution.reconcile_controls()
        self.assertEqual(f.shared["stop_calls"], [started["assignment_id"]])

        snapshot = f.shared["runs"][started["assignment_id"]]
        f.shared["runs"][started["assignment_id"]] = f.adapter._snapshot(
            snapshot["run_id"], snapshot["run_reference"]["session_id"], snapshot["input_ids"],
            status="interrupted", settled=True, output_text=snapshot["output_text"],
        )
        await f.host.execution.reconcile_controls()
        self.assertEqual(f.mission_state(mission_id), "cancelled")
        self.assertEqual(f.host.execution.control_status(mission_id)["stop_state"], "settled")
        self.assertEqual(f.shared["stop_calls"], [started["assignment_id"]])

    async def test_unknown_stop_after_nonconfirming_receipt_never_replays_after_inspection_error(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event()
        started = await f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        mission_id = started["mission_id"]
        assignment_id = started["assignment_id"]
        f.shared["false_stop_receipt"] = True
        f.shared["fail_inspect"] = True
        f.host.execution.control_mission(
            mission_id, "cancel", request_id="cancel-mission-false-receipt",
            actor={"kind": "human"},
        )

        await f.host.execution.reconcile_controls()
        assignment = f.host._execution_service._assignment(assignment_id)
        self.assertEqual(assignment["state"], "unknown")
        self.assertEqual(assignment["error_ref"], f"stop-unknown:{assignment_id}")
        self.assertEqual(f.host.execution.control_status(mission_id)["stop_state"], "unknown")

        await f.host.execution.reconcile_controls()
        self.assertEqual(f.shared["stop_calls"], [assignment_id])
        self.assertEqual(f.mission_state(mission_id), "cancelling")

        snapshot = f.shared["runs"][assignment_id]
        f.shared["runs"][assignment_id] = f.adapter._snapshot(
            snapshot["run_id"], snapshot["run_reference"]["session_id"], snapshot["input_ids"],
            status="interrupted", settled=True, output_text=snapshot["output_text"],
        )
        await f.host.execution.reconcile_controls()
        self.assertEqual(f.mission_state(mission_id), "cancelled")
        self.assertEqual(f.shared["stop_calls"], [assignment_id])

    async def test_cancel_stops_a_capturing_assignment_after_run_settlement(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event()
        started = await f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        snapshot = f.shared["runs"][started["assignment_id"]]
        completed = f.adapter._snapshot(
            snapshot["run_id"], snapshot["run_reference"]["session_id"], snapshot["input_ids"],
            status="completed", settled=True, output_text=snapshot["output_text"],
        )
        completed["unsettled_background"] = 1
        f.shared["runs"][started["assignment_id"]] = completed

        result = await f.host.execution.reconcile(started["assignment_id"])
        self.assertEqual(result["state"], "capturing")
        f.host.execution.control_mission(
            started["mission_id"], "cancel", request_id="cancel-capturing", actor={"kind": "human"},
        )
        await f.host.execution.reconcile_controls()

        assignment = f.host._execution_service._assignment(started["assignment_id"])
        self.assertEqual(assignment["state"], "interrupted")
        self.assertEqual(f.mission_state(started["mission_id"]), "cancelled")
        self.assertEqual(f.shared["stop_calls"], [started["assignment_id"]])

    async def test_pause_defers_attention_creation_until_resume(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event()
        started = await f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        f.host.execution.control_mission(
            started["mission_id"], "pause", request_id="pause-attention", actor={"kind": "human"},
        )
        f.adapter.finish(started["assignment_id"], {
            "status": "attention", "summary": "Human decision required",
            "question": "Which deployment target should be used?",
        })

        await f.host.execution.reconcile(started["assignment_id"])
        self.assertEqual(f.host.execution.list_attentions(accepted["ticket_id"], state="open"), [])
        self.assertEqual(f.mission_state(started["mission_id"]), "running")

        f.host.execution.control_mission(
            started["mission_id"], "resume", request_id="resume-attention", actor={"kind": "human"},
        )
        await f.host.execution.reconcile(started["assignment_id"])
        attentions = f.host.execution.list_attentions(accepted["ticket_id"], state="open")
        self.assertEqual(len(attentions), 1)
        self.assertEqual(f.mission_state(started["mission_id"]), "blocked")

    async def test_host_exposes_artifact_service_under_the_same_lifecycle_lease(self):
        self.assertIsNone(self.fixture.host.artifacts.get("missing-assignment"))

    async def test_single_superagent_service_runs_assessor_planner_executor_recovery_and_close(self):
        f = self.fixture
        instance_id = "phase4-unified-service"
        state_dir = f.root / "service-v2"
        identity = {"project": "harness", "suite": "phase4.2"}
        adapter = fixtures.FakeExecutionAdapter(instance_id, f.profile)

        async def assessor(candidate):
            await asyncio.sleep(0)
            return f._proposal(candidate["triggers"][0]["body"]["text"])

        service = await SuperagentService.open_coordinator(
            state_dir, instance_id=instance_id, identity=identity,
            executor_adapter=adapter,
            local_identity_verifier=f._local_identity, assessor=assessor,
            watchdog_limits={"stale_after": 30.0, "max_stale_ticks": 2, "action": "attention"},
            interval=0.01, once_timeout=10,
        )
        self.addAsyncCleanup(service.aclose)
        host = service.coordinator
        host.sources.create_instance(
            "local-source", kind="local", config={"namespace": "repo"},

        )
        thread_key = "unified-service-thread"
        thread_id = f._thread_id(thread_key)
        scope = {
            "source_instance_ids": ["local-source"], "thread_ids": [thread_id],
            "actions": ["read", "reply"], "tools": [],
            "budget": {"max_turns": 3, "max_seconds": 60},
        }
        host.authorization.publish_policy({
            "mode": "auto", "timeout_seconds": 120,
            "rules": [{
                "id": "implementation", "name": "Implementation", "enabled": True,
                "instruction": "Only accept a settled implementation request.",
            }],
            "sources": {"local-source": {
                "allowed": [{"id": "human:1"}], "scope": scope,
            }},
        }, actor={"kind": "human", "id": "policy-admin"})
        host.sources.receive_local("local-source", {
            "event_id": "service-event-1", "thread_id": thread_key,
            "text": "Fix the existing typo.",
            "occurred_at": "2026-11-06T12:00:00Z",
            "metadata": {"actor_id": "human:1", "actor_kind": "human", "addressed": "direct"},
        })

        create_workspace = adapter.create_workspace
        paused = {}

        async def create_workspace_then_pause(worker, profile):
            receipt = await create_workspace(worker, profile)
            paused["mission_id"] = worker["mission_id"]
            pause = host.execution.control_mission(
                paused["mission_id"], "pause",
                request_id="service-pause-after-workspace", actor={"kind": "human"},
            )
            self.assertEqual(pause["state"], "applied")
            return receipt

        adapter.create_workspace = create_workspace_then_pause
        first = await service.tick()
        self.assertEqual(first["accepted"], 1)
        self.assertEqual(first["routes"], [])
        self.assertEqual(first["route_errors"][0]["code"], "superagent/mission-paused")
        self.assertEqual(first["pending_routes"], 1)
        self.assertEqual(first["active_assignments"], 0)
        self.assertEqual(first["pending_missions"], 1)
        self.assertTrue(first["effect_recovery_enabled"])
        self.assertIsInstance(first["effect_recovery"], dict)

        mission_id = paused["mission_id"]
        ticket_id = first["route_errors"][0]["ticket_id"]
        self.assertEqual(host.execution.mission_control(mission_id)["mode"], "paused")
        self.assertEqual(host.watchdog.get(mission_id)["state"], "healthy")
        self.assertEqual(host.authorization.ticket(ticket_id)["state"], "open")

        resumed = host.execution.control_mission(
            mission_id, "resume", request_id="service-resume-after-pause",
            actor={"kind": "human"},
        )
        self.assertEqual(resumed["state"], "applied")
        adapter.create_workspace = create_workspace

        second = await service.tick()
        self.assertEqual(len(second["routes"]), 1)
        route = second["routes"][0]["receipt"]
        self.assertEqual(route["state"], "running")
        self.assertEqual(second["pending_routes"], 0)
        self.assertEqual(second["active_assignments"], 1)
        self.assertEqual(second["pending_missions"], 1)
        self.assertEqual(adapter.shared["workspace_calls"], 1)
        self.assertEqual(adapter.shared["session_calls"], 1)
        self.assertEqual(adapter.shared["submit_calls"], [route["assignment_id"]])
        assignment_id = route["assignment_id"]
        self.assertEqual(host.execution.mission_control(mission_id)["mode"], "running")

        # 关闭释放旧 Host 和 Executor；重启后只回查同一 Assignment，不再次提交。
        await service.aclose()
        self.assertTrue(adapter.closed)
        self.assertTrue(host._closed)
        restarted_adapter = fixtures.FakeExecutionAdapter(instance_id, f.profile, adapter.shared)
        restarted = await SuperagentService.open_coordinator(
            state_dir, instance_id=instance_id, identity=identity,
            executor_adapter=restarted_adapter,
            local_identity_verifier=f._local_identity, assessor=assessor,
            watchdog_limits={"stale_after": 30.0, "max_stale_ticks": 2, "action": "attention"},
            interval=0.01, once_timeout=10,
        )
        self.addAsyncCleanup(restarted.aclose)
        restarted_host = restarted.coordinator
        recovered = await restarted.tick()
        self.assertEqual(recovered["active_assignments"], 1)
        self.assertEqual(recovered["pending_routes"], 0)
        self.assertEqual(restarted_adapter.shared["submit_calls"], [assignment_id])
        self.assertEqual(restarted_host.execution.mission_control(mission_id)["mode"], "running")
        self.assertEqual(restarted_host.authorization.ticket(ticket_id)["state"], "open")
        self.assertEqual(restarted_host.watchdog.get(mission_id)["state"], "healthy")

        restarted_adapter.finish(assignment_id, {
            "status": "completed", "summary": "Unified service run completed", "reply": "Done",
        })
        third = await restarted.tick()
        self.assertEqual(third["active_assignments"], 0)
        self.assertEqual(third["pending_missions"], 0)
        self.assertTrue(third["effect_recovery_enabled"])
        self.assertIsInstance(third["effect_recovery"], dict)
        with restarted_host._store.transaction() as db:
            mission = db.execute(
                "SELECT state FROM missions WHERE mission_id=?", (mission_id,),
            ).fetchone()
        self.assertEqual(mission["state"], "completed")

        self.assertEqual(await restarted.serve(asyncio.Event(), once=True), 0)
        await restarted.aclose()
        await restarted.aclose()
        self.assertTrue(restarted_adapter.closed)
        self.assertTrue(restarted_adapter.shared["async_closed"])
        self.assertTrue(restarted_host._closed)
        self.assertEqual(restarted.coordinator_status["runtime_state"], "stopped")
        with self.assertRaises(HarnessError) as closed:
            await restarted.tick()
        self.assertEqual(closed.exception.code, "superagent/closed")

    async def test_unified_service_revoke_blocks_resume_and_cancel_settles_original_run(self):
        f = self.fixture
        instance_id = "phase4-unified-service-revoke"
        adapter = fixtures.FakeExecutionAdapter(instance_id, f.profile)
        service = await SuperagentService.open_coordinator(
            f.root / "service-v2-revoke", instance_id=instance_id,
            identity={"project": "harness", "suite": "phase4.2.revoke"},
            executor_adapter=adapter, local_identity_verifier=f._local_identity,
            assessor=lambda candidate: f._proposal(candidate["triggers"][0]["body"]["text"]),
            interval=0.01, once_timeout=10,
        )
        self.addAsyncCleanup(service.aclose)
        host = service.coordinator
        host.sources.create_instance(
            "local-source", kind="local", config={"namespace": "repo"},

        )
        thread_key = "unified-service-revoke-thread"
        thread_id = f._thread_id(thread_key)
        scope = {
            "source_instance_ids": ["local-source"], "thread_ids": [thread_id],
            "actions": ["read", "reply"], "tools": [],
            "budget": {"max_turns": 3, "max_seconds": 60},
        }
        policy = {
            "mode": "auto", "timeout_seconds": 120,
            "rules": [{
                "id": "implementation", "name": "Implementation", "enabled": True,
                "instruction": "Only accept a settled implementation request.",
            }],
            "sources": {"local-source": {
                "allowed": [{"id": "human:1"}], "scope": scope,
            }},
        }
        host.authorization.publish_policy(policy, actor={"kind": "human", "id": "policy-admin"})
        host.sources.receive_local("local-source", {
            "event_id": "service-revoke-event", "thread_id": thread_key,
            "text": "Fix the existing typo.",
            "occurred_at": "2026-11-06T12:00:00Z",
            "metadata": {"actor_id": "human:1", "actor_kind": "human", "addressed": "direct"},
        })
        first = await service.tick()
        self.assertEqual(first["accepted"], 1)
        self.assertEqual(len(first["routes"]), 1)
        route = first["routes"][0]["receipt"]
        mission_id = route["mission_id"]
        assignment_id = route["assignment_id"]

        paused = host.execution.control_mission(
            mission_id, "pause", request_id="revoke-test-pause", actor={"kind": "human"},
        )
        self.assertEqual(paused["state"], "applied")
        revoked_policy = {
            **policy,
            "sources": {"local-source": {
                "allowed": [{"id": "human:2"}], "scope": scope,
            }},
        }
        host.authorization.publish_policy(
            revoked_policy, actor={"kind": "human", "id": "policy-admin"},
        )
        with self.assertRaises(HarnessError) as denied:
            host.execution.control_mission(
                mission_id, "resume", request_id="revoke-test-resume",
                actor={"kind": "human"},
            )
        self.assertEqual(denied.exception.code, "superagent/mandate-unavailable")
        self.assertEqual(host.execution.mission_control(mission_id)["mode"], "paused")

        cancelled = host.execution.control_mission(
            mission_id, "cancel", request_id="revoke-test-cancel",
            actor={"kind": "human"},
        )
        self.assertEqual(cancelled["state"], "applied")
        stopped = await service.tick()
        self.assertEqual(stopped["controls"]["processed"], 1)
        self.assertEqual(host.execution.control_status(mission_id)["stop_state"], "settled")
        self.assertEqual(adapter.shared["stop_calls"], [assignment_id])
        with host._store.transaction() as db:
            mission = db.execute(
                "SELECT state FROM missions WHERE mission_id=?", (mission_id,),
            ).fetchone()
            assignment = db.execute(
                "SELECT state FROM assignments WHERE assignment_id=?", (assignment_id,),
            ).fetchone()
        self.assertEqual(mission["state"], "cancelled")
        self.assertEqual(assignment["state"], "interrupted")

        settled = await service.tick()
        self.assertEqual(settled["controls"]["processed"], 0)
        self.assertEqual(adapter.shared["stop_calls"], [assignment_id])
        await service.aclose()
        self.assertTrue(host._closed)
        self.assertTrue(adapter.closed)

    async def test_reconciliation_rotates_over_backlog_using_persisted_watchdog_cursor(self):
        f = self.fixture
        origin_a, accepted_a, _ = f.accept_event()
        started_a = await f.host.execution.route_trigger(accepted_a["ticket_id"], origin_a["trigger_id"])
        origin_b, accepted_b, _ = f.accept_event(
            event_id="origin-b", thread_key="work-thread-b",
        )
        started_b = await f.host.execution.route_trigger(accepted_b["ticket_id"], origin_b["trigger_id"])

        first = await f.host.execution.reconcile_active(limit=1)
        self.assertEqual(first["processed"], 1)
        first_assignment_id = first["assignments"][0]["assignment_id"]
        first_assignment = f.host._execution_service._assignment(first_assignment_id)
        first_mission_id = first_assignment["mission_id"]
        f.host.watchdog.observe(first_mission_id, activity={"status": "running"}, now="2026-11-06T12:00:00Z")

        second = await f.host.execution.reconcile_active(limit=1)
        self.assertEqual(second["processed"], 1)
        second_assignment_id = second["assignments"][0]["assignment_id"]
        self.assertNotEqual(second_assignment_id, first_assignment_id)
        second_assignment = f.host._execution_service._assignment(second_assignment_id)
        second_mission_id = second_assignment["mission_id"]
        self.assertEqual(
            f.host.execution.active_mission_states(limit=2)[0]["mission_id"],
            second_mission_id,
        )

        for mission_id, suffix in (
            (started_a["mission_id"], "a"), (started_b["mission_id"], "b"),
        ):
            f.host.execution.control_mission(
                mission_id, "cancel", request_id=f"fair-cancel-{suffix}",
                actor={"kind": "human"},
            )
        f.shared["fail_stop"] = True
        first_control = await f.host.execution.reconcile_controls(limit=1)
        self.assertEqual(first_control["processed"], 1)
        first_control_mission = first_control["missions"][0]["mission_id"]
        self.assertEqual(first_control_mission, second_mission_id)
        f.host.watchdog.observe(
            second_mission_id, activity={"status": "running"}, now="2026-11-06T12:00:01Z",
        )
        second_control = await f.host.execution.reconcile_controls(limit=1)
        self.assertEqual(second_control["processed"], 1)
        self.assertEqual(second_control["missions"][0]["mission_id"], first_mission_id)

    async def test_unified_service_serve_failure_is_preserved_after_resource_close(self):
        f = self.fixture
        instance_id = "phase4-unified-service-failure"
        adapter = fixtures.FakeExecutionAdapter(instance_id, f.profile)
        service = await SuperagentService.open_coordinator(
            f.root / "service-v2-failure", instance_id=instance_id,
            identity={"project": "harness", "suite": "phase4.2.failure"},
            executor_adapter=adapter, local_identity_verifier=f._local_identity,
            interval=0.01, once_timeout=5,
        )
        host = service.coordinator

        def fail_reconciliation(*, limit=100):
            raise HarnessError(
                "Injected coordinator reconciliation failure",
                code="superagent/runtime-test-failure",
            )

        host._execution_service.reconcile_controls = fail_reconciliation
        try:
            with self.assertRaises(HarnessError) as caught:
                await service.serve(asyncio.Event(), once=True)
            self.assertEqual(caught.exception.code, "superagent/runtime-test-failure")
        finally:
            try:
                await service.aclose()
            except BaseException:
                pass

        self.assertEqual(service.coordinator_status["runtime_state"], "failed")
        self.assertEqual(
            service.coordinator_status["last_error_code"],
            "superagent/runtime-test-failure",
        )
        self.assertTrue(host._closed)
        self.assertTrue(adapter.closed)
        self.assertTrue(adapter.shared["async_closed"])


class TwoRootJoinPlanner:
    """Deterministic test Planner for two independent roots and one delivered join."""

    def plan(self, *, trigger_id, profile, prompt):
        def node(node_id, instruction, dependencies=()):
            return {
                "node_id": node_id, "role": "work", "profile_id": profile.profile_id,
                "instruction_ref": f"trigger:{trigger_id}:{node_id}",
                "instruction": instruction,
                "instruction_hash": fixtures._hash(instruction),
                "dependencies": list(dependencies),
            }

        return {
            "plan": {"nodes": [
                node("source-a", "Prepare the first independent workstream."),
                node("source-b", "Prepare the second independent workstream."),
                node("join", "Combine the frozen outputs from both workstreams.", [
                    {"node_id": "source-a", "requires_delivery": True},
                    {"node_id": "source-b", "requires_delivery": True},
                ]),
            ]},
            "acceptance": {"kind": "work_dag", "schema_version": 1},
        }


class DagSchedulerExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixtures.SuperagentExecutionTests()
        self.fixture.planner = TwoRootJoinPlanner()
        await self.fixture.asyncSetUp()
        self.addAsyncCleanup(self.fixture.asyncTearDown)

    def _rows(self, query, args=()):
        f = self.fixture
        with f.host._store.transaction() as db:
            return [dict(row) for row in db.execute(query, args).fetchall()]

    async def _start_dag(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event(
            text="Implement a change that requires two independent workstreams and a final integration.",
        )
        receipt = await f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        self.assertEqual(receipt["state"], "running")
        mission = self._rows("SELECT * FROM missions WHERE ticket_id=?", (accepted["ticket_id"],))[0]
        assignments = self._rows(
            "SELECT assignment.*,job.node_id FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "WHERE job.mission_id=? ORDER BY job.node_id",
            (mission["mission_id"],),
        )
        self.assertEqual({row["node_id"] for row in assignments}, {"source-a", "source-b"})
        self.assertEqual(len(assignments), 2)
        return accepted, mission, assignments

    def _create_pending_dag(self, ticket_id, trigger_id):
        f = self.fixture
        service = f.host._execution_service
        ticket = service._authorization.ticket(ticket_id)
        trigger = service._sources.get_trigger(trigger_id)
        input_row = service._input(ticket_id, trigger_id)
        mandate, profile, budget = service._mandate_context(ticket, trigger, input_row, require_work=False)
        prompt, _ = service._build_prompt(ticket, trigger, mandate)
        mission, _ = service._create_mission_plan(ticket, trigger, mandate, profile, budget, prompt)
        return mission

    async def test_dag_initial_input_is_confirmed_for_each_independent_assignment(self):
        accepted, mission, roots = await self._start_dag()
        inputs = self._rows(
            "SELECT input_id,state FROM ticket_inputs WHERE ticket_id=?",
            (accepted["ticket_id"],),
        )
        self.assertEqual(len(inputs), 1)
        input_id = inputs[0]["input_id"]
        deliveries = self._rows(
            "SELECT input_id,assignment_id,state,delivery_key FROM input_deliveries "
            "WHERE input_id=? ORDER BY assignment_id", (input_id,),
        )
        self.assertEqual(len(deliveries), 2)
        self.assertEqual(
            {delivery["assignment_id"]: delivery["state"] for delivery in deliveries},
            {assignment["assignment_id"]: "applied" for assignment in roots},
        )
        self.assertEqual(
            {delivery["delivery_key"] for delivery in deliveries},
            {f"superagent-v2:input:{input_id}:{assignment['assignment_id']}" for assignment in roots},
        )
        self.assertEqual(inputs[0]["state"], "read")
        for assignment in roots:
            self.assertIn(input_id, self.fixture.shared["runs"][assignment["assignment_id"]]["input_ids"])

    async def test_dag_absent_original_run_rejects_only_its_own_input_delivery(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event(
            event_id="dag-input-absent-run", thread_key="dag-input-absent-run", max_turns=4,
        )
        mission = self._create_pending_dag(accepted["ticket_id"], origin["trigger_id"])
        original_submit = f.adapter.submit
        submit_count = 0

        async def submit_with_absent_second_run(worker, assignment, prompt, input_ids):
            nonlocal submit_count
            submit_count += 1
            if submit_count == 2:
                raise RuntimeError("native Run never accepted the second submission")
            return await original_submit(worker, assignment, prompt, input_ids)

        f.adapter.submit = submit_with_absent_second_run
        await f.host._execution_service._dag_scheduler.schedule_mission(mission["mission_id"])
        assignments = self._rows(
            "SELECT assignment.assignment_id,assignment.state FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "WHERE job.mission_id=? ORDER BY job.node_id",
            (mission["mission_id"],),
        )
        self.assertEqual([row["state"] for row in assignments], ["running", "rejected"])
        input_id = self._rows(
            "SELECT input_id FROM ticket_inputs WHERE ticket_id=?", (accepted["ticket_id"],),
        )[0]["input_id"]
        deliveries = self._rows(
            "SELECT assignment_id,state FROM input_deliveries WHERE input_id=?", (input_id,),
        )
        self.assertEqual(
            {row["assignment_id"]: row["state"] for row in deliveries},
            {assignments[0]["assignment_id"]: "applied", assignments[1]["assignment_id"]: "rejected"},
        )
        self.assertEqual(len(f.shared["submit_calls"]), 1)

    async def test_schedule_pending_runs_missions_concurrently_with_bounded_global_capacity(self):
        f = self.fixture
        first, first_accepted, _ = f.accept_event(
            event_id="dag-parallel-first", thread_key="dag-parallel-first", max_turns=4,
        )
        second, second_accepted, _ = f.accept_event(
            event_id="dag-parallel-second", thread_key="dag-parallel-second", max_turns=4,
        )
        third, third_accepted, _ = f.accept_event(
            event_id="dag-parallel-third", thread_key="dag-parallel-third", max_turns=4,
        )
        policy = f.host.authorization.current_policy()["policy"]
        policy["sources"]["local-source"]["scope"]["thread_ids"] = [
            f._thread_id("dag-parallel-first"),
            f._thread_id("dag-parallel-second"),
            f._thread_id("dag-parallel-third"),
        ]
        f.host.authorization.publish_policy(
            policy, actor={"kind": "human", "id": "policy-admin"},
        )
        missions = [
            self._create_pending_dag(first_accepted["ticket_id"], first["trigger_id"]),
            self._create_pending_dag(second_accepted["ticket_id"], second["trigger_id"]),
            self._create_pending_dag(third_accepted["ticket_id"], third["trigger_id"]),
        ]
        scheduler = f.host._execution_service._dag_scheduler
        original_start = scheduler._start_job
        original_submit = f.adapter.submit
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        other_started = asyncio.Event()
        first_mission_id = None
        other_mission_id = None
        other_start_count = 0
        first_submission_gated = False

        async def gate_first_submit(worker, assignment, prompt, input_ids):
            nonlocal first_submission_gated
            if worker["mission_id"] == first_mission_id and not first_submission_gated:
                first_submission_gated = True
                first_entered.set()
                await release_first.wait()
            return await original_submit(worker, assignment, prompt, input_ids)

        async def mark_first_mission(mission, *args, **kwargs):
            nonlocal first_mission_id, other_mission_id, other_start_count
            mission_id = mission["mission_id"]
            if first_mission_id is None:
                first_mission_id = mission_id
            result = await original_start(mission, *args, **kwargs)
            if mission_id != first_mission_id:
                other_mission_id = mission_id
                other_start_count += 1
                if other_start_count == 2:
                    other_started.set()
            return result

        scheduler._start_job = mark_first_mission
        f.adapter.submit = gate_first_submit
        pending = asyncio.create_task(scheduler.schedule_pending(limit=20))
        try:
            await asyncio.wait_for(first_entered.wait(), timeout=10)
            await asyncio.wait_for(other_started.wait(), timeout=20)
            first_count = self._rows(
                "SELECT COUNT(*) AS total FROM assignments AS assignment "
                "JOIN jobs AS job ON job.job_id=assignment.job_id WHERE job.mission_id=?",
                (first_mission_id,),
            )[0]["total"]
            other_count = self._rows(
                "SELECT COUNT(*) AS total FROM assignments AS assignment "
                "JOIN jobs AS job ON job.job_id=assignment.job_id WHERE job.mission_id=?",
                (other_mission_id,),
            )[0]["total"]
            third_mission_id = next(
                mission["mission_id"] for mission in missions
                if mission["mission_id"] not in {first_mission_id, other_mission_id}
            )
            third_count = self._rows(
                "SELECT COUNT(*) AS total FROM assignments AS assignment "
                "JOIN jobs AS job ON job.job_id=assignment.job_id WHERE job.mission_id=?",
                (third_mission_id,),
            )[0]["total"]
            self.assertEqual(first_count, 1)
            self.assertEqual(other_count, 2)
            self.assertEqual(third_count, 0)
            self.assertEqual(scheduler._count_active_assignments(), 3)
            self.assertEqual(scheduler._reserved_global, 1)
            self.assertEqual(scheduler._reserved_by_mission, {first_mission_id: 1})
        except BaseException:
            release_first.set()
            await pending
            raise
        release_first.set()
        result = await asyncio.wait_for(pending, timeout=30)

        self.assertEqual(result["errors"], [])
        self.assertEqual(result["scheduled"], 4)
        counts = {
            row["mission_id"]: row["total"] for row in self._rows(
                "SELECT job.mission_id,COUNT(*) AS total FROM assignments AS assignment "
                "JOIN jobs AS job ON job.job_id=assignment.job_id GROUP BY job.mission_id",
            )
        }
        self.assertEqual(counts, {first_mission_id: 2, other_mission_id: 2})
        self.assertEqual(counts.get(third_mission_id, 0), 0)
        self.assertEqual(scheduler._count_active_assignments(), 4)
        self.assertEqual(scheduler._reserved_global, 0)
        self.assertEqual(scheduler._reserved_by_mission, {})

        completed_assignment = self._rows(
            "SELECT assignment.assignment_id FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "WHERE job.mission_id=? AND assignment.state='running' ORDER BY job.node_id LIMIT 1",
            (first_mission_id,),
        )[0]["assignment_id"]
        f.adapter.finish(completed_assignment, {"summary": "First workstream completed"})
        settled = await f.host.execution.reconcile(completed_assignment)
        self.assertEqual(settled["state"], "succeeded")
        await scheduler.schedule_pending(limit=20)
        third_count = self._rows(
            "SELECT COUNT(*) AS total FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id WHERE job.mission_id=?",
            (third_mission_id,),
        )[0]["total"]
        self.assertEqual(third_count, 1)
        self.assertEqual(scheduler._count_active_assignments(), 4)
        self.assertEqual(scheduler._reserved_global, 0)
        self.assertEqual(scheduler._reserved_by_mission, {})

    async def test_dag_uses_shared_scheduler_freezes_base_and_applies_every_required_delivery(self):
        f = self.fixture
        accepted, mission, roots = await self._start_dag()
        frozen_base = mission["base_commit"]
        self.assertEqual(frozen_base, "a" * 40)

        # A later source HEAD change must not alter the already frozen Mission base.
        f.shared["base_commit"] = "b" * 40
        for assignment in roots:
            f.adapter.finish(assignment["assignment_id"], {"summary": assignment["node_id"]})
            result = await f.host.execution.reconcile(assignment["assignment_id"])
            self.assertEqual(result["state"], "succeeded")

        self.assertEqual(
            {row["node_id"]: row["state"] for row in self._rows(
                "SELECT node_id,state FROM jobs WHERE mission_id=?", (mission["mission_id"],)
            )},
            {"source-a": "done", "source-b": "done", "join": "pending"},
        )
        scheduled = await f.host.execution.schedule_dag_missions(limit=20)
        self.assertGreaterEqual(scheduled["scheduled"], 1)

        join = self._rows(
            "SELECT assignment.*,job.node_id,worker.base_commit FROM assignments AS assignment "
            "JOIN jobs AS job ON job.job_id=assignment.job_id "
            "JOIN workers AS worker ON worker.worker_id=assignment.worker_id "
            "WHERE job.mission_id=? AND job.node_id='join'",
            (mission["mission_id"],),
        )
        self.assertEqual(len(join), 1)
        self.assertEqual(join[0]["state"], "running")
        self.assertEqual(join[0]["base_commit"], frozen_base)
        deliveries = self._rows(
            "SELECT state FROM deliveries WHERE target_assignment_id=? ORDER BY artifact_id",
            (join[0]["assignment_id"],),
        )
        self.assertEqual([row["state"] for row in deliveries], ["applied", "applied"])
        self.assertEqual(len(f.shared["artifact_capture_calls"]), 2)
        self.assertEqual(len(f.shared["artifact_import_calls"]), 1)
        self.assertEqual(len(f.shared["artifact_import_calls"][0][1]), 2)

        f.adapter.finish(join[0]["assignment_id"], {"summary": "Integrated output"})
        completed = await f.host.execution.reconcile(join[0]["assignment_id"])
        self.assertEqual(completed["state"], "succeeded")
        mission_after = self._rows(
            "SELECT state FROM missions WHERE mission_id=?", (mission["mission_id"],),
        )[0]
        self.assertEqual(mission_after["state"], "integrating")
        self.assertEqual(self._rows(
            "SELECT * FROM artifacts WHERE assignment_id IN "
            "(SELECT assignment_id FROM assignments WHERE job_id IN "
            "(SELECT job_id FROM jobs WHERE mission_id=?))", (mission["mission_id"],),
        ).__len__(), 3)

    async def test_dag_capture_waits_for_confirmed_tools_and_settled_background_writes(self):
        f = self.fixture
        _, mission, roots = await self._start_dag()
        assignment_id = roots[0]["assignment_id"]
        f.adapter.finish(assignment_id, {"summary": "Work completed"})
        snapshot = f.shared["runs"][assignment_id]
        snapshot["unconfirmed_tools"] = 1
        snapshot["unsettled_background"] = 1
        f.shared["runs"][assignment_id] = snapshot

        uncertain = await f.host.execution.reconcile(assignment_id)
        self.assertEqual(uncertain["state"], "unknown")
        self.assertEqual(f.shared["artifact_capture_calls"], [])
        self.assertEqual(
            self._rows("SELECT state FROM missions WHERE mission_id=?", (mission["mission_id"],))[0]["state"],
            "unknown",
        )

        snapshot["unconfirmed_tools"] = 0
        capturing = await f.host.execution.reconcile(assignment_id)
        self.assertEqual(capturing["state"], "capturing")
        self.assertEqual(f.shared["artifact_capture_calls"], [])

        snapshot["unsettled_background"] = 0
        completed = await f.host.execution.reconcile(assignment_id)
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(f.shared["artifact_capture_calls"], [
            self._rows(
                "SELECT worker.worker_id FROM assignments AS assignment "
                "JOIN workers AS worker ON worker.worker_id=assignment.worker_id "
                "WHERE assignment.assignment_id=?", (assignment_id,),
            )[0]["worker_id"],
        ])
        self.assertIsNotNone(f.host.artifacts.get(assignment_id))

    async def test_dag_lost_submit_ack_recovers_original_runs_without_replay(self):
        f = self.fixture
        origin, accepted, _ = f.accept_event(
            event_id="dag-lost-submit-ack", thread_key="dag-lost-submit-ack", max_turns=4,
        )
        mission = self._create_pending_dag(accepted["ticket_id"], origin["trigger_id"])
        f.shared["fail_submit_ack"] = True
        scheduled = await f.host._execution_service._dag_scheduler.schedule_mission(mission["mission_id"])
        self.assertEqual([row["state"] for row in scheduled], ["running", "running"])
        self.assertEqual(len(f.shared["submit_calls"]), 2)
        assignments = self._rows(
            "SELECT assignment_id,run_id,state FROM assignments WHERE job_id IN "
            "(SELECT job_id FROM jobs WHERE mission_id=?)", (mission["mission_id"],),
        )
        self.assertEqual({row["state"] for row in assignments}, {"running"})
        self.assertEqual(len({row["run_id"] for row in assignments}), 2)
        await f.host.execution.schedule_dag_missions(limit=20)
        for assignment in assignments:
            await f.host.execution.reconcile(assignment["assignment_id"])
        self.assertEqual(len(f.shared["submit_calls"]), 2)

    async def test_dag_blocking_rolls_back_assignment_and_job_if_input_rejection_fails(self):
        f = self.fixture
        _, mission, roots = await self._start_dag()
        for assignment in roots:
            f.adapter.finish(assignment["assignment_id"], {"summary": assignment["node_id"]})
            await f.host.execution.reconcile(assignment["assignment_id"])
        f.shared["artifact_conflict"] = True
        execution = f.host._execution_service
        scheduler = execution._dag_scheduler
        original = execution._input_deliveries.transition_in_transaction

        def fail_rejection(db, input_id, assignment_id, target, **kwargs):
            if target == "rejected":
                raise RuntimeError("injected input rejection failure")
            return original(db, input_id, assignment_id, target, **kwargs)

        execution._input_deliveries.transition_in_transaction = fail_rejection
        try:
            with self.assertRaisesRegex(RuntimeError, "injected input rejection failure"):
                await scheduler.schedule_mission(mission["mission_id"])
        finally:
            execution._input_deliveries.transition_in_transaction = original

        job = self._rows(
            "SELECT job_id,state FROM jobs WHERE mission_id=? AND node_id='join'",
            (mission["mission_id"],),
        )[0]
        assignment = self._rows(
            "SELECT assignment_id,state FROM assignments WHERE job_id=?", (job["job_id"],),
        )[0]
        self.assertEqual((job["state"], assignment["state"]), ("pending", "pending"))
        self.assertEqual(
            self._rows(
                "SELECT state FROM input_deliveries WHERE assignment_id=?",
                (assignment["assignment_id"],),
            )[0]["state"], "submitting",
        )
        await scheduler.schedule_mission(mission["mission_id"])
        self.assertEqual(
            self._rows("SELECT state FROM jobs WHERE job_id=?", (job["job_id"],))[0]["state"],
            "blocked",
        )
        self.assertEqual(
            self._rows(
                "SELECT state FROM input_deliveries WHERE assignment_id=?",
                (assignment["assignment_id"],),
            )[0]["state"], "rejected",
        )

    async def test_artifact_delivery_conflict_blocks_join_without_overwriting_original_artifacts(self):
        f = self.fixture
        accepted, mission, roots = await self._start_dag()
        for assignment in roots:
            f.adapter.finish(assignment["assignment_id"], {"summary": assignment["node_id"]})
            await f.host.execution.reconcile(assignment["assignment_id"])

        f.shared["artifact_conflict"] = True
        await f.host.execution.schedule_dag_missions(limit=20)
        join_job = self._rows(
            "SELECT * FROM jobs WHERE mission_id=? AND node_id='join'",
            (mission["mission_id"],),
        )[0]
        self.assertEqual(join_job["state"], "blocked")
        self.assertEqual(
            [row["state"] for row in self._rows(
                "SELECT state FROM deliveries WHERE target_assignment_id IN "
                "(SELECT assignment_id FROM assignments WHERE job_id=?)", (join_job["job_id"],)
            )],
            ["conflict", "conflict"],
        )
        self.assertEqual(
            self._rows("SELECT state FROM missions WHERE mission_id=?", (mission["mission_id"],))[0]["state"],
            "blocked",
        )
        self.assertEqual(self._rows(
            "SELECT * FROM artifacts WHERE assignment_id IN "
            "(SELECT assignment_id FROM assignments WHERE job_id IN "
            "(SELECT job_id FROM jobs WHERE mission_id=?))", (mission["mission_id"],),
        ).__len__(), 2)
        self.assertEqual(f.shared["artifact_import_calls"], [])
        # 导入冲突发生在原 Run 之前，取消的节点不能遗留 submitting 输入。
        join_assignment = self._rows(
            "SELECT assignment_id,state,run_id FROM assignments WHERE job_id=?",
            (join_job["job_id"],),
        )[0]
        self.assertEqual(join_assignment["state"], "cancelled")
        self.assertIsNone(join_assignment["run_id"])
        input_row = self._rows(
            "SELECT input_id,state FROM ticket_inputs WHERE ticket_id=?", (accepted["ticket_id"],),
        )[0]
        self.assertEqual(input_row["state"], "read")
        receipts = self._rows(
            "SELECT assignment_id,state FROM input_deliveries WHERE input_id=?",
            (input_row["input_id"],),
        )
        self.assertEqual(
            {row["assignment_id"]: row["state"] for row in receipts},
            {**{assignment["assignment_id"]: "applied" for assignment in roots},
             join_assignment["assignment_id"]: "rejected"},
        )
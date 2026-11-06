"""统一资源归属和持久控制在真实执行边界的定向回归。"""
from __future__ import annotations

import asyncio
import copy
import unittest
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from harness.app.superagent_executor import HarnessExecutorAdapter
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

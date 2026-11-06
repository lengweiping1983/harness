"""统一资源归属和持久控制在真实执行边界的定向回归。"""
from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

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

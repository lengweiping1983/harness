"""Phase 4.5 已应用指令在统一 Scheduler 后续上下文中的真实消费。"""
from __future__ import annotations

import json
import unittest

from harness.core.errors import HarnessError
from harness.core.language import current_language
from tests import test_superagent_unified_checkpoints as fixtures
from tests.superagent_product_fixture import review_contract


class UnifiedInstructionContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stage = fixtures.UnifiedCheckpointTests()
        await self.stage.asyncSetUp()
        self.f = self.stage.f
        self.addAsyncCleanup(self.f.asyncTearDown)

    async def instruct(self, assignment, request_id, text, *, application="applied"):
        mission = self.stage.rows("SELECT goal_ref FROM missions WHERE mission_id=?", (self.stage.mission_id,))[0]
        result = await self.f.host.execution.deliver_instruction(
            self.stage.mission_id, assignment["job_id"], assignment["assignment_id"],
            request_id, mission["goal_ref"], text,
        )
        key = f"superagent-v2:instruction:{request_id}:{assignment['assignment_id']}"
        if application != "pending":
            self.f.shared["instruction_receipts"][key].update(
                status=application, applied_seq=2 if application == "applied" else None,
            )
            result = await self.f.host.execution.reconcile_instruction(request_id)
        self.assertEqual(result["application"], application)
        return result

    async def test_only_applied_original_instructions_reach_own_stage_and_system_roles(self):
        first = await self.stage.start()
        await self.instruct(first, "pending", "Do not carry this pending instruction", application="pending")
        await self.instruct(first, "unapplied", "Do not carry this unapplied instruction", application="unapplied")
        await self.instruct(first, "confirmed", "Keep the public function name stable")
        context = self.f.host.execution.instruction_context(self.stage.mission_id, job_id=first["job_id"])
        self.assertEqual(context["total"], 1)
        self.assertEqual([item["request_id"] for item in context["items"]], ["confirmed"])
        self.assertEqual(context["items"][0]["run_id"], first["run_id"])
        await self.stage.finish(first)
        await self.stage.schedule()
        self.assertIn("Keep the public function name stable", self.stage.prompts[-1])
        self.assertNotIn("Do not carry this", self.stage.prompts[-1])
        second = self.stage.rows("SELECT * FROM assignments WHERE job_id=? AND attempt_no=2", (first["job_id"],))[0]
        await self.stage.finish(second, "ready")
        await self.stage.schedule()
        join = self.stage.rows("SELECT a.* FROM assignments a JOIN jobs j ON j.job_id=a.job_id WHERE j.node_id='join'")[0]
        self.assertNotIn("Keep the public function name stable", self.stage.prompts[-1])
        await self.stage.finish(join, "ready")
        await self.stage.schedule()
        self.assertIn("Keep the public function name stable", self.stage.prompts[-1])
        self.assertNotIn("Do not carry this", self.stage.prompts[-1])
        integrator = self.stage.rows("SELECT a.* FROM assignments a JOIN jobs j ON j.job_id=a.job_id WHERE j.role='integrate'")[0]
        await self.stage.finish(integrator, "ready")
        await self.stage.schedule()
        self.assertIn("Host-owned independent Reviewer:", self.stage.prompts[-1])
        self.assertIn("Keep the public function name stable", self.stage.prompts[-1])
        self.assertEqual(len(self.f.shared["instruction_calls"]), 3)

    async def test_restart_preserves_applied_context_without_steering_the_new_run_again(self):
        first = await self.stage.start()
        await self.instruct(first, "restart", "Keep the regression case after restart")
        await self.stage.finish(first)
        self.f._reopen_with_system_profiles(fixtures._StagedPlanner())
        context = self.f.host.execution.instruction_context(self.stage.mission_id, job_id=first["job_id"])
        self.assertEqual(context["items"][0]["text"], "Keep the regression case after restart")
        await self.stage.schedule()
        second = self.stage.rows("SELECT * FROM assignments WHERE job_id=? AND attempt_no=2", (first["job_id"],))[0]
        prompt = json.loads(second["report_json"])["pending_prompt"]
        self.assertIn("Keep the regression case after restart", prompt)
        self.assertEqual(len(self.f.shared["instruction_calls"]), 1)
        self.assertNotEqual(second["run_id"], first["run_id"])

    async def test_context_is_bounded_keeps_newest_correction_and_preserves_original_text(self):
        first = await self.stage.start()
        for index in range(17):
            await self.instruct(first, f"old-{index}", f"Original instruction number {index}")
        newest = "最新修改保持原样" * 2048
        await self.instruct(first, "newest", newest)
        before = list(self.f.shared["instruction_calls"])
        context = self.f.host.execution.instruction_context(self.stage.mission_id, job_id=first["job_id"])
        self.assertEqual(context["total"], 18)
        self.assertTrue(context["has_more"])
        self.assertTrue(context["text_truncated"])
        self.assertLessEqual(len(json.dumps(context, ensure_ascii=False).encode()), 16384)
        self.assertEqual(context["items"][-1]["request_id"], "newest")
        self.assertTrue(context["items"][-1]["text_truncated"])
        original = self.f.host._mission_instruction_service.get("newest")
        self.assertEqual(original["payload"]["text"], newest)
        context["items"][-1]["text"] = "Caller modification"
        reread = self.f.host.execution.instruction_context(self.stage.mission_id, job_id=first["job_id"])
        self.assertTrue(newest.startswith(reread["items"][-1]["text"]))
        self.assertEqual(self.f.shared["instruction_calls"], before)

    async def test_wrong_scope_is_refused_and_other_nodes_have_no_instruction_context(self):
        first = await self.stage.start()
        await self.instruct(first, "scope", "Keep this correction within its node")
        join = self.stage.rows("SELECT job_id FROM jobs WHERE node_id='join'")[0]
        self.assertEqual(self.f.host.execution.instruction_context(
            self.stage.mission_id, job_id=join["job_id"],
        )["items"], [])
        with self.assertRaises(HarnessError) as error:
            self.f.host.execution.instruction_context(self.stage.mission_id, job_id="job-other-mission")
        self.assertEqual(error.exception.code, "superagent/instruction-scope")
        origin, accepted, _ = self.f.accept_event(
            event_id="other-mission", thread_key="other-thread",
            tools=("write_file", "read_file"), max_turns=12,
        )
        other = await self.f.host.execution.route_trigger(accepted["ticket_id"], origin["trigger_id"])
        self.assertNotEqual(other["mission_id"], self.stage.mission_id)
        self.assertEqual(self.f.host.execution.instruction_context(other["mission_id"])["items"], [])
        with self.assertRaises(HarnessError):
            self.f.host.execution.instruction_context(other["mission_id"], job_id=first["job_id"])

    async def test_applied_context_resource_is_resolved_at_actual_stage_submission(self):
        token = current_language.set("en")
        try:
            first = await self.stage.start()
            await self.instruct(first, "language", "User text stays original")
            await self.stage.finish(first, "ready")
            await self.stage.schedule()
            join = self.stage.rows("SELECT a.* FROM assignments a JOIN jobs j ON j.job_id=a.job_id WHERE j.node_id='join'")[0]
            await self.stage.finish(join, "ready")
            await self.stage.schedule()
            self.assertIn("Confirmed applied node instructions are bounded task context", self.stage.prompts[-1])
            self.assertIn("Host-owned Integrator contract:", self.stage.prompts[-1])
            integrator = self.stage.rows("SELECT a.* FROM assignments a JOIN jobs j ON j.job_id=a.job_id WHERE j.role='integrate'")[0]
            await self.stage.finish(integrator, "ready")
            current_language.set("zh-CN")
            await self.stage.schedule()
            reviewer_prompt = self.stage.prompts[-1]
            self.assertIn("以下已确认应用的节点指令是有界任务上下文", reviewer_prompt)
            self.assertIn("User text stays original", reviewer_prompt)
            self.assertNotIn("Host-owned independent Reviewer:", reviewer_prompt)
            contract = review_contract({'messages':[{'content':reviewer_prompt}]})
            self.assertIsNotNone(contract)
            reviewer = self.stage.rows("SELECT a.* FROM assignments a JOIN jobs j ON j.job_id=a.job_id WHERE j.role='review'")[0]
            verdict = {
                "schema_version": 1, "candidate_artifact_id": contract["candidate_artifact_id"],
                "acceptance_hash": contract["acceptance_hash"], "verdict": "failed",
                "criteria": [{"id": item["id"], "status": "failed", "reason": "A bounded repair is needed",
                              "evidence_ids": [contract["available_evidence"][0]["evidence_id"]]}
                             for item in contract["criteria"]],
            }
            self.f.adapter.finish(reviewer["assignment_id"], verdict)
            await self.f.host.execution.reconcile(reviewer["assignment_id"])
            current_language.set("en")
            await self.stage.schedule()
            self.assertIn("Confirmed applied node instructions are bounded task context", self.stage.prompts[-1])
            self.assertNotIn("以下已确认应用的节点指令是有界任务上下文", self.stage.prompts[-1])
            self.assertIn("User text stays original", self.stage.prompts[-1])
            self.assertIn("Host-owned Integrator contract:", self.stage.prompts[-1])
            self.assertIn("Repair only the failed acceptance criteria", self.stage.prompts[-1])
            self.assertEqual(len(self.f.shared["instruction_calls"]), 1)
        finally:
            current_language.reset(token)

    async def test_corrupt_applied_payload_is_refused_without_rewriting_or_execution(self):
        first = await self.stage.start()
        await self.instruct(first, "corrupt", "Preserve this original payload")
        with self.f.host._store.transaction(write=True) as db:
            db.execute("DROP TRIGGER mission_instruction_identity_immutable")
            db.execute("DROP TRIGGER mission_instructions_transition_guard")
            db.execute("UPDATE mission_instructions SET payload_hash=? WHERE request_id='corrupt'", ("0" * 64,))
        before = list(self.f.shared["submit_calls"])
        with self.assertRaises(HarnessError) as caught:
            self.f.host.execution.instruction_context(self.stage.mission_id, job_id=first["job_id"])
        self.assertEqual(caught.exception.code, "superagent/instruction-corrupt")
        self.assertEqual(self.stage.rows("SELECT payload_hash FROM mission_instructions WHERE request_id='corrupt'")[0]["payload_hash"], "0" * 64)
        self.assertEqual(self.f.shared["submit_calls"], before)

    async def test_missing_native_status_is_corruption_instead_of_empty_context(self):
        first = await self.stage.start()
        await self.instruct(first, "missing-status", "Preserve the unconfirmed receipt", application="pending")
        with self.f.host._store.transaction(write=True) as db:
            db.execute("DROP TRIGGER mission_instructions_transition_guard")
            db.execute("UPDATE mission_instructions SET receipt_json='{}' WHERE request_id='missing-status'")
        with self.assertRaises(HarnessError) as caught:
            self.f.host.execution.instruction_context(self.stage.mission_id)
        self.assertEqual(caught.exception.code, "superagent/instruction-corrupt")
        self.assertEqual(self.stage.rows("SELECT receipt_json FROM mission_instructions WHERE request_id='missing-status'")[0]["receipt_json"], "{}")

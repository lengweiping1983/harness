"""统一服务的信道发送、原发送回查和生命周期回归。"""
from __future__ import annotations

import asyncio
import json

from harness.app.superagent_service import SuperagentService
from tests import test_superagent_execution as fixtures
from tests.superagent_delivery_fixture import DeliveryFixture


class UnifiedDeliveryTests(DeliveryFixture):
    async def test_local_result_is_projected_once_and_restart_does_not_execute_or_send_again(self):
        _, assignment = await self.start()
        status = await self.complete(assignment)
        self.assertEqual(status["outbox"]["sent"], 1)
        message = self.rows("SELECT * FROM outbox_messages")[0]
        self.assertEqual(message["state"], "sent")
        reply = list((self.service.root / "replies").glob("*.json"))
        self.assertEqual(len(reply), 1)
        self.assertEqual(json.loads(reply[0].read_text())["body"], "Verified result")
        before = list(self.adapter.shared["submit_calls"])
        await self.service.aclose()
        restarted = fixtures.FakeExecutionAdapter(self.f.instance_id, self.f.profile, self.adapter.shared)
        self.service = await self.open_service(restarted)
        self.f.host = self.service.coordinator
        await self.service.tick()
        self.assertEqual(restarted.shared["submit_calls"], before)
        self.assertEqual(len(list((self.service.root / "replies").glob("*.json"))), 1)
        self.assertEqual(self.rows("SELECT attempts FROM outbox_messages")[0]["attempts"], 1)


    async def test_lost_local_write_ack_is_reconciled_without_a_second_publish(self):
        _, assignment = await self.start()
        local = self.service._coordinator_delivery.local
        publish = local.publish
        calls = []

        async def lose(**kwargs):
            calls.append(kwargs["key"])
            await publish(**kwargs)
            raise RuntimeError("local acknowledgement lost")

        local.publish = lose
        status = await self.complete(assignment)
        self.assertEqual(status["outbox"]["unknown"], 1)
        self.assertEqual(self.rows("SELECT state FROM outbox_messages")[0]["state"], "sent")
        await self.service.tick()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(list((self.service.root / "replies").glob("*.json"))), 1)


    async def test_revocation_after_capture_prevents_queued_result_publication(self):
        _, assignment = await self.start()
        self.adapter.finish(assignment["assignment_id"], {"status": "completed", "summary": "Done", "reply": "Must not be sent"})
        await self.f.host.execution.reconcile(assignment["assignment_id"])
        self.assertEqual(len(self.rows("SELECT * FROM outbox_messages")), 1)
        source = self.f.host.sources.get_instance("local-source")
        self.f.host.sources.update_instance("local-source", expected_revision=source.config_revision, enabled=False)
        await self.service.tick()
        self.assertEqual(self.rows("SELECT state FROM outbox_messages")[0]["state"], "withdrawn")
        self.assertFalse(list((self.service.root / "replies").glob("*.json")))


    async def test_reply_binding_preserves_immutable_channel_and_requires_explicit_regrant(self):
        import sqlite3

        await self.start()
        source = self.f.host.sources.get_instance("local-source")
        channel = self.rows("SELECT * FROM channels")[0]
        channel_id = channel["channel_id"]
        self.assertTrue(self.f.host.sources.get_channel(channel_id)["capabilities"]["reply"])
        self.f.host.sources.set_channel_reply(channel_id, expected_source_revision=source.config_revision,
                                             enabled=False, actor={"kind": "test"})
        await self.service.tick()
        self.assertFalse(self.f.host.sources.get_channel(channel_id)["capabilities"]["reply"])
        self.assertEqual(self.rows("SELECT * FROM channels")[0], channel)
        disabled = self.f.host.sources.update_instance("local-source", expected_revision=source.config_revision, enabled=False)
        current = self.f.host.sources.update_instance("local-source", expected_revision=disabled.config_revision, enabled=True)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.f.host._store.transaction(write=True) as db:
                db.execute("UPDATE channel_reply_bindings SET enabled=1,aggregate_revision=aggregate_revision+1 WHERE channel_id=?", (channel_id,))
        await self.service.aclose()
        self.service = await self.open_service(self.adapter)
        self.f.host = self.service.coordinator
        await self.service.tick()
        self.assertFalse(self.f.host.sources.get_channel(channel_id)["capabilities"]["reply"])
        self.f.host.sources.set_channel_reply(channel_id, expected_source_revision=current.config_revision,
                                             enabled=True, actor={"kind": "test"})
        self.assertTrue(self.f.host.sources.get_channel(channel_id)["capabilities"]["reply"])
        self.assertEqual(self.rows("SELECT * FROM channels")[0], channel)


    async def test_attention_answer_starts_fresh_bounded_activity_with_original_question_context(self):
        _, assignment = await self.start()
        await self.complete(assignment, status="attention", reply="", question="Which behavior should be preserved?", options=["A", "B"])
        self.assertEqual(self.rows("SELECT state FROM missions")[0]["state"], "blocked")
        self.assertEqual(self.rows("SELECT kind,state FROM outbox_messages")[0], {"kind": "attention", "state": "sent"})
        answer = self.f.receive_followup("answer", text="Preserve behavior A.")
        calls = []
        submit = self.adapter.submit

        async def record(worker, current, prompt, inputs):
            calls.append(prompt)
            return await submit(worker, current, prompt, inputs)

        self.adapter.submit = record
        await self.service.tick()
        self.assertEqual(self.rows("SELECT state FROM attentions")[0]["state"], "answered")
        self.assertEqual(len(self.rows("SELECT * FROM missions")), 2)
        self.assertEqual(len(self.adapter.shared["submit_calls"]), 2)
        self.assertIn("Which behavior should be preserved?", calls[0])
        self.assertIn(answer["trigger_id"], calls[0])
        self.assertNotEqual(self.rows("SELECT run_id FROM assignments ORDER BY rowid")[0]["run_id"], self.rows("SELECT run_id FROM assignments ORDER BY rowid")[1]["run_id"])


    async def test_handback_settles_only_original_followup_and_restart_never_redelivers_it(self):
        origin, assignment = await self.start()
        followup = self.f.receive_followup("handback-followup")
        await self.service.tick()
        self.assertEqual(len(self.adapter.shared["input_calls"]), 1)
        await self.complete(assignment, status="handback", reply="",
                            handback_reason="Another owner must handle this follow-up.",
                            handback_trigger_id=followup["trigger_id"])
        states = {row["trigger_id"]: row["state"] for row in self.rows("SELECT trigger_id,state FROM ticket_inputs")}
        self.assertEqual(states, {origin["trigger_id"]: "read", followup["trigger_id"]: "handed_back"})
        self.assertFalse(self.rows("SELECT * FROM attentions"))
        await self.service.aclose()
        self.service = await self.open_service(self.adapter)
        self.f.host = self.service.coordinator
        await self.service.tick()
        self.assertEqual(len(self.adapter.shared["input_calls"]), 1)
        self.assertEqual(len(self.adapter.shared["submit_calls"]), 1)
        self.assertEqual(len(self.rows("SELECT * FROM missions")), 1)
        self.assertEqual(self.rows("SELECT state FROM ticket_inputs WHERE trigger_id=?", (followup["trigger_id"],))[0]["state"], "handed_back")


    async def test_github_lost_post_ack_recovers_after_restart_without_second_post(self):
        await self._remote_restart("github")


    async def test_feishu_lost_post_ack_recovers_after_restart_without_second_post(self):
        await self._remote_restart("feishu")


    async def test_wrong_github_token_owner_never_posts_or_claims_send(self):
        await self._wrong_provider_identity('github')


    async def test_wrong_feishu_bot_never_posts_or_claims_send(self):
        await self._wrong_provider_identity('feishu')


    async def test_revocation_while_credentials_are_pending_prevents_late_provider_post(self):
        provider, assignment, source_id = await self.remote("feishu")
        entered, release = asyncio.Event(), asyncio.Event()
        provider.barrier = (entered, release)
        task = asyncio.create_task(self.complete(assignment))
        await asyncio.wait_for(entered.wait(), 10)
        source = self.f.host.sources.get_instance(source_id)
        self.f.host.sources.update_instance(source_id, expected_revision=source.config_revision, enabled=False)
        release.set()
        await task
        self.assertEqual(provider.posts, 0)
        self.assertEqual(self.rows("SELECT state FROM outbox_messages")[0]["state"], "unknown")
        await self.service.tick()
        self.assertEqual(provider.posts, 0)


    async def test_close_waits_for_owned_send_and_never_starts_late_post(self):
        provider, assignment, _ = await self.remote("github")
        entered, release = asyncio.Event(), asyncio.Event()
        provider.barrier = (entered, release)
        task = asyncio.create_task(self.complete(assignment))
        await asyncio.wait_for(entered.wait(), 10)
        close = asyncio.create_task(self.service.aclose())
        await asyncio.sleep(0)
        self.assertFalse(close.done())
        release.set()
        await task
        await close
        self.assertEqual(provider.posts, 0)
        self.assertEqual(self.service.coordinator_status["runtime_state"], "stopped")


    async def test_github_followup_uses_original_delivery_key_and_never_starts_extra_mission(self):
        await self._remote_followup("github")


    async def test_feishu_followup_keeps_original_reply_target_without_duplicate_result(self):
        await self._remote_followup("feishu")


    async def test_periodic_instruction_reconciliation_tracks_application_without_resubmission(self):
        origin, assignment = await self.start()
        mission = self.rows("SELECT mission_id FROM missions")[0]["mission_id"]
        row = await self.f.host.execution.deliver_instruction(mission, assignment["job_id"], assignment["assignment_id"], "instruction-1", origin["trigger_id"], "Preserve the public name")
        self.assertEqual(row["application"], "pending")
        await self.f.host.execution.deliver_instruction(mission, assignment["job_id"], assignment["assignment_id"], "instruction-2", origin["trigger_id"], "Preserve the public flags")
        keys = self.adapter.shared["instruction_calls"]
        self.adapter.shared["instruction_receipts"][keys[1]].update(status="applied", applied_seq=2)
        first = await self.f.host.execution.reconcile_instructions(limit=1)
        self.assertTrue(first["backlog"])
        self.assertEqual(first["results"][0]["application"], "pending")
        second = await self.f.host.execution.reconcile_instructions(limit=1)
        self.assertEqual(second["results"][0]["request_id"], "instruction-2")
        self.assertEqual(second["results"][0]["application"], "applied")
        self.adapter.shared["instruction_receipts"][keys[0]].update(status="applied", applied_seq=3)
        status = await self.service.tick()
        self.assertEqual(status["instructions"]["results"][0]["application"], "applied")
        await self.service.tick()
        self.assertEqual(len(self.adapter.shared["instruction_calls"]), 2)


    async def test_real_github_http_transport_sends_then_confirms_original_comment(self):
        from harness.app.superagent_github_http import GitHubClient
        provider, assignment, source_id = await self.remote("github")
        root, calls = await self.loopback(provider)
        provider.root = root
        self.service._coordinator_delivery.clients[source_id] = GitHubClient(root, "fixture-only-token")
        status = await self.complete(assignment)
        self.assertEqual(status["outbox"]["sent"], 1)
        self.assertEqual(provider.posts, 1)
        self.assertEqual([m for m, _, _ in calls], ["GET", "GET", "POST", "GET"])
        self.assertEqual(calls[0][2]["Authorization"], "Bearer fixture-only-token")


    async def test_real_feishu_token_refresh_rechecks_revocation_before_http_reply(self):
        from harness.app.superagent_feishu_http import FeishuClient
        provider, assignment, source_id = await self.remote("feishu")
        entered, release = asyncio.Event(), asyncio.Event()
        root, calls = await self.loopback(provider, token_barrier=(entered, release))
        self.service._coordinator_delivery.clients[source_id] = FeishuClient({"api_url": root, "app_id": "cli_fixture"}, "fixture-secret")
        task = asyncio.create_task(self.complete(assignment))
        await asyncio.wait_for(entered.wait(), 10)
        source = self.f.host.sources.get_instance(source_id)
        self.f.host.sources.update_instance(source_id, expected_revision=source.config_revision, enabled=False)
        release.set()
        await task
        self.assertEqual(provider.posts, 0)
        self.assertFalse(any(path.endswith("/reply") for _, path, _ in calls))


    async def test_explicit_cross_source_subscription_keeps_original_run_and_each_reply_target(self):
        from dataclasses import replace
        from harness.app.superagent_coordinator_store import _hash, _json
        await self.service.aclose()
        self.f.profile = replace(self.f.profile, source_instance_ids=('local-source', 'local-other'))
        self.adapter = fixtures.FakeExecutionAdapter(self.f.instance_id, self.f.profile)
        self.service = await self.open_service(self.adapter)
        self.f.host, self.f.adapter, self.f.shared = self.service.coordinator, self.adapter, self.adapter.shared
        self.f.host.sources.create_instance('local-other', kind='local', config={'namespace':'other'})
        original_thread = 'thr_' + _hash(_json(['local-source', 'local/repo/work-thread']))
        other_thread = 'thr_' + _hash(_json(['local-other', 'local/other/remote-thread']))
        scope = {'source_instance_ids':['local-source', 'local-other'], 'thread_ids':[original_thread, other_thread],
                 'actions':['read', 'reply'], 'tools':[], 'budget':{'max_turns':10, 'max_seconds':60}}
        self.f.host.authorization.publish_policy({'mode':'auto', 'rules':[{'id':'implementation', 'name':'Work', 'enabled':True, 'instruction':'Only settled work.'}],
            'sources':{name:{'allowed':[{'id':'human:1'}], 'scope':scope} for name in ('local-source', 'local-other')}}, actor={'kind':'human'})
        other = self.f.host.sources.receive_local('local-other', {'event_id':'context', 'thread_id':'remote-thread', 'text':'Context only.',
            'occurred_at':'2026-11-08T00:00:00Z', 'metadata':{'actor_id':'bot:1', 'actor_kind':'bot', 'addressed':'none'}})
        origin = self.f.host.sources.receive_local('local-source', {'event_id':'origin', 'thread_id':'work-thread', 'text':'Fix the existing typo.',
            'occurred_at':'2026-11-08T00:01:00Z', 'metadata':{'actor_id':'human:1', 'actor_kind':'human', 'addressed':'direct'}})
        candidate = self.f.host.authorization.begin_evaluation(original_thread, actor={'kind':'host'})
        accepted = self.f.host.authorization.complete_evaluation(candidate['evaluation_id'], self.f._proposal('Fix the existing typo.'), actor={'kind':'assessor'})
        self.f.host.authorization.subscribe(accepted['ticket_id'], other_thread, mode='work', actor={'kind':'human'})
        await self.service.tick()
        assignment = self.rows('SELECT * FROM assignments')[0]
        followup = self.f.host.sources.receive_local('local-other', {'event_id':'followup', 'thread_id':'remote-thread', 'text':'Preserve the public name.',
            'occurred_at':'2026-11-08T00:02:00Z', 'metadata':{'actor_id':'human:1', 'actor_kind':'human', 'addressed':'direct'}})
        await self.service.tick()
        self.assertEqual(len(self.adapter.shared['input_calls']), 1)
        await self.complete(assignment)
        self.assertEqual(len(self.adapter.shared['submit_calls']), 1)
        self.assertEqual(len(self.rows('SELECT * FROM missions')), 1)
        messages = self.rows('SELECT * FROM outbox_messages')
        self.assertEqual([row['state'] for row in messages], ['sent', 'sent'])
        targets = {row['trigger_id'] for row in self.rows('SELECT * FROM outbox_reply_targets')}
        self.assertEqual(targets, {origin['trigger_id'], followup['trigger_id']})
        self.assertNotIn(other['trigger_id'], targets)
        await self.service.tick()
        self.assertEqual(len(self.rows('SELECT * FROM outbox_messages')), 2)

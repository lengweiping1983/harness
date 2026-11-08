"""统一服务的信道发送、原发送回查和生命周期回归。"""
from __future__ import annotations

import asyncio
import json
import unittest

from harness.app.superagent_service import SuperagentService
from tests import test_superagent_execution as fixtures


class UnifiedDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = fixtures.SuperagentExecutionTests()
        await self.f.asyncSetUp()
        self.addAsyncCleanup(self.f.asyncTearDown)
        self.f.host.close()
        self.adapter = fixtures.FakeExecutionAdapter(self.f.instance_id, self.f.profile)
        self.service = await self.open_service(self.adapter)
        self.f.host = self.service.coordinator
        self.f.adapter = self.adapter
        self.f.shared = self.adapter.shared
        self.f.host.sources.create_instance("local-source", kind="local", config={"namespace": "repo"})

    async def open_service(self, adapter, **kwargs):
        service = await SuperagentService.open_coordinator(
            self.f.root / "delivery", instance_id=self.f.instance_id, identity={"suite": "delivery"},
            executor_adapter=adapter, local_identity_verifier=self.f._local_identity,
            interval=0.01, **kwargs,
        )
        self.addAsyncCleanup(service.aclose)
        return service

    def rows(self, sql, args=()):
        with self.f.host._store.transaction() as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]

    async def start(self):
        origin, accepted, thread = self.f.accept_event(max_turns=10)
        await self.service.tick()
        assignment = self.rows("SELECT * FROM assignments")[0]
        self.ticket = accepted["ticket_id"]
        return origin, assignment

    async def complete(self, assignment, **report):
        self.adapter.finish(assignment["assignment_id"], {"status": "completed", "summary": "Work verified", "reply": "Verified result", **report})
        return await self.service.tick()

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

    async def remote(self, kind):
        import base64
        import hashlib
        import hmac
        import time
        from dataclasses import replace
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        await self.service.aclose()
        self.f.profile = replace(self.f.profile, source_instance_ids=("local-source", kind + "-source"))
        provider = ControlledProvider(kind)
        adapter = fixtures.FakeExecutionAdapter(self.f.instance_id, self.f.profile)
        secrets = {"secret://webhook": b"webhook-fixture-secret", "secret://token": b"fixture-token", "secret://encrypt": b"fixture-encryption-secret"}
        self.service = await self.open_service(adapter, provider_clients={kind + "-source": provider}, secret_resolver=secrets.__getitem__)
        self.f.host, self.f.adapter, self.f.shared, self.adapter = self.service.coordinator, adapter, adapter.shared, adapter
        source_id = kind + "-source"
        if kind == "github":
            config = {"repository": "acme/project", "repository_id": 10, "bot_login": "bot", "bot_user_id": 42, "webhook_secret_ref": "secret://webhook"}
            external_thread, actor, text = "issue/7", "github:user:17", "@bot Fix the existing typo."
        else:
            config = {"app_id": "cli_fixture", "tenant_key": "tenant-fixture", "bot_open_id": "ou_bot", "chats": ["oc_chat"], "verification_token_ref": "secret://token", "encrypt_key_ref": "secret://encrypt"}
            external_thread, actor, text = "chat/oc_chat/root/om_origin", "feishu:user:ou_human", "Fix the existing typo."
        self.f.host.sources.create_instance(source_id, kind=kind, config=config)
        from harness.app.superagent_coordinator_store import _hash, _json
        thread_id = "thr_" + _hash(_json([source_id, external_thread]))
        self.f.host.authorization.publish_policy({
            "mode": "auto", "timeout_seconds": 120,
            "rules": [{"id": "implementation", "name": "Implementation", "enabled": True, "instruction": "Accept explicit work."}],
            "sources": {source_id: {"allowed": [{"id": actor}], "scope": {"source_instance_ids": [source_id], "thread_ids": [thread_id], "actions": ["read", "reply"], "tools": [], "budget": {"max_turns": 10, "max_seconds": 60}}}},
        }, actor={"kind": "human"})
        if kind == "github":
            data = {"action": "opened", "repository": {"id": 10, "full_name": "acme/project"}, "sender": {"id": 17, "login": "human", "type": "User"}, "issue": {"id": 70, "number": 7, "title": "Fix typo", "body": text, "created_at": "2026-11-08T00:00:00Z"}}
            raw = json.dumps(data).encode()
            headers = {"x-github-event": "issues", "x-github-delivery": "delivery-1", "x-hub-signature-256": "sha256=" + hmac.new(secrets["secret://webhook"], raw, hashlib.sha256).hexdigest()}
        else:
            data = {"schema": "2.0", "header": {"app_id": "cli_fixture", "tenant_key": "tenant-fixture", "token": "fixture-token", "event_id": "event-1", "event_type": "im.message.receive_v1", "event_time": "1794096000000"}, "event": {"message": {"chat_id": "oc_chat", "message_id": "om_origin", "root_id": "om_origin", "message_type": "text", "chat_type": "p2p", "content": json.dumps({"text": text})}, "sender": {"tenant_key": "tenant-fixture", "sender_type": "user", "sender_id": {"open_id": "ou_human"}}}}
            plain = json.dumps(data).encode()
            padding = 16 - len(plain) % 16
            encryptor = Cipher(algorithms.AES(hashlib.sha256(secrets["secret://encrypt"]).digest()), modes.CBC(b"f" * 16)).encryptor()
            ciphertext = b"f" * 16 + encryptor.update(plain + bytes([padding]) * padding) + encryptor.finalize()
            raw = json.dumps({"encrypt": base64.b64encode(ciphertext).decode()}).encode()
            stamp = str(int(time.time()))
            headers = {"x-lark-request-timestamp": stamp, "x-lark-request-nonce": "fixture", "x-lark-signature": hashlib.sha256(stamp.encode() + b"fixture" + secrets["secret://encrypt"] + raw).hexdigest()}
        received = self.service.receive_source(source_id, headers=headers, raw=raw, stream_key="webhook", cursor={"delivery": 1})
        self.remote_data, self.remote_secrets = data, secrets
        duplicate = self.service.source_ingress(source_id).receive(headers, raw, queued=True)
        self.assertEqual(duplicate["trigger_id"], received["trigger_id"])
        self.assertEqual(duplicate["state"], "duplicate")
        self.assertEqual(self.f.host.sources.get_cursor(source_id, "webhook")["cursor"], {"delivery": 1})
        evaluation = self.f.host.authorization.begin_evaluation(thread_id, actor={"kind": "host"})
        accepted = self.f.host.authorization.complete_evaluation(evaluation["evaluation_id"], self.f._proposal(text), actor={"kind": "assessor"})
        self.assertEqual(accepted["kind"], "accept")
        await self.service.tick()
        return provider, self.rows("SELECT * FROM assignments")[0], source_id

    async def test_github_lost_post_ack_recovers_after_restart_without_second_post(self):
        await self._remote_restart("github")

    async def test_feishu_lost_post_ack_recovers_after_restart_without_second_post(self):
        await self._remote_restart("feishu")

    async def _remote_restart(self, kind):
        provider, assignment, _ = await self.remote(kind)
        provider.lose_ack, provider.mode = True, "absent"
        status = await self.complete(assignment)
        self.assertEqual(status["outbox"]["unknown"], 1)
        self.assertEqual(self.rows("SELECT state FROM outbox_messages")[0]["state"], "unknown")
        self.assertEqual(provider.posts, 1)
        for mode in ("absent", "duplicate", "incomplete"):
            provider.mode = mode
            await self.service.tick()
            self.assertEqual(self.rows("SELECT state FROM outbox_messages")[0]["state"], "unknown", mode)
            self.assertEqual(provider.posts, 1)
        await self.service.aclose()
        provider.mode = "exact"
        adapter = fixtures.FakeExecutionAdapter(self.f.instance_id, self.f.profile, self.adapter.shared)
        self.service = await self.open_service(adapter, provider_clients={kind + "-source": provider}, secret_resolver=lambda _: b"unused-for-lookup")
        self.f.host = self.service.coordinator
        await self.service.tick()
        message = self.rows("SELECT * FROM outbox_messages")[0]
        self.assertEqual(message["state"], "sent")
        self.assertEqual(message["attempts"], 1)
        self.assertEqual(provider.posts, 1)
        self.assertEqual(len(adapter.shared["submit_calls"]), 1)
        self.assertEqual(len(self.rows("SELECT * FROM channels")), 1)

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

    def remote_followup(self, kind):
        import base64
        import copy
        import hashlib
        import hmac
        import time
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        data = copy.deepcopy(self.remote_data)
        if kind == "github":
            data["action"] = "created"
            data["comment"] = {"id": 71, "user": data["sender"], "body": "Also preserve the public name.", "created_at": "2026-11-08T00:00:01Z"}
            raw = json.dumps(data).encode()
            headers = {"x-github-event": "issue_comment", "x-github-delivery": "delivery-2", "x-hub-signature-256": "sha256=" + hmac.new(self.remote_secrets["secret://webhook"], raw, hashlib.sha256).hexdigest()}
        else:
            data["header"]["event_id"] = "event-2"
            data["event"]["message"].update(message_id="om_followup", content=json.dumps({"text": "Also preserve the public name."}))
            plain = json.dumps(data).encode()
            padding = 16 - len(plain) % 16
            encryptor = Cipher(algorithms.AES(hashlib.sha256(self.remote_secrets["secret://encrypt"]).digest()), modes.CBC(b"g" * 16)).encryptor()
            raw = json.dumps({"encrypt": base64.b64encode(b"g" * 16 + encryptor.update(plain + bytes([padding]) * padding) + encryptor.finalize()).decode()}).encode()
            stamp = str(int(time.time()))
            headers = {"x-lark-request-timestamp": stamp, "x-lark-request-nonce": "followup", "x-lark-signature": hashlib.sha256(stamp.encode() + b"followup" + self.remote_secrets["secret://encrypt"] + raw).hexdigest()}
        result = self.service.receive_source(kind + "-source", headers=headers, raw=raw)
        self.assertEqual(self.service.receive_source(kind + "-source", headers=headers, raw=raw)["state"], "duplicate")
        return result

    async def test_github_followup_uses_original_delivery_key_and_never_starts_extra_mission(self):
        await self._remote_followup("github")

    async def test_feishu_followup_keeps_original_reply_target_without_duplicate_result(self):
        await self._remote_followup("feishu")

    async def _remote_followup(self, kind):
        provider, assignment, _ = await self.remote(kind)
        followup = self.remote_followup(kind)
        self.adapter.shared["fail_input_ack"] = True
        await self.service.tick()
        self.assertEqual(len(self.adapter.shared["input_calls"]), 1)
        await self.service.tick()
        delivery = self.rows("SELECT d.* FROM input_deliveries d JOIN ticket_inputs i ON i.input_id=d.input_id WHERE i.trigger_id=?", (followup["trigger_id"],))[0]
        self.assertEqual(delivery["state"], "applied")
        self.assertEqual(len(self.adapter.shared["input_calls"]), 1)
        self.assertEqual(self.rows("SELECT state FROM ticket_inputs WHERE trigger_id=?", (followup["trigger_id"],))[0]["state"], "read")
        await self.complete(assignment)
        await self.service.tick()
        self.assertEqual(provider.posts, 1)
        self.assertEqual(len(self.rows("SELECT * FROM missions")), 1)
        if kind == "feishu":
            self.assertEqual(provider.objects[0]["parent_id"], "om_origin")
            self.assertEqual(len(self.rows("SELECT * FROM channels")), 2)
        self.assertEqual(self.rows("SELECT state FROM outbox_messages")[0]["state"], "sent")

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

    async def loopback(self, provider, *, token_barrier=None):
        calls = []
        async def handle(reader, writer):
            try:
                raw = await reader.readuntil(b"\r\n\r\n")
                lines = raw.decode().split("\r\n")
                method, path, _ = lines[0].split(" ")
                headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
                size = int(headers.get("Content-Length", "0"))
                body = json.loads(await reader.readexactly(size)) if size else None
                calls.append((method, path, headers))
                if path.endswith("tenant_access_token/internal"):
                    if token_barrier is not None:
                        token_barrier[0].set()
                        await token_barrier[1].wait()
                    value = {"code": 0, "expire": 3600, "tenant_access_token": "fixture-token"}
                else:
                    value = await provider.request(method, path, body, before_request=lambda: None)
                    if provider.kind == "feishu" and method == "POST":
                        value = {"code": 0, "data": value}
                payload = json.dumps(value).encode()
                status = 201 if provider.kind == "github" and method == "POST" else 200
                writer.write(f"HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        async def close_server():
            server.close()
            await server.wait_closed()
        self.addAsyncCleanup(close_server)
        return "http://127.0.0.1:" + str(server.sockets[0].getsockname()[1]), calls

    async def test_real_github_http_transport_sends_then_confirms_original_comment(self):
        from harness.app.superagent_github_http import GitHubClient
        provider, assignment, source_id = await self.remote("github")
        root, calls = await self.loopback(provider)
        provider.root = root
        self.service._coordinator_delivery.clients[source_id] = GitHubClient(root, "fixture-only-token")
        status = await self.complete(assignment)
        self.assertEqual(status["outbox"]["sent"], 1)
        self.assertEqual(provider.posts, 1)
        self.assertEqual([m for m, _, _ in calls], ["POST", "GET"])
        self.assertEqual(calls[0][2]["Authorization"], "Bearer fixture-only-token")

    async def test_real_feishu_token_refresh_rechecks_revocation_before_http_reply(self):
        from harness.app.superagent_feishu import FeishuClient
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



class ControlledProvider:
    """受控 REST Provider 保留远端对象，明确区分写入和回查。"""

    def __init__(self, kind):
        self.kind = kind
        self.root = "https://api.github.com"
        self.app_id = "cli_fixture"
        self.posts, self.objects, self.calls = 0, [], []
        self.mode, self.lose_ack, self.barrier = "exact", False, None

    async def request(self, method, path, data=None, *, before_request=None):
        self.calls.append((method, path, data))
        if method == "POST":
            if self.barrier is not None:
                entered, release = self.barrier
                entered.set()
                await release.wait()
            before_request()
            self.posts += 1
            if self.kind == "github":
                obj = {"id": 901, "body": data["body"], "user": {"id": 42}, "issue_url": self.root + "/repos/acme/project/issues/7", "created_at": "2026-11-08T00:01:00Z"}
            else:
                self.uuid = data["uuid"]
                obj = {"message_id": "om_reply", "chat_id": "oc_chat", "parent_id": "om_origin", "root_id": "om_origin", "msg_type": "text", "deleted": False, "sender": {"sender_type": "app", "id_type": "app_id", "id": "cli_fixture", "tenant_key": "tenant-fixture"}, "body": {"content": data["content"]}, "create_time": "1794096060000"}
            self.objects.append(obj)
            if self.lose_ack:
                self.lose_ack = False
                raise RuntimeError("POST accepted; response lost")
            return obj
        if method != "GET":
            raise AssertionError(method)
        items = [] if self.mode == "absent" else list(self.objects)
        if self.mode == "duplicate" and items:
            other = dict(items[0])
            other.update(id=902) if self.kind == "github" else other.update(message_id="om_duplicate")
            items.append(other)
        if self.kind == "github":
            if self.mode == "incomplete":
                return [{"id": i + 1000, "body": "other", "user": {"id": 42}} for i in range(100)]
            return items
        return {"code": 0, "data": {"items": items, "has_more": self.mode == "incomplete", "page_token": "next-" + str(len(self.calls))}}

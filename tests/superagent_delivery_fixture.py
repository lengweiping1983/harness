"""统一信道的隔离账本、原生端口和受控 Provider 夹具。"""
from __future__ import annotations

import asyncio
import json
import unittest

from harness.app.superagent_service import SuperagentService
from tests import test_superagent_execution as fixtures


class DeliveryFixture(unittest.IsolatedAsyncioTestCase):
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


    async def _wrong_provider_identity(self, kind):
        provider, assignment, _ = await self.remote(kind)
        provider.wrong_identity = True
        status = await self.complete(assignment)
        self.assertEqual(provider.posts, 0)
        self.assertEqual(status['outbox']['unavailable'], 1)
        self.assertEqual(status['outbox']['errors'][0]['code'], 'superagent/' + kind + '-scope')
        self.assertEqual(self.rows('SELECT state,attempts FROM outbox_messages')[0], {'state': 'pending', 'attempts': 0})


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


    def packet(self, kind, data, *, signed=True):
        """仅在隔离夹具中生成真实提供方格式，不读取外部凭据。"""
        import base64
        import hashlib
        import hmac
        import time
        if kind == 'github':
            raw = json.dumps(data).encode()
            event = 'issue_comment' if 'comment' in data else 'issues'
            return {'x-github-event': event, 'x-github-delivery': 'delivery-fixture',
                    'x-hub-signature-256': 'sha256=' + hmac.new(self.remote_secrets['secret://webhook'], raw, hashlib.sha256).hexdigest()}, raw
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        plain = json.dumps(data).encode()
        padding = 16 - len(plain) % 16
        encryptor = Cipher(algorithms.AES(hashlib.sha256(self.remote_secrets['secret://encrypt']).digest()), modes.CBC(b'p' * 16)).encryptor()
        raw = json.dumps({'encrypt': base64.b64encode(b'p' * 16 + encryptor.update(plain + bytes([padding]) * padding) + encryptor.finalize()).decode()}).encode()
        if not signed:
            return {}, raw
        stamp = str(int(time.time()))
        return {'x-lark-request-timestamp': stamp, 'x-lark-request-nonce': 'packet',
                'x-lark-signature': hashlib.sha256(stamp.encode() + b'packet' + self.remote_secrets['secret://encrypt'] + raw).hexdigest()}, raw


class ControlledProvider:
    """受控 REST Provider 保留远端对象，明确区分写入和回查。"""

    def __init__(self, kind):
        self.kind = kind
        self.root = "https://api.github.com"
        self.app_id = "cli_fixture"
        self.posts, self.objects, self.calls = 0, [], []
        self.mode, self.lose_ack, self.barrier = "exact", False, None
        self.wrong_identity = False

    async def request(self, method, path, data=None, *, before_request=None):
        self.calls.append((method, path, data))
        if method == 'GET' and path == '/user':
            return {'id': 999 if self.wrong_identity else 42, 'login': 'bot'}
        if method == 'GET' and path == '/repos/acme/project':
            return {'id': 10}
        if method == 'GET' and path == '/open-apis/bot/v3/info/':
            return {'code': 0, 'bot': {'open_id': 'ou_wrong' if self.wrong_identity else 'ou_bot'}}
        if method == 'GET' and path == '/open-apis/im/v1/messages/om_origin?user_id_type=open_id':
            return {'code': 0, 'data': {'items': [{'message_id': 'om_origin', 'chat_id': 'oc_chat', 'root_id': 'om_origin', 'deleted': False}]}}
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

"""GitHub / 飞书 Outbox Provider 回查适配器的只读沙箱回归。"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from harness.app.superagent_coordinator_store import CoordinatorStore
from harness.app.superagent_effect_recovery import EffectRecoveryService
from harness.app.superagent_outbox import OutboxEffectService
from harness.app.superagent_provider_adapters import (
    OutboxProviderLookupAdapter,
    ProviderChannel,
    feishu_outbox_body,
    feishu_outbox_request_uuid,
    github_outbox_body,
)

class _ProviderSandbox:
    """模拟 Provider REST 只读端点，并拒绝任何非 GET 请求。"""

    def __init__(self, responder, *, root=None):
        self.responder = responder
        self.root = root
        self.calls = []

    async def request(self, method, path, data=None):
        self.calls.append((method, path, data))
        if method != "GET" or data is not None:
            raise AssertionError("Provider lookup attempted a non-read-only request")
        return self.responder(method, path)

class OutboxProviderAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = CoordinatorStore(
            Path(temporary.name) / "superagent.sqlite3",
            instance_id="provider-adapter-test",
            identity={"profile": "provider-adapter"},
        )
        self.outbox = OutboxEffectService(self.store)
        self.channel_id = "channel-provider"
        self.message_id = "message-provider-1"
        self.idempotency_key = "outbox-provider-key-1"
        self.body_ref = "artifact://outbox/provider-1"
        self.body = "The requested work is complete."
        self.body_hash = hashlib.sha256(self.body.encode("utf-8")).hexdigest()
        with self.store.unit_of_work(actor={"kind": "human"}) as uow:
            ticket = uow.tickets.create({
                "ticket_id": "ticket-provider-1",
                "mandate_id": "mandate-provider-1",
                "origin_trigger_id": "trigger-provider-1",
                "title": "Provider lookup sandbox",
            })
            uow.outbox.create({
                "message_id": self.message_id,
                "ticket_id": ticket["ticket_id"],
                "channel_id": self.channel_id,
                "kind": "result",
                "body_ref": self.body_ref,
                "body_hash": self.body_hash,
                "idempotency_key": self.idempotency_key,
            })
        self.outbox.claim(
            owner="crashed-sender", limit=1, lease_until="2999-01-01T00:00:00Z",
        )
        with self.store.transaction(write=True) as db:
            db.execute(
                "UPDATE effects SET lease_until='2000-01-01T00:00:00.000Z' "
                "WHERE idempotency_key=?",
                (self.idempotency_key,),
            )
            expired = self.store.mark_expired_effects_unknown(db)
            self.assertEqual(len(expired), 1)

    def _channel(self, provider_id, client, *, address, identity):
        return ProviderChannel(
            provider_id=provider_id, client=client, address=address, identity=identity,
        )

    async def _recover(self, channel, *, body=None):
        adapter = OutboxProviderLookupAdapter(
            {self.channel_id: channel},
            lambda reference: self.body if body is None else body,
        )
        return await EffectRecoveryService(
            self.store, {"outbox.send": adapter},
        ).recover_async()

    def _states(self):
        with self.store.transaction() as db:
            effect = db.execute(
                "SELECT state,receipt_json FROM effects WHERE idempotency_key=?",
                (self.idempotency_key,),
            ).fetchone()
            message = db.execute(
                "SELECT state,provider_id,provider_key,sent_at FROM outbox_messages WHERE message_id=?",
                (self.message_id,),
            ).fetchone()
        return tuple(effect), tuple(message)

    async def test_github_exact_comment_receipt_completes_outbox_without_post(self):
        comment = {
            "id": 901,
            "body": github_outbox_body(self.body, self.idempotency_key),
            "user": {"id": 42},
            "issue_url": "https://api.github.com/repos/acme/project/issues/7",
            "created_at": "2026-11-04T12:30:00Z",
        }
        client = _ProviderSandbox(
            lambda _method, _path: [comment], root="https://api.github.com",
        )
        channel = self._channel(
            "github", client,
            address={"repository": "acme/project", "issue_number": 7},
            identity={"bot_user_id": 42},
        )
        report = await self._recover(channel)
        self.assertEqual((report.looked_up, report.completed, report.errors), (1, 1, 0))
        effect, message = self._states()
        self.assertEqual(effect[0], "completed")
        self.assertEqual(message[:3], ("sent", "901", self.idempotency_key))
        self.assertEqual(message[3], "2026-11-04T12:30:00.000Z")
        self.assertTrue(client.calls)
        self.assertTrue(all(method == "GET" for method, _, _ in client.calls))
        self.assertIn("/repos/acme/project/issues/7/comments", client.calls[0][1])

    async def test_github_absence_and_duplicate_matches_remain_unknown(self):
        absent_client = _ProviderSandbox(lambda _method, _path: [], root="https://api.github.com")
        absent = await self._recover(self._channel(
            "github", absent_client,
            address={"repository": "acme/project", "issue_number": 7},
            identity={"bot_user_id": 42},
        ))
        self.assertEqual((absent.left_unknown, absent.retry_wait), (1, 0))
        self.assertEqual(self._states()[0][0], "unknown")

        comment = {
            "id": 902,
            "body": github_outbox_body(self.body, self.idempotency_key),
            "user": {"id": 42},
            "issue_url": "https://api.github.com/repos/acme/project/issues/7",
            "created_at": "2026-11-04T12:31:00Z",
        }

        duplicate_client = _ProviderSandbox(

            lambda _method, _path: [comment, {**comment, "id": 903}],
            root="https://api.github.com",
        )
        duplicate = await self._recover(self._channel(
            "github", duplicate_client,
            address={"repository": "acme/project", "issue_number": 7},
            identity={"bot_user_id": 42},
        ))
        self.assertEqual((duplicate.left_unknown, duplicate.completed), (1, 0))
        self.assertEqual(self._states()[0][0], "unknown")

    async def test_github_boolean_author_id_cannot_match_numeric_bot_identity(self):
        comment = {
            "id": 904,
            "body": github_outbox_body(self.body, self.idempotency_key),
            "user": {"id": True},
            "issue_url": "https://api.github.com/repos/acme/project/issues/7",
            "created_at": "2026-11-04T12:32:00Z",
        }
        client = _ProviderSandbox(
            lambda _method, _path: [comment], root="https://api.github.com",
        )
        report = await self._recover(self._channel(
            "github", client,
            address={"repository": "acme/project", "issue_number": 7},
            identity={"bot_user_id": 1},
        ))
        self.assertEqual((report.errors, report.left_unknown, report.completed), (1, 1, 0))
        self.assertEqual(self._states()[0][0], "unknown")

    async def test_github_bounded_history_never_claims_absence(self):
        page = [{"body": "an unrelated comment"} for _ in range(100)]

        def response(_method, path):
            page_number = int(parse_qs(urlsplit(path).query)["page"][0])
            self.assertLessEqual(page_number, 10)
            return page

        client = _ProviderSandbox(response, root="https://api.github.com")
        report = await self._recover(self._channel(
            "github", client,
            address={"repository": "acme/project", "issue_number": 7},
            identity={"bot_user_id": 42},
        ))
        self.assertEqual((report.left_unknown, report.retry_wait), (1, 0))
        self.assertEqual(len(client.calls), 10)
        self.assertEqual(self._states()[0][0], "unknown")

    async def test_feishu_exact_thread_reply_receipt_completes_outbox_without_post(self):
        remote_id = "om_reply_1"
        created_ms = 1793795400000
        reply = {
            "chat_id": "oc_chat_1",
            "message_id": remote_id,
            "root_id": "om_root_1",
            "parent_id": "om_root_1",
            "msg_type": "text",
            "deleted": False,
            "body": {"content": json.dumps({
                "text": feishu_outbox_body(self.body, self.idempotency_key),
            })},
            "sender": {
                "sender_type": "app", "id_type": "app_id",
                "id": "cli_test_app", "tenant_key": "tenant-test",
            },
            "create_time": str(created_ms),
        }
        client = _ProviderSandbox(
            lambda _method, _path: {"data": {"items": [reply], "has_more": False}},
        )
        channel = self._channel(
            "feishu", client,
            address={"chat_id": "oc_chat_1", "message_id": "om_root_1", "root_id": "om_root_1"},
            identity={"app_id": "cli_test_app", "tenant_key": "tenant-test"},
        )
        report = await self._recover(channel)
        self.assertEqual((report.looked_up, report.completed, report.errors), (1, 1, 0))
        effect, message = self._states()
        self.assertEqual(effect[0], "completed")
        self.assertEqual(message[:3], ("sent", remote_id, self.idempotency_key))
        self.assertTrue(message[3].endswith("Z"))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0], "GET")
        self.assertIn("only_thread_root_messages=false", client.calls[0][1])

    async def test_feishu_absence_and_incomplete_pagination_remain_unknown(self):
        absent_client = _ProviderSandbox(
            lambda _method, _path: {"data": {"items": [], "has_more": False}},
        )
        absent = await self._recover(self._channel(
            "feishu", absent_client,
            address={"chat_id": "oc_chat_1", "message_id": "om_root_1", "root_id": "om_root_1"},
            identity={"app_id": "cli_test_app", "tenant_key": "tenant-test"},
        ))
        self.assertEqual((absent.left_unknown, absent.retry_wait), (1, 0))
        self.assertEqual(self._states()[0][0], "unknown")

        def more(_method, path):
            query = parse_qs(urlsplit(path).query)
            previous = query.get("page_token", [None])[0]
            page_number = 0 if previous is None else int(previous.split("-")[1])
            return {
                "data": {
                    "items": [], "has_more": True,
                    "page_token": f"page-{page_number + 1}",
                },
            }

        incomplete_client = _ProviderSandbox(more)
        incomplete = await self._recover(self._channel(
            "feishu", incomplete_client,
            address={"chat_id": "oc_chat_1", "message_id": "om_root_1", "root_id": "om_root_1"},
            identity={"app_id": "cli_test_app", "tenant_key": "tenant-test"},
        ))
        self.assertEqual((incomplete.left_unknown, incomplete.retry_wait), (1, 0))
        self.assertEqual(len(incomplete_client.calls), 10)
        self.assertEqual(self._states()[0][0], "unknown")

    async def test_feishu_repeated_page_token_is_a_lookup_error_not_absence(self):
        client = _ProviderSandbox(
            lambda _method, _path: {
                "data": {"items": [], "has_more": True, "page_token": "same-token"},
            },
        )
        report = await self._recover(self._channel(
            "feishu", client,
            address={"chat_id": "oc_chat_1", "message_id": "om_root_1", "root_id": "om_root_1"},
            identity={"app_id": "cli_test_app", "tenant_key": "tenant-test"},
        ))
        self.assertEqual((report.errors, report.left_unknown, report.retry_wait), (1, 1, 0))
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(self._states()[0][0], "unknown")

    async def test_body_hash_mismatch_stops_before_provider_request(self):
        client = _ProviderSandbox(lambda _method, _path: [])
        channel = self._channel(
            "github", client,
            address={"repository": "acme/project", "issue_number": 7},
            identity={"bot_user_id": 42},
        )
        report = await self._recover(channel, body="tampered body")
        self.assertEqual((report.errors, report.left_unknown), (1, 1))
        self.assertEqual(client.calls, [])
        self.assertEqual(self._states()[0][0], "unknown")

    async def test_missing_channel_and_provider_never_authorize_retry(self):
        adapter = OutboxProviderLookupAdapter({}, lambda _reference: self.body)
        report = await EffectRecoveryService(
            self.store, {"outbox.send": adapter},

        ).recover_async()

        self.assertEqual((report.left_unknown, report.retry_wait), (1, 0))
        self.assertEqual(self._states()[0][0], "unknown")

        client = _ProviderSandbox(lambda _method, _path: [])
        channel = self._channel(
            "unregistered-provider", client, address={}, identity={},
        )
        report = await self._recover(channel)
        self.assertEqual((report.left_unknown, report.retry_wait), (1, 0))
        self.assertEqual(client.calls, [])
        self.assertEqual(self._states()[0][0], "unknown")

    async def test_sync_recovery_refuses_async_adapter_without_running_it(self):
        client = _ProviderSandbox(lambda _method, _path: [], root="https://api.github.com")
        adapter = OutboxProviderLookupAdapter(
            {self.channel_id: self._channel(
                "github", client,
                address={"repository": "acme/project", "issue_number": 7},

                identity={"bot_user_id": 42},
            )},
            lambda _reference: self.body,
        )
        with self.assertRaises(RuntimeError):
            EffectRecoveryService(self.store, {"outbox.send": adapter}).recover()
        self.assertEqual(client.calls, [])
        self.assertEqual(self._states()[0][0], "unknown")

    def test_feishu_request_uuid_is_stable_for_original_idempotency_key(self):
        first = feishu_outbox_request_uuid(self.idempotency_key)
        self.assertEqual(first, feishu_outbox_request_uuid(self.idempotency_key))
        self.assertNotEqual(first, feishu_outbox_request_uuid(self.idempotency_key + "-next"))
        self.assertEqual(len(first), 36)

if __name__ == "__main__":
    unittest.main()
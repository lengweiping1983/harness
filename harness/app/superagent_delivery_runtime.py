"""统一信道、冻结发布候选和 Outbox Sender；不装配旧信道账本。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from ..core.cleanup import join_cleanup
from ..core.language import current_language
from ..i18n import tr
from .superagent_common import _error, _json as strict_loads
from .superagent_coordinator_service import CoordinatorApplicationService
from .superagent_coordinator_store import _hash, _json, _now
from .superagent_effect_recovery import EffectLookupResult
from .superagent_provider_adapters import (
    OutboxProviderLookupAdapter, ProviderChannel, feishu_outbox_body,
    feishu_outbox_request_uuid, github_outbox_body,
)


_ACTOR = {"kind": "host", "component": "superagent-delivery"}


class LocalReplyClient:
    """本机结果只投影到独占发布的文件；文件不是第二份业务账本。"""

    def __init__(self, root):
        self.root = Path(root)

    def _path(self, key):
        return self.root / (_hash(key) + ".json")

    def lookup(self, key):
        path = self._path(key)
        if path.is_symlink():
            raise _error("conflict")
        try:
            with path.open("rb") as stream:
                raw = stream.read(1048577)
        except FileNotFoundError:
            return None
        if len(raw) > 1048576:
            raise _error("corrupt")
        value = strict_loads(raw.decode("utf-8"))
        if not isinstance(value, dict) or value.get("idempotency_key") != key:
            raise _error("corrupt")
        return value

    async def publish(self, *, key, channel_id, body, before_request):
        def write():
            before_request()
            self.root.mkdir(parents=True, exist_ok=True)
            if self.root.is_symlink():
                raise _error("conflict")
            value = {"idempotency_key": key, "channel_id": channel_id, "body": body,
                     "provider_id": "local:" + _hash(key), "sent_at": _now()}
            prior = self.lookup(key)
            if prior is not None:
                if prior.get("channel_id") != channel_id or prior.get("body") != body:
                    raise _error("conflict")
                return prior
            raw = _json(value).encode("utf-8")
            name = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as stream:
                    name = stream.name
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                before_request()
                try:
                    os.link(name, self._path(key))
                except FileExistsError:
                    prior = self.lookup(key)
                    if prior is None or prior.get("body") != body or prior.get("channel_id") != channel_id:
                        raise _error("conflict")
                    return prior
                return value
            finally:
                if name is not None:
                    Path(name).unlink()
        return await join_cleanup(asyncio.create_task(asyncio.to_thread(write)))


class SourceIngress:
    """把现有有界 HTTP 监听器接到同一 SourceRegistry，不建立收件箱。"""

    def __init__(self, runtime, source_id):
        self.runtime, self.source_id = runtime, source_id

    def receive(self, headers, raw, *, queued=False):
        try:
            result = self.runtime.receive(self.source_id, headers=headers, raw=raw)
            return {'challenge': result['challenge']} if result.get('state') == 'challenge' else result
        except PermissionError as exc:
            source = self.runtime.host.sources.get_instance(self.source_id)
            raise _error(source.kind + '-signature') from exc
        except (ValueError, TypeError) as exc:
            source = self.runtime.host.sources.get_instance(self.source_id)
            raise _error(source.kind + '-request') from exc


class CoordinatorDeliveryRuntime:
    """发送准入与原操作回查分离；任何不明确的 POST 结果都不重放。"""

    def __init__(self, host, *, provider_clients=None, closing=lambda: False):
        self.host = host
        self.e = host._execution_service
        self.clients = dict(provider_clients or {})
        self.local = LocalReplyClient(host.state_dir / "replies")
        self.closing = closing
        self._publication_cursor = 0
        self._sender_cursor = ("", "")
        self._source_revisions = {}

    def receive(self, source_id, *, headers, raw, stream_key=None, cursor=None, expected_revision=0):
        if self.closing():
            raise _error("closed")
        result = self.host.sources.receive(source_id, headers=headers, raw=raw)
        if cursor is not None:
            if not isinstance(stream_key, str) or not result.get("trigger_id"):
                raise _error("invalid", field="source.cursor")
            self.host.sources.advance_cursor(source_id, stream_key, cursor,
                expected_revision=expected_revision, actor=_ACTOR)
        return result

    def ingress(self, source_id):
        if self.host.sources.get_instance(source_id) is None:
            raise _error("source-unavailable")
        return SourceIngress(self, source_id)

    def channel(self, channel_id):
        channel = self.host.sources.get_channel(channel_id)
        if channel is None or _hash(_json(channel["address"])) != channel["address_hash"]:
            raise _error("corrupt")
        source = self.host.sources.get_instance(channel["source_instance_id"])
        if source is None:
            raise _error("source-unavailable")
        if source.kind == "local":
            return channel, ProviderChannel("local", self.local, channel["address"], {})
        client = self.clients.get(source.source_instance_id)
        if client is None:
            return channel, None
        address = dict(channel["address"])
        if source.kind == "github":
            if address.get("repository", "").casefold() != source.config["repository"].casefold():
                raise _error("conflict")
            number = address.get("issue_number")
            if not isinstance(number, str) or not number.isascii() or not number.isdigit() or int(number) < 1:
                raise _error("corrupt")
            address["issue_number"] = int(number)
            identity = {"bot_user_id": source.config["bot_user_id"]}
        elif source.kind == "feishu":
            if address.get("chat_id") not in source.config["chats"]:
                raise _error("conflict")
            identity = {"app_id": source.config["app_id"], "tenant_key": source.config["tenant_key"]}
        else:
            return channel, None
        return channel, ProviderChannel(source.kind, client, address, identity)

    def prepare_channels(self):
        for channel in self.host.sources.list_channels(limit=1000):
            source = self.host.sources.get_instance(channel["source_instance_id"])
            if source is not None and source.enabled and source.installed:
                revision = self._source_revisions.setdefault(source.source_instance_id, source.config_revision)
                if revision != source.config_revision:
                    continue
                _, provider = self.channel(channel["channel_id"])
                binding = self.e._read_one("SELECT channel_id FROM channel_reply_bindings WHERE channel_id=?", (channel["channel_id"],))
                if provider is not None and binding is None:
                    self.host.sources.set_channel_reply(channel["channel_id"],
                        expected_source_revision=source.config_revision, enabled=True, actor=_ACTOR)

    def _result_body(self, mission_id, review_id, language):
        if language not in {"en", "zh-CN"}:
            raise _error("corrupt")
        with self.host._store.unit_of_work(actor=_ACTOR) as uow:
            mission = uow.missions.get(mission_id)
            app = CoordinatorApplicationService(uow)
            latest = app._latest_review(mission_id, mission["plan_revision"]) if mission else None
            if (mission is None or mission["state"] != "completed" or latest is None
                    or latest["review_id"] != review_id or not app._review_passed(mission_id, mission)):
                raise _error("corrupt")
            with current_language.set(language):
                return tr("superagent.delivery.result", mission=mission_id,
                          artifact=latest["candidate_artifact_id"], review=review_id)

    def body(self, reference):
        parts = reference.split(":")
        if len(parts) == 4 and parts[0] == "mission-result":
            return self._result_body(parts[1], parts[2], parts[3])
        if len(parts) == 3 and parts[0] == "attention":
            row = self.e._read_one("SELECT question_text FROM attentions WHERE attention_id=?", (parts[1],))
            if row is None or _hash(row["question_text"]) != parts[2]:
                raise _error("corrupt")
            return row["question_text"]
        row = self.e._read_one("SELECT message_id FROM outbox_messages WHERE body_ref=? LIMIT 1", (reference,))
        if row is None:
            raise _error("corrupt")
        return self.host.execution.outbox_body(row["message_id"])

    def _authorized(self, message, *, new_write=True):
        if new_write and self.closing():
            raise _error("closed")
        ticket = self.host.authorization.ticket(message["ticket_id"])
        mandate = (ticket or {}).get("mandate") or {}
        mandate_id = mandate.get("mandate_id")
        if (ticket is None or ticket["state"] != "open" or not mandate_id
                or self.host.authorization.revalidate_mandate(mandate_id, actor=_ACTOR).get("status") not in {"valid", "affected"}):
            raise _error("mandate.unavailable")
        channel, provider = self.channel(message["channel_id"])
        source = self.host.sources.get_instance(channel["source_instance_id"])
        thread = self.host.sources.get_thread(channel["thread_id"])
        scope = mandate.get("scope") or {}
        if (source is None or not source.enabled or not source.installed
                or thread is None or thread["source_state"] == "closed"
                or channel["capabilities"].get("reply") is not True
                or "reply" not in scope.get("actions", [])
                or channel["source_instance_id"] not in scope.get("source_instance_ids", [])
                or channel["thread_id"] not in scope.get("thread_ids", [])):
            raise _error("mandate.unavailable")
        reference = message["body_ref"].split(":")
        if reference[0] == "mission-result":
            mission_id = reference[1]
        elif reference[0] == "attention":
            attention = self.e._read_one("SELECT * FROM attentions WHERE attention_id=?", (reference[1],))
            if attention is None or attention["state"] != "open":
                raise _error("mandate.unavailable")
            mission_id = attention["mission_id"]
        else:
            assignment = self.e._assignment(reference[1]) if len(reference) == 4 else None
            if assignment is None or assignment["ticket_id"] != ticket["ticket_id"]:
                raise _error("corrupt")
            mission_id = assignment["mission_id"]
        mission = self.e._read_one("SELECT * FROM missions WHERE mission_id=?", (mission_id,))
        binding = self.e._read_one("SELECT trigger_id FROM outbox_reply_targets WHERE message_id=?", (message['message_id'],))
        if binding is None and message.get('reply_trigger_id'):
            binding = {'trigger_id': message['reply_trigger_id']}
        trigger = self.host.sources.get_trigger(binding['trigger_id'] if binding else mission['goal_ref']) if mission else None
        if trigger is not None and trigger['trigger_id'] not in {item['trigger_id'] for item in self.e.reply_triggers(mission_id)}:
            raise _error('mandate.unavailable')
        if (mission is None or mission["ticket_id"] != ticket["ticket_id"] or trigger is None
                or trigger["source_instance_id"] != source.source_instance_id
                or trigger["source_revision"] != source.config_revision):
            raise _error("mandate.unavailable")
        if channel["channel_id"] not in {item["channel_id"] for item in self.host.sources.reply_channels(trigger["trigger_id"])}:
            raise _error("mandate.unavailable")
        mode = self.e._control.mode(mission_id)["mode"]
        if mode == "paused":
            raise _error("mission.paused")
        if mode != "running":
            raise _error("mandate.unavailable")
        watchdog = self.host.watchdog.get(mission_id)
        if watchdog is not None and watchdog["state"] == "blocked":
            raise _error("mission.paused")
        if _hash(self.body(message["body_ref"])) != message["body_hash"]:
            raise _error("corrupt")
        return channel, provider

    def _candidate(self, mission, *, kind, reference, identity):
        ticket = self.host.authorization.ticket(mission["ticket_id"])
        trigger = self.host.sources.get_trigger(mission["goal_ref"])
        if ticket is None or trigger is None:
            raise _error("corrupt")
        targets = [(channel, target) for target in self.e.reply_triggers(mission['mission_id'])
                   for channel in self.host.sources.reply_channels(target['trigger_id'])]
        for channel, target in targets:
            message_id = "message_" + _hash(_json([identity, channel["channel_id"], kind]))[:32]
            old = self.e._read_one("SELECT * FROM outbox_messages WHERE message_id=?", (message_id,))
            if old is not None:
                self.body(old["body_ref"])
                continue
            message = {"message_id": message_id, "ticket_id": mission["ticket_id"],
                       "channel_id": channel["channel_id"], "kind": kind, "body_ref": reference,
                       "body_hash": _hash(self.body(reference)), "idempotency_key": "superagent:" + message_id,
                       "reply_trigger_id": target['trigger_id']}
            try:
                self._authorized(message)
            except Exception as exc:
                if getattr(exc, "code", None) in {"superagent/mandate.unavailable", "superagent/mission.paused"}:
                    continue
                raise
            with self.host._store.unit_of_work(actor=_ACTOR) as uow:
                if uow.outbox.get(message_id) is None:
                    uow.outbox.create({key:value for key,value in message.items() if key != 'reply_trigger_id'})
                    uow.db.execute("INSERT INTO outbox_reply_targets VALUES(?,?)", (message_id, target['trigger_id']))

    def prepare_candidates(self, *, limit=100):
        rows = self.e._read_all("SELECT rowid AS cursor,* FROM missions WHERE rowid>? ORDER BY rowid LIMIT ?",
                                (self._publication_cursor, limit))
        for mission in rows:
            if mission["state"] == "completed":
                with self.host._store.unit_of_work(actor=_ACTOR) as uow:
                    latest = CoordinatorApplicationService(uow)._latest_review(mission["mission_id"], mission["plan_revision"])
                if latest is not None and latest["state"] == "passed":
                    reference = ":".join(["mission-result", mission["mission_id"], latest["review_id"], current_language.get()])
                    self._candidate(mission, kind="result", reference=reference, identity=latest["review_id"])
                else:
                    for row in self.e._read_all("SELECT a.assignment_id FROM assignments a JOIN jobs j ON j.job_id=a.job_id WHERE j.mission_id=? AND a.state='succeeded'", (mission["mission_id"],)):
                        self.e._finalize_report_effects(self.e._assignment(row["assignment_id"]))
            for attention in self.e.list_attentions(mission["ticket_id"], state="open"):
                if attention["mission_id"] == mission["mission_id"]:
                    self._candidate(mission, kind="attention", identity=attention["attention_id"],
                                    reference="attention:" + attention["attention_id"] + ":" + _hash(attention["question_text"]))
        self._publication_cursor = rows[-1]["cursor"] if len(rows) == limit else 0
        return len(rows) == limit

    async def lookup(self, *, effect):
        payload = effect["payload"]
        channel, provider = self.channel(payload["channel_id"])
        if provider is None:
            return EffectLookupResult("indeterminate")
        if provider.provider_id == "local":
            prior = self.local.lookup(payload["idempotency_key"])
            if prior is None:
                return EffectLookupResult("indeterminate")
            if prior.get("channel_id") != channel["channel_id"] or prior.get("body") != self.body(payload["body_ref"]):
                raise _error("corrupt")
            return EffectLookupResult("applied", receipt={key: prior[key] for key in ("idempotency_key", "provider_id", "sent_at")})
        adapter = OutboxProviderLookupAdapter({channel["channel_id"]: provider}, self.body)
        return await adapter.lookup(effect=effect)

    async def _verify_provider(self, channel, provider):
        """原生 POST 前核验凭据主体与提供方资源，不从事件 URL 推断发言权。"""
        source = self.host.sources.get_instance(channel['source_instance_id'])
        if provider.provider_id == 'github':
            user = await provider.client.request('GET', '/user')
            repo_path = '/repos/' + '/'.join(quote(part, safe='') for part in provider.address['repository'].split('/'))
            repo = await provider.client.request('GET', repo_path)
            if (not isinstance(user, dict) or type(user.get('id')) is not int or user['id'] != source.config['bot_user_id']
                    or not isinstance(user.get('login'), str) or user['login'].casefold() != source.config['bot_login'].casefold()
                    or not isinstance(repo, dict) or type(repo.get('id')) is not int or repo['id'] != source.config['repository_id']):
                raise _error('github-scope')
        elif provider.provider_id == 'feishu':
            bot = await provider.client.request('GET', '/open-apis/bot/v3/info/')
            if not isinstance(bot, dict) or not isinstance(bot.get('bot'), dict) or bot['bot'].get('open_id') != source.config['bot_open_id']:
                raise _error('feishu-scope')
            original = await provider.client.request('GET', '/open-apis/im/v1/messages/' + quote(provider.address['message_id'], safe='') + '?user_id_type=open_id')
            items = (original.get('data') or {}).get('items') if isinstance(original, dict) else None
            if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
                raise _error('feishu-response')
            item = items[0]
            if (item.get('message_id') != provider.address['message_id'] or item.get('chat_id') != provider.address['chat_id']
                    or (item.get('root_id') or item.get('message_id')) != provider.address['root_id'] or item.get('deleted') is True):
                raise _error('feishu-scope')

    async def tick(self, *, limit=16):
        sent = unknown = unavailable = withdrawn = 0
        errors = []
        owner = "sender:" + self.e.instance_id
        pending = self.e._read_all("SELECT * FROM outbox_messages WHERE state IN ('pending','retry_wait') ORDER BY (created_at,message_id) <= (?,?),created_at,message_id LIMIT ?", (*self._sender_cursor, limit))
        if pending:
            self._sender_cursor = (pending[-1]["created_at"], pending[-1]["message_id"])
        for message in pending:
            try:
                channel, provider = self._authorized(message)
            except Exception as exc:
                if getattr(exc, "code", None) in {"superagent/mandate.unavailable", "superagent/mission.paused", "superagent/closed"}:
                    if getattr(exc, "code", None) != "superagent/mission.paused" and not self.closing():
                        withdrawn += self.host.outbox.withdraw(message["message_id"], expected_revision=message["aggregate_revision"], actor=_ACTOR)
                    continue
                raise
            if provider is None:
                unavailable += 1
                continue
            body = self.body(message['body_ref'])
            wire = feishu_outbox_body(body, message['idempotency_key']) if provider.provider_id == 'feishu' else body
            if len(re.findall(r'[\u3400-\u9fff]|[^\s\u3400-\u9fff]+', wire)) > 100:
                raise _error('invalid', field='reply')
            try:
                await self._verify_provider(channel, provider)
                self._authorized(message)
            except Exception as exc:
                unavailable += 1
                errors.append({'message_id':message['message_id'], 'code':getattr(exc, 'code', type(exc).__name__)})
                continue
            lease = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
            effects = self.host.outbox.claim(owner=owner, limit=1, lease_until=lease, message_id=message["message_id"])
            if not effects:
                continue
            effect = effects[0]
            effect["payload"] = strict_loads(effect["payload_json"])
            key = message["idempotency_key"]
            body = self.body(message["body_ref"])
            def guard():
                self._authorized(message)
            try:
                if provider.provider_id == "local":
                    await self.local.publish(key=key, channel_id=channel["channel_id"], body=body, before_request=guard)
                elif provider.provider_id == "github":
                    address = provider.address
                    path = "/repos/" + "/".join(quote(part, safe="") for part in address["repository"].split("/")) + "/issues/" + str(address["issue_number"]) + "/comments"
                    await provider.client.request("POST", path, {"body": github_outbox_body(body, key)}, before_request=guard)
                elif provider.provider_id == "feishu":
                    await provider.client.request("POST", "/open-apis/im/v1/messages/" + quote(provider.address["message_id"], safe="") + "/reply",
                        {"msg_type": "text", "content": _json({"text": feishu_outbox_body(body, key)}),
                         "reply_in_thread": True, "uuid": feishu_outbox_request_uuid(key)}, before_request=guard)
                else:
                    raise _error("source-unavailable")
                result = await self.lookup(effect=effect)
                if result.status != "applied":
                    raise _error("conflict")
            except BaseException as exc:
                self.host.outbox.settle(effect_id=effect["effect_id"], owner=owner, generation=effect["generation"],
                                       state="unknown", error=getattr(exc, "code", type(exc).__name__))
                unknown += 1
                if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
            else:
                settled = self.host.outbox.settle(effect_id=effect["effect_id"], owner=owner, generation=effect["generation"],
                                                  state="completed", receipt=result.receipt)
                if settled:
                    sent += 1
                else:
                    unknown += 1
        total = self.e._read_one("SELECT count(*) AS n FROM outbox_messages WHERE state IN ('pending','retry_wait')")["n"]
        return {"sent": sent, "unknown": unknown, "unavailable": unavailable, "withdrawn": withdrawn,
                "pending": total, "backlog": total > len(pending), "errors": errors}

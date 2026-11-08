"""GitHub 与飞书 Outbox 的只读 Provider 回查适配器。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Callable, Mapping, Protocol
from urllib.parse import quote, urlencode, urlsplit
import uuid

from .superagent_effect_recovery import EffectLookupResult
from .superagent_http import api_root

class ProviderRequestClient(Protocol):
    """复用已认证客户端；回查只用 GET，发送回调在原生写入前同步执行。"""

    async def request(self, method: str, path: str, data: object | None = None,
                      *, before_request: Callable[[], None] | None = None) -> object:
        ...

@dataclass(frozen=True)
class ProviderChannel:
    """由宿主解析的稳定 Channel 地址、Provider 身份和已认证客户端。"""

    provider_id: str
    client: ProviderRequestClient
    address: Mapping[str, object]
    identity: Mapping[str, object]

def _text(value: object, name: str, *, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or len(value) > maximum or "\x00" in value
    ):
        raise ValueError(f"{name} must be a non-empty trimmed string")
    return value

def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value

def _iso_time(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must include a valid timezone") from exc

def _message_marker(idempotency_key: str) -> str:
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()

def github_outbox_body(body: str, idempotency_key: str) -> str:
    """GitHub 评论正文使用原幂等键的稳定标记供只读回查。"""
    _text(idempotency_key, "idempotency_key")
    return body + "\n\n<!-- harness-superagent-outbox:" + _message_marker(idempotency_key) + " -->"

def feishu_outbox_body(body: str, idempotency_key: str) -> str:
    """飞书线程回复附带原幂等键的稳定引用标记，供消息列表回查。"""
    _text(idempotency_key, "idempotency_key")
    return body + "\n\n[ref:" + _message_marker(idempotency_key) + "]"

def feishu_outbox_request_uuid(idempotency_key: str) -> str:
    """把原 Outbox 幂等键稳定映射为飞书回复 API 的 UUID。"""
    key = _text(idempotency_key, "idempotency_key")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "harness-superagent-outbox:" + key))

class GitHubIssueCommentLookupAdapter:
    """只读查询原 Issue 的评论列表；没有找到评论不构成安全重试证明。"""

    async def lookup(self, *, channel: ProviderChannel, payload: Mapping[str, object],
                     body: str) -> EffectLookupResult:
        address, identity = channel.address, channel.identity
        repository = _text(address.get("repository"), "repository")
        parts = repository.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("repository must be owner/repository")
        owner, name = parts
        issue_number = _positive_int(address.get("issue_number"), "issue_number")
        bot_user_id = _positive_int(identity.get("bot_user_id"), "bot_user_id")
        root_value = api_root(_text(getattr(channel.client, "root", None), "GitHub API root"))
        root = urlsplit(root_value)
        issue_path = (
            root.path.rstrip("/") + "/repos/" + quote(owner, safe="") + "/"
            + quote(name, safe="") + "/issues/" + str(issue_number)
        )
        expected_body = github_outbox_body(body, _text(payload.get("idempotency_key"), "idempotency_key"))
        matches = []
        complete = False
        for page in range(1, 11):
            path = (
                "/repos/" + quote(owner, safe="") + "/" + quote(name, safe="")
                + "/issues/" + str(issue_number) + "/comments?per_page=100&page=" + str(page)
            )
            comments = await channel.client.request("GET", path)
            if not isinstance(comments, list) or len(comments) > 100:
                raise ValueError("GitHub comment lookup returned an invalid page")
            for comment in comments:
                if not isinstance(comment, dict):
                    raise ValueError("GitHub comment entry must be an object")
                if comment.get("body") != expected_body:
                    continue
                user = comment.get("user")
                if not isinstance(user, dict):
                    raise ValueError("matching GitHub comment has no author identity")
                author_id = _positive_int(user.get("id"), "comment.user.id")
                if author_id != bot_user_id:
                    continue
                comment_id = _positive_int(comment.get("id"), "comment.id")
                issue_url = _text(comment.get("issue_url"), "comment.issue_url")
                actual = urlsplit(issue_url)
                expected_port = root.port or (443 if root.scheme == "https" else 80)
                actual_port = actual.port or (443 if actual.scheme == "https" else 80)
                if (
                    actual.scheme != root.scheme or actual.hostname != root.hostname
                    or actual_port != expected_port or actual.username is not None
                    or actual.password is not None or actual.query or actual.fragment
                    or actual.path.casefold() != issue_path.casefold()
                ):
                    raise ValueError("matching GitHub comment belongs to another Issue")
                sent_at = _iso_time(comment.get("created_at"), "comment.created_at")
                matches.append((comment_id, sent_at))
            if len(comments) < 100:
                complete = True
                break

        if not complete:
            return EffectLookupResult(
                status="indeterminate",
                detail="GitHub comment history exceeded the bounded lookup window",
            )
        if len(matches) != 1:
            return EffectLookupResult(
                status="indeterminate",
                detail="GitHub lookup did not identify exactly one original Outbox comment",

            )
        comment_id, sent_at = matches[0]
        return EffectLookupResult(
            status="applied",
            receipt={
                "provider": "github",
                "idempotency_key": payload["idempotency_key"],
                "provider_id": str(comment_id),

                "sent_at": sent_at,
                "lookup": "exact_issue_comment",
            },
        )

class FeishuThreadReplyLookupAdapter:
    """只读分页查询原飞书线程；未找到回复仍保持 unknown，不授权重试。"""

    async def lookup(self, *, channel: ProviderChannel, payload: Mapping[str, object],
                     body: str) -> EffectLookupResult:
        address, identity = channel.address, channel.identity
        chat_id = _text(address.get("chat_id"), "chat_id")
        message_id = _text(address.get("message_id"), "message_id")
        root_id = _text(address.get("root_id"), "root_id")
        app_id = _text(identity.get("app_id"), "app_id")
        tenant_key = _text(identity.get("tenant_key"), "tenant_key")
        if (
            not re.fullmatch(r"oc_[A-Za-z0-9_-]{1,128}", chat_id)
            or not re.fullmatch(r"om_[A-Za-z0-9_-]{1,128}", message_id)
            or not re.fullmatch(r"om_[A-Za-z0-9_-]{1,128}", root_id)
            or not re.fullmatch(r"cli_[A-Za-z0-9_-]{1,128}", app_id)
        ):
            raise ValueError("Feishu Channel address or application identity is invalid")
        configured_app = getattr(channel.client, "app_id", app_id)
        if configured_app != app_id:
            raise ValueError("Feishu client application identity does not match Channel")
        expected_body = feishu_outbox_body(body, _text(payload.get("idempotency_key"), "idempotency_key"))
        token = None
        seen_tokens = set()
        matches = []
        complete = False
        for _ in range(10):
            query = {
                "container_id_type": "chat",
                "container_id": chat_id,
                "page_size": "50",
                "sort_type": "ByCreateTimeDesc",
                "only_thread_root_messages": "false",
            }
            if token is not None:
                query["page_token"] = token
            response = await channel.client.request(
                "GET", "/open-apis/im/v1/messages?" + urlencode(query),
            )
            data = response.get("data") if isinstance(response, dict) else None
            if (
                not isinstance(data, dict) or not isinstance(data.get("items"), list)
                or len(data["items"]) > 50 or type(data.get("has_more")) is not bool
            ):
                raise ValueError("Feishu message lookup returned an invalid page")
            for item in data["items"]:
                if not isinstance(item, dict):
                    raise ValueError("Feishu message entry must be an object")
                if item.get("chat_id") != chat_id or item.get("parent_id") != message_id:
                    continue
                if item.get("msg_type") != "text" or item.get("deleted", False) is not False:
                    continue
                sender = item.get("sender")
                if not isinstance(sender, dict):
                    raise ValueError("matching Feishu thread entry has no sender identity")
                if (
                    sender.get("sender_type") != "app" or sender.get("id_type") != "app_id"
                    or sender.get("id") != app_id or sender.get("tenant_key") != tenant_key
                ):
                    continue
                message_body = item.get("body")
                if not isinstance(message_body, dict) or not isinstance(message_body.get("content"), str):
                    raise ValueError("matching Feishu thread entry has an invalid body")
                try:
                    content = json.loads(message_body["content"])
                except (TypeError, ValueError) as exc:
                    raise ValueError("matching Feishu thread body is corrupt") from exc
                if content != {"text": expected_body}:
                    continue
                remote_id = _text(item.get("message_id"), "message.message_id")
                if not re.fullmatch(r"om_[A-Za-z0-9_-]{1,128}", remote_id):
                    raise ValueError("Feishu reply ID is invalid")
                remote_root = item.get("root_id")
                if remote_root in (None, ""):
                    remote_root = remote_id
                if remote_root != root_id:
                    raise ValueError("matching Feishu reply belongs to another thread")
                created = item.get("create_time")
                if isinstance(created, str) and re.fullmatch(r"[0-9]{1,16}", created):
                    milliseconds = int(created)
                elif type(created) is int and created > 0:
                    milliseconds = created
                else:
                    raise ValueError("Feishu reply has no valid provider creation time")
                try:
                    sent_at = datetime.fromtimestamp(
                        milliseconds / 1000, timezone.utc,
                    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                except (OverflowError, OSError, ValueError) as exc:
                    raise ValueError("Feishu reply creation time is out of range") from exc
                matches.append((remote_id, sent_at))

            if not data["has_more"]:
                complete = True
                break
            next_token = _text(data.get("page_token"), "page_token")
            if next_token in seen_tokens:
                raise ValueError("Feishu message lookup repeated a pagination token")
            seen_tokens.add(next_token)
            token = next_token

        if not complete:
            return EffectLookupResult(
                status="indeterminate",
                detail="Feishu message history exceeded the bounded lookup window",
            )
        if len(matches) != 1:
            return EffectLookupResult(
                status="indeterminate",
                detail="Feishu lookup did not identify exactly one original thread reply",
            )
        remote_id, sent_at = matches[0]
        return EffectLookupResult(
            status="applied",
            receipt={
                "provider": "feishu",
                "idempotency_key": payload["idempotency_key"],
                "provider_id": remote_id,
                "sent_at": sent_at,
                "lookup": "exact_thread_reply",
            },
        )

class OutboxProviderLookupAdapter:
    """按 Channel 身份分发只读 Provider 查询，并核验正文引用的内容哈希。"""

    _PAYLOAD_FIELDS = {
        "schema_version", "message_id", "ticket_id", "channel_id", "message_kind",
        "body_ref", "body_hash", "idempotency_key",
    }

    def __init__(
        self,
        channels: Mapping[str, ProviderChannel],
        body_resolver: Callable[[str], str | bytes],

    ):
        self._channels = dict(channels)
        self._body_resolver = body_resolver
        self._providers = {
            "github": GitHubIssueCommentLookupAdapter(),
            "feishu": FeishuThreadReplyLookupAdapter(),
        }

    async def lookup(self, *, effect: Mapping[str, object]) -> EffectLookupResult:

        """只读取原 Effect、Channel 与正文引用；不会调用 Provider 写入接口。"""
        if effect.get("kind") != "outbox.send":
            raise ValueError("Provider lookup requires an outbox.send Effect")
        payload = effect.get("payload")
        if not isinstance(payload, dict) or set(payload) != self._PAYLOAD_FIELDS:
            raise ValueError("Outbox Effect payload does not match schema version 1")
        if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
            raise ValueError("Outbox Effect schema version is unsupported")
        message_id = _text(payload.get("message_id"), "message_id")
        _text(payload.get("ticket_id"), "ticket_id")
        channel_id = _text(payload.get("channel_id"), "channel_id")
        _text(payload.get("message_kind"), "message_kind")
        body_ref = _text(payload.get("body_ref"), "body_ref")
        body_hash = _text(payload.get("body_hash"), "body_hash")
        idempotency_key = _text(payload.get("idempotency_key"), "idempotency_key")
        if not re.fullmatch(r"[0-9a-f]{64}", body_hash):
            raise ValueError("body_hash must be a lowercase SHA-256 digest")
        if (
            effect.get("aggregate_id") != "outbox:" + message_id
            or effect.get("idempotency_key") != idempotency_key
        ):
            raise ValueError("Outbox Effect identity does not match its payload")
        channel = self._channels.get(channel_id)
        if channel is None:
            return EffectLookupResult(
                status="indeterminate",
                detail="Channel configuration is unavailable; original send remains unknown",
            )
        if not isinstance(channel, ProviderChannel):
            raise TypeError("Channel resolver must return ProviderChannel")
        raw_body = self._body_resolver(body_ref)
        if isinstance(raw_body, str):
            raw = raw_body.encode("utf-8", errors="strict")
        elif isinstance(raw_body, bytes):
            raw = raw_body
        else:
            raise TypeError("body resolver must return UTF-8 text or bytes")
        if len(raw) > 1048576 or hashlib.sha256(raw).hexdigest() != body_hash:
            raise ValueError("Outbox body content does not match its persisted hash")
        try:
            body = raw.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError("Outbox body is not valid UTF-8") from exc
        adapter = self._providers.get(channel.provider_id)
        if adapter is None:
            return EffectLookupResult(
                status="indeterminate",
                detail="Provider lookup adapter is unavailable; original send remains unknown",
            )
        return await adapter.lookup(channel=channel, payload=payload, body=body)

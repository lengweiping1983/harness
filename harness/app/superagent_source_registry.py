"""下一代 Superagent 的来源插件契约、实例注册表和可信事件规范化。"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import re
import time
from threading import RLock
from typing import Any, Callable, Mapping, Protocol
from ..i18n import tr

from .superagent_coordinator_store import CoordinatorStore, _hash, _json, _now, _text, _time


_MAX_WEBHOOK_BYTES = 1_048_576
_MAX_EVENT_TEXT_BYTES = 65_536
_MAX_THREADS = 16


def _strict_json(raw: str) -> Any:
    """拒绝重复对象键和非标准数值，避免歧义输入进入授权边界。"""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def invalid(_value):
        raise ValueError("non-finite JSON number")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def _bounded_text(value: object, name: str, *, limit: int = 4096, empty: bool = False) -> str:
    try:
        valid = (
            isinstance(value, str) and "\x00" not in value
            and (empty or bool(value.strip())) and len(value.encode("utf-8")) <= limit
        )
    except UnicodeError:
        valid = False
    if not valid:
        raise ValueError(f"{name} is invalid")
    return value


def _positive_id(value: object, name: str) -> int:
    if type(value) is not int or not 1 <= value <= 9_223_372_036_854_775_807:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _header(headers: Mapping[str, object], name: str) -> object:
    wanted = name.casefold()
    return next((value for key, value in headers.items() if isinstance(key, str) and key.casefold() == wanted), None)


def _secret(resolver: Callable[[str], bytes | str], reference: str, name: str) -> bytes:
    value = resolver(reference)
    if isinstance(value, str):
        value = value.encode("utf-8")
    if not isinstance(value, bytes) or not value:
        raise PermissionError(f"{name} secret is unavailable")
    return value


def _timestamp(value: object, fallback: str) -> str:
    if value is None or value == "":
        return _time(fallback, "received_at")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not 0 <= value <= 9_999_999_999_999:
            raise ValueError("timestamp is out of range")
        parsed = datetime.fromtimestamp(float(value) / (1000 if value > 9_999_999_999 else 1), timezone.utc)
        return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO-8601 string or Unix time")
    return _time(value, "occurred_at")


@dataclass(frozen=True)
class NormalizedThread:
    external_thread_id: str
    relation: str = "primary"
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ThreadLifecycleUpdate:
    state: str
    outcome: str | None = None
    provider_revision: str | None = None
    observed_at: str | None = None


@dataclass(frozen=True)
class NormalizedTrigger:
    external_event_id: str
    event_kind: str
    actor_id: str
    actor_kind: str
    occurred_at: str
    addressed: str
    threads: tuple[NormalizedThread, ...]
    primary_thread_id: str
    body: Mapping[str, Any]
    metadata: Mapping[str, Any]
    channel_address: Mapping[str, str] | None = None
    lifecycle: ThreadLifecycleUpdate | None = None
    verification_state: str = "verified"


@dataclass(frozen=True)
class SourceChallenge:
    challenge: str


@dataclass(frozen=True)
class SourceIgnored:
    reason: str


@dataclass(frozen=True)
class SourceInstance:
    source_instance_id: str
    kind: str
    kind_api_version: int
    enabled: bool
    config: Mapping[str, Any]
    config_revision: int
    identity_hash: str
    installed: bool


class SecretResolver(Protocol):
    def __call__(self, reference: str) -> bytes | str: ...


class LocalIdentityVerifier(Protocol):
    def __call__(self, event: Mapping[str, Any]) -> Mapping[str, str]: ...


class SourceKind(Protocol):
    kind: str
    api_version: int

    def validate_config(self, config: object) -> dict[str, Any]: ...
    def identity(self, config: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def normalize(
        self, *, headers: Mapping[str, object], raw: bytes, config: Mapping[str, Any],
        secret_resolver: Callable[[str], bytes | str], received_at: str,
        local_identity_verifier: LocalIdentityVerifier | None = None,
    ) -> NormalizedTrigger | SourceChallenge | SourceIgnored: ...


class GitHubSourceKind:
    """GitHub issue/comment webhook；只验签和规范化，不发起 GitHub API 调用。"""

    kind = "github"
    api_version = 1

    def validate_config(self, config: object) -> dict[str, Any]:
        allowed = {"repository", "repository_id", "bot_login", "bot_user_id", "webhook_secret_ref"}
        if not isinstance(config, dict) or set(config) != allowed:
            raise ValueError("GitHub source config has an invalid shape")
        repository = _bounded_text(config["repository"], "repository", limit=200)
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
            raise ValueError("repository must be owner/name")
        bot_login = _bounded_text(config["bot_login"], "bot_login", limit=100)
        secret_ref = _bounded_text(config["webhook_secret_ref"], "webhook_secret_ref", limit=512)
        if not secret_ref.startswith("secret://"):
            raise ValueError("webhook_secret_ref must reference the host secret store")
        return {
            "repository": repository,
            "repository_id": _positive_id(config["repository_id"], "repository_id"),
            "bot_login": bot_login,
            "bot_user_id": _positive_id(config["bot_user_id"], "bot_user_id"),
            "webhook_secret_ref": secret_ref,
        }

    def identity(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"repository_id": config["repository_id"], "repository": config["repository"].casefold()}

    def normalize(self, *, headers, raw, config, secret_resolver, received_at, local_identity_verifier=None):
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= _MAX_WEBHOOK_BYTES:
            raise ValueError("GitHub webhook body is invalid")
        secret = _secret(secret_resolver, config["webhook_secret_ref"], "GitHub webhook")
        expected = "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()
        signature = _header(headers, "x-hub-signature-256")
        if (
            not isinstance(signature, str) or re.fullmatch(r"sha256=[0-9a-f]{64}", signature) is None
            or not hmac.compare_digest(expected, signature)
        ):
            raise PermissionError("GitHub webhook signature is invalid")
        try:
            data = _strict_json(raw.decode("utf-8"))
            event_kind = _bounded_text(_header(headers, "x-github-event"), "x-github-event", limit=80)
            delivery = _bounded_text(_header(headers, "x-github-delivery"), "x-github-delivery", limit=200)
            if not isinstance(data, dict):
                raise ValueError("webhook payload must be an object")
            if event_kind == "ping":
                return SourceIgnored("github_ping")
            if (event_kind in {'pull_request_review','pull_request_review_comment','check_run','check_suite','workflow_run'}
                    or event_kind=='pull_request' and data.get('action') not in {'opened','closed','reopened'}):
                from .superagent_github_events import activity
                return activity(data,event_kind,data.get('action'),delivery,config,received_at)
            supported = {
                ("issues", "opened"), ("issues", "closed"), ("issues", "reopened"),
                ("issue_comment", "created"),
                ("pull_request", "opened"), ("pull_request", "closed"), ("pull_request", "reopened"),
            }
            action = data.get("action")
            if (event_kind, action) not in supported:
                return SourceIgnored("unsupported_github_event")
            repository = data["repository"]
            sender = data["sender"]
            entity = data["comment"] if event_kind == "issue_comment" else (
                data["pull_request"] if event_kind == "pull_request" else data["issue"]
            )
            repo_id = _positive_id(repository["id"], "repository.id")
            repo_name = _bounded_text(repository["full_name"], "repository.full_name", limit=200)
            if repo_id != config["repository_id"] or repo_name.casefold() != config["repository"].casefold():
                raise PermissionError("GitHub repository is outside the configured source")
            sender_id = _positive_id(sender["id"], "sender.id")
            sender_login = _bounded_text(sender["login"], "sender.login", limit=100)
            sender_type = _bounded_text(sender["type"], "sender.type", limit=20)
            if sender_type not in {"User", "Bot"}:
                raise ValueError("GitHub sender type is invalid")
            if event_kind == "issue_comment":
                comment_author = entity["user"]
                if (
                    _positive_id(comment_author["id"], "comment.user.id") != sender_id
                    or _bounded_text(comment_author["login"], "comment.user.login", limit=100) != sender_login
                    or _bounded_text(comment_author["type"], "comment.user.type", limit=20) != sender_type
                ):
                    raise ValueError("GitHub comment author does not match sender")
            actor_id = sender_id
            author_login = sender_login
            author_type = sender_type
            number = _positive_id(entity.get("number", data.get("issue", {}).get("number")), "thread.number")
            entity_id = _positive_id(entity["id"], "entity.id")
            if event_kind == "issue_comment":
                external_id = f"github:comment:{entity_id}"
                text = _bounded_text(entity.get("body") or "", "comment.body", limit=_MAX_EVENT_TEXT_BYTES, empty=True)
                title = _bounded_text(data.get("issue", {}).get("title") or "", "issue.title", limit=4096, empty=True)
                url = _bounded_text(entity.get("html_url") or "", "comment.html_url", limit=2048, empty=True)
                occurred = _timestamp(entity.get("created_at"), received_at)
            else:
                external_id = f"github:issue:{entity_id}" if action == "opened" else f"github:issue:{entity_id}:{action}:{delivery}"
                text = _bounded_text(entity.get("body") or "", "issue.body", limit=_MAX_EVENT_TEXT_BYTES, empty=True)
                title = _bounded_text(entity.get("title") or "", "issue.title", limit=4096, empty=True)
                url = _bounded_text(entity.get("html_url") or "", "issue.html_url", limit=2048, empty=True)
                occurred = _timestamp(entity.get("updated_at") or entity.get("created_at"), received_at)
            human = author_type == "User" and actor_id != config["bot_user_id"]
            first = next((line.strip() for line in text.splitlines() if line.strip()), "")
            mentioned = human and bool(re.match(
                r"@" + re.escape(config["bot_login"]) + r"(?=$|[\s,:])", first, re.I | re.ASCII
            ))
            lifecycle_only = event_kind in {"issues", "pull_request"} and action == "closed"
            lifecycle = None
            if event_kind in {"issues", "pull_request"}:
                lifecycle_observed_at = _timestamp(
                    entity.get("updated_at") or entity.get("created_at"), received_at,
                )
                if action in {"opened", "reopened"}:
                    lifecycle = ThreadLifecycleUpdate(
                        "open", provider_revision=str(entity.get("updated_at") or delivery),
                        observed_at=lifecycle_observed_at,
                    )
                elif action == "closed":
                    merged = bool(entity.get("merged")) if event_kind == "pull_request" else False
                    lifecycle = ThreadLifecycleUpdate(
                        "closed", "merged" if merged else "closed",
                        provider_revision=str(entity.get("updated_at") or delivery),
                        observed_at=lifecycle_observed_at,
                    )
            thread_id = f"issue/{number}"
            return NormalizedTrigger(
                external_event_id=external_id,
                event_kind=f"github.{event_kind}.{action}",
                actor_id=f"github:{'user' if author_type == 'User' else 'bot'}:{actor_id}",
                actor_kind="human" if human else "bot",
                occurred_at=occurred,
                addressed="mention" if mentioned and not lifecycle_only else "none",
                threads=(NormalizedThread(thread_id, "primary", {"repository": repo_name}),),
                primary_thread_id=thread_id,
                body={"text": text, "title": title, "url": url},
                metadata={
                    "repository": repo_name, "repository_id": repo_id, "delivery_id": delivery,
                    "thread_number": number, "entity_id": entity_id, "scope_row": "default",
                    "lifecycle_only": lifecycle_only,
                },
                channel_address={"repository": repo_name, "issue_number": str(number), "thread_id": thread_id},
                lifecycle=lifecycle,
            )
        except PermissionError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as exc:
            raise ValueError("GitHub webhook payload is invalid") from exc


class FeishuSourceKind:
    """飞书加密事件回调；验签、解密并约束 app、tenant 与 chat。"""

    kind = "feishu"
    api_version = 1

    def validate_config(self, config: object) -> dict[str, Any]:
        allowed = {
            "app_id", "tenant_key", "bot_open_id", "chats",
            "verification_token_ref", "encrypt_key_ref",
        }
        if not isinstance(config, dict) or set(config) != allowed:
            raise ValueError("Feishu source config has an invalid shape")
        chats = config["chats"]
        if not isinstance(chats, list) or not 1 <= len(chats) <= 256:
            raise ValueError("Feishu chats must be a non-empty list")
        normalized_chats = sorted({_bounded_text(item, "chat_id", limit=128) for item in chats})
        if len(normalized_chats) != len(chats) or any(not item.startswith("oc_") for item in normalized_chats):
            raise ValueError("Feishu chat IDs are invalid")
        app_id = _bounded_text(config["app_id"], "app_id", limit=128)
        tenant_key = _bounded_text(config["tenant_key"], "tenant_key", limit=128)
        bot_open_id = _bounded_text(config["bot_open_id"], "bot_open_id", limit=128)
        token_ref = _bounded_text(config["verification_token_ref"], "verification_token_ref", limit=512)
        encrypt_ref = _bounded_text(config["encrypt_key_ref"], "encrypt_key_ref", limit=512)
        if not token_ref.startswith("secret://") or not encrypt_ref.startswith("secret://"):
            raise ValueError("Feishu credentials must reference the host secret store")
        if not bot_open_id.startswith("ou_"):
            raise ValueError("bot_open_id is invalid")
        return {
            "app_id": app_id, "tenant_key": tenant_key, "bot_open_id": bot_open_id,
            "chats": normalized_chats, "verification_token_ref": token_ref, "encrypt_key_ref": encrypt_ref,
        }

    def identity(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"app_id": config["app_id"], "tenant_key": config["tenant_key"]}

    def normalize(self, *, headers, raw, config, secret_resolver, received_at, local_identity_verifier=None):
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= _MAX_WEBHOOK_BYTES:
            raise ValueError("Feishu webhook body is invalid")
        encrypt_key = _secret(secret_resolver, config["encrypt_key_ref"], "Feishu encrypt")
        verification_token = _secret(secret_resolver, config["verification_token_ref"], "Feishu verification token")
        timestamp = _header(headers, "x-lark-request-timestamp")
        nonce = _header(headers, "x-lark-request-nonce")
        signature = _header(headers, "x-lark-signature")
        signed = any(item is not None for item in (timestamp, nonce, signature))
        if signed:
            if (
                not isinstance(timestamp, str) or re.fullmatch(r"[0-9]{1,16}", timestamp) is None
                or not isinstance(nonce, str) or not 1 <= len(nonce.encode("utf-8")) <= 256
                or not isinstance(signature, str) or re.fullmatch(r"[0-9a-f]{64}", signature) is None
                or not -300 <= time.time() - int(timestamp) <= 28_800
            ):
                raise PermissionError("Feishu request signature is invalid")
            expected = hashlib.sha256(timestamp.encode() + nonce.encode() + encrypt_key + raw).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise PermissionError("Feishu request signature is invalid")
        try:
            envelope = _strict_json(raw.decode("utf-8"))
            if not isinstance(envelope, dict) or set(envelope) != {"encrypt"} or not isinstance(envelope["encrypt"], str):
                raise ValueError("encrypted envelope is invalid")
            encrypted = base64.b64decode(envelope["encrypt"], validate=True)
            if len(encrypted) < 32 or len(encrypted) % 16:
                raise ValueError("encrypted payload length is invalid")
            try:
                from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            except ImportError as exc:
                raise RuntimeError("Feishu source requires the cryptography package") from exc
            decoder = Cipher(
                algorithms.AES(hashlib.sha256(encrypt_key).digest()),
                modes.CBC(encrypted[:16]),
            ).decryptor()
            plain = decoder.update(encrypted[16:]) + decoder.finalize()
            padding = plain[-1]
            if not 1 <= padding <= 16 or plain[-padding:] != bytes([padding]) * padding:
                raise ValueError("encrypted payload padding is invalid")
            data = _strict_json(plain[:-padding].decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("decrypted event must be an object")
            challenge = data.get("type") == "url_verification"
            token = data.get("token") if challenge else data.get("header", {}).get("token")
            if not isinstance(token, str) or not hmac.compare_digest(token.encode(), verification_token):
                raise PermissionError("Feishu verification token is invalid")
            if challenge:
                return SourceChallenge(_bounded_text(data.get("challenge"), "challenge", limit=1024))
            if not signed:
                raise PermissionError(tr("superagent.feishu-signature"))
            if data.get("schema") != "2.0":
                raise ValueError("unsupported Feishu event schema")
            header = data["header"]
            if header["app_id"] != config["app_id"] or header["tenant_key"] != config["tenant_key"]:
                raise PermissionError("Feishu app or tenant is outside the configured source")
            if header["event_type"] != "im.message.receive_v1":
                return SourceIgnored("unsupported_feishu_event")
            message, sender = data["event"]["message"], data["event"]["sender"]
            if sender["tenant_key"] != config["tenant_key"] or message["chat_id"] not in config["chats"]:
                raise PermissionError("Feishu tenant or chat is outside the configured source")
            if message["message_type"] not in {'text','image','file','audio','media','post'}:
                return SourceIgnored("unsupported_feishu_message_type")
            if message["chat_type"] not in {"group", "p2p"} or sender["sender_type"] not in {"user", "app"}:
                raise ValueError("Feishu message or sender type is invalid")
            chat_id = _bounded_text(message["chat_id"], "chat_id", limit=128)
            message_id = _bounded_text(message["message_id"], "message_id", limit=128)
            root_value = message.get("root_id")
            root_id = message_id if root_value in (None, "") else _bounded_text(root_value, "root_id", limit=128)
            actor = _bounded_text(sender["sender_id"]["open_id"], "sender.open_id", limit=128)
            if not chat_id.startswith("oc_") or not message_id.startswith("om_") or not root_id.startswith("om_") or not actor.startswith("ou_"):
                raise ValueError("Feishu identifiers are invalid")
            content = _strict_json(_bounded_text(message["content"], "message.content", limit=524_288))
            from .superagent_media_content import feishu_content
            body,rich_mention = feishu_content(message['message_type'],content,bot_id=config['bot_open_id'])
            text = body['text']
            actor_kind = "human" if sender["sender_type"] == "user" and actor != config["bot_open_id"] else "bot"
            mentions = message.get("mentions", [])
            if not isinstance(mentions, list) or len(mentions) > 64:
                raise ValueError("Feishu mentions are invalid")
            first = next((line.strip() for line in text.splitlines() if line.strip()), "")
            mentioned = rich_mention
            for mention in mentions:
                key = _bounded_text(mention["key"], "mention.key", limit=64)
                if (
                    mention["id"].get("open_id") == config["bot_open_id"]
                    and re.fullmatch(r"@_user_[0-9]+", key)
                    and re.match(re.escape(key) + r"(?=$|[\s,:])", first)
                ):
                    mentioned = True
            delivery = _bounded_text(header["event_id"], "event_id", limit=200)
            event_time = header.get("event_time")
            if isinstance(event_time, str) and re.fullmatch(r"[0-9]{1,16}", event_time):
                event_time = int(event_time)
            occurred = _timestamp(event_time, received_at)
            thread_id = f"chat/{chat_id}/root/{root_id}"
            return NormalizedTrigger(
                external_event_id=f"feishu:message:{message_id}",
                event_kind="feishu.im.message.receive_v1",
                actor_id=f"feishu:{sender['sender_type']}:{actor}",
                actor_kind=actor_kind,
                occurred_at=occurred,
                addressed=("direct" if message["chat_type"] == "p2p" else "mention" if mentioned and actor_kind == "human" else "none"),
                threads=(NormalizedThread(thread_id, "primary", {"chat_id": chat_id, "root_id": root_id}),),
                primary_thread_id=thread_id,
                body=body,
                metadata={
                    "app_id": config["app_id"], "tenant_key": config["tenant_key"],
                    "chat_id": chat_id, "chat_type": message["chat_type"],
                    "message_id": message_id, "delivery_id": delivery, "scope_row": "default",
                },
                channel_address={"chat_id": chat_id, "message_id": message_id, "root_id": root_id},
            )
        except PermissionError:
            raise
        except RuntimeError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as exc:
            raise ValueError("Feishu webhook payload is invalid") from exc


class LocalSourceKind:
    """宿主本地事件适配器；未注入宿主身份核验器时只能产生 system actor。"""

    kind = "local"
    api_version = 1

    def validate_config(self, config: object) -> dict[str, Any]:
        if not isinstance(config, dict) or set(config) != {"namespace"}:
            raise ValueError("Local source config has an invalid shape")
        return {"namespace": _bounded_text(config["namespace"], "namespace", limit=128)}

    def identity(self, config: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"namespace": config["namespace"]}

    def normalize(self, *, headers, raw, config, secret_resolver, received_at, local_identity_verifier=None):
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= 131_072:
            raise ValueError("Local event body is invalid")
        try:
            event = _strict_json(raw.decode("utf-8"))
            if (
                not isinstance(event, dict)
                or set(event) - {"event_id", "thread_id", "text", "occurred_at", "metadata", "channel_address"}
                or not {"event_id", "thread_id", "text", "occurred_at"} <= set(event)
            ):
                raise ValueError("Local event has an invalid shape")
            external_event_id = _bounded_text(event["event_id"], "event_id", limit=200)
            thread_id = _bounded_text(event["thread_id"], "thread_id", limit=512)
            text = _bounded_text(event["text"], "text", limit=_MAX_EVENT_TEXT_BYTES, empty=True)
            metadata = event.get("metadata", {})
            if not isinstance(metadata, dict):
                raise ValueError("Local metadata must be an object")
            identity = (
                local_identity_verifier(event)
                if local_identity_verifier is not None
                else {"actor_id": "harness:local", "actor_kind": "system", "addressed": "unknown"}
            )
            if not isinstance(identity, Mapping) or set(identity) != {"actor_id", "actor_kind", "addressed"}:
                raise PermissionError("Local identity verifier returned invalid evidence")
            actor_id = _bounded_text(identity["actor_id"], "actor_id", limit=256)
            actor_kind = identity["actor_kind"]
            addressed = identity["addressed"]
            if actor_kind not in {"human", "bot", "system"} or addressed not in {"mention", "direct", "none", "unknown"}:
                raise PermissionError("Local identity evidence is invalid")
            channel = event.get("channel_address", {"thread_id": thread_id})
            if channel is not None:
                if not isinstance(channel, dict) or len(channel) > 16:
                    raise ValueError("Local channel address is invalid")
                channel = {str(key): _bounded_text(value, "channel_address", limit=512) for key, value in channel.items()}
            return NormalizedTrigger(
                external_event_id=external_event_id,
                event_kind="local.message",
                actor_id=actor_id,
                actor_kind=actor_kind,
                occurred_at=_timestamp(event["occurred_at"], received_at),
                addressed=addressed,
                threads=(NormalizedThread(f"local/{config['namespace']}/{thread_id}"),),
                primary_thread_id=f"local/{config['namespace']}/{thread_id}",
                body={"text": text},
                metadata={**metadata, "namespace": config["namespace"], "scope_row": "default"},
                channel_address=channel,
                verification_state="local_attested",
            )
        except PermissionError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as exc:
            raise ValueError("Local event payload is invalid") from exc


class SourceRegistry:
    """统一 CLI／HTTP 的来源实例、规范化事件、修订与持久游标边界。"""

    def __init__(
        self, store: CoordinatorStore, *, ingest: Callable[..., dict[str, Any]] | None = None,
        on_config_changed: Callable[..., None] | None = None,
        on_kind_changed: Callable[..., None] | None = None,
        initial_kinds: tuple[SourceKind, ...] = (),
        secret_resolver: Callable[[str], bytes | str] | None = None,
        local_identity_verifier: LocalIdentityVerifier | None = None,
        register_builtins: bool = True,
    ):
        if not isinstance(initial_kinds, tuple):
            raise TypeError("initial_kinds must be a tuple of SourceKind plugins")
        self._store = store
        self._ingest = ingest
        self._on_config_changed = on_config_changed
        self._on_kind_changed = on_kind_changed
        self._secret_resolver = secret_resolver or self._missing_secret
        self._local_identity_verifier = local_identity_verifier
        self._kind_lock = RLock()
        self._kinds: dict[str, SourceKind] = {}
        self._initializing = True
        try:
            if register_builtins:
                for kind in (GitHubSourceKind(), FeishuSourceKind(), LocalSourceKind()):
                    self.register(kind)
            for kind in initial_kinds:
                self.register(kind)
        finally:
            self._initializing = False
        if register_builtins or initial_kinds:
            self._reconcile_kind_inventory()

    @staticmethod
    def _missing_secret(reference: str) -> bytes:
        raise PermissionError(f"host secret reference is unavailable: {reference}")

    def register(self, kind: SourceKind) -> Callable[[], None]:
        with self._kind_lock:
            return self._register(kind)

    def _register(self, kind: SourceKind) -> Callable[[], None]:
        name = _bounded_text(getattr(kind, "kind", None), "source_kind", limit=80)
        if re.fullmatch(r"[a-z][a-z0-9-]*", name) is None:
            raise ValueError("SourceKind IDs must be lowercase stable identifiers")
        if type(getattr(kind, "api_version", None)) is not int or kind.api_version < 1:
            raise ValueError("SourceKind api_version must be a positive integer")
        for method in ("validate_config", "identity", "normalize"):
            if not callable(getattr(kind, method, None)):
                raise TypeError(f"SourceKind is missing {method}")
        if name in self._kinds:
            raise ValueError(f"SourceKind already registered: {name}")
        self._kinds[name] = kind
        if not self._initializing:
            try:
                self._notify_kind_changed(name, kind.api_version)
            except BaseException:
                if self._kinds.get(name) is kind:
                    del self._kinds[name]
                raise

        def unregister() -> None:
            with self._kind_lock:
                if self._kinds.get(name) is kind:
                    self._notify_kind_changed(name, None)
                    del self._kinds[name]

        return unregister

    def _notify_kind_changed(self, kind_id: str, api_version: int | None) -> None:
        if self._on_kind_changed is None:
            return
        with self._store.transaction(write=True) as db:
            self._on_kind_changed(
                db, kind_id, api_version, actor={"kind": "host"},
            )

    def _reconcile_kind_inventory(self) -> None:
        if self._on_kind_changed is None:
            return
        with self._store.transaction(write=True) as db:
            rows = db.execute(
                "SELECT DISTINCT kind FROM source_instances ORDER BY kind"
            ).fetchall()
            for row in rows:
                plugin = self._kinds.get(row["kind"])
                self._on_kind_changed(
                    db, row["kind"], None if plugin is None else plugin.api_version,
                    actor={"kind": "host"},
                )

    def kind(self, kind_id: str) -> SourceKind | None:
        with self._kind_lock:
            return self._kinds.get(_bounded_text(kind_id, "kind_id", limit=80))

    def kinds(self) -> list[str]:
        with self._kind_lock:
            return sorted(self._kinds)

    def _row(self, source_instance_id: str):
        with self._store.transaction() as db:
            row = db.execute(
                "SELECT * FROM source_instances WHERE source_instance_id=?",
                (_bounded_text(source_instance_id, "source_instance_id", limit=128),),
            ).fetchone()
        if row is None:
            raise LookupError("source instance does not exist")
        return row

    @staticmethod
    def _view(row, *, installed: bool) -> SourceInstance:
        try:
            config = json.loads(row["config_json"])
            identity = json.loads(row["identity_json"])
            health = json.loads(row["health_json"])
            if (
                not isinstance(config, dict) or not isinstance(identity, dict) or not isinstance(health, dict)
                or _hash(_json(identity)) != row["identity_hash"]
                or type(row["enabled"]) is not int or row["enabled"] not in (0, 1)
                or type(row["config_revision"]) is not int or row["config_revision"] < 1
            ):
                raise ValueError("source instance record is corrupt")
        except (TypeError, ValueError, KeyError, RecursionError) as exc:
            raise RuntimeError("source instance record is corrupt") from exc
        return SourceInstance(
            source_instance_id=row["source_instance_id"], kind=row["kind"],
            kind_api_version=row["kind_api_version"], enabled=bool(row["enabled"]),
            config=config, config_revision=row["config_revision"],
            identity_hash=row["identity_hash"],
            installed=installed,
        )

    def create_instance(
        self, source_instance_id: str, *, kind: str, config: object, enabled: bool = True,
        actor: object | None = None,
    ) -> SourceInstance:
        source_instance_id = _bounded_text(source_instance_id, "source_instance_id", limit=128)
        plugin = self._kinds.get(_text(kind, "kind"))
        if plugin is None:
            raise LookupError(f"SourceKind is not installed: {kind}")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        parsed = plugin.validate_config(config)
        identity = dict(plugin.identity(parsed))
        identity_json = _json(identity)
        identity_hash = _hash(identity_json)
        now = _now()
        with self._store.transaction(write=True) as db:
            db.execute(
                """INSERT INTO source_instances(
                    source_instance_id,kind,kind_api_version,enabled,config_json,config_revision,
                    identity_json,identity_hash,health_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,1,?,?,?,?,?)""",
                (source_instance_id, kind, plugin.api_version, int(enabled), _json(parsed),
                 identity_json, identity_hash, _json({"state": "unknown"}), now, now),
            )
            CoordinatorStore.append_audit(
                db, aggregate_type="source_instance", aggregate_id=source_instance_id,
                event_type="source_instance.created",
                payload={"kind": kind, "kind_api_version": plugin.api_version, "enabled": enabled,
                         "config_revision": 1, "identity_hash": identity_hash},
                actor={"kind": "host"} if actor is None else actor,
            )
        return self.get_instance(source_instance_id)

    def update_instance(self, source_instance_id: str, *, expected_revision: int,
                        config: object | None = None, enabled: bool | None = None, actor: object | None = None) -> SourceInstance:
        with self._store.transaction(write=True) as db:
            return self.update_instance_in_transaction(db, source_instance_id, expected_revision=expected_revision,
                                                       config=config, enabled=enabled, actor=actor)

    def update_instance_in_transaction(self, db, source_instance_id: str, *, expected_revision: int,
                                      config: object | None = None, enabled: bool | None = None, actor: object | None = None):
        """来源修订、撤权及工作台操作回执共用原事务。"""
        source_instance_id = _bounded_text(source_instance_id, "source_instance_id", limit=128)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        row = db.execute(
            "SELECT * FROM source_instances WHERE source_instance_id=?", (source_instance_id,)
        ).fetchone()
        if row is None:
            raise LookupError("source instance does not exist")
        if row["config_revision"] != expected_revision:
            raise RuntimeError("source instance revision conflict")
        plugin = self._kinds.get(row["kind"])
        if plugin is None:
            raise LookupError("SourceKind is not installed")
        if plugin.api_version != row["kind_api_version"]:
            raise RuntimeError("SourceKind API version mismatch")
        old_config = json.loads(row["config_json"])
        parsed = plugin.validate_config(old_config if config is None else config)
        identity_json = _json(dict(plugin.identity(parsed)))
        if _hash(identity_json) != row["identity_hash"] or identity_json != row["identity_json"]:
            raise PermissionError("SourceInstance identity is immutable; create a new instance")
        target_enabled = bool(row["enabled"]) if enabled is None else enabled
        if type(target_enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        changed = _json(parsed) != row["config_json"] or int(target_enabled) != row["enabled"]
        if changed:
            new_revision = expected_revision + 1
            now = _now()
            db.execute(
                """UPDATE source_instances SET config_json=?,enabled=?,config_revision=?,
                   updated_at=? WHERE source_instance_id=? AND config_revision=?""",
                (_json(parsed), int(target_enabled), new_revision, now,
                 source_instance_id, expected_revision),
            )
            CoordinatorStore.append_audit(
                db, aggregate_type="source_instance", aggregate_id=source_instance_id,
                event_type="source_instance.configured",
                payload={"config_revision": new_revision, "enabled": target_enabled,
                         "config_hash": _hash(_json(parsed))},
                actor={"kind": "host"} if actor is None else actor,
            )
            if self._on_config_changed is not None:
                self._on_config_changed(
                    db, source_instance_id,
                    actor={"kind": "host"} if actor is None else actor,
                )
        current = db.execute('SELECT * FROM source_instances WHERE source_instance_id=?', (source_instance_id,)).fetchone()
        return self._view(current, installed=plugin.api_version == current['kind_api_version'])

    def get_instance(self, source_instance_id: str) -> SourceInstance:
        row = self._row(source_instance_id)
        plugin = self._kinds.get(row["kind"])
        compatible = plugin is not None and plugin.api_version == row["kind_api_version"]
        return self._view(row, installed=compatible)

    def list_instances(self) -> list[SourceInstance]:
        with self._store.transaction() as db:
            rows = db.execute("SELECT * FROM source_instances ORDER BY source_instance_id").fetchall()
        return [
            self._view(
                row,
                installed=(self._kinds.get(row["kind"]) is not None
                           and self._kinds[row["kind"]].api_version == row["kind_api_version"]),
            )
            for row in rows
        ]

    @staticmethod
    def _thread_view(row) -> dict[str, Any]:
        return {
            "thread_id": row["thread_id"],
            "source_instance_id": row["source_instance_id"],
            "external_thread_id": row["external_thread_id"],
            "metadata": json.loads(row["metadata_json"]),
            "source_state": row["source_state"],
            "source_outcome": row["source_outcome"],
            "provider_revision": row["provider_revision"],
            "source_observed_at": row["source_observed_at"],
            "last_event_at": row["last_event_at"],
            "aggregate_revision": row["aggregate_revision"],
            "lifecycle_revision": row["lifecycle_revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _channel_view(row, db) -> dict[str, Any]:
        capabilities = json.loads(row["capabilities_json"])
        binding = db.execute(
            "SELECT b.enabled,b.source_revision,s.config_revision,s.enabled AS source_enabled "
            "FROM channel_reply_bindings b JOIN channels c ON c.channel_id=b.channel_id "
            "JOIN source_instances s ON s.source_instance_id=c.source_instance_id WHERE b.channel_id=?",
            (row["channel_id"],),
        ).fetchone()
        if binding is not None:
            capabilities["reply"] = bool(binding["enabled"] and binding["source_enabled"]
                                         and binding["source_revision"] == binding["config_revision"])
        return {
            "channel_id": row["channel_id"],
            "source_instance_id": row["source_instance_id"],
            "thread_id": row["thread_id"],
            "address": json.loads(row["address_json"]),
            "address_hash": row["address_hash"],
            "capabilities": capabilities,
            "created_at": row["created_at"],
        }

    @staticmethod
    def _trigger_view(
        db, row, *, include_events: bool = False, include_payload: bool = True,
        event_limit: int = 200,
    ) -> dict[str, Any]:
        threads = db.execute(
            """SELECT thread_id,relation FROM trigger_threads
               WHERE trigger_id=? ORDER BY relation,thread_id""",
            (row["trigger_id"],),
        ).fetchall()
        disposition_row = db.execute(
            "SELECT * FROM trigger_dispositions WHERE trigger_id=?", (row["trigger_id"],),
        ).fetchone()
        disposition = None
        if disposition_row is not None:
            disposition = {
                "state": disposition_row["state"],
                "evaluation_id": disposition_row["evaluation_id"],
                "decision_id": disposition_row["decision_id"],
                "fanout_ticket_ids": json.loads(disposition_row["fanout_json"]),
                "context_ticket_ids": json.loads(disposition_row["context_json"]),
                "updated_at": disposition_row["updated_at"],
            }
            if include_events:
                event_count = db.execute(
                    "SELECT count(*) FROM trigger_disposition_events WHERE trigger_id=?",
                    (row["trigger_id"],),
                ).fetchone()[0]
                events = db.execute(
                    """SELECT event_id,event_type,payload_json,occurred_at,audit_event_id
                       FROM trigger_disposition_events
                       WHERE trigger_id=? ORDER BY occurred_at DESC,event_id DESC LIMIT ?""",
                    (row["trigger_id"], event_limit),
                ).fetchall()
                disposition["events"] = [
                    {
                        "event_id": event["event_id"],
                        "event_type": event["event_type"],
                        "payload": json.loads(event["payload_json"]),
                        "occurred_at": event["occurred_at"],
                        "audit_event_id": event["audit_event_id"],
                    }
                    for event in reversed(events)
                ]
                disposition["events_truncated"] = event_count > len(events)
        view = {
            "trigger_id": row["trigger_id"],
            "source_instance_id": row["source_instance_id"],
            "external_event_id": row["external_event_id"],
            "event_kind": row["event_kind"],
            "actor": json.loads(row["actor_json"]),
            "occurred_at": row["occurred_at"],
            "received_at": row["received_at"],
            "addressed": row["addressed"],
            "primary_thread_id": row["primary_thread_id"],
            "body_ref": row["body_ref"],
            "body_hash": row["body_hash"],
            "metadata_ref": row["meta_ref"],
            "metadata_hash": row["meta_hash"],
            "source_revision": row["source_revision"],
            "adapter_version": row["adapter_version"],
            "verification_state": row["verification_state"],
            "fingerprint": row["fingerprint"],
            "threads": [dict(item) for item in threads],
            "disposition": disposition,
        }
        if include_payload:
            view["body"] = json.loads(row["body_json"])
            view["metadata"] = json.loads(row["metadata_json"])
        return view

    def get_trigger(
        self, trigger_id: str, *, event_limit: int = 200,
    ) -> dict[str, Any] | None:
        """读取不可变 Trigger 事实及有界的追加式处置历史。"""
        trigger_id = _bounded_text(trigger_id, "trigger_id")
        if type(event_limit) is not int or not 1 <= event_limit <= 1000:
            raise ValueError("event_limit must be between 1 and 1000")
        with self._store.transaction() as db:
            row = db.execute(
                "SELECT * FROM triggers WHERE trigger_id=?", (trigger_id,),
            ).fetchone()
            return None if row is None else self._trigger_view(
                db, row, include_events=True, include_payload=True, event_limit=event_limit,
            )

    def list_triggers(
        self, *, source_instance_id: str | None = None, thread_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """按接收时间倒序列出来源事实；列表不展开逐条处置事件。"""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        conditions, params = [], []
        join = ""
        if source_instance_id is not None:
            conditions.append("t.source_instance_id=?")
            params.append(_bounded_text(source_instance_id, "source_instance_id", limit=128))
        if thread_id is not None:
            join = " JOIN trigger_threads tt ON tt.trigger_id=t.trigger_id"
            conditions.append("tt.thread_id=?")
            params.append(_bounded_text(thread_id, "thread_id", limit=128))
        sql = "SELECT DISTINCT t.* FROM triggers t" + join
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY t.received_at DESC,t.trigger_id DESC LIMIT ?"
        params.append(limit)
        with self._store.transaction() as db:
            rows = db.execute(sql, params).fetchall()
            return [self._trigger_view(db, row, include_payload=False) for row in rows]

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        thread_id = _bounded_text(thread_id, "thread_id", limit=128)
        with self._store.transaction() as db:
            row = db.execute("SELECT * FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
        return None if row is None else self._thread_view(row)

    def list_threads(
        self, *, source_instance_id: str | None = None, limit: int = 200,
    ) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        sql = "SELECT * FROM threads"
        params: list[Any] = []
        if source_instance_id is not None:
            sql += " WHERE source_instance_id=?"
            params.append(_bounded_text(source_instance_id, "source_instance_id", limit=128))
        sql += " ORDER BY created_at,thread_id LIMIT ?"
        params.append(limit)
        with self._store.transaction() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._thread_view(row) for row in rows]

    def get_channel(self, channel_id: str) -> dict[str, Any] | None:
        channel_id = _bounded_text(channel_id, "channel_id", limit=128)
        with self._store.transaction() as db:
            row = db.execute("SELECT * FROM channels WHERE channel_id=?", (channel_id,)).fetchone()
            return None if row is None else self._channel_view(row, db)

    def reply_channels(self, trigger_id):
        """回复绑定原 Trigger 的提供方目标，不能因同一 Thread 后续消息扩大收件集合。"""
        trigger = self.get_trigger(trigger_id)
        if trigger is None:
            raise LookupError(tr("superagent.corrupt"))
        source = self.get_instance(trigger["source_instance_id"])
        sql = "SELECT * FROM channels WHERE source_instance_id=? AND thread_id=?"
        params = [trigger["source_instance_id"], trigger["primary_thread_id"]]
        if source.kind == "feishu":
            sql += " AND json_extract(address_json,'$.message_id')=?"
            params.append(trigger["metadata"].get("message_id"))
        elif source.kind == "github":
            sql += " AND json_extract(address_json,'$.issue_number')=? AND json_extract(address_json,'$.repository')=?"
            params.extend([str(trigger["metadata"].get("thread_number")), trigger["metadata"].get("repository")])
        with self._store.transaction() as db:
            return [self._channel_view(row, db) for row in db.execute(sql + " ORDER BY channel_id LIMIT 1000", params)]

    def set_channel_reply(self, channel_id: str, *, expected_source_revision: int, enabled: bool, actor: object):
        """可信宿主登记传输能力；能力不代替每次发送的 Mandate 授权。"""
        with self._store.transaction(write=True) as db:
            return self.set_channel_reply_in_transaction(db, channel_id,
                expected_source_revision=expected_source_revision, enabled=enabled, actor=actor)

    def set_channel_reply_in_transaction(self, db, channel_id, *, expected_source_revision, enabled, actor):
        """回复能力修订与操作者请求回执共享原子事务。"""
        if type(enabled) is not bool or type(expected_source_revision) is not int:
            raise ValueError(tr("superagent.invalid", field="channel.reply"))
        row = db.execute("SELECT * FROM channels WHERE channel_id=?", (channel_id,)).fetchone()
        if row is None:
            raise LookupError(tr("superagent.corrupt"))
        source = db.execute("SELECT * FROM source_instances WHERE source_instance_id=?", (row["source_instance_id"],)).fetchone()
        if source is None or source["config_revision"] != expected_source_revision or enabled and not source["enabled"]:
            raise PermissionError(tr("superagent.mandate.unavailable"))
        binding = db.execute("SELECT * FROM channel_reply_bindings WHERE channel_id=?", (channel_id,)).fetchone()
        if binding is not None and bool(binding["enabled"]) is enabled and binding["source_revision"] == expected_source_revision:
            return self._channel_view(row, db)
        db.execute(
            "INSERT INTO channel_reply_bindings VALUES(?,?,?,1,?) ON CONFLICT(channel_id) DO UPDATE SET "
            "source_revision=excluded.source_revision,enabled=excluded.enabled,aggregate_revision=channel_reply_bindings.aggregate_revision+1,updated_at=excluded.updated_at",
            (channel_id, expected_source_revision, int(enabled), _now()),
        )
        self._store.append_audit(db, aggregate_type="channel", aggregate_id=channel_id,
            event_type="channel.reply_configured", payload={"reply": enabled, "source_revision": expected_source_revision}, actor=actor)
        return self._channel_view(row, db)

    def list_channels(
        self, *, source_instance_id: str | None = None, thread_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        conditions, params = [], []
        if source_instance_id is not None:
            conditions.append("source_instance_id=?")
            params.append(_bounded_text(source_instance_id, "source_instance_id", limit=128))
        if thread_id is not None:
            conditions.append("thread_id=?")
            params.append(_bounded_text(thread_id, "thread_id", limit=128))
        sql = "SELECT * FROM channels"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY created_at,channel_id LIMIT ?"
        params.append(limit)
        with self._store.transaction() as db:
            rows = db.execute(sql, params).fetchall()
            return [self._channel_view(row, db) for row in rows]

    def get_cursor(self, source_instance_id: str, stream_key: str) -> dict[str, Any] | None:
        source_instance_id = _bounded_text(source_instance_id, "source_instance_id", limit=128)
        stream_key = _bounded_text(stream_key, "stream_key", limit=256)
        with self._store.transaction() as db:
            row = db.execute(
                "SELECT cursor_json,cursor_revision,updated_at FROM source_cursors WHERE source_instance_id=? AND stream_key=?",
                (source_instance_id, stream_key),
            ).fetchone()
        if row is None:
            return None
        return {
            "source_instance_id": source_instance_id, "stream_key": stream_key,
            "cursor": json.loads(row["cursor_json"]), "revision": row["cursor_revision"],
            "updated_at": row["updated_at"],
        }

    def advance_cursor(
        self, source_instance_id: str, stream_key: str, cursor: Mapping[str, Any], *,
        expected_revision: int, actor: object,
    ) -> dict[str, Any]:
        source_instance_id = _bounded_text(source_instance_id, "source_instance_id", limit=128)
        stream_key = _bounded_text(stream_key, "stream_key", limit=256)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        if not isinstance(cursor, Mapping):
            raise ValueError("cursor must be an object")
        encoded = _json(dict(cursor))
        if len(encoded.encode("utf-8")) > 16_384:
            raise ValueError("cursor payload is too large")
        now = _now()
        with self._store.transaction(write=True) as db:
            source = db.execute(
                "SELECT kind,kind_api_version,enabled FROM source_instances WHERE source_instance_id=?",
                (source_instance_id,),
            ).fetchone()
            if source is None:
                raise LookupError("source instance does not exist")
            if not source["enabled"]:
                raise PermissionError("SourceInstance is disabled")
            plugin = self._kinds.get(source["kind"])
            if plugin is None:
                raise LookupError("SourceKind is not installed")
            if plugin.api_version != source["kind_api_version"]:
                raise RuntimeError("SourceKind API version mismatch")
            current = db.execute(
                "SELECT cursor_json,cursor_revision,updated_at FROM source_cursors WHERE source_instance_id=? AND stream_key=?",
                (source_instance_id, stream_key),
            ).fetchone()
            if current is None:
                if expected_revision != 0:
                    raise RuntimeError("source cursor revision conflict")
                revision = 1
                db.execute(
                    """INSERT INTO source_cursors(source_instance_id,stream_key,cursor_json,cursor_revision,updated_at)
                       VALUES(?,?,?,?,?)""",
                    (source_instance_id, stream_key, encoded, revision, now),
                )
            else:
                if current["cursor_json"] == encoded:
                    return {
                        "source_instance_id": source_instance_id, "stream_key": stream_key,
                        "cursor": json.loads(encoded), "revision": current["cursor_revision"],
                        "updated_at": current["updated_at"], "state": "duplicate",
                    }
                if current["cursor_revision"] != expected_revision:
                    raise RuntimeError("source cursor revision conflict")
                revision = expected_revision + 1
                cursor_update = db.execute(
                    """UPDATE source_cursors SET cursor_json=?,cursor_revision=?,updated_at=?
                       WHERE source_instance_id=? AND stream_key=? AND cursor_revision=?""",
                    (encoded, revision, now, source_instance_id, stream_key, expected_revision),
                )
                if cursor_update.rowcount != 1:
                    raise RuntimeError("source cursor compare-and-swap failed")
            CoordinatorStore.append_audit(
                db, aggregate_type="source_cursor", aggregate_id=f"{source_instance_id}:{stream_key}",
                event_type="source.cursor_advanced",
                payload={"source_instance_id": source_instance_id, "stream_key": stream_key,
                         "revision": revision, "cursor_hash": _hash(encoded)},
                actor=actor,
            )
        return {
            "source_instance_id": source_instance_id, "stream_key": stream_key,
            "cursor": json.loads(encoded), "revision": revision, "updated_at": now, "state": "advanced",
        }

    def receive(self, source_instance_id: str, *, headers: Mapping[str, object], raw: bytes) -> dict[str, Any]:
        with self._kind_lock:
            return self._receive_locked(source_instance_id, headers=headers, raw=raw)

    def _receive_locked(
        self, source_instance_id: str, *, headers: Mapping[str, object], raw: bytes,
    ) -> dict[str, Any]:
        source_instance_id = _bounded_text(source_instance_id, "source_instance_id", limit=128)
        if not isinstance(headers, Mapping):
            raise ValueError("headers must be a mapping")
        if (
            len(headers) > 128
            or any(
                not isinstance(key, str) or not key or len(key) > 128 or "\x00" in key
                for key in headers
            )
            or any(
                not isinstance(value, str) or len(value) > 8192 or "\x00" in value
                for value in headers.values()
            )
            or sum(len(value) for value in headers.values()) > 32_768
        ):
            raise ValueError("headers contain invalid or excessive entries")
        normalized_headers = [key.casefold() for key in headers]
        if len(set(normalized_headers)) != len(normalized_headers):
            raise ValueError("headers contain duplicate case-insensitive names")
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= _MAX_WEBHOOK_BYTES:
            raise ValueError("source event body is invalid or too large")
        row = self._row(source_instance_id)
        plugin = self._kinds.get(row["kind"])
        if plugin is None:
            raise LookupError("SourceKind is not installed")
        if plugin.api_version != row["kind_api_version"]:
            raise RuntimeError("SourceKind API version mismatch")
        instance = self._view(row, installed=True)
        if not instance.enabled:
            raise PermissionError("SourceInstance is disabled")
        config = plugin.validate_config(instance.config)
        normalized = plugin.normalize(
            headers=headers, raw=raw, config=config, secret_resolver=self._secret_resolver,
            received_at=_now(), local_identity_verifier=self._local_identity_verifier,
        )
        if isinstance(normalized, SourceChallenge):
            return {"state": "challenge", "challenge": normalized.challenge}
        if isinstance(normalized, SourceIgnored):
            return {"state": "ignored", "reason": normalized.reason}
        if not isinstance(normalized, NormalizedTrigger):
            raise TypeError("SourceKind.normalize must return a normalized trigger, challenge or ignored event")
        if self._ingest is None:
            raise RuntimeError("SourceRegistry has no CoordinatorAuthorizationEngine intake")
        delivery = None
        if plugin.kind == 'github':
            with self._store.transaction() as db:
                prior = db.execute('SELECT trigger_id,metadata_json FROM triggers WHERE source_instance_id=? AND external_event_id=?', (source_instance_id, normalized.external_event_id)).fetchone()
            if prior is not None:
                metadata = json.loads(prior['metadata_json'])
                incoming = normalized.metadata.get('delivery_id')
                if incoming != metadata.get('delivery_id'):
                    delivery = {'delivery_id':incoming, 'original_delivery_id':metadata.get('delivery_id')}
                    # Provider 重投的传输身份单独审计，原 Trigger 的已验证事实不改写。
                    normalized = replace(normalized, metadata={**normalized.metadata, 'delivery_id':metadata.get('delivery_id')},
                        lifecycle=replace(normalized.lifecycle, provider_revision=metadata.get('delivery_id')) if normalized.lifecycle is not None else None)
        result = self._ingest(
            source_instance_id=source_instance_id,
            expected_source_revision=instance.config_revision,
            kind=plugin.kind,
            adapter_version=f"{plugin.kind}/{plugin.api_version}",
            trigger=normalized,
        )
        if delivery is not None and result.get('state') == 'duplicate':
            payload = _json(delivery)
            with self._store.transaction(write=True) as db:
                if db.execute("SELECT 1 FROM audit_events WHERE aggregate_id=? AND event_type='trigger.redelivered' AND payload_json=?", (result['trigger_id'], payload)).fetchone() is None:
                    self._store.append_audit(db, aggregate_type='trigger', aggregate_id=result['trigger_id'],
                        event_type='trigger.redelivered', payload=delivery, actor={'kind':'source', 'source_instance_id':source_instance_id})
        return result


    def receive_local(self, source_instance_id: str, event: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(event, Mapping):
            raise ValueError("Local event must be a mapping")
        raw = _json(dict(event)).encode("utf-8")
        return self.receive(source_instance_id, headers={}, raw=raw)

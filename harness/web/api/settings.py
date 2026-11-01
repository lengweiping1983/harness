"""运行时设置：模型默认值与思考强度。

为什么需要它
------------
模型选择天然是**两级**的：全局默认 + 会话覆盖。缺了全局默认这一级，用户
只能"先建会话再改模型"，每换一个项目都要重来一遍；而 ``HARNESS_MODEL``
环境变量是进程启动时读死的，改一次就得重启后端 —— 这在"设置面板里点一下
应该立刻生效"的直觉面前非常不友好。

所以这里做一份**可写、原子落盘、重启存活**的运行时设置：写在
``<HARNESS_HOME>/ui-settings.json``，并同步写回 ``harness.config``，
让随后创建的 AgentLoop 立刻采用新默认值。已经在跑的回合不受影响
（会话自己的模型覆盖优先级更高）。
"""

from __future__ import annotations

from harness.i18n import tr
from ...core.fsatomic import atomic_write
from harness.llm.routes import mask_api_key

import json
import os
import threading
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from ..responses import json_response
from ..router import Router

#: 设置文件名（与 ``workspaces.json`` 同级）。
SETTINGS_FILENAME = "ui-settings.json"

#: 允许从接口改写的键 —— 白名单，不接受任意字段落盘。
EDITABLE: tuple[str, ...] = ("default_model", "reasoning_effort", "model_visibility", "language", "deepseek_web_enabled", "deepseek_web_api_key", "brave_web_enabled", "brave_web_api_key")


def _effort_ok(value: Any) -> bool:
    from ..models import REASONING_EFFORTS

    return isinstance(value, str) and value in set(REASONING_EFFORTS) | {'none', 'off', 'xhigh', 'max', 'ultra'}


def _normalize_setting(key: str, value: Any) -> Any:
    if key in {"deepseek_web_enabled", "brave_web_enabled"} and type(value) is bool:
        return value
    if key in {"deepseek_web_api_key", "brave_web_api_key"} and isinstance(value, str):
        secret = value.strip()
        if all(33 <= ord(char) <= 126 for char in secret):
            return secret
        raise ValueError(tr('python.settings.invalid_key'))
    if key == "language":
        from ...core.language import LANGUAGES
        if isinstance(value, str) and value in LANGUAGES:
            return value
    if key == "default_model" and isinstance(value, str) and value.strip():
        return value.strip()
    if key == "reasoning_effort":
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if _effort_ok(value):
            return value
    if key == "model_visibility" and isinstance(value, dict):
        if all(isinstance(model_id, str) and model_id.strip() and isinstance(visible, bool)
               for model_id, visible in value.items()):
            return dict(value)
    raise ValueError(tr('python.settings.invalid_field'))


class SettingsStore:
    """运行时设置。

    :param home: ``HARNESS_HOME``；``None`` 表示纯内存（测试用）。
    """

    def __init__(self, home: str | os.PathLike[str] | None = None) -> None:
        self._path = Path(home).expanduser() / SETTINGS_FILENAME if home is not None else None
        self._lock = threading.RLock()
        self._items: dict[str, Any] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        if self._path is None:
            self._loaded = True
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._loaded = True
            return
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(tr('python.settings.format')) from exc
        if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1 or not isinstance(raw.get("settings"), dict):
            raise ValueError(tr('python.settings.format'))
        inner = raw["settings"]
        if any(key not in EDITABLE for key in inner):
            raise ValueError(tr('python.settings.unknown'))
        candidate = {key: _normalize_setting(key, value) for key, value in inner.items()}
        self._items = candidate
        self._loaded = True

    def _persist(self, items: dict[str, Any]) -> None:
        if self._path is None:
            return
        payload = {"version": 1, "settings": items}
        # 固定 UTF-8/LF 字节，避免共享发布器的文本模式在 Windows 转换换行。
        atomic_write(self._path, json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8'))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._load()
            return deepcopy(self._items)

    @contextmanager
    def locked(self) -> Iterator[None]:
        """串行化已保存设置的操作及其运行时应用。"""
        with self._lock:
            yield

    def update(self, changes: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """合入白名单内的改动。

        返回 ``(最新快照, 被拒绝的键列表)``。非法值不当成错误抛出去 ——
        设置项写错一个字段就把整个面板卡死，比"这一项没生效"糟糕得多。
        """
        rejected: list[str] = []
        with self._lock:
            self._load()
            candidate = dict(self._items)
            for key, value in changes.items():
                try:
                    normalized = _normalize_setting(key, value)
                except ValueError:
                    rejected.append(key)
                    continue
                if key == "model_visibility":
                    candidate[key] = {**candidate.get(key, {}), **normalized}
                else:
                    candidate[key] = normalized
            if candidate != self._items:
                self._persist(candidate)
                self._items = candidate
            return deepcopy(self._items), rejected


def build_router(ctx: Any) -> Router:
    router = Router()

    def store() -> SettingsStore:
        cached = ctx.__dict__.get("_ui_settings")
        if cached is None:
            home = getattr(getattr(ctx.harness, "config", None), "home", None)
            cached = SettingsStore(home)
            ctx.__dict__["_ui_settings"] = cached
        return cached

    def apply_runtime(settings: dict[str, Any], *, restoring: bool = False) -> None:
        config = getattr(ctx.harness, "config", None)
        if config is None:
            return
        for key in ("deepseek_web_enabled", "deepseek_web_api_key", "brave_web_enabled", "brave_web_api_key"):
            if key in settings:
                setattr(config, key, settings[key])
        if "language" in settings and not (restoring and getattr(config, "_language_explicit", False)):
            config.language = settings["language"]
        model = settings.get("default_model")
        if isinstance(model, str) and model.strip() and (model.startswith(('harness/', 'chatgpt/')) or not getattr(config, 'model_routes', [])):
            config.model = model.strip()
            agent = getattr(ctx.harness, 'agent', None)
            if agent is not None:
                agent.config = replace(agent.config, model=model.strip())
        if "reasoning_effort" in settings:
            effort = settings["reasoning_effort"]
            if effort is None or (isinstance(effort, str) and _effort_ok(effort)):
                config.reasoning_effort = effort
                # 请求默认模板与宿主配置分别持有快照，二者都须采用已保存强度。
                agent = getattr(ctx.harness, 'agent', None)
                if agent is not None:
                    agent.config = replace(agent.config, reasoning_effort=effort)

    # 服务端提供目录或执行回合前，先恢复已持久保存的默认值。
    with store().locked():
        apply_runtime(store().snapshot(), restoring=True)

    def response_settings(snapshot: dict[str, Any]) -> dict[str, Any]:
        from ...core.language import resolve_language
        config = getattr(ctx.harness, "config", None)
        snapshot = dict(snapshot)
        for name in ("deepseek", "brave"):
            secret = snapshot.pop(f"{name}_web_api_key", getattr(config, f"{name}_web_api_key", ""))
            snapshot[f"{name}_web_api_key_configured"] = bool(secret)
            snapshot[f"{name}_web_api_key_mask"] = mask_api_key(secret)
            snapshot.setdefault(f"{name}_web_enabled", getattr(config, f"{name}_web_enabled", False))
        account = getattr(config, 'chatgpt_account', None)
        if account is not None and account.logged_in and not hasattr(config, 'provider_connections'):
            snapshot = {**snapshot, 'default_model': account.selected}
        elif str(snapshot.get('default_model', '')).startswith('chatgpt/') and not hasattr(config, 'provider_connections'):
            snapshot = {**snapshot, 'default_model': config.model}
        return {**snapshot, **resolve_language(getattr(config, "language", "auto")).to_dict()}

    @router.get("/api/v1/ui-settings")
    def get_settings(request: Any) -> Any:
        with store().locked():
            snapshot = response_settings(store().snapshot())
        return json_response({"settings": snapshot})

    @router.patch("/api/v1/ui-settings")
    def patch_settings(request: Any) -> Any:
        """改写运行时设置，并把模型默认值同步到 ``harness.config``。

        同步这一步是刻意的：光落盘的话，已经在内存里建好的 AgentLoop 还拿着
        旧模型，用户会看到"改了没反应"。
        """
        payload = request.json()
        if not isinstance(payload, dict):
            from ..errors import BadRequest

            raise BadRequest(tr('python.settings.5eb5b468bd70'), code="settings/bad-body")
        rejected_fields = []
        for key, value in payload.items():
            try:
                _normalize_setting(key, value)
            except ValueError:
                rejected_fields.append(key)
        if payload and len(rejected_fields) == len(payload):
            from ..errors import BadRequest

            raise BadRequest(
                tr('python.settings.invalid_field'),
                code='settings/invalid-fields',
                details={'rejected': rejected_fields},
            )
        with store().locked():
            applied_model = None
            try:
                account = getattr(getattr(ctx.harness, 'config', None), 'chatgpt_account', None)
                changes = dict(payload)
                if account is not None and account.logged_in and 'default_model' in changes and str(changes['default_model']).startswith('chatgpt/') and not hasattr(ctx.harness, 'provider_connections'):
                    try:
                        requested_model = changes.pop('default_model')
                        if account.select(requested_model):
                            applied_model = requested_model
                    except ValueError as exc:
                        from ..errors import BadRequest
                        raise BadRequest(str(exc), code='model/unknown') from None
                settings, rejected = store().update(changes)
            except OSError as error:
                from ..errors import ServiceUnavailable

                if applied_model is not None:
                    raise ServiceUnavailable(
                        tr('python.settings.partially_applied'),
                        code='settings/partially-applied',
                        details={'applied': {'default_model': applied_model}},
                    ) from error
                raise ServiceUnavailable(
                    tr('python.settings.9dce6b30f7bf'),
                    code="settings/persist-failed",
                ) from error
            apply_runtime(settings)
            snapshot = response_settings(settings)
        if 'language' in payload and 'language' not in rejected and hasattr(ctx, 'loop') and hasattr(ctx, 'hub'):
            def publish():
                for sid in ctx.hub.rooms():
                    ctx.hub.publish(sid, ctx.hub.stamp(sid, {'type': 'settings/changed', 'session_id': sid}))
            ctx.loop.call_soon_threadsafe(publish)
        return json_response({"settings": snapshot, "rejected": rejected})

    return router


__all__ = ["build_router", "SettingsStore", "SETTINGS_FILENAME", "EDITABLE"]

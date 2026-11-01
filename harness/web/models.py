"""已配置模型的只读目录投影，不在读取菜单时发起网络探测。

宿主汇总供应商接入及订阅账号池；模型目录以用户在设置里保存的供应商接入为准，
目录外模型由当前 ``config.model`` / ``model_routes`` 兜底合成展示项。
模型身份由供应商、接入和模型组成，展示名称与第三方名称保持原文。
"""

from __future__ import annotations

from harness.i18n import tr

from dataclasses import dataclass
from typing import Any

#: 支持的思考强度；``None`` 表示该模型不暴露 reasoning 参数。
REASONING_EFFORTS: tuple[str, ...] = ("minimal", "low", "medium", "high")


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """一个可选模型的展示元数据。"""

    id: str
    label: str
    provider: str
    description_key: str = ""
    context_window: int = 0
    supports_reasoning: bool = False
    #: 已知的输出上限；0 表示未知（前端不显示）。
    max_output: int = 0

    def to_dict(self, *, active: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "description": tr(self.description_key) if self.description_key else "",
            "supports_reasoning": self.supports_reasoning,
            "active": active,
        }
        if self.context_window:
            payload["context_window"] = self.context_window
        if self.max_output:
            payload["max_output"] = self.max_output
        if self.supports_reasoning:
            payload["reasoning_efforts"] = list(REASONING_EFFORTS)
        return payload


def _synthetic(spec_id: str, provider: str) -> ModelSpec:
    """为目录外的模型兜底出一份最小元数据。

    配置里的模型（私有网关、临时灰度）必须永远可选 —— 否则用户一旦改了
    ``HARNESS_MODEL``，界面就会变成"没有可用模型"，这比没有选择功能更糟。
    """
    tail = spec_id.rsplit("/", 1)[-1] if "/" in spec_id else spec_id
    label = " ".join(part.capitalize() for part in tail.replace("_", "-").split("-") if part)
    return ModelSpec(
        id=spec_id,
        label=label or spec_id,
        provider=provider,
        description_key='python.models.8006c3f670ae',
        context_window=0,
    )


def model_catalog(config: Any) -> dict[str, Any]:
    """返回 ``{"models": [...], "active": id, "providers": [...]}``。

    :param config: :class:`~harness.app.config.HarnessConfig`；只需读
        ``model`` / ``base_url`` / ``reasoning_effort``。
    """
    connections = getattr(config, 'provider_connections', None)
    if connections is not None:
        configured = connections.catalog()
        pool = getattr(config, 'subscription_pool', None)
        seen = {m['id'] for m in configured}
        if pool is not None:
            for slot, duplicate in pool._unique():
                if slot.enabled and not duplicate and slot.account.logged_in:
                    for model in slot.account.status()['models']:
                        if model['id'] not in seen:
                            configured.append({**model, 'provider': 'chatgpt'})
                            seen.add(model['id'])
        active = getattr(config, 'model', '')
        if not configured and getattr(config, 'api_key', ''):
            return {'models': [_synthetic(active, 'OpenRouter').to_dict(active=True)], 'active': active,
                    'providers': ['OpenRouter'], 'reasoning_effort': getattr(config, 'reasoning_effort', None)}
        return {'models': [{**m, 'active': m['id'] == active} for m in configured], 'active': active if active in seen else '',
                'providers': list(dict.fromkeys(m['provider'] for m in configured)), 'reasoning_effort': getattr(config, 'reasoning_effort', None)}
    account = getattr(config, 'chatgpt_account', None)
    if account is not None and account.logged_in:
        status = account.status()
        return {'models': [{**m, 'active': m['id'] == status['selected']} for m in status['models']],
                'active': status['selected'], 'providers': ['chatgpt'],
                'reasoning_effort': getattr(config, 'reasoning_effort', None)}
    active_id = str(getattr(config, "model", "") or "")
    provider = "OpenRouter"
    items: list[ModelSpec] = []
    seen_ids: set[str] = set()
    for connection in reversed(getattr(config, 'model_routes', []) or []):
        configured_id = connection['model']
        if configured_id and configured_id not in seen_ids:
            items.append(_synthetic(configured_id, provider))
            seen_ids.add(configured_id)
    if active_id and active_id not in seen_ids:
        items.insert(0, _synthetic(active_id, provider))
        seen_ids.add(active_id)
    models = [spec.to_dict(active=spec.id == active_id) for spec in items]
    providers = list(dict.fromkeys(spec.provider for spec in items))
    return {
        "models": models,
        "active": active_id,
        "providers": providers,
        "reasoning_effort": getattr(config, "reasoning_effort", None),
    }


__all__ = ["ModelSpec", "REASONING_EFFORTS", "model_catalog"]

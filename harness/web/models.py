"""已配置模型的只读目录投影，不在读取菜单时发起网络探测。

宿主汇总用户在设置里保存的供应商接口及已登录的订阅账号池，
不从单模型配置或环境变量合成展示项。
模型身份由供应商、接口和模型组成，展示名称与第三方名称保持原文。
"""

from __future__ import annotations

from typing import Any

#: 支持的思考强度；``None`` 表示该模型不暴露 reasoning 参数。
REASONING_EFFORTS: tuple[str, ...] = ("minimal", "low", "medium", "high")


def model_catalog(config: Any) -> dict[str, Any]:
    """返回 ``{"models": [...], "active": id, "providers": [...]}``。

    :param config: :class:`~harness.app.config.HarnessConfig`；读取提供商连接、
        订阅账号和当前模型及思考强度。
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
        return {'models': [{**m, 'active': m['id'] == active} for m in configured], 'active': active if active in seen else '',
                'providers': list(dict.fromkeys(m['provider'] for m in configured)), 'reasoning_effort': getattr(config, 'reasoning_effort', None)}
    account = getattr(config, 'chatgpt_account', None)
    if account is not None and account.logged_in:
        status = account.status()
        return {'models': [{**m, 'active': m['id'] == status['selected']} for m in status['models']],
                'active': status['selected'], 'providers': ['chatgpt'],
                'reasoning_effort': getattr(config, 'reasoning_effort', None)}
    return {
        "models": [],
        "active": "",
        "providers": [],
        "reasoning_effort": getattr(config, "reasoning_effort", None),
    }


__all__ = ["REASONING_EFFORTS", "model_catalog"]

"""子 Agent 模型目录查询。

该工具固定使用 OpenRouter provider 标识及构造时传入的连接和默认模型；
不汇总运行时的多连接路由目录。无参返回本地配置，指定 provider 查询目录，
指定 model 再显示该条目及本地推理档位映射。

目录读取有大小上限并严格检查编码与结构。网络、鉴权或解析失败时，
保留明确的目录不可用提示和本地默认模型信息，不把失败当成有效目录。
"""

from __future__ import annotations

from harness.i18n import tr

import asyncio
import json
from typing import Any

from ...core.errors import ToolExecutionError
from ...core.cleanup import join_cleanup, raise_cleanup_errors
from ...llm.types import TextBlock
from ...llm.catalog_fetch import read_catalog
from ..registry import ToolDefinition, ToolRunContext, ToolResult
from ..processes import ProcessCleanupError

#: 本 harness 唯一注册的 provider id（与 OpenRouter 端点一致）
PROVIDER_ID = "openrouter"
PROVIDER_NAME = "OpenRouter"
#: 目录查询超时（秒）
CATALOG_TIMEOUT = 20.0
#: 目录返回的模型条数上限（避免把上千个模型塞进上下文）
MAX_MODELS = 200
#: 目录读取大小上限；不能先无界读取再截断模型条目。
MAX_CATALOG_BYTES = 8 * 1024 * 1024
#: 目录明确声明 reasoning 参数后可展示的本地预设，不代表远端逐档声明。
_EFFORT_KEYS: tuple[tuple[str, str], ...] = (
    ("low", 'python.subagent_model_tools.aa9e366f68d3'),
    ("medium", 'python.subagent_model_tools.a567bdaa1136'),
    ("high", 'python.subagent_model_tools.b1c27820fec2'),
)

_DESCRIPTION_KEY = 'python.subagent_model_tools.af2589c32fe3'


def _model_line(provider: str, model: dict[str, Any]) -> str:
    model_id = model['id']
    name = model.get('name') or model_id
    return f"{provider}/{model_id} — {name}"


def _fetch_catalog(base_url: str, api_key: str) -> list[dict[str, Any]]:
    """有界读取当前目录，拒绝损坏编码或结构，不伪装成合法空目录。"""
    url = base_url.rstrip("/") + "/api/v1/models"
    raw = read_catalog(url, api_key, timeout=CATALOG_TIMEOUT, limit=MAX_CATALOG_BYTES)
    if len(raw) > MAX_CATALOG_BYTES:
        raise ValueError(tr('tools.models.catalogTooLarge', limit=MAX_CATALOG_BYTES))
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError
            value[key] = item
        return value
    try:
        payload = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
        pending = [payload]
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                value.encode('utf-8')
            elif isinstance(value, dict):
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError(tr('tools.models.catalogInvalid')) from None
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list) or not all(isinstance(item, dict) for item in payload['data']):
        raise ValueError(tr('tools.models.catalogInvalid'))
    seen = set()
    for item in payload['data']:
        identifier = item.get('id')
        if (not isinstance(identifier, str) or not identifier.strip() or identifier in seen
                or 'name' in item and not isinstance(item['name'], str)):
            raise ValueError(tr('tools.models.catalogInvalid'))
        seen.add(identifier)
    return payload['data']


def _efforts_of(model: dict[str, Any]) -> list[tuple[str, str]] | None:
    """区分未报告、明确无 reasoning 参数，以及可展示的本地预设。"""
    supported = model.get("supported_parameters")
    if not isinstance(supported, list) or not all(isinstance(item, str) for item in supported):
        return None
    if not any(item.startswith("reasoning") for item in supported):
        return []
    return [(identifier, tr(key)) for identifier, key in _EFFORT_KEYS]


def build_list_subagent_models_tool(
    *, base_url: str, model: str, api_key: str = "", model_directory=None
) -> ToolDefinition:
    """构造 ``list_subagent_models``。

    :param base_url: OpenRouter 端点。
    :param model: 当前 harness 配置的默认模型 id。
    :param api_key: 用于目录查询的 key（缺失时只查未鉴权目录）。
    """

    async def list_subagent_models(args: Any, ctx: ToolRunContext) -> ToolResult:
        if not isinstance(args, dict):
            raise ToolExecutionError(tr('tools.models.argumentsInvalid'))
        for field in ('provider', 'model'):
            if field in args and not isinstance(args[field], str):
                raise ToolExecutionError(tr('tool.arguments.text', argument=field))
        provider = args.get('provider', '').strip()
        wanted_model = args.get('model', '').strip()

        if wanted_model and not provider:
            raise ToolExecutionError(tr('python.subagent_model_tools.e78934039982'))

        if model_directory is not None:
            catalog = model_directory()
            providers = list(dict.fromkeys(item['provider'] for item in catalog))
            if provider and provider not in providers:
                raise ToolExecutionError(tr('models.model.missing'))
            selected = [item for item in catalog if (not provider or item['provider'] == provider) and (not wanted_model or item['id'] == wanted_model)]
            if wanted_model and not selected:
                raise ToolExecutionError(tr('models.model.missing'))
            data = {'providers': providers, 'models': selected, 'catalog_available': True}
            return ToolResult(content=[TextBlock(text=json.dumps(data, ensure_ascii=False))], display=data, data=data)

        data: dict[str, Any] = {"provider": provider or PROVIDER_ID}

        if not provider:
            data["providers"] = [
                {"id": PROVIDER_ID, "name": PROVIDER_NAME, "default_model": model}
            ]
            text = (
                tr('python.subagent_model_tools.81ce938a6396', p0=f'{PROVIDER_ID}', p1=f'{PROVIDER_NAME}', p2=f'{model}', p3=f'{PROVIDER_ID}')
            )
            return ToolResult(
                content=[TextBlock(text=text)], display=data, data=data
            )

        if provider != PROVIDER_ID:
            raise ToolExecutionError(
                tr('python.subagent_model_tools.806540458c59', p0=f'{provider}', p1=f'{PROVIDER_ID}')
            )

        try:
            reading = asyncio.create_task(asyncio.to_thread(_fetch_catalog, base_url, api_key))
            try:
                await asyncio.wait((reading,))
                catalog = reading.result()
            except asyncio.CancelledError as cancelled:
                # 线程读取不能撤销；保持归属直到响应已关闭，再传播取消。
                try:
                    await join_cleanup(reading)
                except Exception as failure:
                    raise_cleanup_errors([cancelled, failure])
                raise
            catalog_error = ""
        except ProcessCleanupError:
            raise
        except Exception as exc:  # 网络/鉴权/解析问题都降级为提示
            catalog = []
            catalog_error = f"{type(exc).__name__}: {exc}"

        data['catalog_available'] = not bool(catalog_error)
        if wanted_model:
            entry = next(
                (item for item in catalog if item["id"] == wanted_model), None
            )
            if entry is None and not catalog_error:
                raise ToolExecutionError(
                    tr('python.subagent_model_tools.2c94a08e5391', p0=f'{wanted_model}', p1=f'{PROVIDER_ID}')
                )
            info = entry or {"id": wanted_model}
            efforts = _efforts_of(info)
            data["model"] = info
            data['reasoning_support'] = 'unreported' if efforts is None else 'supported' if efforts else 'unsupported'
            data['reasoning_efforts_source'] = 'local_presets' if efforts else None
            data["reasoning_efforts"] = [{"id": eid, "label": label} for eid, label in (efforts or [])]
            lines = [_model_line(PROVIDER_ID, info)]
            if efforts:
                lines.append(tr('tools.models.localReasoningNote'))
                lines.append(tr('tools.models.reasoning_efforts'))
                lines.extend(f"  {eid} — {label}" for eid, label in efforts)
            elif efforts is None:
                lines.append(tr('tools.models.reasoningUnreported'))
            else:
                lines.append(tr('python.subagent_model_tools.b850e9efba62'))
            if catalog_error:
                lines.append("")
                lines.append(tr('python.subagent_model_tools.fa685b35e815', p0=f'{catalog_error}'))
            return ToolResult(
                content=[TextBlock(text="\n".join(lines))], display=data, data=data
            )

        models = [
            item
            for item in catalog
            if isinstance(item.get("id"), str) and "/" in str(item["id"])
        ]
        models.sort(key=lambda item: str(item["id"]).lower())
        data["models"] = models[:MAX_MODELS]
        data["model_count"] = len(models)
        data["truncated"] = len(models) > MAX_MODELS

        if not models:
            text = (
                tr('python.subagent_model_tools.7b012da64bbe', p0=f'{PROVIDER_ID}', p1=f'{model}')
            )
            if catalog_error:
                text += tr('python.subagent_model_tools.47e64a103278', p0=f'{catalog_error}')
            return ToolResult(
                content=[TextBlock(text=text)], display=data, data=data
            )

        lines = [tr('tools.models.catalogTruncated' if data['truncated'] else 'tools.models.catalogCount',
                    provider=PROVIDER_ID, count=len(models), limit=MAX_MODELS)]
        lines.extend(_model_line(PROVIDER_ID, item) for item in data["models"])
        if catalog_error:
            lines.append("")
            lines.append(tr('python.subagent_model_tools.5b3efecee863', p0=f'{catalog_error}'))
        return ToolResult(content=[TextBlock(text="\n".join(lines))], display=data, data=data)

    return ToolDefinition(
        schema={
                "description_key": _DESCRIPTION_KEY,
                "parameter_keys": {
                    ('properties', 'provider', 'description'): 'python.subagent_model_tools.e66d4aee4a05',
                    ('properties', 'model', 'description'): 'python.subagent_model_tools.935dde70bf76',
                },
            "name": "list_subagent_models",
            "description": tr(_DESCRIPTION_KEY),
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "description": tr('python.subagent_model_tools.e66d4aee4a05'),
                    },
                    "model": {
                        "type": "string",
                        "description": (
                            tr('python.subagent_model_tools.935dde70bf76')
                        ),
                    },
                },
                "additionalProperties": False,
            },
        },
        execute=list_subagent_models,
        concurrency_safe=True,
        timeout=CATALOG_TIMEOUT + 15.0,
    )


__all__ = [
    "build_list_subagent_models_tool",
    "PROVIDER_ID",
    "PROVIDER_NAME",
]

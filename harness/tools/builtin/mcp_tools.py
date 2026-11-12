"""MCP 工具层。

两件事：

1. 把远端 MCP 服务器声明的工具包装成 :class:`ToolDefinition`，让它们出现在
   模型的工具列表里，公开名固定为 ``mcp__<server>__<raw>``；
2. 提供三个共享资源工具 ``list_mcp_resources`` /
   ``list_mcp_resource_templates`` / ``read_mcp_resource``。

资源工具 schema：``server`` 必填、``cursor`` 可选，
``read_mcp_resource`` 额外要求 ``uri``。
"""

from __future__ import annotations

from harness.i18n import tr

import json
import logging
from typing import Any

from ...core.errors import ToolExecutionError
from ...core.jsonutil import to_jsonable
from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolResult, ToolRunContext
from .mcp_client import (
    McpError,
    McpRegistry,
    McpRemoteTool,
    McpServerSupervisor,
    public_tool_name,
    remote_tool_contract,
)

logger = logging.getLogger(__name__)

#: 单个资源文本块的超长截断阈值（字符），防止一次性灌爆上下文。
MAX_TEXT_CHARS = 20_000
#: 单次 ``read_mcp_resource`` 最多返回的块数。
MAX_RESOURCE_BLOCKS = 32

def _list_parameters():
    """资源列表参数按构建时语言生成，避免导入时冻结说明。"""
    return {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": tr('python.mcp_tools.72ad2cb26ab4')},
            "cursor": {
                "type": "string",
                "description": tr('python.mcp_tools.5e857b9c2db7'),
            },
        },
        "required": ["server"],
        "additionalProperties": False,
    }



def _server_arg(args: Any, *, tool: str) -> str:
    if not isinstance(args, dict):
        raise ToolExecutionError(tr('mcp.render.argumentsObject', tool=tool))
    server = args.get("server")
    if not isinstance(server, str) or not server:
        raise ToolExecutionError(tr('mcp.render.serverRequired', tool=tool))
    cursor = args.get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise ToolExecutionError(tr('mcp.render.cursorString', tool=tool))
    return server


def _render_resource_result(server: str, value: Any) -> str:
    """把资源响应渲染成给模型看的文本，附带 ``server`` 归属。"""
    if isinstance(value, dict) and isinstance(value.get("contents"), list):
        return _render_contents(server, value["contents"])
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _render_contents(server: str, contents: list[Any]) -> str:
    lines = [tr('mcp.render.resourceHeading', server=server)]
    shown = 0
    truncated = False
    for item in contents[:MAX_RESOURCE_BLOCKS]:
        if not isinstance(item, dict):
            lines.append(json.dumps(item, ensure_ascii=False, default=str)[:500])
            shown += 1
            continue
        uri = item.get("uri")
        if uri:
            lines.append("")
            lines.append("## " + str(uri))
        mime = item.get("mimeType")
        if mime:
            lines.append(tr('mcp.render.mime', mime=str(mime)))
        if isinstance(item.get("text"), str):
            text = item["text"]
            if len(text) > MAX_TEXT_CHARS:
                text = tr('mcp.render.truncatedText', text=text[:MAX_TEXT_CHARS])
                truncated = True
            lines.append(text)
        elif isinstance(item.get("blob"), str):
            blob = item["blob"]
            lines.append(tr('mcp.render.binaryLength', count=len(blob)))
            lines.append(blob[:2000] + ("…" if len(blob) > 2000 else ""))
        else:
            lines.append(json.dumps(item, ensure_ascii=False, default=str)[:1000])
        shown += 1
    if len(contents) > shown:
        truncated = True
    if truncated:
        lines.append("")
        lines.append(tr('python.mcp_tools.0880f458b9c7'))
    return "\n".join(lines)


def _render_tool_result(value: Any) -> str:
    """把 ``tools/call`` 的结果渲染成文本块序列。"""
    if not isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value.get('structuredContent'), dict):
        # 结构化结果优先，避免等价文本重复回灌。
        return json.dumps(value['structuredContent'], ensure_ascii=False, separators=(',', ':'), default=str)
    content = value.get("content")
    if not isinstance(content, list):
        return json.dumps({key: item for key, item in value.items() if key != '_meta'}, ensure_ascii=False, default=str)
    lines: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            lines.append(json.dumps(item, ensure_ascii=False, default=str)[:500])
            continue
        kind = item.get("type")
        if kind == "text":
            text = str(item.get("text") or "")
            # 原始文本交给会话保存，再对整个结果生成一次有界投影。
            lines.append(text)
        elif kind == "image":
            lines.append(tr('mcp.render.image', mime=str(item.get('mimeType') or 'image'),
                            count=len(str(item.get('data') or ''))))
        elif kind == "audio":
            lines.append(tr('mcp.render.audio', mime=str(item.get('mimeType') or 'audio')))
        elif kind == "resource_link":
            lines.append(tr('mcp.render.resourceLink', name=str(item.get('name') or ''),
                            uri=str(item.get('uri') or '')))
        elif kind == "resource":
            resource = item.get("resource")
            if isinstance(resource, dict):
                lines.append(_render_contents(tr('python.mcp_tools.975f9da19eee'), [resource]))
        else:
            lines.append(json.dumps(item, ensure_ascii=False, default=str)[:500])
    if not lines:
        return json.dumps({key: item for key, item in value.items() if key != '_meta'}, ensure_ascii=False, default=str)
    return "\n".join(lines)


def build_mcp_resource_tools(registry: McpRegistry) -> list[ToolDefinition]:
    """构造三个共享资源工具。"""

    async def _list(args: Any, ctx: ToolRunContext, method: str, tool: str) -> ToolResult:
        server = _server_arg(args, tool=tool)
        cursor = args.get("cursor") if isinstance(args, dict) else None
        try:
            supervisor = registry.get(server)
            value = await supervisor.request_resource(method, cursor=cursor)
        except McpError as exc:
            raise ToolExecutionError(str(exc)) from exc
        text = _render_resource_result(server, value)
        return ToolResult(content=[TextBlock(text=text)], display=value, data=value)

    async def list_mcp_resources(args: Any, ctx: ToolRunContext) -> ToolResult:
        return await _list(args, ctx, "resources/list", "list_mcp_resources")

    async def list_mcp_resource_templates(args: Any, ctx: ToolRunContext) -> ToolResult:
        return await _list(
            args, ctx, "resources/templates/list", "list_mcp_resource_templates"
        )

    async def read_mcp_resource(args: Any, ctx: ToolRunContext) -> ToolResult:
        server = _server_arg(args, tool="read_mcp_resource")
        uri = args.get("uri") if isinstance(args, dict) else None
        if not isinstance(uri, str) or not uri:
            raise ToolExecutionError(tr('python.mcp_tools.71e18cd38ce4'))
        try:
            supervisor = registry.get(server)
            value = await supervisor.request_resource("resources/read", uri=uri)
        except McpError as exc:
            raise ToolExecutionError(str(exc)) from exc
        text = _render_resource_result(server, value)
        return ToolResult(content=[TextBlock(text=text)], display=value, data=value)

    timeout = 90.0
    return [
        ToolDefinition(
            schema={
                "description_key": 'python.mcp_tools.b78ba904620f',
                "parameter_keys": {
                    ('properties', 'server', 'description'): 'python.mcp_tools.72ad2cb26ab4',
                    ('properties', 'cursor', 'description'): 'python.mcp_tools.5e857b9c2db7',
                },
                "name": "list_mcp_resources",
                "description": tr('python.mcp_tools.b78ba904620f'),
                "parameters": _list_parameters(),
            },
            execute=list_mcp_resources,
            concurrency_safe=True,
            timeout=timeout,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.mcp_tools.90ac0e461074',
                "parameter_keys": {
                    ('properties', 'server', 'description'): 'python.mcp_tools.72ad2cb26ab4',
                    ('properties', 'cursor', 'description'): 'python.mcp_tools.5e857b9c2db7',
                },
                "name": "list_mcp_resource_templates",
                "description": tr('python.mcp_tools.90ac0e461074'),
                "parameters": _list_parameters(),
            },
            execute=list_mcp_resource_templates,
            concurrency_safe=True,
            timeout=timeout,
        ),
        ToolDefinition(
            schema={
                "description_key": 'python.mcp_tools.ecba63728ff1',
                "parameter_keys": {
                    ('properties', 'server', 'description'): 'python.mcp_tools.72ad2cb26ab4',
                    ('properties', 'uri', 'description'): 'python.mcp_tools.9a6b36397dd7',
                },
                "name": "read_mcp_resource",
                "description": (
                    tr('python.mcp_tools.ecba63728ff1')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "server": {
                            "type": "string",
                            "description": tr('python.mcp_tools.72ad2cb26ab4'),
                        },
                        "uri": {"type": "string", "description": tr('python.mcp_tools.9a6b36397dd7')},
                    },
                    "required": ["server", "uri"],
                    "additionalProperties": False,
                },
            },
            execute=read_mcp_resource,
            concurrency_safe=True,
            timeout=timeout,
        ),
    ]


def build_remote_tool(
    supervisor: McpServerSupervisor,
    remote: McpRemoteTool,
    *,
    timeout: float | None = None,
) -> ToolDefinition:
    """把一个远端工具包装成模型可直接调用的工具定义。"""
    expected_contract = remote_tool_contract(remote)
    public_name = public_tool_name(supervisor.server_name, remote.name)
    description = remote.description
    fallback_key = 'mcp.tool.fallback_description' if not description.strip() else None
    if fallback_key:
        description = tr(fallback_key, tool=remote.name)
    effective_timeout = timeout
    if effective_timeout is None:
        effective_timeout = 90.0

    async def call_remote(args: Any, ctx: ToolRunContext) -> ToolResult:
        if not isinstance(args, dict):
            arguments: dict[str, Any] = {}
        else:
            arguments = args
        try:
            ctx.check_execution()
            value = await supervisor.call(public_name, arguments, expected_contract=expected_contract,
                                          execution_check=ctx.check_execution)
        except McpError as exc:
            raise ToolExecutionError(str(exc)) from exc
        text = _render_tool_result(value)
        return ToolResult(content=[TextBlock(text=text)], display=value, data=value,
                          is_error=isinstance(value, dict) and value.get('isError') is True)

    # 未声明副作用的远程工具保持未知，不按服务名称推测只读。
    read_only = remote.annotations.get("readOnlyHint") is True

    return ToolDefinition(
        schema={
            "name": public_name,
            "description_key": fallback_key,
            "description_values": {"tool": remote.name} if fallback_key else {},
            "description": description,
            "parameters": to_jsonable(remote.input_schema),
        },
        execute=call_remote,
        effect="read" if read_only else "unknown",
        concurrency_safe=True,
        timeout=effective_timeout,
        defer_loading=True,
        discovery_namespace=supervisor.server_name,
    )


def build_mcp_tools(
    registry: McpRegistry, *, include_resources: bool = True
) -> list[ToolDefinition]:
    """按当前已连接的工具表构造全部 MCP 工具。"""
    tools: list[ToolDefinition] = []
    for supervisor in registry.supervisors:
        for name in supervisor.public_names():
            remote = supervisor.remote_tool(name)
            if remote is not None:
                tools.append(build_remote_tool(supervisor, remote))
    if include_resources:
        tools.extend(build_mcp_resource_tools(registry))
    return tools


__all__ = [
    "build_mcp_resource_tools",
    "build_mcp_tools",
    "build_remote_tool",
    "MAX_RESOURCE_BLOCKS",
    "MAX_TEXT_CHARS",
]


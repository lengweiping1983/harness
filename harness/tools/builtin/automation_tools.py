"""把对话中的定时请求接入持久化自动化服务。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from harness.i18n import tr
from ...app.schedules import validate_schedule
from ...core.errors import HarnessError
from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolRunContext, ToolResult


def build_automation_tools() -> list[ToolDefinition]:
    """构造模型可用的当前对话定时任务工具。"""

    async def schedule(args: Any, ctx: ToolRunContext) -> ToolResult:
        service = ctx.services.get("automations")
        if service is None:
            return ToolResult(
                content=[TextBlock(text=tr("python.automation_tools.disabled"))],
                is_error=True,
            )
        if not isinstance(args, dict):
            return ToolResult(content=[TextBlock(text=tr("python.automation_tools.invalid"))], is_error=True)
        body = dict(args)
        body["session_id"] = ctx.session_id
        body["creation_id"] = hashlib.sha256(f"schedule_automation:{ctx.session_id}:{ctx.call_id}".encode()).hexdigest()[:32]
        body.setdefault("enabled", True)
        body.setdefault("mode", "session")
        body.setdefault("timezone", "Asia/Shanghai")
        body["name"] = body["name"].strip()
        body["prompt"] = body["prompt"].strip()
        recovered = False
        try:
            saved = service.save(body)
        except Exception as error:
            if getattr(error, "code", None) == "automation/create-exists":
                body["schedule"], body["timezone"] = validate_schedule(body)
                saved = next((row for row in service.list() if row.get("id") == "auto_" + body["creation_id"]), None)
                same = saved is not None and all(saved.get(key) == body.get(key) for key in ("name", "prompt", "session_id", "mode", "timezone", "enabled", "schedule"))
                if not same:
                    return ToolResult(content=[TextBlock(text=str(error))], is_error=True)
                recovered = True
            else:
                return ToolResult(content=[TextBlock(text=str(error))], is_error=True)
        return ToolResult(
            content=[TextBlock(text=json.dumps(saved, ensure_ascii=False, separators=(",", ":")))],
            display=saved,
            data=saved,
            progress=not recovered,
        )

    async def list_current(args: Any, ctx: ToolRunContext) -> ToolResult:
        service = ctx.services.get("automations")
        if service is None:
            return ToolResult(content=[TextBlock(text=tr("python.automation_tools.disabled"))], is_error=True)
        try:
            tasks = [row for row in service.list() if row["session_id"] == ctx.session_id]
        except Exception as error:
            return ToolResult(content=[TextBlock(text=str(error))], is_error=True)
        data = {"automations": tasks}
        return ToolResult(content=[TextBlock(text=json.dumps(data, ensure_ascii=False))], display=data, data=data)

    async def remove_current(args: Any, ctx: ToolRunContext) -> ToolResult:
        service = ctx.services.get("automations")
        if service is None:
            return ToolResult(content=[TextBlock(text=tr("python.automation_tools.disabled"))], is_error=True)
        if not isinstance(args, dict) or not isinstance(args.get("automation_id"), str) or not args["automation_id"].strip():
            return ToolResult(content=[TextBlock(text=tr("python.automation_tools.invalid"))], is_error=True)
        aid = args["automation_id"]
        try:
            service.remove(aid, session_id=ctx.session_id)
        except Exception as error:
            return ToolResult(content=[TextBlock(text=str(error))], is_error=True)
        data = {"automation_id": aid, "deleted": True}
        return ToolResult(content=[TextBlock(text=json.dumps(data))], display=data, data=data, progress=True)

    async def update_current(args: Any, ctx: ToolRunContext) -> ToolResult:
        """修改当前对话已有任务的名称、正文、计划或启用状态，身份不匹配不写入。"""
        service = ctx.services.get("automations")
        if service is None:
            return ToolResult(content=[TextBlock(text=tr("python.automation_tools.disabled"))], is_error=True)
        if not isinstance(args, dict) or not isinstance(args.get("automation_id"), str) or not args["automation_id"].strip():
            return ToolResult(content=[TextBlock(text=tr("python.automation_tools.invalid"))], is_error=True)
        aid = args["automation_id"].strip()
        fields = {key: args[key] for key in ("name", "prompt", "schedule", "enabled") if key in args}
        if not fields:
            return ToolResult(content=[TextBlock(text=tr("python.automation_tools.update_empty"))], is_error=True)
        try:
            current = next((row for row in service.list() if row["id"] == aid and row["session_id"] == ctx.session_id), None)
            if current is None:
                raise HarnessError(tr("python.automations.6205508a57ad"), code="automation/not-found")
            body = {key: fields.get(key, current[key]) for key in ("name", "prompt", "schedule")}
            body["enabled"] = fields.get("enabled", bool(current["enabled"]))
            body.update(id=aid, session_id=ctx.session_id, timezone=current["timezone"], mode=current["mode"])
            saved = service.save(body)
        except Exception as error:
            return ToolResult(content=[TextBlock(text=str(error))], is_error=True)
        return ToolResult(
            content=[TextBlock(text=json.dumps(saved, ensure_ascii=False, separators=(",", ":")))],
            display=saved,
            data=saved,
            progress=True,
        )

    return [
        ToolDefinition(
            schema={
                "name": "schedule_automation",
                "description_key": "python.automation_tools.description",
                "description": tr("python.automation_tools.description"),
                "parameter_keys": {
                    ("properties", "name", "description"): "python.automation_tools.name",
                    ("properties", "prompt", "description"): "python.automation_tools.prompt",
                    ("properties", "schedule", "description"): "python.automation_tools.schedule",
                    ("properties", "mode", "description"): "python.automation_tools.mode",
                },
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1, "maxLength": 120, "description": tr("python.automation_tools.name")},
                        "prompt": {"type": "string", "minLength": 1, "maxLength": 32000, "description": tr("python.automation_tools.prompt")},
                        "schedule": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": ["once", "interval", "daily", "weekly", "monthly", "yearly"]},
                                "at": {"type": "number", "minimum": 0},
                                "seconds": {"type": "number", "minimum": 60},
                                "time": {"type": "string", "pattern": "^[0-2][0-9]:[0-5][0-9]$"},
                                "days": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 6}, "minItems": 1, "uniqueItems": True},
                                "monthdays": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 31}, "minItems": 1, "uniqueItems": True},
                                "months": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 12}, "minItems": 1, "uniqueItems": True},
                            },
                            "required": ["kind"],
                            "description": tr("python.automation_tools.schedule"),
                            "additionalProperties": False,
                        },
                        "mode": {"type": "string", "enum": ["session", "fresh"], "default": "session", "description": tr("python.automation_tools.mode")},
                    },
                    "required": ["name", "prompt", "schedule"],
                    "additionalProperties": False,
                },
            },
            execute=schedule,
            effect="workspace_write",
            concurrency_safe=False,
            idempotent=False,
        ),
        ToolDefinition(
            schema={
                "name": "list_automations",
                "description_key": "python.automation_tools.list_description",
                "description": tr("python.automation_tools.list_description"),
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            execute=list_current,
            effect="read",
            concurrency_safe=True,
            idempotent=True,
        ),
        ToolDefinition(
            schema={
                "name": "delete_automation",
                "description_key": "python.automation_tools.delete_description",
                "description": tr("python.automation_tools.delete_description"),
                "parameter_keys": {("properties", "automation_id", "description"): "python.automation_tools.automation_id"},
                "parameters": {
                    "type": "object",
                    "properties": {"automation_id": {"type": "string", "minLength": 1, "description": tr("python.automation_tools.automation_id")}},
                    "required": ["automation_id"],
                    "additionalProperties": False,
                },
            },
            execute=remove_current,
            effect="workspace_write",
            concurrency_safe=False,
            idempotent=False,
        ),
        ToolDefinition(
            schema={
                "name": "update_automation",
                "description_key": "python.automation_tools.update_description",
                "description": tr("python.automation_tools.update_description"),
                "parameter_keys": {
                    ("properties", "automation_id", "description"): "python.automation_tools.automation_id",
                    ("properties", "name", "description"): "python.automation_tools.update_name",
                    ("properties", "prompt", "description"): "python.automation_tools.update_prompt",
                    ("properties", "schedule", "description"): "python.automation_tools.update_schedule",
                    ("properties", "enabled", "description"): "python.automation_tools.update_enabled",
                },
                "parameters": {
                    "type": "object",
                    "properties": {
                        "automation_id": {"type": "string", "minLength": 1, "description": tr("python.automation_tools.automation_id")},
                        "name": {"type": "string", "minLength": 1, "maxLength": 120, "description": tr("python.automation_tools.update_name")},
                        "prompt": {"type": "string", "minLength": 1, "maxLength": 32000, "description": tr("python.automation_tools.update_prompt")},
                        "schedule": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": ["once", "interval", "daily", "weekly", "monthly", "yearly"]},
                                "at": {"type": "number", "minimum": 0},
                                "seconds": {"type": "number", "minimum": 60},
                                "time": {"type": "string", "pattern": "^[0-2][0-9]:[0-5][0-9]$"},
                                "days": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 6}, "minItems": 1, "uniqueItems": True},
                                "monthdays": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 31}, "minItems": 1, "uniqueItems": True},
                                "months": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 12}, "minItems": 1, "uniqueItems": True},
                            },
                            "required": ["kind"],
                            "description": tr("python.automation_tools.update_schedule"),
                            "additionalProperties": False,
                        },
                        "enabled": {"type": "boolean", "description": tr("python.automation_tools.update_enabled")},
                    },
                    "required": ["automation_id"],
                    "additionalProperties": False,
                },
            },
            execute=update_current,
            effect="workspace_write",
            concurrency_safe=False,
            idempotent=False,
        ),
    ]

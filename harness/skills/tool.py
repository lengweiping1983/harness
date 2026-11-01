"""模型可见的技能加载工具。

采用"**目录 + 按需加载**"两段式：

1. **目录**：系统提示里有一个 ``<available_skills>`` 块，列出技能名与一句话描述，
   让模型知道有什么可用，目录本身仍占用输入 token；
2. **加载**：模型判断某个技能有用时调用 ``skill(name=...)``，把该技能的完整
   正文作为工具结果追加到会话，后续请求复用历史前缀。
"""

from __future__ import annotations

from harness.i18n import tr

from typing import Any, Callable

from ..core.errors import SkillNotFoundError
from ..llm.types import TextBlock
from ..tools.registry import ToolDefinition, ToolRunContext, ToolResult
from .registry import SkillRegistry

#: 渲染给模型的目录块
CATALOG_HEADER = "<available_skills>"
CATALOG_FOOTER = "</available_skills>"


def render_catalog(registry: SkillRegistry, *, max_description: int = 500) -> str:
    """渲染技能目录块。"""
    return (
        tr('python.tool.1ef1dcce1bba', p0=f'{CATALOG_HEADER}', p1=f'{registry.catalog(max_description=max_description)}', p2=f'{CATALOG_FOOTER}')
    )


def build_skill_tool(registry: SkillRegistry, *, registry_for_session: Callable[[Any], SkillRegistry | None] | None = None) -> list[ToolDefinition]:
    """构造 ``skill`` 加载工具。"""

    async def load_skill(args: Any, ctx: ToolRunContext) -> ToolResult:
        """按名字加载技能正文。"""
        name = str(args.get("name", "")).strip()
        try:
            scoped = registry_for_session(ctx.services.get("session") or ctx.session_id) if registry_for_session else registry
            if scoped is None:
                raise SkillNotFoundError(name)
            if registry_for_session is None:
                scoped.refresh()
            skill = scoped.get(name)
            # 用户显式入口直接注入正文；模型工具始终遵守隐式调用策略。
            if (not skill.model_invocable or skill.configuration_errors
                    or skill.policy.get("allow_implicit_invocation", True) is not True):
                return ToolResult(
                    content=[TextBlock(text=tr('skill.load.implicit_disabled'))],
                    is_error=True,
                    display={"error": "model_invocation_disabled", "requested": name},
                )
        except SkillNotFoundError:
            error = tr('skill.load.unavailable')
            return ToolResult(
                content=[TextBlock(text=error)],
                is_error=True,
                display={"error": error, "requested": name},
            )

        # 正文前面加上明确的调用说明，让模型知道这是"已激活的指引"
        from .context import snapshot
        value = snapshot(skill)
        body = value.text
        return ToolResult(
            content=[TextBlock(text=body)],
            display={"skill": skill.name, "description": skill.description, "chars": len(skill.content),
                     "path": str(skill.path) if skill.path else None, "version": value.version},
            data=skill,
        )

    return [
        ToolDefinition(
            schema={
                "name": "skill",
                "description_key": "python.tool.24742fca6c8d",
                "parameter_keys": {("properties", "name", "description"): "python.tool.4ac8d795426b"},
                "description": (
                    tr('python.tool.24742fca6c8d')
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": tr('python.tool.4ac8d795426b'),
                        }
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
            execute=load_skill,
            effect='read', idempotent=True,
            concurrency_safe=True,
        )
    ]


__all__ = ["build_skill_tool", "render_catalog", "CATALOG_HEADER", "CATALOG_FOOTER"]

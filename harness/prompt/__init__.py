"""提示词层：有序片段注册表与变量插值。"""

from .registry import (
    PromptAssembly,
    PromptSection,
    SystemPromptRegistry,
    build_base_prompt,
    interpolate_variables,
)

__all__ = [
    "SystemPromptRegistry",
    "PromptSection",
    "PromptAssembly",
    "build_base_prompt",
    "interpolate_variables",
]

"""技能层：SKILL.md 解析、注册表与模型可见的加载工具。"""

from .parser import SkillMetadata, is_valid_skill_name, parse_frontmatter, parse_skill_file
from .registry import (
    FilesystemSkillProvider,
    SkillProvider,
    SkillRegistry,
    build_default_registry,
)
from .tool import build_skill_tool, render_catalog

__all__ = [
    "SkillMetadata",
    "parse_frontmatter",
    "parse_skill_file",
    "is_valid_skill_name",
    "SkillRegistry",
    "SkillProvider",
    "FilesystemSkillProvider",
    "build_default_registry",
    "build_skill_tool",
    "render_catalog",
]

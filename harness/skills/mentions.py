"""识别正文中明确的技能引用，不把代码示例当成调用。"""
from __future__ import annotations

import re

from .registry import SkillRegistry
from .parser import SKILL_NAME_PATTERN

_TOKEN = re.compile(r'(?<!\S)\$(' + SKILL_NAME_PATTERN + r'(?::' + SKILL_NAME_PATTERN + r')?)(?=$|[\s,，。;；!?！？])')
_CODE = re.compile(r'(`+|~{3,})')


def mentioned_skills(text: str, registry: SkillRegistry) -> list[str]:
    """按出现顺序返回可由用户调用的技能，同一技能只加载一次。"""
    available = {skill.name for skill in registry.list() if skill.user_invocable}
    names: list[str] = []
    delimiter = ''
    for part in _CODE.split(text):
        if _CODE.fullmatch(part):
            if not delimiter:
                delimiter = part
            elif part[0] == delimiter[0] and len(part) >= len(delimiter):
                delimiter = ''
            continue
        if delimiter:
            continue
        for match in _TOKEN.finditer(part):
            name = match.group(1)
            if name in available and name not in names:
                names.append(name)
    return names

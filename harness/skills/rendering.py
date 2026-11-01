"""所有调用表面共享的技能正文和资源基目录。"""
import json

from .parser import SkillMetadata
from ..i18n import tr


def render_loaded_skill(skill: SkillMetadata) -> str:
    """告知模型相对资源的实际位置，不改变技能内容或执行权限。"""
    content = skill.content
    location = ''
    if skill.path is not None:
        root = json.dumps(str(skill.path.resolve().parent), ensure_ascii=False)
        location = tr('skill.render.location', root=root)
    dependency = ''
    if skill.dependencies.get('tools'):
        dependency = tr('skill.render.dependencies') + json.dumps(skill.dependencies['tools'], ensure_ascii=False)
    return f'<skill name="{skill.name}">\n{location}{content.strip()}{dependency}\n</skill>'

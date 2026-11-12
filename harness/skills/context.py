"""技能快照与会话工作集投影；只复用当前表面内完整存在的相同版本。"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json

from ..core.errors import SessionInvariantError
from ..core.jsonutil import to_jsonable
from ..i18n import tr
from .rendering import render_loaded_skill

_loads = ContextVar('skill_context_loads', default=None)


@dataclass(frozen=True)
class SkillSnapshot:
    """宿主生成的语义版本与完整渲染正文。"""
    name: str
    version: str
    text: str


def snapshot(skill):
    """来源、正文和声明变化都生成新身份，不把它称作原文件摘要。"""
    body = render_loaded_skill(skill)
    identity = {'name': skill.name, 'body': body,
        'path': str(skill.path.resolve()) if skill.path else None,
        'source': skill.source, 'provider': skill.provider, 'plugin_id': skill.plugin_id,
        'policy': skill.policy, 'model_invocable': skill.model_invocable,
        'user_invocable': skill.user_invocable, 'configuration_errors': skill.configuration_errors}
    version = hashlib.sha256(json.dumps(to_jsonable(identity), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    text = tr('skill.context.snapshot', name=skill.name, version=version, body=body)
    return SkillSnapshot(skill.name, version, text)


@contextmanager
def load_scope(session_id):
    """每次受管输入独占收集器，子会话不能继承父会话的激活标记。"""
    token = _loads.set((session_id, []))
    try:
        yield
    finally:
        _loads.reset(token)


def record_user_load(value):
    """只记录宿主实际执行的技能展开，不解析用户文本伪造的标记。"""
    scope = _loads.get()
    if scope is not None:
        scope[1].append(value)


def spans(text, values):
    """只标记唯一、互不重叠的宿主快照，含糊的文本保持原样。"""
    rows = []
    for value in values:
        if text.count(value.text) != 1:
            continue
        start = text.index(value.text)
        row = {'name': value.name, 'version': value.version, 'start': start,
            'end': start + len(value.text), 'sha256': hashlib.sha256(value.text.encode()).hexdigest()}
        if row not in rows:
            rows.append(row)
    return [row for row in rows if not any(other is not row and
        row['start'] < other['end'] and other['start'] < row['end'] for other in rows)]


def user_spans(session_id, text):
    scope = _loads.get()
    return spans(text, scope[1]) if scope is not None and scope[0] == session_id else []


def project(text, records, seen):
    """第一份完整快照保留全文，后续相同快照仅在本次工作集内引用。"""
    return project_with_parts(text, records, seen)[0]


def project_with_parts(text, records, seen, *, source='user'):
    """将投影后的文字与宿主验证的来源段一起派生，不解析伪造标记。"""
    if not isinstance(records, (list, tuple)):
        raise SessionInvariantError(tr('skill.context.invalid'))
    if not records:
        return text, ((source, text),)
    ordered = []
    for row in records:
        if (not isinstance(row, Mapping) or set(row) != {'name', 'version', 'start', 'end', 'sha256'}
                or not isinstance(row['name'], str) or not row['name']
                or any(not isinstance(row[key], str) or len(row[key]) != 64
                       or any(char not in '0123456789abcdef' for char in row[key]) for key in ('version', 'sha256'))
                or type(row['start']) is not int or type(row['end']) is not int
                or not 0 <= row['start'] < row['end'] <= len(text)):
            raise SessionInvariantError(tr('skill.context.invalid'))
        body = text[row['start']:row['end']]
        if hashlib.sha256(body.encode()).hexdigest() != row['sha256']:
            raise SessionInvariantError(tr('skill.context.invalid'))
        ordered.append((row, body))
    ordered.sort(key=lambda pair: pair[0]['start'])
    previous, parts = 0, []
    for row, body in ordered:
        if row['start'] < previous:
            raise SessionInvariantError(tr('skill.context.invalid'))
        prior = seen.get(row['version'])
        if prior is not None and prior != (row['name'], row['sha256']):
            raise SessionInvariantError(tr('skill.context.invalid'))
        replacement = tr('skill.context.reference', name=row['name'], version=row['version'])
        reused = prior is not None and len(replacement) < len(body)
        if row['start'] > previous:
            parts.append((source, text[previous:row['start']]))
        parts.append((f"skill:{row['name']}:{'reference' if reused else 'full'}", replacement if reused else body))
        seen[row['version']] = (row['name'], row['sha256'])
        previous = row['end']
    if previous < len(text):
        parts.append((source, text[previous:]))
    return ''.join(chunk for _, chunk in parts), tuple(parts)

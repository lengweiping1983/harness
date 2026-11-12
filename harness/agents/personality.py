"""16 种 Agent 沟通人格模板与可回放的聊天覆盖，不改变工具权限。"""
from __future__ import annotations

import asyncio
import re
import weakref
from typing import Any
from harness.i18n import tr
from ..core.errors import HarnessError, SessionNotFoundError

STYLE_AXES = (("reflective", "proactive"), ("conceptual", "concrete"),
              ("analytical", "empathetic"), ("structured", "adaptive"))
STYLE_PREFERENCES = {
    'systems-planner': ('reflective', 'conceptual', 'analytical', 'structured'),
    'research-analyst': ('reflective', 'conceptual', 'analytical', 'adaptive'),
    'long-term-adviser': ('reflective', 'conceptual', 'empathetic', 'structured'),
    'creative-partner': ('reflective', 'conceptual', 'empathetic', 'adaptive'),
    'reliable-executor': ('reflective', 'concrete', 'analytical', 'structured'),
    'troubleshooter': ('reflective', 'concrete', 'analytical', 'adaptive'),
    'detail-steward': ('reflective', 'concrete', 'empathetic', 'structured'),
    'experience-designer': ('reflective', 'concrete', 'empathetic', 'adaptive'),
    'delivery-lead': ('proactive', 'conceptual', 'analytical', 'structured'),
    'solution-explorer': ('proactive', 'conceptual', 'analytical', 'adaptive'),
    'collaboration-coach': ('proactive', 'conceptual', 'empathetic', 'structured'),
    'idea-catalyst': ('proactive', 'conceptual', 'empathetic', 'adaptive'),
    'delivery-manager': ('proactive', 'concrete', 'analytical', 'structured'),
    'action-tester': ('proactive', 'concrete', 'analytical', 'adaptive'),
    'team-supporter': ('proactive', 'concrete', 'empathetic', 'structured'),
    'demo-partner': ('proactive', 'concrete', 'empathetic', 'adaptive'),
}
STYLE_CODES = tuple(STYLE_PREFERENCES)


def normalize(code: Any) -> str | None:
    """严格校验人格代号，None 表示跟随Agent的默认配置。"""
    if code is None:
        return None
    if not isinstance(code, str) or code.strip() not in STYLE_CODES:
        raise HarnessError(tr("python.personality.invalid"), code="personality/invalid")
    return code.strip()


def profiles() -> list[dict[str, str]]:
    return [{"code": code, "name": tr("personality." + code + ".name"),
             "description": tr("personality." + code + ".description"), "prompt": render(code)} for code in STYLE_CODES]


def render(code: str | None) -> str:
    code = normalize(code)
    if not code:
        return ""
    return tr("python.personality.prompt",
              name=tr("personality." + code + ".name"),
              style="\n".join(tr("personality.axis." + letter) for letter in STYLE_PREFERENCES[code]))


def recommend(choices: Any) -> str:
    """按四个明确的 Agent 风格偏好组合模板，不进行人的心理测评。"""
    if not isinstance(choices, list) or len(choices) != 4 or any(
            not isinstance(letter, str) or letter not in axis for letter, axis in zip(choices, STYLE_AXES)):
        raise HarnessError(tr("python.personality.invalid"), code="personality/invalid")
    return next(code for code, preferences in STYLE_PREFERENCES.items() if tuple(choices) == preferences)


def normalize_prompt(value: Any) -> str | None:
    """正文不包含系统标记；空字符串表示明确清空，None 表示继承。"""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 1000:
        raise HarnessError(tr('python.personality.invalid_prompt'), code='personality/invalid-prompt')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise HarnessError(tr('python.personality.invalid_prompt'), code='personality/invalid-prompt') from exc
    if re.search(r'</?agent_personality\b', value):
        raise HarnessError(tr('python.personality.invalid_prompt'), code='personality/invalid-prompt')
    value = value.strip()
    return value


def system_personality(prompt: str | None) -> str:
    """系统独占标记的生成，用户只编辑和查看正文。"""
    content = normalize_prompt(prompt)
    return '<agent_personality>\n' + content + '\n</agent_personality>' if content else ''


def render_agent(agent: Any) -> str:
    """Agent 人格仅使用保存的正文，不根据模板代号推导。"""
    return normalize_prompt(agent.metadata.get('personality_prompt')) or ''


_UNSET = object()


class PersonalityController:
    """人格文本先写事件再生效，模板选择形成可编辑副本。"""

    def __init__(self, harness: Any):
        self.harness = harness
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def _session(self, session_id):
        try:
            return self.harness.sessions.get(session_id)
        except SessionNotFoundError:
            return self.harness.subagents.contexts.get(session_id)

    @staticmethod
    def _choice(session):
        return next((event for event in reversed(session.events_of_type('note'))
                     if event.data.get('kind') == 'agent/personality'), None)

    @staticmethod
    def _content(event):
        code = normalize(event.data.get('code'))
        custom = normalize_prompt(event.data.get('prompt'))
        return code, custom if custom is not None else render(code) if code else None

    def snapshot(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        latest = self._choice(session)
        code, prompt = self._content(latest) if latest else (None, None)
        effective = code
        inherited = False
        source = 'session' if code or prompt is not None else 'default'
        if latest is None and session.header.parent_session:
            seen = {session_id}
            parent = session.header.parent_session
            while parent and parent not in seen:
                seen.add(parent)
                parent_session = self._session(parent)
                if parent_session is None:
                    break
                selected = self._choice(parent_session)
                if selected is not None:
                    effective, prompt = self._content(selected)
                    inherited = bool(effective or prompt is not None)
                    source = 'parent' if inherited else 'default'
                    break
                parent = parent_session.header.parent_session
        default_prompt = None
        if self.harness.agent_mount is not None:
            mounted = self.harness.agent_mount.resolve_active(session_id)
            if mounted:
                default_prompt = render_agent(mounted[1])
                if prompt is None and effective is None:
                    prompt = default_prompt
                    source = 'agent' if effective or prompt else 'default'
        return {'code': code, 'effective': effective, 'source': source,
                'prompt': prompt, 'default_prompt': default_prompt,
                'inherited': inherited, 'version': latest.seq if latest else -1}

    async def set(self, session_id: str, code: Any, expected_version: int, *, prompt: Any = _UNSET) -> dict[str, Any]:
        code = normalize(code)
        custom = render(code) if prompt is _UNSET and code else None if prompt is _UNSET else normalize_prompt(prompt)
        if type(expected_version) is not int:
            raise HarnessError(tr('python.personality.invalid'), code='personality/invalid')
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            current = self.snapshot(session_id)
            if current['version'] != expected_version:
                raise HarnessError(tr('python.personality.changed'), code='personality/changed')
            session = self._session(session_id)
            latest = self._choice(session)
            if current['code'] != code or current['prompt'] != custom or latest is None or 'prompt' not in latest.data:
                await session.append('note', {'kind': 'agent/personality', 'code': code, 'prompt': custom})
            return self.snapshot(session_id)

    def context(self, variables: dict[str, str]) -> str:
        from .personality_selection import PersonalitySelection
        selection = PersonalitySelection(self.harness.config.home).read()
        if selection['revision']:
            selected = selection['profile']
            return system_personality(selected['prompt']) if selected else ''
        sid = variables.get('session_id', '')
        if not sid:
            return ''
        try:
            return system_personality(self.snapshot(sid)['prompt'])
        except (SessionNotFoundError, HarnessError):
            return ''

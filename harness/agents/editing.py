"""Agent配置的版本快照与原子更新，服务内并发保存只允许一个版本胜出。"""
from __future__ import annotations
import hashlib
import dataclasses
import os
import re
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any
from harness.i18n import tr
from ..core.errors import ValidationError
from .authoring import draft, markdown
from .parser import parse_agent_text

_SAVE_LOCK = threading.RLock()


class RevisionConflict(Exception):
    """配置自编辑开始后发生变化，调用者必须保留草稿。"""


def snapshot(path: Path, *, source: str) -> dict[str, Any]:
    """从同一份字节生成正文和版本，避免旧正文搭配新版本。"""
    data = Path(path).read_bytes()
    agent = parse_agent_text(data.decode('utf-8-sig'), path=Path(path), source=source)
    return {'agent': agent.to_dict(), 'revision': hashlib.sha256(data).hexdigest()}


def update(path: Path, data: Any, registry: Any, *, expected_revision: str, source: str = 'user') -> dict[str, Any]:
    """验证后原子替换；文件失败与版本冲突不会清空或覆盖原配置。"""
    if not isinstance(expected_revision, str) or re.fullmatch('[0-9a-f]{64}', expected_revision) is None:
        raise ValidationError(tr('agent.authoring.invalid', field='expected_revision'))
    agent = draft(data, registry)
    target = Path(path)
    with _SAVE_LOCK:
        previous = target.read_bytes()
        if hashlib.sha256(previous).hexdigest() != expected_revision:
            raise RevisionConflict()
        current = parse_agent_text(previous.decode('utf-8-sig'), path=target, source=source)
        if agent.name != current.name:
            raise ValidationError(tr('agent.authoring.invalid', field='name'))
        if agent.agent_type != current.agent_type:
            raise ValidationError(tr('agent.authoring.invalid', field='agent_type'))
        content = markdown(agent)
        descriptor, filename = tempfile.mkstemp(prefix='.AGENT-edit-', suffix='.tmp', dir=target.parent)
        temporary = Path(filename)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
            # 同时发现未通过本服务修改的文件；外部编辑器不遵守服务内锁。
            if target.read_bytes() != previous:
                raise RevisionConflict()
            os.replace(temporary, target)
            return {'agent': dataclasses.replace(agent, path=target, source=source).to_dict(),
                    'revision': hashlib.sha256(content.encode('utf-8')).hexdigest()}
        finally:
            temporary.unlink(missing_ok=True)

"""所有对话共享的人格选择；内置风格按语言解析，用户正文保留副本。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import tempfile
import threading
import uuid

from harness.core.errors import HarnessError
from harness.i18n import tr
from .personality import normalize, normalize_prompt, render
from .personality_lock import personality_write_lock

_LOCK = threading.Lock()


class PersonalitySelection:
    def __init__(self, home):
        self.path = Path(home or Path.home() / '.harness') / 'personality-selection.json'

    def read(self):
        """不存在时默认未选择；损坏的配置不得被静默覆盖。"""
        try:
            raw = self.path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return {'profile': None, 'revision': ''}
        except (OSError, UnicodeError) as exc:
            raise HarnessError(tr('python.personality.storageUnavailable'), code='personality/storage-unavailable') from exc
        try:
            row = json.loads(raw)
            if not isinstance(row, dict) or 'profile' not in row or not isinstance(row.get('revision'), str) or not re.fullmatch(r'[a-f0-9]{32}', row['revision']):
                raise ValueError(tr('python.catalog.validation.selectionRevision'))
            profile = row.get('profile')
            if profile is not None:
                if not isinstance(profile, dict) or profile.get('source') not in ('user', 'builtin'):
                    raise ValueError(tr('python.catalog.validation.personalitySource'))
                for key, limit in [('id', 120), ('name', 120), ('description', 500)]:
                    if not isinstance(profile.get(key), str) or len(profile[key]) > limit or (key != 'description' and not profile[key].strip()):
                        raise ValueError(tr('python.catalog.validation.personalityContent'))
                code = normalize(profile.get('code'))
                content = normalize_prompt(profile.get('prompt'))
                if not content:
                    raise ValueError(tr('python.catalog.validation.personalityEmpty'))
                profile = {**profile, 'prompt': content}
                if profile['source'] == 'builtin':
                    if code is None:
                        raise ValueError(tr('python.catalog.validation.personalityContent'))
                    # 内置模板是产品资源；读取仅解析当前语言，不改写持久原件。
                    profile = {**profile, 'name': tr('personality.' + code + '.name'),
                               'description': tr('personality.' + code + '.description'), 'prompt': render(code)}
            return {'profile': profile, 'revision': row['revision']}
        except (ValueError, TypeError, HarnessError) as exc:
            raise HarnessError(tr('python.personality.storageInvalid'), code='personality/storage-invalid') from exc

    def select(self, profile, expected_revision):
        """调用者只传入目录中已验证的条目，存储失败统一报告为可恢复错误。"""
        try:
            return self._select(profile, expected_revision)
        except OSError as exc:
            raise HarnessError(tr('python.personality.storageUnavailable'), code='personality/storage-unavailable') from exc

    def _select(self, profile, expected_revision):
        """按版本原子保存选择，拒绝并发覆盖。"""
        with _LOCK, personality_write_lock(self.path):
            if self.read()['revision'] != expected_revision:
                raise HarnessError(tr('python.personality.changed'), code='personality/changed')
            row = {'profile': profile, 'revision': uuid.uuid4().hex}
            fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='.personality-selection-')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump(row, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            except OSError as exc:
                raise HarnessError(tr('python.personality.storageUnavailable'), code='personality/storage-unavailable') from exc
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return row

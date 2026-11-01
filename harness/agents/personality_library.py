"""用户人格目录：保存独立副本，使用版本检查避免覆盖并发编辑。"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
import threading
import uuid

from .personality import normalize_prompt
from .personality_lock import personality_write_lock
from ..core.errors import HarnessError
from harness.i18n import tr

_LOCK = threading.Lock()


class PersonalityLibrary:
    def __init__(self, home):
        self.path = Path(home or Path.home() / '.harness') / 'personalities.json'

    def _read(self):
        """拒绝损坏的目录，不将读取失败当成空目录写回。"""
        try:
            raw = self.path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError) as exc:
            raise HarnessError(tr('python.personality.storageUnavailable'), code='personality/storage-unavailable') from exc
        return self._validate(raw)

    def _validate(self, raw):
        """校验整份当前格式目录，不接受缺失或损坏字段。"""
        try:
            rows = json.loads(raw)
            identities = set()
            if not isinstance(rows, list):
                raise ValueError(tr('python.catalog.validation.catalogArray'))
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError(tr('python.catalog.validation.catalogEntry'))
                identity = row.get('id')
                if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity) or identity in identities:
                    raise ValueError(tr('python.catalog.validation.personalityIdentity'))
                identities.add(identity)
                if row.get('source') != 'user' or 'code' not in row or row['code'] is not None:
                    raise ValueError(tr('python.catalog.validation.userSource'))
                for key, limit in [('name', 120), ('description', 500), ('prompt', 1000)]:
                    value = row.get(key)
                    if not isinstance(value, str) or len(value) > limit or (key != 'description' and not value.strip()):
                        raise ValueError(tr('python.catalog.validation.personalityContent'))
                if not isinstance(row.get('revision'), str) or not re.fullmatch(r'[a-f0-9]{64}', row['revision']):
                    raise ValueError(tr('python.catalog.validation.personalityRevision'))
                normalize_prompt(row['prompt'])
            return rows
        except (ValueError, TypeError, HarnessError) as exc:
            raise HarnessError(tr('python.personality.storageInvalid'), code='personality/storage-invalid') from exc

    def list(self):
        with _LOCK:
            return [{**row, "prompt": normalize_prompt(row["prompt"])} for row in self._read()]

    def save(self, body):
        fields = {}
        for key, limit in [('name', 120), ('description', 500)]:
            value = body.get(key, '')
            if not isinstance(value, str) or len(value) > limit or (key == 'name' and not value.strip()):
                raise HarnessError(tr('python.catalog.validation.personalityName'), code='personality/invalid')
            fields[key] = value.strip()
        fields['prompt'] = normalize_prompt(body.get('prompt'))
        if not fields['prompt']:
            raise HarnessError(tr('python.catalog.validation.personalityInstructions'), code='personality/invalid')
        with _LOCK, personality_write_lock(self.path):
            rows = self._read()
            identity = body.get('id')
            previous = next((row for row in rows if row['id'] == identity), None)
            if identity and previous is None:
                raise HarnessError(tr('python.catalog.validation.personalityMissing'), code='personality/changed')
            if previous and body.get('revision') != previous['revision']:
                raise HarnessError(tr('python.catalog.validation.personalityEdited'), code='personality/changed')
            fields.update(id=identity or uuid.uuid4().hex, source='user', code=None)
            fields['revision'] = hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            rows = [fields if row['id'] == identity else row for row in rows] if previous else [*rows, fields]
            self._write(rows)
            return fields

    def delete(self, identity, revision):
        """只移除目录记录，已复制到聊天或 Agent中的文本保持独立。"""
        with _LOCK, personality_write_lock(self.path):
            rows = self._read()
            previous = next((row for row in rows if row['id'] == identity), None)
            if previous is None or previous['revision'] != revision:
                raise HarnessError(tr('python.catalog.validation.personalityChanged'), code='personality/changed')
            self._write([row for row in rows if row['id'] != identity])

    def _write(self, rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='.personalities-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(rows, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

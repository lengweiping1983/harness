"""按内容寻址的有界文本证据，不提供公开的任意数据块读取。"""
from __future__ import annotations

from harness.i18n import tr

import hashlib
import os
import re
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

LIMIT = 262144


class FileEvidenceStore:
    def __init__(self, root: Path | None = None):
        self.root = root
        self._memory: dict[str, bytes] = {}
        self._lock = threading.RLock()

    def _put(self, text: str, exists: bool) -> dict[str, Any]:
        raw = text.encode('utf-8')
        if len(raw) > LIMIT or b'\0' in raw:
            raise ValueError(tr('python.file_evidence.162cb547341a'))
        key = hashlib.sha256(raw).hexdigest()
        with self._lock:
            if self.root is None:
                self._memory[key] = raw
            else:
                self.root.mkdir(parents=True, exist_ok=True)
                path = self.root/key
                if path.is_symlink():
                    raise ValueError(tr('python.file_evidence.58abd877d8c9'))
                if not path.exists():
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as handle:
                            temporary = Path(handle.name)
                            handle.write(raw)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(temporary, path)
                    finally:
                        if temporary is not None:
                            temporary.unlink(missing_ok=True)
        return {'sha256': key, 'bytes': len(raw), 'exists': exists}

    def capture(self, value: Mapping[str, Any]) -> dict[str, Any]:
        before, after, exists = value.get('before'), value.get('after'), value.get('before_exists')
        if not isinstance(before, str) or not isinstance(after, str) or type(exists) is not bool:
            raise ValueError(tr('python.file_evidence.baf5fc9c3e6c'))
        if not exists and before:
            raise ValueError(tr('python.file_evidence.472fe3227cc6'))
        return {'version': 1, 'before': self._put(before, exists), 'after': self._put(after, True)}

    @staticmethod
    def identity(reference: Any) -> tuple[str, int, bool]:
        if not isinstance(reference, Mapping):
            raise ValueError(tr('python.file_evidence.2e730588f1c3'))
        key, size, exists = reference.get('sha256'), reference.get('bytes'), reference.get('exists')
        if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key) or type(size) is not int or not 0 <= size <= LIMIT or type(exists) is not bool:
            raise ValueError(tr('python.file_evidence.1957ff5cfb24'))
        if not exists and size:
            raise ValueError(tr('python.file_evidence.472fe3227cc6'))
        return key, size, exists

    def read(self, reference: Any) -> str:
        key, size, exists = self.identity(reference)
        with self._lock:
            if self.root is None:
                raw = self._memory.get(key)
            else:
                path = self.root/key
                if path.is_symlink():
                    raise ValueError(tr('python.file_evidence.f4d070b3a6b9'))
                with path.open('rb') as stream:
                    raw = stream.read(LIMIT + 1)
        if raw is None or len(raw) != size or hashlib.sha256(raw).hexdigest() != key or b'\0' in raw or not exists and raw:
            raise ValueError(tr('python.file_evidence.e1794ce7337b'))
        return raw.decode('utf-8')

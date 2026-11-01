"""重放可信文件版本，不能将当前磁盘当作过去的证据。"""
from __future__ import annotations

import re
import os
import weakref
from collections.abc import Mapping

_LIVE: weakref.WeakSet = weakref.WeakSet()


def _key(path: str) -> str:
    # 事件路径已在执行时解析；重放不能依据当前磁盘重新解析符号链接，
    # 也不能改变原目标的标识。
    return path.replace('/', '\\').rstrip('\\').casefold() if os.name == 'nt' else path.rstrip('/') or '/'


def invalidate_target(key: str) -> None:
    for ledger in tuple(_LIVE):
        ledger.records.pop(key, None)


class FileObservations:
    def __init__(self):
        self.records: dict[str, tuple[str, str]] = {}
        _LIVE.add(self)

    def lookup(self, path: str) -> tuple[str, str] | None:
        """读取已记录的身份，不接触磁盘或刷新观察。"""
        return self.records.get(_key(path))

    def record(self, value) -> None:
        if not isinstance(value, Mapping):
            return
        path, kind, version = value.get('path'), value.get('kind'), value.get('version')
        if not isinstance(path, str) or not path or kind not in {'present', 'absent', 'invalidated'}:
            return
        if kind == 'present' and (not isinstance(version, str) or not re.fullmatch('[0-9a-f]{16}', version)):
            return
        if kind in {'absent', 'invalidated'} and version != '':
            return
        if kind == 'invalidated':
            self.records.pop(_key(path), None)
            return
        self.records[_key(path)] = (kind, version)

    def absorb(self, event) -> None:
        data = event.data
        if not isinstance(data, Mapping):
            return
        if event.type == 'note' and data.get('kind') == 'file_restore_prepared':
            # 即使还原被中断，也必须重新观察文件。
            path = data.get('path')
            if isinstance(path, str):
                self.records.pop(_key(path), None)
            return
        if event.type != 'tool/result' or data.get('name') not in {'read', 'write', 'edit'}:
            return
        results = data.get('results', ())
        if not results:
            return
        proof = data.get('file_observation')
        if any(result.get('is_error') for result in results):
            # 失败只能记录不存在或撤销观察，不能授予或刷新写入权限。
            if isinstance(proof, Mapping) and proof.get('kind') in {'absent', 'invalidated'}:
                self.record(proof)
            return
        if proof is not None:
            self.record(proof)
            return
        # 此前成功的写入已经持久保存按内容寻址的快照。
        # 渲染后的读取或 Shell 输出没有权威版本，不能据此推断。
        display = data.get('display')
        if data.get('name') == 'read' or not isinstance(display, Mapping):
            return
        snapshot = display.get('snapshot')
        after = snapshot.get('after') if isinstance(snapshot, Mapping) and snapshot.get('version') == 1 else None
        if not isinstance(after, Mapping) or after.get('exists') is not True:
            return
        digest = after.get('sha256')
        if isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest):
            self.record({'path': display.get('path'), 'kind': 'present', 'version': digest[:16]})

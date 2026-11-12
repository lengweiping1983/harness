"""只从已提交事件派生工具原件身份索引与有界哈希缓存。"""
from harness.i18n import tr
from collections import OrderedDict
from collections.abc import Mapping
import hashlib
from threading import RLock


class ToolResultEvidence:
    """引用不可变事件，不复制正文；每个会话拥有独立索引。"""

    def __init__(self):
        self._lock = RLock()
        self._origins = {}
        self._digests = OrderedDict()

    def absorb(self, event):
        """只索引原始结果，替换投影不得成为新的原件。"""
        with self._lock:
            if event.type != 'tool/result' or event.source_event_seqs:
                return
            for item in event.data.get('results', ()):
                if isinstance(item, Mapping) and isinstance(item.get('tool_use_id'), str):
                    identity = item['tool_use_id']
                    self._origins.setdefault(identity, []).append((event, item))
                    self._digests.pop(identity, None)

    def origins(self, call_id):
        """返回只读快照，保留全部重复身份供调用方拒绝歧义。"""
        with self._lock:
            return tuple(self._origins.get(call_id, ()))

    def digest(self, call_id):
        """唯一文本原件的哈希按需计算，最多保留 128 项而不保存正文副本。"""
        with self._lock:
            matches = self._origins.get(call_id, ())
            if len(matches) != 1 or not isinstance(matches[0][1].get('content'), str):
                raise ValueError(tr('tool.result.642e31d23664'))
            if call_id not in self._digests:
                self._digests[call_id] = hashlib.sha256(matches[0][1]['content'].encode('utf-8')).hexdigest()
                if len(self._digests) > 128:
                    self._digests.popitem(last=False)
            self._digests.move_to_end(call_id)
            return self._digests[call_id]

    def lookup(self, call_id):
        """同一锁内读取身份与哈希，不能把不同索引状态的原文和哈希配对。"""
        with self._lock:
            matches = self.origins(call_id)
            digest = self.digest(call_id) if len(matches) == 1 and isinstance(matches[0][1].get('content'), str) else None
            return matches, digest

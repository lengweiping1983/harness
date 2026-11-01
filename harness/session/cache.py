"""持久会话的弱身份表与有限热缓存；纯内存仓库保持强持有。"""
from collections import OrderedDict
from collections.abc import MutableMapping
import weakref
import threading


class SessionCache(MutableMapping):
    """外部强引用就是持有权，淘汰热缓存不能产生第二个活投影。"""
    def __init__(self, *, persistent: bool, limit: int = 32, lock=None):
        self.lock = lock or threading.RLock()
        self.identities = weakref.WeakValueDictionary() if persistent else {}
        self.recent = OrderedDict()
        self.limit = limit if persistent else None

    def __getitem__(self, key):
        with self.lock:
            value = self.identities[key]
            self.recent[key] = value
            self.recent.move_to_end(key)
            if self.limit is not None:
                while len(self.recent) > self.limit:
                    self.recent.popitem(last=False)
            return value

    def __setitem__(self, key, value):
        with self.lock:
            self.identities[key] = value
            self[key]

    def __delitem__(self, key):
        with self.lock:
            self.recent.pop(key, None)
            del self.identities[key]

    def __iter__(self):
        return iter(self.snapshot())

    def __len__(self):
        with self.lock:
            return len(self.identities)

    def values(self):
        with self.lock:
            return tuple(self.identities.values())

    def snapshot(self):
        with self.lock:
            return dict(self.identities.items())

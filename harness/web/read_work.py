"""合并同一归档的在途只读计算，不缓存权限或读取结果。"""
from concurrent.futures import Future
from threading import Lock
from weakref import WeakKeyDictionary


_lock = Lock()
_pending = WeakKeyDictionary()


def shared_read(owner, key, read):
    """多个等待者共享一次读取；完成和失败都移除，不重放计算。"""
    with _lock:
        pending = _pending.setdefault(owner, {})
        future = pending.get(key)
        leader = future is None
        if leader:
            future = pending[key] = Future()
    if not leader:
        return future.result()
    try:
        result = read()
    except BaseException as error:
        future.set_exception(error)
        raise
    else:
        future.set_result(result)
        return result
    finally:
        with _lock:
            pending.pop(key, None)
            if not pending:
                _pending.pop(owner, None)

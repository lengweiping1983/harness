"""同步退出前可核对持有者与等待者的异步操作锁。"""
import asyncio


class OperationLock(asyncio.Lock):
    """同时跟踪等待者，释放到下一等待者恢复之间仍属于忙碌状态。"""

    def __init__(self) -> None:
        super().__init__()
        self.waiting = 0

    async def acquire(self) -> bool:
        self.waiting += 1
        try:
            return await super().acquire()
        finally:
            self.waiting -= 1

    @property
    def busy(self) -> bool:
        return self.locked() or self.waiting > 0

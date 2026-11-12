"""对已有进程收集器补充内存边界，不改变原 Agent 行为。"""
from __future__ import annotations


class BoundedReader:
    """保留输出前缀，持续排空余下管道，避免内存增长与死锁。"""

    def __init__(self, stream, limit):
        self.stream = stream
        self.limit = limit
        self.total = 0
        self.kept = 0

    async def read(self, size):
        while True:
            data = await self.stream.read(size)
            self.total += len(data)
            if not data:
                return b""
            remaining = self.limit - self.kept
            if remaining > 0:
                value = data[:remaining]
                self.kept += len(value)
                return value


class BoundedProcess:
    """保留原进程控制接口，仅限制输出捕获。"""

    def __init__(self, process, limit):
        self.process = process
        self.stdout = BoundedReader(process.stdout, limit)
        self.stderr = BoundedReader(process.stderr, limit)

    def __getattr__(self, name):
        return getattr(self.process, name)

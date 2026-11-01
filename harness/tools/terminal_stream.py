"""独立且有界的原始终端重放，使用绝对字符游标。"""
from collections import deque


class TerminalStream:
    def __init__(self, limit: int = 1024 * 1024):
        self.limit = max(1, limit)
        self.chunks: deque[str] = deque()
        self.end = 0
        self.size = 0

    @property
    def start(self):
        return self.end - self.size

    def append(self, text: str):
        if not text:
            return
        self.end += len(text)
        self.size += len(text)
        self.chunks.append(text)
        excess = self.size - self.limit
        while excess > 0:
            head = self.chunks.popleft()
            removed = min(excess, len(head))
            if removed < len(head):
                self.chunks.appendleft(head[removed:])
            self.size -= removed
            excess -= removed

    def read(self, cursor: int, limit: int = 65536):
        start = max(self.start, min(cursor, self.end))
        skip, remaining = start - self.start, min(65536, max(1, limit))
        parts = []
        for chunk in self.chunks:
            if skip >= len(chunk):
                skip -= len(chunk)
                continue
            part = chunk[skip:skip + remaining]
            parts.append(part)
            remaining -= len(part)
            skip = 0
            if not remaining:
                break
        text = ''.join(parts)
        return {'text': text, 'cursor': start + len(text), 'start': self.start,
                'end': self.end, 'reset': cursor < self.start or cursor > self.end}

"""有索引的临时证据，序列化和分页缓冲区大小受限。

这是可丢弃的展示缓存，不作为模型上下文的权威依据。淘汰仅移除缓存引用，
已经使用文档的读取者保留自身句柄。
"""

from harness.i18n import tr
import json
import tempfile
import threading
import weakref
from collections import OrderedDict
from collections.abc import Mapping

from .errors import BadRequest

CHUNK_CHARS = 24000
MAX_DOCUMENTS = 4
_cache = OrderedDict()
_cache_lock = threading.Lock()


def json_chunks(value, level=0):
    """即使字符串很大，也保持与 json.dumps(ensure_ascii=False, indent=2) 一致。"""
    if isinstance(value, str):
        yield '"'
        for offset in range(0, len(value), 1024):
            yield json.dumps(value[offset:offset + 1024], ensure_ascii=False)[1:-1]
        yield '"'
    elif isinstance(value, Mapping):
        if not value:
            yield '{}'
            return
        yield '{\n'
        for index, (key, item) in enumerate(value.items()):
            if not isinstance(key, str):
                raise TypeError(tr('runtime.validation.5df542a93c4e'))
            if index:
                yield ',\n'
            yield '  ' * (level + 1)
            yield from json_chunks(key, level + 1)
            yield ': '
            yield from json_chunks(item, level + 1)
        yield '\n' + '  ' * level + '}'
    elif isinstance(value, (list, tuple)):
        if not value:
            yield '[]'
            return
        yield '[\n'
        for index, item in enumerate(value):
            if index:
                yield ',\n'
            yield '  ' * (level + 1)
            yield from json_chunks(item, level + 1)
        yield '\n' + '  ' * level + ']'
    else:
        yield json.dumps(value, ensure_ascii=False)


def source_chunks(session, seqs, summary_seq):
    if not seqs:
        yield '[]'
        return
    yield '[\n'
    for index, seq in enumerate(seqs):
        if index:
            yield ',\n'
        yield '  '
        event = session.event_at(seq) if type(seq) is int and 0 <= seq < summary_seq else None
        yield from json_chunks(event.json_view() if event else {'seq': seq, 'missing': True}, 1)
    yield '\n]'


class EvidenceDocument:
    def __init__(self):
        self.lock = threading.Lock()
        self.file = None
        self.positions = []
        self.total = 0

    def _build(self, chunks):
        spool = tempfile.TemporaryFile(mode='w+b')
        positions, total, buffered = [0], 0, ''
        try:
            for piece in chunks:
                while piece:
                    take = min(CHUNK_CHARS - len(buffered), len(piece))
                    buffered += piece[:take]
                    piece = piece[take:]
                    if len(buffered) == CHUNK_CHARS:
                        spool.write(buffered.encode('utf-8', errors='surrogatepass'))
                        total += len(buffered)
                        buffered = ''
                        positions.append(spool.tell())
            if buffered:
                spool.write(buffered.encode('utf-8', errors='surrogatepass'))
                total += len(buffered)
                positions.append(spool.tell())
            spool.flush()
        except BaseException:
            spool.close()
            raise
        weakref.finalize(self, spool.close)
        self.file, self.positions, self.total = spool, positions, total

    def read(self, offset, factory):
        if type(offset) is not int or offset < 0:
            raise BadRequest(tr('python.evidence_reader.abd2306c4445'))
        with self.lock:
            if self.file is None:
                self._build(factory())
            if offset > self.total:
                raise BadRequest(tr('python.evidence_reader.354de65fab4f'))
            block = offset // CHUNK_CHARS
            end_block = min(block + 2, len(self.positions) - 1)
            start = self.positions[block]
            self.file.seek(start)
            text = self.file.read(self.positions[end_block] - start).decode('utf-8', errors='surrogatepass')
            relative = offset % CHUNK_CHARS
            text = text[relative:relative + CHUNK_CHARS]
            end = offset + len(text)
            return {'text': text, 'offset': offset, 'total_chars': self.total,
                    'next_offset': end if end < self.total else None}


def read_sources(session, summary_seq, start_seq, seqs, offset):
    # 源事件和摘要节点不可变，后续 through 边界不能改变此文档。
    # 不同摘要使用不同的键。
    key = (weakref.ref(session), session._history_generation, summary_seq, start_seq)
    with _cache_lock:
        document = _cache.pop(key, None) or EvidenceDocument()
        _cache[key] = document
        while len(_cache) > MAX_DOCUMENTS:
            _cache.popitem(last=False)
    return document.read(offset, lambda: source_chunks(session, seqs, summary_seq))

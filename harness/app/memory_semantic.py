"""显式注入的本地语义候选服务；不改变默认关键词注入。"""
from __future__ import annotations

import threading
import re
import hashlib
import math
import json
from array import array
from collections import OrderedDict
from pathlib import Path

from .memory_consolidation import inspect_record
from .memory_search import rank
from .memory_sources import SourceChecker
from ..core.errors import HarnessError
from harness.i18n import tr


def candidate_window(store, cwd, rows: list[dict], offset: int, snapshot: str | None) -> tuple[list[dict], dict]:
    """分页绑定作用域和当前数据库快照，变化时拒绝继续而不是静默漏扫。"""
    digest = hashlib.sha256((str(store.path(cwd).resolve()) + '\0' + store.format).encode())
    digest.update(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
    identity = digest.hexdigest()
    if snapshot is not None and snapshot != identity:
        raise HarnessError(tr('python.memory.scan_changed'), code='memory/scan-changed')
    selected = rows[offset:offset + 200]
    end = offset + len(selected)
    return selected, {'offset': offset, 'snapshot': identity, 'total_records': len(rows),
        'next_offset': end if end < len(rows) else None, 'scanned': len(selected),
        'omitted': len(rows) - len(selected), 'remaining': max(0, len(rows) - end)}


def fuse_candidates(rows: list[dict], query: str, similarities, limit: int) -> list[tuple[dict, dict]]:
    """保留每个排序来源的名次；相似度与融合得分都不是事实置信度。"""
    try:
        scores = tuple(float(value) for value in similarities)
    except (OverflowError, TypeError, ValueError):
        raise ValueError(tr('python.memory.semantic.similarities')) from None
    if len(scores) != len(rows) or not all(math.isfinite(value) for value in scores):
        raise ValueError(tr('python.memory.semantic.similarities'))
    lexical = {row['id']: index for index, row in enumerate(rank(rows, query), start=1)}
    order = sorted(range(len(rows)), key=lambda index: scores[index], reverse=True)
    dense = {rows[index]['id']: position for position, index in enumerate(order, start=1)
             if scores[index] >= 0.3}
    candidates = []
    for row in rows:
        identifier = row['id']
        if identifier not in lexical and identifier not in dense:
            continue
        score = ((1 / (60 + lexical[identifier])) if identifier in lexical else 0)
        score += (1 / (60 + dense[identifier])) if identifier in dense else 0
        candidates.append((row, {'keyword_rank': lexical.get(identifier), 'semantic_rank': dense.get(identifier),
            'fusion_score': score, 'match': 'both' if identifier in lexical and identifier in dense
                else 'keyword' if identifier in lexical else 'semantic'}))
    return sorted(candidates, key=lambda item: item[1]['fusion_score'], reverse=True)[:limit]


class LocalMemoryCandidates:
    """仅加载已经存在的模型；向量缓存只在内存中，不持久化用户向量或正文。"""

    def __init__(self, cache_dir: str | Path) -> None:
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self._lock = threading.Lock()
        self._model = None
        self._vectors = OrderedDict()
        self._worker = None
        self._closed = False

    async def search_async(self, store, cwd, query, *, limit=5, offset=0, snapshot=None):
        """工具调用隔离原生推理；精确查询不加载模型，也不启动子进程。"""
        if self._closed:
            raise RuntimeError(tr('python.memory.semantic.closed'))
        exact = self._validate_query(store, query, limit, offset, snapshot)
        options = {'limit': limit, 'offset': offset, 'snapshot': snapshot}
        if exact:
            import asyncio
            return await asyncio.to_thread(self.search, store, cwd, query, **options)
        if self._worker is None:
            from .memory_semantic_worker import SemanticWorker
            self._worker = SemanticWorker(self.cache_dir)
        return await self._worker.search(store, cwd, query, **options)

    async def aclose(self):
        """关闭异步检索所属进程；同步SDK调用的模型由直接调用者负责。"""
        self._closed = True
        if self._worker is not None:
            await self._worker.aclose()

    def _embeddings(self, store, cwd, rows, model):
        """缓存键绑定真实作用域、身份、修订和完整内容摘要，不缓存正文或查询。"""
        scope = hashlib.sha256((str(store.path(cwd).resolve()) + '\0' + store.format).encode()).digest()
        keys = [(scope, row['id'], row['revision'],
                 hashlib.sha256((row['topic'] + '\0' + row['content']).encode()).digest()) for row in rows]
        missing = [index for index, key in enumerate(keys) if key not in self._vectors]
        generated = list(model.passage_embed([rows[index]['topic'][:50] + ' ' + rows[index]['content'][:256]
                                             for index in missing])) if missing else []
        if len(generated) != len(missing):
            raise ValueError(tr('python.memory.semantic.document_count'))
        validated = []
        for vector in generated:
            try:
                value = tuple(float(item) for item in vector)
            except (OverflowError, TypeError, ValueError):
                raise ValueError(tr('python.memory.semantic.document_vector')) from None
            if len(value) != 384 or not all(math.isfinite(item) for item in value) or not 0 < math.hypot(*value) < math.inf:
                raise ValueError(tr('python.memory.semantic.document_vector'))
            compact = array('f', value)
            if not all(math.isfinite(item) for item in compact) or not any(compact):
                raise ValueError(tr('python.memory.semantic.compact_vector'))
            validated.append(compact)
        for index, value in zip(missing, validated):
            self._vectors[keys[index]] = value
        result = []
        for key in keys:
            result.append(self._vectors[key])
            self._vectors.move_to_end(key)
        while len(self._vectors) > 512:
            self._vectors.popitem(last=False)
        return result, len(missing)

    def _load(self):
        if self._model is None:
            if not self.cache_dir.is_dir():
                raise ValueError(tr('python.memory.semantic.cache_missing'))
            try:
                from fastembed import TextEmbedding
            except ImportError:
                raise ValueError(tr('python.memory.semantic.dependency_missing')) from None
            self._model = TextEmbedding(model_name='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
                cache_dir=str(self.cache_dir), local_files_only=True, threads=2)
        return self._model

    @staticmethod
    def _validate_query(store, query, limit, offset, snapshot):
        """同步与异步入口共享校验，非法请求不能触发进程或模型初始化。"""
        store._query_fields(query, limit)
        if not query.strip() or limit > 10:
            raise ValueError(tr('python.memory.semantic.query'))
        exact = bool(re.fullmatch(r'[\x00-\x7f]+', query.strip()) and not re.search(r'\s', query.strip()))
        if (type(offset) is not int or not 0 <= offset < 1000 or offset and (snapshot is None or exact)
                or snapshot is not None and (not isinstance(snapshot, str) or not re.fullmatch('[0-9a-f]{64}', snapshot))):
            raise HarnessError(tr('python.memory.invalid_scan'), code='memory/invalid')
        return exact

    def search(self, store, cwd, query: str, *, limit: int = 5, offset: int = 0, snapshot: str | None = None) -> dict:
        """只返回候选，不提供已验证答案；每次重新读取，避免陈旧向量覆盖当前版本。"""
        if self._closed:
            raise RuntimeError(tr('python.memory.semantic.closed'))
        exact = self._validate_query(store, query, limit, offset, snapshot)
        rows = store.list(cwd)
        by_id = {row['id']: row for row in rows}
        scanned, window = candidate_window(store, cwd, rows, offset, snapshot)
        if exact:
            checker = SourceChecker(cwd) if store.check_sources else None
            return {'candidates': [{**row, **inspect_record(row, by_id, checker),
                                   'retrieval': {'match': 'exact', 'keyword_rank': position,
                                                 'semantic_rank': None, 'fusion_score': None}}
                                   for position, row in enumerate(rank(rows, query)[:limit], start=1)],
                    **window, 'scanned': len(rows), 'omitted': 0, 'remaining': 0, 'next_offset': None,
                    'meaning': 'exact-query-candidates-only',
                    'requires_answer_support_check': True}
        if not scanned:
            return {'candidates': [], **window, 'meaning': 'semantic-candidates-only',
                    'requires_answer_support_check': True}
        with self._lock:
            model = self._load()
            import numpy as np
            # 量化模型最长128 token；仅编码短前缀且明确不声称完整正文已索引。
            embeddings, encoded = self._embeddings(store, cwd, scanned, model)
            vectors = np.asarray(embeddings, dtype=np.float64)
            vector = np.asarray(next(iter(model.query_embed(query))), dtype=np.float64)
            if (vectors.ndim != 2 or vectors.shape[0] != len(scanned) or vector.shape != (vectors.shape[1],)
                    or not np.isfinite(vectors).all() or not np.isfinite(vector).all()):
                raise ValueError(tr('python.memory.semantic.dimensions'))
            norms, norm = np.linalg.norm(vectors, axis=1), np.linalg.norm(vector)
            if np.any(norms == 0) or norm == 0 or not np.isfinite(norms).all() or not np.isfinite(norm):
                raise ValueError(tr('python.memory.semantic.zero_vector'))
            scores = (vectors / norms[:, None]) @ (vector / norm)
        positions = {row['id']: index for index, row in enumerate(scanned)}
        chosen = fuse_candidates(scanned, query, scores, limit)
        checker = SourceChecker(cwd) if store.check_sources else None
        return {'candidates': [{**row, **inspect_record(row, by_id, checker),
            'semantic_score': float(scores[positions[row['id']]]), 'indexed_content_prefix_chars': min(256, len(row['content'])),
            'indexed_topic_prefix_chars': min(50, len(row['topic'])),
            'retrieval': explanation, 'embedding_may_truncate': True} for row, explanation in chosen],
            **window, 'meaning': 'semantic-candidates-only',
            'encoded_records': encoded, 'reused_records': len(scanned) - encoded,
            'model': 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
            'threshold': 0.3, 'requires_answer_support_check': True,
            'retrieval_policy': {'fusion': 'rrf', 'rrf_constant': 60, 'working_set_limit': 200,
                                 'model_token_limit': 128, 'score_meaning': 'similarity-not-confidence'}}

"""无副作用的记忆注入选择与召回解释。"""
from __future__ import annotations

import json
from dataclasses import dataclass

from harness.i18n import tr
from .memory_search import rank
from .memory_sources import PREFIX, SourceChecker
from .memory_consolidation import PREFIX as DERIVATION_PREFIX, covered_inputs, inspect_record


@dataclass(frozen=True, slots=True)
class MemoryReference:
    """解释中仅保留身份和修订，不复制用户正文到诊断。"""

    id: str
    revision: int
    reason: str
    source_status: str = 'untracked'
    derivation_status: str = 'none'


@dataclass(frozen=True, slots=True)
class MemoryContext:
    """一次读取快照的选择结果；不是已发送模型请求的执行回执。"""

    text: str
    budget: int
    total: int
    selected: tuple[MemoryReference, ...] = ()
    skipped_budget: tuple[MemoryReference, ...] = ()
    skipped_query_limit: int = 0
    unmatched: int = 0
    suppressed: tuple[tuple[MemoryReference, MemoryReference], ...] = ()

    def explain(self) -> dict:
        """默认解释不携带提示正文、查询文本、证据或用户偏好。"""
        def reference(item):
            return {'id': item.id, 'revision': item.revision, 'reason': item.reason,
                    'source_status': item.source_status, 'derivation_status': item.derivation_status}
        return {'budget': self.budget, 'used_chars': len(self.text), 'total': self.total,
                'selected': [reference(item) for item in self.selected],
                'skipped_budget': [reference(item) for item in self.skipped_budget],
                'skipped_query_limit': self.skipped_query_limit, 'unmatched': self.unmatched,
                'suppressed': [{'id': item.id, 'revision': item.revision,
                                'summary_id': summary.id, 'summary_revision': summary.revision}
                               for item, summary in self.suppressed]}


def select_context(rows: list[dict], *, context_key: str, query: str, budget: int,
                   checker: SourceChecker | None = None) -> MemoryContext:
    """固定资料优先；被预算跳过的资料不会阻塞后续较小条目。"""
    pinned = [row for row in rows if row['pinned']]
    remaining = [row for row in rows if not row['pinned']]
    matches = rank(remaining, query[:500]) if query.strip() else []
    candidates = [(row, 'pinned') for row in pinned] + [(row, 'query') for row in matches]
    encoded, selected = [], []
    text = ''
    by_id = {row['id']: row for row in rows}
    decisions: dict[str, tuple[MemoryReference, str]] = {}
    coverage: dict[str, set[str]] = {}
    owners: dict[str, MemoryReference] = {}
    query_attempts = 0
    explicit = query.strip().casefold()
    for row, reason in candidates:
        initial = MemoryReference(row['id'], row['revision'], reason,
            'unchecked' if checker is not None and row['evidence'].startswith((PREFIX, DERIVATION_PREFIX)) else 'untracked',
            'unchecked' if row['evidence'].startswith(DERIVATION_PREFIX) else 'none')
        if row['id'] in owners:
            decisions[row['id']] = (initial, 'suppressed')
            continue
        if reason == 'query' and query_attempts >= 5:
            decisions[row['id']] = (initial, 'limit')
            continue
        if reason == 'query':
            query_attempts += 1
        potential = covered_inputs(row, by_id) - {explicit} if row['evidence'].startswith(DERIVATION_PREFIX) else set()
        retained = [(ref, data) for ref, data in zip(selected, encoded) if ref.id not in potential]
        item = {key: row[key] for key in ('id', 'content', 'topic', 'source', 'evidence')}
        data = json.dumps(item, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e')
        candidate = tr(context_key, data='[' + ', '.join([*(value for _, value in retained), data]) + ']')
        status, derivation = initial.source_status, initial.derivation_status
        if len(candidate) <= budget:
            metadata = inspect_record(row, by_id, checker)
            if metadata.get('derivation', {}).get('requires_reverification', True):
                potential = set()
                retained = list(zip(selected, encoded))
            if metadata:
                derivation = metadata.get('derivation', {}).get('status', 'none')
                status = metadata.get('source_check', {}).get('status',
                    'unchecked' if checker is not None and derivation in {'changed', 'missing', 'invalid'} else 'untracked')
                item.update(metadata)
                data = json.dumps(item, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e')
            candidate = tr(context_key, data='[' + ', '.join([*(value for _, value in retained), data]) + ']')
        reference = MemoryReference(row['id'], row['revision'], reason, status, derivation)
        if len(candidate) <= budget:
            selected = [ref for ref, _ in retained] + [reference]
            encoded = [value for _, value in retained] + [data]
            coverage[row['id']] = potential
            owners = {}
            for summary in selected:
                for identifier in coverage.get(summary.id, ()):
                    owners.setdefault(identifier, summary)
            selected_ids = {ref.id for ref in selected}
            for identifier, (previous, decision) in tuple(decisions.items()):
                if identifier in owners and identifier not in selected_ids:
                    if previous.reason == 'query' and decision in {'selected', 'budget'}:
                        query_attempts -= 1
                    decisions[identifier] = (previous, 'suppressed')
            decisions[row['id']] = (reference, 'selected')
            text = candidate
        else:
            decisions[row['id']] = (reference, 'budget')
    return MemoryContext(text, budget, len(rows), tuple(selected),
        tuple(ref for ref, decision in decisions.values() if decision == 'budget'),
        sum(decision == 'limit' for _, decision in decisions.values()), len(remaining) - len(matches),
        tuple((ref, owners[ref.id]) for ref, decision in decisions.values() if decision == 'suppressed'))

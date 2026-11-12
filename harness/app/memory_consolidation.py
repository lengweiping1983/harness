"""记忆整理的当前格式和输入版本引用。"""
from __future__ import annotations

import json
import re

PREFIX = 'harness:consolidation:v1\n'
MAX_INPUTS = 6


def parse(evidence: str) -> dict | None:
    """普通证据不是整理记录；格式不符的整理记录必须明确拒绝。"""
    if not evidence.startswith(PREFIX):
        return None
    try:
        if len(evidence) > 1000:
            raise ValueError
        value = json.loads(evidence[len(PREFIX):])
        if (not isinstance(value, dict) or set(value) != {'request', 'note', 'inputs'}
                or not isinstance(value['request'], str) or not re.fullmatch('[0-9a-f]{64}', value['request'])
                or not isinstance(value['note'], str) or not isinstance(value['inputs'], list)
                or not 2 <= len(value['inputs']) <= MAX_INPUTS):
            raise ValueError
        ids = set()
        for item in value['inputs']:
            if (not isinstance(item, dict) or set(item) != {'id', 'input_revision', 'revision', 'pinned'}
                    or not isinstance(item['id'], str) or not 1 <= len(item['id']) <= 100 or item['id'] in ids
                    or type(item['input_revision']) is not int or item['input_revision'] < 1
                    or type(item['pinned']) is not bool or type(item['revision']) is not int
                    or item['revision'] != item['input_revision'] + int(item['pinned'])):
                raise ValueError
            ids.add(item['id'])
    except (ValueError, TypeError):
        raise ValueError('memory/invalid-consolidation') from None
    return value


def lineage(row: dict, rows: dict[str, dict], *, seen: frozenset[str] = frozenset()) -> tuple[str, list[dict]]:
    """核对摘要依据的当前版本；有界展开，不以正文相似度猜测身份。"""
    if row['id'] in seen or len(seen) >= 8:
        return 'invalid', []
    try:
        value = parse(row['evidence'])
    except ValueError:
        return 'invalid', []
    if value is None:
        return 'none', [row]
    leaves = []
    for item in value['inputs']:
        parent = rows.get(item['id'])
        if parent is None:
            return 'missing', []
        if parent['revision'] != item['revision']:
            return 'changed', []
        status, nested = lineage(parent, rows, seen=seen | {row['id']})
        if status not in {'none', 'current'}:
            return status, []
        leaves.extend(nested)
        if len(leaves) > 32:
            return 'invalid', []
    return 'current', leaves


def inspect_record(row: dict, rows: dict[str, dict], checker=None) -> dict:
    """摘要继承输入的来源核验；输入版本变化时不能借用新版资料补证。"""
    status, leaves = lineage(row, rows)
    result = {}
    if status != 'none':
        result['derivation'] = {'status': status, 'requires_reverification': status != 'current'}
    if checker is not None and leaves:
        checks = [value for leaf in {item['id']: item for item in leaves}.values()
                  if (value := checker.check(leaf['evidence'])) is not None]
        if checks:
            priority = ('unsafe', 'invalid', 'missing', 'changed', 'unavailable', 'unchecked', 'unchanged')
            state = next(value for value in priority if any(check['status'] == value for check in checks))
            files = {(item['path'], item['status']): item for check in checks for item in check['files']}
            result['source_check'] = {'status': state, 'files': list(files.values())[:8],
                                      'omitted_files': max(0, len(files) - 8)}
            if state != 'unchanged' and 'derivation' in result:
                result['derivation']['requires_reverification'] = True
    return result


def covered_inputs(row: dict, rows: dict[str, dict]) -> set[str]:
    """只为完整有效的引用图返回未固定输入，不能覆盖用户显式固定的条目。"""
    if lineage(row, rows)[0] != 'current':
        return set()
    pending, seen, covered = [row], {row['id']}, set()
    while pending:
        current = pending.pop()
        try:
            value = parse(current['evidence'])
        except ValueError:
            return set()
        if value is None:
            continue
        for reference in value['inputs']:
            parent = rows.get(reference['id'])
            if parent is None or parent['revision'] != reference['revision']:
                return set()
            if parent['id'] not in seen:
                seen.add(parent['id'])
                covered.add(parent['id'])
                pending.append(parent)
                if len(seen) > 256:
                    return set()
    return {identifier for identifier in covered if not rows[identifier]['pinned']}

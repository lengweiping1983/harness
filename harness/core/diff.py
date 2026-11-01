"""按统一补丁的 LF 边界保留原文，不把 Unicode 字符当成换行。"""
from __future__ import annotations

import difflib


def _lines(text: str) -> list[str]:
    """保留实际 LF 和末行状态，CR 仍属于原始行内容。"""
    parts = text.split('\n')
    return [line + '\n' for line in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def unified_patch(before: str, after: str, *, fromfile: str, tofile: str) -> str:
    """生成可逐行解析的补丁，未终止行用标准元信息标记。"""
    return ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n'
                   for line in difflib.unified_diff(_lines(before), _lines(after),
                                                    fromfile=fromfile, tofile=tofile))


def patch_counts(patch: str) -> tuple[int, int]:
    """只统计补丁块内的增删行，正文中的加减号和 Unicode 分隔符不另拆行。"""
    added = removed = 0
    in_hunk = False
    for line in patch.split('\n'):
        if line.startswith('diff --git '):
            in_hunk = False
        elif line.startswith('@@ '):
            in_hunk = True
        elif in_hunk and line.startswith('+'):
            added += 1
        elif in_hunk and line.startswith('-'):
            removed += 1
    return added, removed

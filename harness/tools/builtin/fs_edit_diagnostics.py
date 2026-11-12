"""精确编辑的有界恢复提示，诊断不能授予写入权限。"""

from __future__ import annotations

from harness.i18n import tr

from pathlib import Path
from collections import Counter

from ...core.errors import ToolExecutionError
from .fs_text import text_lines, fenced_source


def edit_not_found(target: Path, text: str, old: str, new: str) -> ToolExecutionError:
    """定位少量当前行，不进行模糊替换，也不回显超大匹配文本。"""
    # 这些仅是位置提示，匹配一行不能证明
    # 整个代码块就是预期替换目标。
    anchors = {line.strip() for line in text_lines(old)[:80] if len(line.strip()) >= 12}
    lines = text_lines(text)
    stripped = [line.strip() for line in lines]
    frequencies = Counter(stripped)
    # 稀有上下文权重更高；相邻多个锚点优于文件前部的孤立通用语句。
    hits = [index for index, line in enumerate(stripped) if line in anchors]
    ranked = sorted(hits, key=lambda index: (
        -sum(1 / frequencies[line] for line in set(stripped[max(0, index - 2):index + 8])
             if line in anchors), index,
    ))
    old_lines = text_lines(old)[:80]
    # 只对多个行锚点（忽略首尾空白）支持的位置提供差异提示，不能据此执行写入。
    alignments: dict[int, set[int]] = {}
    old_positions: dict[str, list[int]] = {}
    for index, line in enumerate(old_lines):
        if line.strip() in anchors:
            old_positions.setdefault(line.strip(), []).append(index)
    for index in hits:
        anchor = stripped[index]
        if frequencies[anchor] > 100:
            continue
        for offset in old_positions[anchor]:
            start = index - offset
            if start >= 0:
                alignments.setdefault(start, set()).add(offset)
    supported = [start for start, offsets in alignments.items() if len(offsets) >= 2]
    mismatch = ''
    difference: dict[str, object] = {}
    if supported:
        start = min(supported, key=lambda start: (
            -sum(1 / frequencies[old_lines[offset].strip()] for offset in alignments[start]), start))
        for offset, expected in enumerate(old_lines):
            index = start + offset
            actual = lines[index] if index < len(lines) else ''
            if actual != expected:
                # 只描述可直接核验的文本关系，不推断外部修改或模型记忆来源。
                kind = ('whitespace' if actual.strip() == expected.strip() else
                        'source_line_absent' if expected.strip() and expected.strip() not in frequencies else
                        'content')
                difference = {'line': index + 1, 'kind': kind,
                              'expected': expected[:160], 'actual': actual[:160]}
                mismatch = tr('python.fs_edit_diagnostics.first_difference',
                              line=index + 1, expected=repr(expected[:160]), actual=repr(actual[:160]))
                # 把差异附近优先列出，避免目标首行拼错时只展示后半段。
                ranked.insert(0, min(index, len(lines) - 1))
                break
    candidates: list[dict[str, int]] = []
    excerpts: list[str] = []
    remaining = 2400
    for index in ranked:
        number = index + 1
        start, end = max(1, number - 2), min(len(lines), number + 7)
        if any(start <= item['end'] and end >= item['start'] for item in candidates):
            continue
        candidates.append({'start': start, 'end': end})
        excerpt = '\n'.join(f'{i + 1:6d}\t{lines[i]}' for i in range(start - 1, end))
        if len(excerpt) > remaining:
            excerpt = excerpt[:remaining] + tr('python.fs_edit_diagnostics.e85f100d3ac9')
        excerpts.append(tr('python.fs_edit_diagnostics.554431afc770', p0=f'{start}', p1=f'{end}',
                           p2=fenced_source(excerpt)))
        remaining -= min(len(excerpt), remaining)
        if len(candidates) >= 3 or remaining <= 0:
            break

    new_text_present = bool(new) and new in text
    message = (
        tr('python.fs_edit_diagnostics.2bda536f15f8')
    )
    if mismatch:
        message += '\n' + mismatch
        if difference['kind'] == 'whitespace':
            message += '\n' + tr('python.fs_edit_diagnostics.whitespace')
        elif difference['kind'] == 'source_line_absent':
            message += '\n' + tr('python.fs_edit_diagnostics.source_line_absent')
    suggested_read = None
    if candidates:
        first = candidates[0]
        suggested_read = {'path': str(target), 'offset': first['start'],
                          'limit': first['end'] - first['start'] + 1, 'line_numbers': False}
        message += '\n' + tr('python.fs_edit_diagnostics.read_next',
                              path=str(target), offset=first['start'], limit=suggested_read['limit'])
    if new_text_present:
        message += (
            tr('python.fs_edit_diagnostics.33db282f2578')
        )
    if excerpts:
        message += '\n\n' + '\n\n'.join(excerpts)
    else:
        message += tr('python.fs_edit_diagnostics.87c39f226c21')
    return ToolExecutionError(
        message, code='FS_EDIT_NOT_FOUND', path=str(target),
        old_chars=len(old), new_text_present=new_text_present,
        candidate_ranges=candidates, first_difference=difference, suggested_read=suggested_read,
    )


def ambiguous_edit(target: Path, matched_lines: list[int]) -> ToolExecutionError:
    """限制重复匹配的诊断大小，包括异常详情。"""
    shown = matched_lines[:20]
    suffix = tr('python.fs_edit_diagnostics.3514f9a1a81a') if len(matched_lines) > len(shown) else ''
    return ToolExecutionError(
        tr('python.fs_edit_diagnostics.0e4fcd16e64a', p0=f'{len(matched_lines)}', p1=f'{shown}', p2=f'{suffix}'),
        code='FS_AMBIGUOUS_EDIT', path=str(target),
        occurrences=len(matched_lines), matched_lines=shown,
    )


def edited_context(text: str, offset: int, replacement: str) -> str:
    """回传写入后的有界原文，避免后续编辑继续沿用写入前的上下文。"""
    lines = text_lines(text)
    first = text.count('\n', 0, offset)
    start = max(0, first - 2)
    end = min(len(lines), first + min(replacement.count('\n') + 1, 40) + 2)
    selected: list[str] = []
    size = 0
    for line in lines[start:end]:
        if size + len(line) + 1 > 6000:
            break
        selected.append(line)
        size += len(line) + 1
    if not selected:
        return '\n' + tr('python.fs_edit_diagnostics.reread_after_edit')
    excerpt = '\n'.join(selected)
    result = '\n' + tr('python.fs_edit_diagnostics.current_after_edit',
                       start=start + 1, end=start + len(selected),
                       excerpt=fenced_source(excerpt))
    if start + len(selected) < min(len(lines), first + replacement.count('\n') + 1):
        result += '\n' + tr('python.fs_edit_diagnostics.context_incomplete')
    return result

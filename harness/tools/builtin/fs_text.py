"""文件读取、编辑及恢复提示共用的原文行边界。"""
from __future__ import annotations

import codecs
import re


def normalize_eol(text: str) -> str:
    """仅统一 CRLF／CR，不把源码中的 Unicode 分隔符改成换行。"""
    return text.replace('\r\n', '\n').replace('\r', '\n')


def text_lines(text: str) -> list[str]:
    """按编辑匹配使用的换行拆行，不虚构文件末尾的空行。"""
    return normalize_eol(text).removesuffix('\n').split('\n') if text else []


def splice_source(raw: str, edits: list[tuple[int, int, str]]) -> str:
    """按归一文本中有序、不重叠的区间编辑，保留区间外原始字符。

    替换内已有的行尾依次复用；新增行尾沿用匹配内最后一个，
    或目标行的行尾。插入优先沿用前一行，文件开头沿用后一行。
    """
    endings = re.finditer(r'\r\n|\r|\n', raw)
    following = next(endings, None)
    previous = '\n'
    extra = 0
    pieces: list[str] = []
    cursor = 0

    def advance(offset: int, keep: int = 0) -> tuple[int, list[str], str]:
        """顺序换算原始位置，只保留新文本实际需要复用的行尾。"""
        nonlocal following, previous, extra
        original: list[str] = []
        last = ''
        while following is not None and following.start() - extra < offset:
            last = previous = following.group()
            if len(original) < keep:
                original.append(last)
            extra += len(last) - 1
            following = next(endings, None)
        return offset + extra, original, last

    def append(piece: str) -> None:
        """避免编辑接缝把原本两个换行合并成一个 CRLF。"""
        if not piece:
            return
        if pieces and pieces[-1].endswith('\r') and piece.startswith('\n'):
            pieces.append('\n')
        pieces.append(piece)

    for start, end, new in edits:
        lines = normalize_eol(new).split('\n')
        raw_start, _, _ = advance(start)
        if start == end and raw_start:
            fallback = previous
        else:
            fallback = following.group() if following else previous
        raw_end, original, last = advance(end, len(lines) - 1)
        if last:
            fallback = last
        append(raw[cursor:raw_start])
        for index, line in enumerate(lines[:-1]):
            append(line + (original[index] if index < len(original) else fallback))
        append(lines[-1])
        cursor = raw_end
    append(raw[cursor:])
    return ''.join(pieces)


def fenced_source(text: str) -> str:
    """围栏长于源码中的反引号串，避免原文关闭展示代码块。"""
    fence = '`' * max(3, 1 + max((len(run) for run in re.findall(r'`+', text)), default=0))
    suffix = '' if text.endswith('\n') else '\n'
    return f'{fence}text\n{text}{suffix}{fence}'


def decode_utf8_prefix(data: bytes, *, truncated: bool) -> str:
    """严格解码已读取前缀，仅允许截断点留下未完成的多字节字符。"""
    return codecs.getincrementaldecoder('utf-8')(errors='strict').decode(data, final=not truncated)

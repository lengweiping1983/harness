"""无外部模型的记忆关键词排序，支持连续中文短语。"""
from __future__ import annotations
import re
from .memory_consolidation import parse as parse_consolidation
from .memory_sources import parse_sources

_HAN = re.compile(r'^[\u3400-\u9fff]+$')
_TOKENS = re.compile(r'[\u3400-\u9fff]+|[^\W\u3400-\u9fff]+', re.UNICODE)
_PATH = re.compile(r'(?:[\w.-]+[/\\])+[\w.-]+', re.UNICODE)


def rank_queries(rows: list[dict], queries: list[str]) -> tuple[list[dict], dict[str, list[int]]]:
    """合并有限查询的排名；重复改写不加权，返回命中查询的位置以便核查。"""
    scores, matches, seen = {}, {}, set()
    for index, query in enumerate(queries):
        identity = query.strip().casefold()
        if identity in seen:
            continue
        seen.add(identity)
        for position, row in enumerate(rank(rows, query)[:100], start=1):
            identifier = row['id']
            scores[identifier] = scores.get(identifier, 0) + 1 / (60 + position)
            matches.setdefault(identifier, []).append(index)
    return sorted((row for row in rows if row['id'] in scores),
                  key=lambda row: scores[row['id']], reverse=True), matches


def searchable_text(row: dict) -> str:
    """只索引事实、说明和来源路径；机器身份与版本不作为相关性依据。"""
    evidence = row['evidence']
    try:
        files = parse_sources(evidence)
        summary = parse_consolidation(evidence)
        if files is not None:
            evidence = ' '.join([files['note'], *(item['path'] for item in files['files'])])
        elif summary is not None:
            evidence = summary['note']
    except ValueError:
        # 已知格式损坏不能退回原始机器字段误命中，仍可按正文回查。
        evidence = ''
    return ' '.join((row['topic'], row['content'], evidence)).casefold()


def rank(rows: list[dict], query: str) -> list[dict]:
    """优先完整匹配；较长中文至少重合两个不同双字片段。"""
    identifier = query.strip().casefold()
    if re.fullmatch('mem_[0-9a-f]{32}', identifier):
        # 显式编号只解析对应条目，不能用包含该编号的摘要冒充来源。
        return [row for row in rows if row.get('id') == identifier]
    # 独立路径查询保持整体身份，不能靠公共目录或扩展名冒充命中。
    if _PATH.fullmatch(identifier):
        pattern = re.compile(r'(?<![\w./\\-])' + re.escape(identifier) + r'(?![\w./\\-])')
        return [row for row in rows if pattern.search(searchable_text(row))]
    tokens = list(dict.fromkeys(_TOKENS.findall(query.casefold())))
    if not tokens:
        return rows if not query.strip() else []
    patterns = [(token, {token[index:index+2] for index in range(len(token)-1)}
                 if _HAN.fullmatch(token) and len(token) > 2 else set()) for token in tokens]
    def score(row):
        text = searchable_text(row)
        words = set(_TOKENS.findall(text))
        value = 0
        for token, pairs in patterns:
            matched = token in text if _HAN.fullmatch(token) else token in words
            if matched:
                value += 8 + min(len(token), 20)
            elif pairs:
                hits = sum(pair in text for pair in pairs)
                if hits >= min(2, len(pairs)):
                    value += hits / len(pairs)
        return value
    scored = [(score(row),row) for row in rows]
    # 稳定排序保留同等相关度下的固定／更新时间优先序。
    return [row for value,row in sorted(scored,key=lambda item:item[0],reverse=True) if value]

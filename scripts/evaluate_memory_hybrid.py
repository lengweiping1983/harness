"""用固定向量报告评估混合策略，不下载模型或启用产品检索。"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.app.memory_search import rank


def evaluate(data: dict, semantic: dict, raw_hash: str, *, threshold: float = 0.3) -> dict:
    """先冻结阈值和符号门控，再报告全量案例，不按留出答案调整规则。"""
    if semantic['fixture_sha256'] != raw_hash:
        raise ValueError('向量报告和语料哈希不匹配')
    if not math.isfinite(threshold) or not -1 <= threshold <= 1:
        raise ValueError('阈值无效')
    rows, cases = data['rows'], data['cases']
    if type(semantic['top_k']) is not int or not 1 <= semantic['top_k'] <= 100:
        raise ValueError('向量报告的top_k无效')
    ids = {row['id'] for row in rows}
    if len(ids) != len(rows) or not cases or len(semantic['results']) != len(cases):
        raise ValueError('记录身份或案例数量不一致')
    recalls, reciprocal, negatives, results = [], [], [], []
    for case, evidence in zip(cases, semantic['results']):
        if case['name'] != evidence['name'] or set(case['expected']) != set(evidence['expected']):
            raise ValueError('向量报告的案例顺序或期望身份不一致')
        expected = set(case['expected'])
        if not expected <= ids:
            raise ValueError('期望身份不在语料中')
        dense_ids = set()
        if len(evidence['retrieved']) > semantic['top_k']:
            raise ValueError('向量报告候选超过声明上限')
        for item in evidence['retrieved']:
            if (item['id'] not in ids or item['id'] in dense_ids or type(item['score']) not in (int, float)
                    or not math.isfinite(item['score']) or not -1.000001 <= item['score'] <= 1.000001):
                raise ValueError('向量候选身份或分数无效')
            dense_ids.add(item['id'])
        query = case['query'].strip()
        lexical = [row['id'] for row in rank(rows, query)[:100]]
        # 单个ASCII符号或独立路径只使用原有精确检索，保留不存在身份的空结果。
        exact = bool(re.fullmatch(r'[\x00-\x7f]+', query) and not re.search(r'\s', query))
        dense = [] if exact else [item['id'] for item in evidence['retrieved'] if item['score'] >= threshold]
        scores = {}
        for candidates in (lexical, dense):
            for position, identifier in enumerate(candidates, start=1):
                scores[identifier] = scores.get(identifier, 0) + 1 / (60 + position)
        selected = sorted((row['id'] for row in rows if row['id'] in scores), key=scores.__getitem__, reverse=True)[:semantic['top_k']]
        if expected:
            recalls.append(len(expected.intersection(selected)) / len(expected))
            reciprocal.append(next((1 / (index + 1) for index, identifier in enumerate(selected) if identifier in expected), 0))
        else:
            negatives.append(bool(selected))
        results.append({'name': case['name'], 'expected': case['expected'], 'retrieved': selected,
                        'exact_gate': exact, 'lexical': lexical, 'semantic': dense})
    return {'policy': 'ascii-single-query-exact-otherwise-rrf60', 'threshold': threshold,
            'fixture_sha256': raw_hash, 'model': semantic['model'], 'top_k': semantic['top_k'],
            'recall_at_k': statistics.mean(recalls) if recalls else None,
            'mrr_at_k': statistics.mean(reciprocal) if reciprocal else None,
            'no_answer_false_positive_rate': statistics.mean(negatives) if negatives else None,
            'results': results, 'limitation': '探索策略，未启用产品；留出结果不得反向调整本次规则后冒充独立验收'}


def main() -> int:
    """输入必须来自同一语料和已完成的向量评估，不能凭模型名称拼接报告。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--semantic-report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--threshold', type=float, default=0.3)
    args = parser.parse_args()
    raw, report = args.input.read_bytes(), args.semantic_report.read_bytes()
    result = evaluate(json.loads(raw), json.loads(report), hashlib.sha256(raw).hexdigest(), threshold=args.threshold)
    result['semantic_report_sha256'] = hashlib.sha256(report).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('policy', 'threshold', 'recall_at_k', 'mrr_at_k', 'no_answer_false_positive_rate')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

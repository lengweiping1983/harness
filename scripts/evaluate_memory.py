"""在固定语料上测量本地记忆召回，不调用模型或读取用户记忆库。"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.app.memory_search import rank


def evaluate(data: dict, *, top_k: int, repeat: int) -> dict:
    """正例和无答案查询分别计量，避免用空期望人为抬高召回率。"""
    rows, cases = data['rows'], data['cases']
    ids = {row['id'] for row in rows}
    if len(ids) != len(rows) or not cases:
        raise ValueError('语料必须有唯一编号及非空案例')
    results, elapsed = [], []
    recalls, reciprocal, negatives = [], [], []
    for case in cases:
        expected = set(case['expected'])
        if not expected <= ids:
            raise ValueError('期望编号必须存在于语料')
        selected = []
        for _ in range(repeat):
            started = time.perf_counter_ns()
            selected = [row['id'] for row in rank(rows, case['query'])[:top_k]]
            elapsed.append((time.perf_counter_ns() - started) / 1_000_000)
        if expected:
            recalls.append(len(expected.intersection(selected)) / len(expected))
            reciprocal.append(next((1 / (index + 1) for index, value in enumerate(selected)
                                    if value in expected), 0))
        else:
            negatives.append(bool(selected))
        results.append({'name': case['name'], 'query': case['query'],
                        'expected': sorted(expected), 'retrieved': selected})
    return {'rows': len(rows), 'cases': len(cases), 'top_k': top_k, 'repeat': repeat,
            'recall_at_k': statistics.mean(recalls) if recalls else None,
            'mrr_at_k': statistics.mean(reciprocal) if reciprocal else None,
            'no_answer_false_positive_rate': statistics.mean(negatives) if negatives else None,
            'latency_ms_median': statistics.median(elapsed),
            'latency_ms_p95': sorted(elapsed)[min(len(elapsed) - 1, int(len(elapsed) * .95))],
            'results': results}


def main() -> int:
    """输出可复核指标；此探索性评估不冒充生产验收阈值。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'tests/fixtures/memory_retrieval.json')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--top-k', type=int, default=5)
    parser.add_argument('--repeat', type=int, default=50)
    args = parser.parse_args()
    if not 1 <= args.top_k <= 100 or not 1 <= args.repeat <= 1000:
        parser.error('top-k 必须为 1–100，repeat 必须为 1–1000')
    raw = args.input.read_bytes()
    result = evaluate(json.loads(raw), top_k=args.top_k, repeat=args.repeat)
    result.update(fixture_sha256=hashlib.sha256(raw).hexdigest(), python=platform.python_version(),
                  algorithm='harness.app.memory_search.rank')
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

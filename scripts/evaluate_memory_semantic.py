"""隔离评估公开固定语料的本地向量召回，不读取项目记忆或自动下载模型。"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def model_artifacts(cache: Path) -> list[dict]:
    """记录本地模型文件摘要，模型名称不能替代可复核的实际文件版本。"""
    root = cache.resolve()
    artifacts = []
    for path in sorted(root.rglob('*')):
        if path.suffix not in {'.onnx', '.json', '.txt', '.model'} or not path.is_file():
            continue
        if not path.resolve().is_relative_to(root):
            raise ValueError('模型缓存文件链接越出缓存目录')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        artifacts.append({'path': path.relative_to(root).as_posix(), 'bytes': path.stat().st_size,
                          'sha256': digest.hexdigest()})
    return artifacts


def main() -> int:
    """默认只用本地缓存；下载必须由命令显式选择，结果不是生产验收。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'tests/fixtures/memory_retrieval.json')
    parser.add_argument('--model', required=True)
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--allow-model-download', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--top-k', type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.top_k <= 100:
        parser.error('top-k须为1至100')
    try:
        import numpy as np
        from fastembed import TextEmbedding
    except ImportError:
        parser.error('请在隔离实验环境安装fastembed；不需要修改Harness基础依赖')
    raw = args.input.read_bytes()
    data = json.loads(raw)
    rows, cases = data['rows'], data['cases']
    ids = [row['id'] for row in rows]
    if not rows or not cases or len(set(ids)) != len(ids):
        parser.error('语料须有唯一编号、非空记录和非空案例')
    if any(not set(case['expected']) <= set(ids) for case in cases):
        parser.error('期望身份必须存在于固定语料')
    started = time.perf_counter()
    model = TextEmbedding(model_name=args.model, cache_dir=str(args.cache_dir.resolve()),
                          local_files_only=not args.allow_model_download, threads=2)
    load_seconds = time.perf_counter() - started
    # 仅支持评估普通文本证据，防止机器格式噪声与产品检索投影产生不同口径。
    if any(row['evidence'].startswith('harness:') for row in rows):
        parser.error('当前实验脚本只接受普通文本证据语料')
    started = time.perf_counter()
    documents = list(model.passage_embed([' '.join(row[key] for key in ('topic', 'content', 'evidence')) for row in rows]))
    vectors = np.asarray(documents, dtype=np.float64)
    if vectors.ndim != 2 or not np.isfinite(vectors).all() or np.any(np.linalg.norm(vectors, axis=1) == 0):
        raise ValueError('文档向量维度或数值无效')
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    index_seconds = time.perf_counter() - started
    results, timings = [], []
    for case in cases:
        started = time.perf_counter()
        query = np.asarray(next(iter(model.query_embed(case['query']))), dtype=np.float64)
        if query.shape != (vectors.shape[1],) or not np.isfinite(query).all() or np.linalg.norm(query) == 0:
            raise ValueError('查询向量维度或数值无效')
        scores = vectors @ (query / np.linalg.norm(query))
        order = np.argsort(-scores, kind='stable')[:args.top_k]
        timings.append((time.perf_counter() - started) * 1000)
        results.append({'name': case['name'], 'expected': case['expected'],
                        'retrieved': [{'id': ids[int(index)], 'score': float(scores[index])} for index in order]})
    curves = []
    for threshold in (None, 0.3, 0.5, 0.7, 0.85):
        recalls, reciprocal, negatives = [], [], []
        for result in results:
            selected = [item['id'] for item in result['retrieved'] if threshold is None or item['score'] >= threshold]
            expected = set(result['expected'])
            if expected:
                recalls.append(len(expected.intersection(selected)) / len(expected))
                reciprocal.append(next((1 / (index + 1) for index, identifier in enumerate(selected) if identifier in expected), 0))
            else:
                negatives.append(bool(selected))
        curves.append({'threshold': threshold, 'recall_at_k': statistics.mean(recalls) if recalls else None,
                       'mrr_at_k': statistics.mean(reciprocal) if reciprocal else None,
                       'no_answer_false_positive_rate': statistics.mean(negatives) if negatives else None})
    result = {'model': args.model, 'fastembed': importlib.metadata.version('fastembed'),
              'onnxruntime': importlib.metadata.version('onnxruntime'), 'numpy': np.__version__,
              'python': platform.python_version(), 'fixture_sha256': hashlib.sha256(raw).hexdigest(),
              'rows': len(rows), 'cases': len(cases), 'top_k': args.top_k, 'thresholds': curves,
              'load_seconds': load_seconds, 'index_seconds': index_seconds,
              'query_ms_median': statistics.median(timings), 'results': results,
              'model_artifacts': model_artifacts(args.cache_dir),
              'limitation': '固定小语料探索；阈值未独立校准，不代表生产质量、长期安全性或竞品比较'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('model', 'rows', 'cases', 'thresholds')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

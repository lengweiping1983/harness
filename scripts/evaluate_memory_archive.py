"""固定合成归档的流式往返实测；仅使用本次临时目录，不读取用户记忆。"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import platform
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.app.memory import ProjectMemory
from harness.app.memory_archive import MemoryArchive
from archive_memory_metrics import process_memory


def measure(operation):
    """耗时包含Python分配追踪开销，不将它当作无探针生产耗时。"""
    gc.collect()
    native_before = process_memory()
    tracemalloc.start()
    try:
        started = time.perf_counter()
        result = operation()
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        return result, {'seconds_with_tracemalloc': elapsed, 'python_peak_bytes': peak,
                        'process_before': native_before, 'process_after': process_memory()}
    finally:
        tracemalloc.stop()


def file_hash(path):
    """分块核对整个产物，不为比较而载入完整备份。"""
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate(count, content_chars):
    """数据库直接填充仅用于合成fixture，业务操作均调用实际SDK。"""
    def timed(name, operation):
        print(json.dumps({'records': count, 'stage': name, 'status': 'running'}), file=sys.stderr, flush=True)
        result, stats = measure(operation)
        print(json.dumps({'records': count, 'stage': name, 'status': 'completed', **stats}), file=sys.stderr, flush=True)
        return result, stats

    with tempfile.TemporaryDirectory(prefix='harness-archive-eval-') as directory:
        root = Path(directory)
        source, target = root / 'source', root / 'target'
        source.mkdir()
        target.mkdir()
        archive = MemoryArchive(ProjectMemory())
        fixture_hash = hashlib.sha256()
        with closing(archive._connect(archive.path(source), write=True)) as db, db:
            for index in range(count):
                identifier = 'mem_' + hashlib.sha256(str(index // 3).encode()).hexdigest()[:32]
                prefix = f'规则{index:08d}🙂\n'
                row = {'id': identifier, 'revision': index % 3 + 1,
                       'content': prefix + '固定合成记忆内容' * ((content_chars - len(prefix)) // 8)
                                  + '文' * ((content_chars - len(prefix)) % 8),
                       'topic': 'benchmark', 'pinned': False, 'source': 'synthetic-eval',
                       'evidence': '固定输入，不来自用户项目', 'created_at': 1700000000000,
                       'updated_at': 1700000000000 + index}
                raw = json.dumps(row, ensure_ascii=False, sort_keys=True)
                fixture_hash.update((raw + '\n').encode())
                db.execute('INSERT INTO archives(seq,id,revision,record,archived_at) VALUES(?,?,?,?,?)',
                           (index + 1, identifier, row['revision'], raw, 1700000000000 + index))
        backup, copied = root / 'backup.jsonl', root / 'copied.jsonl'

        def export(cwd, path):
            with path.open('x', encoding='utf-8', newline='') as stream:
                return archive.export_jsonl(cwd, stream)

        def read(operation):
            with backup.open(encoding='utf-8', newline='') as stream:
                return operation(stream)

        exported, export_stats = timed('export', lambda: export(source, backup))
        verified, verify_stats = timed('verify', lambda: read(archive.verify_jsonl))
        imported, import_stats = timed('import', lambda: read(lambda stream: archive.import_jsonl(target, stream)))
        replayed, replay_stats = timed('replay_import', lambda: read(lambda stream: archive.import_jsonl(target, stream)))
        reexported, reexport_stats = timed('reexport', lambda: export(target, copied))
        if (exported != verified or exported != reexported or exported['count'] != count
                or imported['imported'] != count or imported['replayed'] != 0
                or replayed['imported'] != 0 or replayed['replayed'] != count
                or file_hash(backup) != file_hash(copied) or archive.memory.path(target).exists()):
            raise AssertionError('归档往返或活动存储隔离校验失败')
        return {'records': count, 'content_chars': content_chars, 'fixture_sha256': fixture_hash.hexdigest(),
                'backup_bytes': backup.stat().st_size, 'backup_sha256': file_hash(backup),
                'export': export_stats, 'verify': verify_stats, 'import': import_stats,
                'replay_import': replay_stats, 'reexport': reexport_stats, 'roundtrip_exact': True,
                'active_database_created': False}


def main():
    """保留原始样本，不据单次合成测量声称生产性能或RSS上界。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', type=int, nargs='+', default=[100, 10000])
    parser.add_argument('--content-chars', type=int, default=1024)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if any(not 1 <= size <= 100000 for size in args.sizes) or not 32 <= args.content_chars <= 4000:
        parser.error('sizes必须为1–100000，content-chars必须为32–4000')
    if args.output and (args.output.exists() or args.output.is_symlink() or not args.output.parent.is_dir()):
        parser.error('输出文件必须不存在，且父目录必须已存在')
    result = {'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
              'platform': platform.platform(), 'memory_metric': 'tracemalloc-python-allocations-only',
              'limitations': ['single instrumented sample per size', 'tracemalloc excludes SQLite/native memory',
                             'no fsync or power-loss measurement', 'synthetic fixtures, no production claim',
                             'process snapshots include interpreter and native allocations; no per-operation RSS peak',
                             'lifetime peaks include fixture creation and all earlier operations/sizes'],
              'source_sha256': {name: file_hash(ROOT / name) for name in
                               ('scripts/evaluate_memory_archive.py', 'scripts/archive_memory_metrics.py',
                                'harness/app/memory_archive.py')},
              'results': [evaluate(size, args.content_chars) for size in args.sizes]}
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        with args.output.open('x', encoding='utf-8', newline='') as stream:
            stream.write(text)
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

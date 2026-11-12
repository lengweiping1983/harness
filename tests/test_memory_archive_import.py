"""验证归档备份显式导入的整批事务、作用域和恢复边界。"""
import hashlib
import io
import json
import tempfile
import unittest
import sqlite3
import subprocess
import sys
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harness.app.memory import ProjectMemory
from harness.app.memory_archive import MemoryArchive
from harness.app.layered_memory import LayeredMemory
from harness.core.errors import HarnessError


class ArchiveImportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / 'source'
        self.target = self.root / 'target'
        self.source.mkdir()
        self.target.mkdir()
        self.memory = ProjectMemory()
        self.archive = MemoryArchive(self.memory)

    def backup(self, root):
        stream = io.StringIO()
        self.archive.export_jsonl(root, stream)
        return stream.getvalue()

    def save_archive(self, content, root=None):
        root = root or self.source
        row = self.memory.save(root, content)
        self.archive.archive(root, [{'id': row['id'], 'revision': row['revision']}])
        return row

    @staticmethod
    def rewrite(text, mutate):
        frames = [json.loads(line) for line in text.splitlines()]
        mutate(frames)
        body = ''.join(json.dumps(frame, ensure_ascii=False) + '\n' for frame in frames[:-1])
        return body + json.dumps({'type': 'footer', 'count': len(frames) - 2,
                                 'sha256': hashlib.sha256(body.encode()).hexdigest()}) + '\n'

    def test_roundtrip_preserves_versions_replays_and_restores_explicitly(self):
        row = self.save_archive('中文🙂完整历史')
        restored = self.archive.restore(self.source, row['id'], 1)['memory']
        self.archive.archive(self.source, [{'id': restored['id'], 'revision': 2}])
        text = self.backup(self.source)
        result = self.archive.import_jsonl(self.target, io.StringIO(text))
        self.assertEqual((result['imported'], result['replayed']), (2, 0))
        self.assertFalse(self.memory.path(self.target).exists())
        self.assertEqual([item['memory'] for item in self.archive.list(self.target)['archives']], [restored, row])
        replay = self.archive.import_jsonl(self.target, io.StringIO(text))
        self.assertEqual((replay['imported'], replay['replayed']), (0, 2))
        self.assertEqual(self.archive.restore(self.target, row['id'], 2)['memory']['revision'], 3)

    def test_invalid_tail_duplicates_and_integer_overflow_create_no_target(self):
        self.save_archive('原文')
        text = self.backup(self.source)
        duplicate = self.rewrite(text, lambda frames: frames.insert(-1, {**frames[1], 'seq': 2}))
        overflow = self.rewrite(text, lambda frames: frames[1].update(archived_at=2**64))
        for invalid in (text[:-20], text.replace('原文', '篡改'), duplicate, overflow):
            with self.assertRaises(HarnessError):
                self.archive.import_jsonl(self.target, io.StringIO(invalid))
            self.assertFalse(self.archive.path(self.target).exists())
        repeated_key = text.replace('"type":"header"', '"type":"header","type":"header"')
        with self.assertRaises(HarnessError):
            self.archive.import_jsonl(self.target, io.StringIO(repeated_key))
        self.assertFalse(self.archive.path(self.target).exists())

    def test_concurrent_identical_imports_are_serialized_and_wal_is_preserved(self):
        row = self.save_archive('并发导入')
        text = self.backup(self.source)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.archive.import_jsonl, self.target, io.StringIO(text)) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(item['imported'] for item in results), [0, 1])
        self.assertEqual([item['memory'] for item in self.archive.list(self.target)['archives']], [row])
        with closing(sqlite3.connect(self.archive.path(self.target))) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
            with self.assertRaises(HarnessError) as error:
                self.archive.import_jsonl(self.target, io.StringIO(text))
            self.assertEqual(error.exception.code, 'memory/archive-journal')
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertEqual(len(self.archive.list(self.target)['archives']), 1)

    def test_late_conflict_rolls_back_earlier_insert_and_keeps_active_data(self):
        first = self.save_archive('较早新记录')
        second = self.save_archive('稍后冲突记录')
        text = self.backup(self.source)
        only_second = self.rewrite(text, lambda frames: frames.pop(1))
        self.archive.import_jsonl(self.target, io.StringIO(only_second))
        active = self.memory.save(self.target, '活动记忆不受影响')
        changed = self.rewrite(text, lambda frames: frames[2]['memory'].update(content='同版本不同原文'))
        with self.assertRaises(HarnessError) as error:
            self.archive.import_jsonl(self.target, io.StringIO(changed))
        self.assertEqual(error.exception.code, 'memory/changed')
        self.assertEqual([item['memory'] for item in self.archive.list(self.target)['archives']], [second])
        self.assertEqual(self.memory.list(self.target), [active])
        self.assertNotIn(first['id'], [item['memory']['id'] for item in self.archive.list(self.target)['archives']])

    def test_scope_mismatch_and_empty_import_do_not_create_stores(self):
        self.save_archive('项目资料')
        text = self.backup(self.source)
        layers = LayeredMemory(self.root / 'home', self.memory)
        for scope in ('user', 'session'):
            archive = MemoryArchive(layers.store(scope, session_id='one'))
            with self.assertRaises(HarnessError):
                archive.import_jsonl(self.target, io.StringIO(text))
            self.assertFalse(archive.path(self.target).exists())
        result = self.archive.import_jsonl(self.target, io.StringIO(self.backup(self.target)))
        self.assertEqual(result['imported'], 0)
        self.assertFalse(self.archive.path(self.target).exists())

    def test_process_exit_around_import_commit_is_recoverable_without_duplicate_history(self):
        row = self.save_archive('进程中断归档导入')
        backup = self.root / 'recovery.jsonl'
        backup.write_text(self.backup(self.source), encoding='utf-8', newline='')
        script = '''
import os, sys
from pathlib import Path
from harness.app.memory import ProjectMemory
from harness.app.memory_archive import MemoryArchive
archive = MemoryArchive(ProjectMemory())
target = Path(sys.argv[1])
original = archive._connect
class Interrupted:
    def __init__(self, connection): self.connection = connection
    def __getattr__(self, key): return getattr(self.connection, key)
    def __enter__(self): return self
    def __exit__(self, *args):
        if sys.argv[3] == 'before': os._exit(70)
        self.connection.__exit__(*args)
        os._exit(71)
def connect(path, **kwargs):
    connection = original(path, **kwargs)
    return Interrupted(connection) if path == archive.path(target) else connection
archive._connect = connect
with open(sys.argv[2], encoding='utf-8', newline='') as stream:
    archive.import_jsonl(target, stream)
'''
        for stage, code in [('before', 70), ('after', 71)]:
            target = self.root / stage
            target.mkdir()
            process = subprocess.run([sys.executable, '-c', script, str(target), str(backup), stage],
                cwd=Path(__file__).resolve().parent.parent, capture_output=True, timeout=30)
            self.assertEqual(process.returncode, code, process.stderr.decode(errors='replace'))
            self.assertEqual(len(self.archive.list(target)['archives']), 0 if stage == 'before' else 1)
            with backup.open(encoding='utf-8', newline='') as stream:
                receipt = self.archive.import_jsonl(target, stream)
            self.assertEqual(receipt['imported'], 1 if stage == 'before' else 0)
            self.assertEqual([item['memory'] for item in self.archive.list(target)['archives']], [row])

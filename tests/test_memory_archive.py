"""验证显式跨库归档、原文保留、恢复版本和失败整批回滚。"""
import sqlite3
import tempfile
import unittest
import subprocess
import sys
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from contextlib import closing
from threading import Event

from harness.app.memory import ProjectMemory, SCHEMA as ACTIVE_SCHEMA
from harness.app.memory_archive import MemoryArchive
from harness.core.errors import HarnessError


class MemoryArchiveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.memory = ProjectMemory()
        self.archive = MemoryArchive(self.memory)

    def save(self, content='原文🙂', **kwargs):
        return self.memory.save(self.root, content, evidence='原始依据', **kwargs)

    @staticmethod
    def reference(row):
        return {'id': row['id'], 'revision': row['revision']}

    def test_move_preserves_record_and_restores_as_next_revision_without_replay(self):
        row = self.save(pinned=True)
        result = self.archive.archive(self.root, [self.reference(row)])
        self.assertFalse(result['archived'][0]['replayed'])
        self.assertEqual(self.memory.list(self.root), [])
        saved = self.archive.list(self.root)['archives'][0]['memory']
        self.assertEqual(saved, row)
        self.assertTrue(self.archive.archive(self.root, [self.reference(row)])['archived'][0]['replayed'])
        restored = self.archive.restore(self.root, row['id'], 1)
        self.assertEqual(restored['memory']['revision'], 2)
        self.assertEqual(restored['memory']['content'], row['content'])
        self.assertEqual(restored['memory']['evidence'], row['evidence'])
        self.assertTrue(self.archive.restore(self.root, row['id'], 1)['replayed'])
        with self.assertRaises(HarnessError):
            self.archive.archive(self.root, [self.reference(row)])
        self.assertEqual(self.memory.list(self.root)[0]['revision'], 2)
        self.assertEqual(self.archive.list(self.root)['archives'][0]['memory'], row)

    def test_search_retains_versions_and_empty_page_continuation(self):
        row = self.save('历史部署规则')
        self.archive.archive(self.root, [self.reference(row)])
        newer = self.archive.restore(self.root, row['id'], 1)['memory']
        self.archive.archive(self.root, [self.reference(newer)])
        versions = self.archive.search(self.root, '部署规则')
        self.assertEqual([item['memory']['revision'] for item in versions['archives']], [2, 1])
        for index in range(0, 102, 6):
            batch = [self.save(f'其他资料{number}') for number in range(index, index + 6)]
            self.archive.archive(self.root, [self.reference(item) for item in batch])
        first = self.archive.search(self.root, '部署规则')
        self.assertEqual(first['scanned'], 100)
        self.assertEqual(first['archives'], [])
        self.assertIsNotNone(first['next_before'])
        last = self.archive.search(self.root, '部署规则', before=first['next_before'])
        self.assertEqual(last['scanned'], 4)
        self.assertEqual([item['memory']['revision'] for item in last['archives']], [2, 1])
        self.assertIsNone(last['next_before'])
        self.assertEqual(last['ranking_scope'], 'current-page')

    def test_search_exact_identity_and_invalid_queries_preserve_empty_storage(self):
        for query in ('', '  ', None, 8, 'x' * 501):
            with self.assertRaises(HarnessError):
                self.archive.search(self.root, query)
        self.assertEqual(self.archive.search(self.root, '规则')['scanned'], 0)
        self.assertFalse(self.archive.path(self.root).exists())
        row = self.save('API 约定')
        other = self.save('rapid 引用 ' + row['id'])
        self.archive.archive(self.root, [self.reference(row), self.reference(other)])
        for query in (row['id'], 'API'):
            result = self.archive.search(self.root, query)
            self.assertEqual([item['memory'] for item in result['archives']], [row])

    def test_maximum_control_char_fields_remain_readable_after_archiving(self):
        row = self.memory.save(self.root, '\x00' * 4000, topic='\x02' * 100,
                               evidence='\x01' * 1000, source='\x03' * 200)
        self.archive.archive(self.root, [self.reference(row)])
        self.assertEqual(self.archive.list(self.root)['archives'][0]['memory'], row)
        stream = io.StringIO()
        receipt = self.archive.export_jsonl(self.root, stream)
        self.assertEqual(self.archive.verify_jsonl(io.StringIO(stream.getvalue())), receipt)
        destination = self.root / 'control-import'
        destination.mkdir()
        imported = self.archive.import_jsonl(destination, io.StringIO(stream.getvalue()))
        self.assertEqual(imported['imported'], 1)
        self.assertEqual(self.archive.list(destination)['archives'][0]['memory'], row)
        self.assertEqual(self.archive.restore(self.root, row['id'], 1)['memory']['content'], row['content'])

    def test_export_is_complete_scoped_and_preserves_original_versions(self):
        row = self.save('原始中文🙂\n第二行')
        self.archive.archive(self.root, [self.reference(row)])
        newer = self.archive.restore(self.root, row['id'], 1)['memory']
        self.archive.archive(self.root, [self.reference(newer)])
        before = self.archive.path(self.root).read_bytes()
        stream = io.StringIO()
        result = self.archive.export_jsonl(self.root, stream)
        self.assertEqual(result['count'], 2)
        self.assertEqual(self.archive.verify_jsonl(io.StringIO(stream.getvalue())), result)
        destination = self.root / 'explicit-backup.jsonl'
        with destination.open('x', encoding='utf-8', newline='') as output:
            receipt = self.archive.export_jsonl(self.root, output)
        with destination.open(encoding='utf-8', newline='') as source:
            self.assertEqual(self.archive.verify_jsonl(source), receipt)
        saved = destination.read_bytes()
        with self.assertRaises(FileExistsError):
            with destination.open('x', encoding='utf-8', newline='') as output:
                self.archive.export_jsonl(self.root, output)
        self.assertEqual(destination.read_bytes(), saved)
        frames = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([frame['memory'] for frame in frames[1:-1]], [row, newer])
        self.assertEqual(self.archive.path(self.root).read_bytes(), before)
        for damaged in (stream.getvalue().rsplit('\n', 2)[0] + '\n',
                        stream.getvalue().replace('原始中文', '改写中文'),
                        stream.getvalue().replace(self.memory.format, 'wrong-scope'),
                        stream.getvalue() + '{}\n'):
            with self.assertRaises(HarnessError):
                self.archive.verify_jsonl(io.StringIO(damaged))

    def test_export_snapshot_excludes_append_started_during_streaming(self):
        first, second = self.save('第一条'), self.save('第二条')
        self.archive.archive(self.root, [self.reference(first), self.reference(second)])
        started = Event()
        def append():
            started.set()
            row = self.save('导出期间新增')
            self.archive.archive(self.root, [self.reference(row)])
        with ThreadPoolExecutor(max_workers=1) as executor:
            class ConcurrentStream(io.StringIO):
                future = None
                def write(stream, value):
                    if stream.future is None and json.loads(value)['type'] == 'archive':
                        stream.future = executor.submit(append)
                        self.assertTrue(started.wait(5))
                    return super().write(value)
            stream = ConcurrentStream()
            result = self.archive.export_jsonl(self.root, stream)
            stream.future.result(timeout=15)
        self.assertEqual(result['count'], 2)
        self.assertEqual(self.archive.verify_jsonl(io.StringIO(stream.getvalue())), result)
        self.assertEqual(len(self.archive.list(self.root)['archives']), 3)

    def test_empty_export_creates_no_database_and_stream_errors_are_not_swallowed(self):
        stream = io.StringIO()
        result = self.archive.export_jsonl(self.root, stream)
        self.assertEqual(result['count'], 0)
        self.assertEqual(self.archive.verify_jsonl(io.StringIO(stream.getvalue())), result)
        self.assertFalse(self.archive.path(self.root).exists())
        class BrokenStream:
            def write(self, value):
                raise OSError('disk full')
        with self.assertRaisesRegex(OSError, 'disk full'):
            self.archive.export_jsonl(self.root, BrokenStream())
        class ShortStream:
            def write(self, value):
                return len(value) - 1
        with self.assertRaises(OSError):
            self.archive.export_jsonl(self.root, ShortStream())
        with self.assertRaises(HarnessError):
            self.archive.verify_jsonl(io.StringIO('x' * 65536 + '\n'))

    def test_capacity_is_freed_and_full_restore_preserves_archive(self):
        row = self.save()
        self.archive.archive(self.root, [self.reference(row)])
        with patch('harness.app.memory.MAX_MEMORIES', 1):
            other = self.save('新记录')
            with self.assertRaises(HarnessError) as error:
                self.archive.restore(self.root, row['id'], 1)
            self.assertEqual(error.exception.code, 'memory/full')
        self.assertEqual(self.memory.list(self.root), [other])
        self.assertEqual(self.archive.list(self.root)['archives'][0]['memory'], row)

    def test_identity_history_pages_skip_interleaved_unrelated_records(self):
        row = self.save()
        self.archive.archive(self.root, [self.reference(row)])
        newer = self.archive.restore(self.root, row['id'], 1)['memory']
        other = self.save('正文提到 ' + row['id'])
        self.archive.archive(self.root, [self.reference(other), self.reference(newer)])
        page = self.archive.list(self.root, memory_id=row['id'], limit=1)
        self.assertEqual(page['archives'][0]['memory'], newer)
        last = self.archive.list(self.root, memory_id=row['id'], limit=1, before=page['next_before'])
        self.assertEqual(last['archives'][0]['memory'], row)
        self.assertIsNone(last['next_before'])
        self.assertEqual(self.archive.list(self.root, memory_id='missing')['archives'], [])
        for invalid in ('', 8, True, 'x' * 101):
            with self.assertRaises(HarnessError):
                self.archive.list(self.root, memory_id=invalid)

    def test_batch_conflict_rolls_back_both_files(self):
        one, two = self.save(), self.save('另一条')
        with self.assertRaises(HarnessError):
            self.archive.archive(self.root, [self.reference(one), {**self.reference(two), 'revision': 8}])
        self.assertEqual({row['id'] for row in self.memory.list(self.root)}, {one['id'], two['id']})
        self.assertEqual(self.archive.list(self.root)['archives'], [])

    def test_active_summary_dependency_requires_including_summary(self):
        one, two = self.save(), self.save('另一条')
        summary = self.memory.consolidate(self.root, [self.reference(one), self.reference(two)], '整理摘要')['memory']
        with self.assertRaises(HarnessError) as error:
            self.archive.archive(self.root, [self.reference(one)])
        self.assertEqual(error.exception.code, 'memory/archive-referenced')
        self.archive.archive(self.root, [self.reference(one), self.reference(summary)])
        self.assertEqual(len(self.memory.list(self.root)), 1)
        self.assertEqual(len(self.archive.list(self.root)['archives']), 2)

    def test_older_restore_cannot_revive_after_newer_archive(self):
        row = self.save()
        self.archive.archive(self.root, [self.reference(row)])
        restored = self.archive.restore(self.root, row['id'], 1)['memory']
        self.archive.archive(self.root, [self.reference(restored)])
        with self.assertRaises(HarnessError):
            self.archive.restore(self.root, row['id'], 1)
        self.assertEqual(self.memory.list(self.root), [])
        self.assertEqual(len(self.archive.list(self.root)['archives']), 2)

    def test_duplicate_restore_does_not_overwrite_active_record(self):
        row = self.save()
        self.archive.archive(self.root, [self.reference(row)])
        other = self.save()
        with self.assertRaises(HarnessError) as error:
            self.archive.restore(self.root, row['id'], 1)
        self.assertEqual(error.exception.code, 'memory/duplicate')
        self.assertEqual(self.memory.list(self.root), [other])

    def test_wal_is_rejected_without_conversion_or_record_move(self):
        row = self.save()
        with closing(sqlite3.connect(self.memory.path(self.root))) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        with self.assertRaises(HarnessError) as error:
            self.archive.archive(self.root, [self.reference(row)])
        self.assertEqual(error.exception.code, 'memory/archive-journal')
        with closing(sqlite3.connect(self.memory.path(self.root))) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertEqual(self.memory.list(self.root), [row])
        self.assertFalse(self.archive.path(self.root).exists())

    def test_empty_read_is_side_effect_free_and_pagination_is_stable(self):
        self.assertEqual(self.archive.list(self.root), {'archives': [], 'next_before': None})
        self.assertFalse((self.root / '.harness').exists())
        rows = [self.save(str(index)) for index in range(3)]
        self.archive.archive(self.root, [self.reference(row) for row in rows])
        page = self.archive.list(self.root, limit=2)
        next_page = self.archive.list(self.root, before=page['next_before'], limit=2)
        self.assertEqual(len(page['archives']) + len(next_page['archives']), 3)
        self.assertIsNone(next_page['next_before'])

    def test_corrupt_archive_preserved_and_active_schema_unchanged(self):
        row = self.save()
        path = self.archive.path(self.root)
        path.write_bytes(b'corrupt archive')
        with self.assertRaises(sqlite3.DatabaseError):
            self.archive.archive(self.root, [self.reference(row)])
        self.assertEqual(path.read_bytes(), b'corrupt archive')
        self.assertEqual(self.memory.list(self.root), [row])
        from harness.core.sqlite import prepare_database
        with closing(sqlite3.connect(self.memory.path(self.root))) as db:
            prepare_database(db, ACTIVE_SCHEMA, initialize=False)

    def test_concurrent_identical_moves_store_one_snapshot(self):
        row = self.save()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.archive.archive, self.root, [self.reference(row)]) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(sorted(result['archived'][0]['replayed'] for result in results), [False, True])
        self.assertEqual(len(self.archive.list(self.root)['archives']), 1)
        self.assertEqual(self.memory.list(self.root), [])

    def test_attached_validation_is_readonly_and_rejects_unsafe_schema_names(self):
        from harness.core.sqlite import prepare_database
        from harness.app.memory_archive import SCHEMA
        row = self.save()
        self.archive.archive(self.root, [self.reference(row)])
        with closing(self.memory._connect(self.memory.path(self.root), write=True)) as db:
            db.execute('ATTACH DATABASE ? AS archive', (str(self.archive.path(self.root)),))
            prepare_database(db, SCHEMA, initialize=False, schema='archive')
            for name in ('archive; DROP TABLE memories', 'archive.extra', '', None):
                with self.subTest(schema=name), self.assertRaises(ValueError):
                    prepare_database(db, SCHEMA, initialize=False, schema=name)
            with self.assertRaises(ValueError):
                prepare_database(db, SCHEMA, schema='archive')
            self.assertEqual(db.execute('SELECT count(*) FROM memories').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM archive.archives').fetchone()[0], 1)

    def test_archive_wal_is_also_rejected_without_moving_active_record(self):
        row = self.save()
        self.archive.archive(self.root, [self.reference(row)])
        current = self.archive.restore(self.root, row['id'], 1)['memory']
        with closing(sqlite3.connect(self.archive.path(self.root))) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        with self.assertRaises(HarnessError) as error:
            self.archive.archive(self.root, [self.reference(current)])
        self.assertEqual(error.exception.code, 'memory/archive-journal')
        self.assertEqual(self.memory.list(self.root), [current])
        self.assertEqual(len(self.archive.list(self.root)['archives']), 1)

    def test_damaged_record_is_reported_without_repair_or_restore(self):
        row = self.save()
        self.archive.archive(self.root, [self.reference(row)])
        path = self.archive.path(self.root)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE archives SET record='invalid-json'")
        before = path.read_bytes()
        for read in (lambda: self.archive.list(self.root), lambda: self.archive.restore(self.root, row['id'], 1)):
            with self.assertRaises(HarnessError) as error:
                read()
            self.assertEqual(error.exception.code, 'memory/archive-invalid')
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.memory.list(self.root), [])

    def test_process_exit_before_commit_rolls_back_and_after_commit_is_replayable(self):
        row = self.save()
        script = '''
import os, sys
from pathlib import Path
from contextlib import contextmanager
from harness.app.memory import ProjectMemory
from harness.app.memory_archive import MemoryArchive
archive = MemoryArchive(ProjectMemory())
original = archive._transaction
@contextmanager
def interrupted(cwd):
    with original(cwd) as db:
        yield db
        if sys.argv[4] == 'before':
            os._exit(70)
    os._exit(71)
archive._transaction = interrupted
archive.archive(Path(sys.argv[1]), [{'id': sys.argv[2], 'revision': int(sys.argv[3])}])
'''
        for stage, expected in [('before', 70), ('after', 71)]:
            result = subprocess.run([sys.executable, '-c', script, str(self.root), row['id'], '1', stage],
                                    cwd=Path(__file__).resolve().parent.parent, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, expected, result.stderr.decode(errors='replace'))
            if stage == 'before':
                self.assertEqual(self.memory.list(self.root), [row])
                self.assertEqual(self.archive.list(self.root)['archives'], [])
            else:
                self.assertEqual(self.memory.list(self.root), [])
                self.assertEqual(self.archive.list(self.root)['archives'][0]['memory'], row)
                self.assertTrue(self.archive.archive(self.root, [self.reference(row)])['archived'][0]['replayed'])

"""验证锁的显式生命周期与存储初始化失败清理。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.execution.store import ExecutionStore
from harness.project.board import BoardError
from harness.project.lease import RepositoryLease


class FileLockResourceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_repository_context_releases_after_exception(self):
        with self.assertRaisesRegex(RuntimeError, 'probe'):
            with RepositoryLease(self.root) as lease:
                with self.assertRaises(BoardError):
                    RepositoryLease(self.root)
                raise RuntimeError('probe')
        self.assertTrue(lease.stream.closed)
        with RepositoryLease(self.root) as replacement:
            lease.close()
            with self.assertRaises(BoardError):
                RepositoryLease(self.root)
            self.assertFalse(replacement.stream.closed)

    def test_store_context_closes_database_and_owner(self):
        with self.assertRaisesRegex(RuntimeError, 'probe'):
            with ExecutionStore(self.root) as store:
                raise RuntimeError('probe')
        self.assertTrue(store._owner.closed)
        with self.assertRaises(sqlite3.ProgrammingError):
            store.db.execute('SELECT 1')
        with ExecutionStore(self.root) as replacement:
            store.close()
            self.assertEqual(replacement.db.execute('SELECT 1').fetchone()[0], 1)

    def test_connect_failure_closes_owner_without_garbage_collection(self):
        owner = (self.root / 'executor.owner').open('a+b')
        self.addCleanup(owner.close)
        with patch.object(Path, 'open', return_value=owner), patch(
            'harness.execution.store.sqlite3.connect', side_effect=sqlite3.OperationalError('probe')
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'probe'):
                ExecutionStore(self.root)
        self.assertTrue(owner.closed)
        with ExecutionStore(self.root):
            pass

    def test_schema_failure_closes_database_and_owner(self):
        connection = sqlite3.connect(self.root / 'executor.sqlite3')
        self.addCleanup(connection.close)
        owner = (self.root / 'executor.owner').open('a+b')
        self.addCleanup(owner.close)
        with patch.object(Path, 'open', return_value=owner), patch(
            'harness.execution.store.sqlite3.connect', return_value=connection
        ), patch('harness.execution.store.prepare_database', side_effect=ValueError('probe')):
            with self.assertRaisesRegex(ValueError, 'probe'):
                ExecutionStore(self.root)
        self.assertTrue(owner.closed)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')
        with ExecutionStore(self.root):
            pass

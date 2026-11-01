"""验证锁的显式生命周期与存储初始化失败清理。"""
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness.execution.config import ExecutionConfig, ProjectConfig
from harness.execution.runtime import ExecutionRuntime
from harness.execution.store import ExecutionStore
from harness.core.file_lock import lock_file, unlock_file
from harness.project.board import BoardError
from harness.project.lease import RepositoryLease


class FileLockResourceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_empty_lock_file_never_needs_an_unlocked_initial_write(self):
        """首个进程持锁时第二句柄必须冲突，空锁文件无需写占位字节。"""
        path = self.root / 'empty.lock'
        with path.open('a+b') as owner, path.open('a+b') as contender:
            lock_file(owner)
            try:
                with self.assertRaises(OSError):
                    lock_file(contender)
                self.assertEqual(path.stat().st_size, 0)
            finally:
                unlock_file(owner)
            lock_file(contender)
            unlock_file(contender)
        self.assertEqual(path.read_bytes(), b'')

    def test_lock_contends_across_processes_and_preserves_existing_bytes(self):
        """锁定范围不依赖流当前位置，子进程不能绕过已持有的锁。"""
        path = self.root / 'existing.lock'
        path.write_bytes(b'Original bytes')
        code = ('import pathlib,sys;from harness.core.file_lock import lock_file;'
                'stream=pathlib.Path(sys.argv[1]).open("a+b");lock_file(stream)')
        with path.open('a+b') as owner:
            owner.seek(0, 2)
            lock_file(owner)
            try:
                result = subprocess.run([sys.executable, '-c', code, str(path)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b'Error', result.stderr)
            finally:
                unlock_file(owner)
        result = subprocess.run([sys.executable, '-c', code, str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.read_bytes(), b'Original bytes')

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

    def test_runtime_construction_failure_closes_store_without_garbage_collection(self):
        """宿主装配在取得存储后失败，不能留下数据库或运行所有权锁。"""
        for failure in ('settings', 'start-event'):
            with self.subTest(failure=failure):
                home = self.root / failure
                original = ValueError('fixture construction failed')
                with ExecutionStore(home) as store:
                    target = ('harness.execution.runtime.EscStateStore' if failure == 'settings'
                              else 'harness.execution.store.ExecutionStore.event')
                    with patch('harness.execution.runtime.ExecutionStore', return_value=store), \
                         patch(target, side_effect=original):
                        with self.assertRaises(ValueError) as caught:
                            ExecutionRuntime(ExecutionConfig(home, {'fixture': ProjectConfig(self.root)}))
                    self.assertIs(caught.exception, original)
                    self.assertTrue(store._owner.closed)
                    with self.assertRaises(sqlite3.ProgrammingError):
                        store.db.execute('SELECT 1')
                    with ExecutionStore(home):
                        pass

    def test_runtime_construction_and_store_cleanup_errors_are_both_preserved(self):
        """回收失败不能覆盖装配原异常，两个错误保持原对象。"""
        original = ValueError('fixture construction failed')
        cleanup = OSError('fixture store close failed')
        store = Mock()
        store.close.side_effect = cleanup
        with patch('harness.execution.runtime.ExecutionStore', return_value=store), \
             patch('harness.execution.runtime.EscStateStore', side_effect=original):
            with self.assertRaises(BaseExceptionGroup) as caught:
                ExecutionRuntime(ExecutionConfig(self.root / 'state', {'fixture': ProjectConfig(self.root)}))
        self.assertEqual(caught.exception.exceptions, (original, cleanup))
        store.close.assert_called_once_with()

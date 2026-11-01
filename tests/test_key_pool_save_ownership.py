"""Key 池状态仅使用本次临时文件，不覆盖其他保存者或失败前的状态。"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from harness.llm.key_pool import ApiKeyPool


class KeyPoolSaveOwnershipTests(unittest.TestCase):
    def test_existing_foreign_temporary_is_preserved_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            foreign = path.with_name(path.name + '.tmp')
            foreign.write_bytes(b'other operation data')
            pool = ApiKeyPool(['fixture-one', 'fixture-two'], state_path=path)
            pool.rotate()
            self.assertFalse(pool.persistence_error)
            self.assertEqual(foreign.read_bytes(), b'other operation data')
            self.assertEqual(ApiKeyPool(['fixture-one', 'fixture-two'], state_path=path).current()[0], 1)
            self.assertEqual(set(path.parent.iterdir()), {path, foreign, path.with_name(path.name + '.lock')})

    def test_failed_replace_keeps_original_state_and_removes_only_owned_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            pool = ApiKeyPool(['fixture-one', 'fixture-two'], state_path=path)
            pool.rotate()
            original = path.read_bytes()
            foreign = path.with_name(path.name + '.tmp')
            foreign.write_bytes(b'other operation data')
            with patch.object(Path, 'replace', side_effect=OSError('fixture replace failed')):
                pool.mark_failed(1)
            self.assertTrue(pool.persistence_error)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(foreign.read_bytes(), b'other operation data')
            self.assertEqual(set(path.parent.iterdir()), {path, foreign, path.with_name(path.name + '.lock')})
            pool.rotate()
            self.assertFalse(pool.persistence_error)
            self.assertEqual(foreign.read_bytes(), b'other operation data')

    def test_failed_temporary_write_removes_owned_file_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            pool = ApiKeyPool(['fixture-one', 'fixture-two'], state_path=path)
            pool.rotate()
            original = path.read_bytes()
            original_temporary = tempfile.NamedTemporaryFile

            def broken_file(*args, **kwargs):
                handle = original_temporary(*args, **kwargs)
                def write(_):
                    raise OSError('fixture write failed')
                handle.write = write
                return handle

            with patch('harness.llm.key_pool.tempfile.NamedTemporaryFile', side_effect=broken_file):
                pool.mark_failed(1)
            self.assertTrue(pool.persistence_error)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(set(path.parent.iterdir()), {path, path.with_name(path.name + '.lock')})

    def test_independent_savers_have_distinct_complete_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            pools = [ApiKeyPool(['fixture-one', 'fixture-two'], state_path=path) for _ in range(2)]
            barrier = threading.Barrier(2)
            sources = []
            original_replace = Path.replace

            def replace(source, target):
                payload = json.loads(source.read_text())
                self.assertIn(payload['current'], (0, 1))
                sources.append(source)
                return original_replace(source, target)

            def rotate(pool):
                barrier.wait(timeout=3)
                return pool.rotate()

            with patch.object(Path, 'replace', replace), ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(rotate, pool) for pool in pools]
                self.assertEqual(sorted(future.result(timeout=5)[0] for future in futures), [0, 1])
            self.assertEqual(len(set(sources)), 2)
            self.assertTrue(all(not pool.persistence_error for pool in pools))
            self.assertEqual(set(path.parent.iterdir()), {path, path.with_name(path.name + '.lock')})
            self.assertEqual(json.loads(path.read_text())['current'], 0)

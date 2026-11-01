"""执行器搜索在文件预检后增长时仍限制实际读取。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.execution.files import search
from harness.execution.policy import PathPolicy


class ExecutorSearchBoundsTests(unittest.TestCase):
    def test_growth_after_size_check_does_not_read_or_search_an_unbounded_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / 'source.txt'
            target.write_bytes(b'needle\n')
            original_open = Path.open
            reads = []

            class Reader:
                def __init__(self, stream):
                    self.stream = stream

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.stream.close()

                def read(self, count=-1):
                    reads.append(count)
                    return self.stream.read(count)

            def open_file(path, mode='r', *args, **kwargs):
                if path == target and mode == 'rb':
                    with original_open(target, 'ab') as stream:
                        stream.write(b'needle\n' * 1000)
                    return Reader(original_open(path, mode, *args, **kwargs))
                return original_open(path, mode, *args, **kwargs)

            policy = PathPolicy(root, root / 'private')
            with patch.object(Path, 'open', open_file):
                result = search(policy, 'needle', 'source.txt', 10, 64)
            self.assertEqual(reads, [65])
            self.assertEqual(result, {'matches': [], 'truncated': False, 'scanned': 1})
            self.assertEqual(target.read_bytes(), b'needle\n' * 1001)

    def test_exact_boundary_unicode_and_binary_filter_keep_current_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            policy = PathPolicy(root, root / 'private')
            raw = '你好 needle\n'.encode('utf-8')
            (root / 'source.txt').write_bytes(raw)
            result = search(policy, '你好', '.', 10, len(raw))
            self.assertEqual(result['matches'], [{'path': 'source.txt', 'line': 1, 'text': '你好 needle'}])
            (root / 'source.txt').write_bytes(b'needle\0')
            self.assertEqual(search(policy, 'needle', '.', 10, 64)['matches'], [])

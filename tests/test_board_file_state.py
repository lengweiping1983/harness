"""任务板缺失与损坏必须可区分，读取不得隐式初始化已有坏文件。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harness.core.errors import ToolExecutionError
from harness.project.board import BoardError, TaskBoard, MAX_BOARD_BYTES
from harness.tools.builtin.project_tools import build_project_tools


class BoardFileStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_board_read_is_bounded_and_original_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            board = TaskBoard(Path(directory))
            board.path.parent.mkdir()
            raw = b' ' * (MAX_BOARD_BYTES * 2)
            board.path.write_bytes(raw)
            original_open = Path.open
            sizes = []
            class Reader:
                def __enter__(self):
                    self.stream = original_open(board.path, 'rb')
                    return self
                def read(self, size=-1):
                    sizes.append(size)
                    return self.stream.read(size)
                def __exit__(self, *args):
                    self.stream.close()
            with patch.object(Path, 'open', return_value=Reader()):
                with self.assertRaisesRegex(BoardError, 'board/too-large'):
                    board.read()
            self.assertEqual(sizes, [MAX_BOARD_BYTES + 1])
            self.assertEqual(board.path.read_bytes(), raw)

    async def test_missing_board_is_explicit_and_read_does_not_create(self):
        with tempfile.TemporaryDirectory() as directory:
            board = TaskBoard(Path(directory))
            snapshot = board.read()
            self.assertFalse(snapshot['file_exists'])
            self.assertEqual(snapshot['tasks'], [])
            self.assertFalse(board.path.exists())
            self.assertFalse(board.path.parent.exists())
            tool = next(t for t in build_project_tools(enable_git=False) if t.schema.name == 'project_board_read')
            result = await tool.execute({}, SimpleNamespace(cwd=directory))
            self.assertFalse(result['file_exists'])
            self.assertFalse(board.path.exists())

    async def test_valid_empty_board_is_existing_and_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            board = TaskBoard(Path(directory))
            board.path.parent.mkdir()
            raw = b'{"schema":2,"tasks":[]}\n'
            board.path.write_bytes(raw)
            self.assertTrue(board.read()['file_exists'])
            tool = next(t for t in build_project_tools(enable_git=False) if t.schema.name == 'project_board_read')
            result = await tool.execute({'limit': 1}, SimpleNamespace(cwd=directory))
            self.assertTrue(result['file_exists'])
            self.assertEqual(result['total'], 0)
            self.assertEqual(board.path.read_bytes(), raw)

    async def test_existing_empty_and_corrupt_files_reject_read_and_write_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            board = TaskBoard(Path(directory))
            board.path.parent.mkdir()
            tool = next(t for t in build_project_tools(enable_git=False) if t.schema.name == 'project_board_read')
            for raw in (b'', b' ', b'{', b'null', json.dumps({'schema': 1, 'tasks': []}).encode(),
                        b'{"schema":2.0,"tasks":[]}',
                        b'{"schema":2,"tasks":[],"legacy":{"original":"retain"}}',
                        b'{"schema":2,"tasks":[],"file_exists":true}',
                        b'{"schema":2,"tasks":[{"original":true}],"tasks":[]}',
                        b'{"schema":1,"schema":2,"tasks":[]}',
                        b'{"schema":2,"tasks":[{"id":"task","title":"original","title":"replacement","acceptance":["check"],"verification_policy":"git","status":"READY","depends_on":[],"allowed_paths":["notes.md"]}]}'):
                with self.subTest(raw=raw):
                    board.path.write_bytes(raw)
                    with self.assertRaisesRegex(BoardError, 'board/invalid-file'):
                        board.read()
                    with self.assertRaisesRegex(BoardError, 'board/invalid-file'):
                        board.append_plan('0' * 64, [])
                    with self.assertRaisesRegex(ToolExecutionError, 'board/invalid-file'):
                        await tool.execute({}, SimpleNamespace(cwd=directory))
                    self.assertEqual(board.path.read_bytes(), raw)

    async def test_explicit_creation_initializes_current_format_without_snapshot_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            board = TaskBoard(Path(directory))
            before = board.read()
            result = board.append_plan(before['revision'], [{'id': 'task', 'title': '核对资料',
                'allowed_paths': ['notes.md'], 'acceptance': ['来源可核对']}])
            self.assertTrue(result['file_exists'])
            self.assertEqual(set(json.loads(board.path.read_bytes())), {'schema', 'tasks'})
            self.assertEqual(result['tasks'][0]['id'], 'task')

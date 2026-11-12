"""工具结果搜索必须完整显示命中，分页保持码点、原件和搜索起点。"""
import hashlib
import tempfile
import unittest

from harness.session.events import AppendOp
from harness.session.store import SessionStore
from harness.web.errors import BadRequest
from harness.web.tool_results import read_result


class ToolResultSearchBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_persisted_results_keep_matches_inside_small_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SessionStore(directory).create()
            await session.start_turn()
            text = 'x' * 300 + '中文😀needle' + 'z' * 20 + '中文😀needle'
            event = await session.append('tool/result', {'call_id': 'fixture', 'name': 'fixture',
                'turn': 1, 'results': [{'tool_use_id': 'fixture', 'content': text, 'is_error': False}]},
                surface_op=AppendOp())
            original = event.to_dict()
            digest = hashlib.sha256(text.encode()).hexdigest()
            for query in ('needle', '中文😀needle'):
                for limit in (len(query), len(query) + 1, 10, 8192):
                    with self.subTest(query=query, limit=limit):
                        first = read_result(session, event.seq, query=query, limit=limit, expected_hash=digest)
                        self.assertIn(query, first['text'])
                        self.assertLessEqual(len(first['text']), limit)
                        self.assertEqual(first['text'], text[first['offset']:first['offset'] + len(first['text'])])
                        cursor = first['match_offset'] + len(query)
                        second = read_result(session, event.seq, query=query, limit=limit, offset=cursor)
                        self.assertGreaterEqual(second['offset'], cursor)
                        self.assertIn(query, second['text'])
                        missing = read_result(session, event.seq, query=query, limit=limit,
                            offset=second['match_offset'] + len(query))
                        self.assertIsNone(missing['match_offset'])
                        self.assertEqual(missing['text'], '')
                        self.assertIsNone(missing['next_offset'])
            self.assertEqual(event.to_dict(), original)
            with self.assertRaises(BadRequest) as failure:
                read_result(session, event.seq, query='needle', limit=5)
            self.assertEqual(failure.exception.code, 'tool-result/invalid-query')

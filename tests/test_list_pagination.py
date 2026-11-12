"""列表分页吸收不足五项尾数，保留游标和严格阈值。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web.api.workspace import _directory_page
from harness.web.execution_trace import page
from harness.web.conversation_search import search_dialogue
from types import SimpleNamespace


class ListPaginationTests(unittest.TestCase):
    def test_trace_tail_and_cursor(self):
        """每一页都吸收尾数，五项仍分页且游标不重复。"""
        for total in (10, 11, 14, 15, 16, 24):
            with self.subTest(total=total):
                rows = [dict(id=str(i), time=i, kind='tool', order=0) for i in range(total)]
                first = page(rows, limit=10)
                count = total if total < 15 else 10
                self.assertEqual(len(first['entries']), count)
                self.assertEqual(first['has_more'], total >= 15)
                if first['has_more']:
                    second = page(rows, after=first['next_after'], limit=10)
                    self.assertEqual(first['entries'] + second['entries'], rows)
                    self.assertFalse(second['has_more'])

    def test_directory_tail(self):
        """文件列表在首批和后续批次吸收尾数。"""
        with tempfile.TemporaryDirectory() as folder, patch('harness.web.api.workspace.MAX_LIST_ENTRIES', 10):
            root = Path(folder)
            for i in range(24):
                (root / f'{i:02}').touch()
            first, cursor = _directory_page(root, root, 0, '')
            self.assertEqual((len(first), cursor), (10, 10))
            second, cursor = _directory_page(root, root, cursor, '')
            self.assertEqual((len(second), cursor), (14, None))
            self.assertEqual(len({row['path'] for row in first + second}), 24)

    def test_search_tail(self):
        """搜索结果尾数与继续游标一致。"""
        for total in (14, 15, 24):
            with self.subTest(total=total):
                events = [SimpleNamespace(seq=i, time=i, type='user/message') for i in range(total)]
                session = SimpleNamespace(id="search-owner", events=events)
                with patch('harness.web.conversation_search._dialogue', side_effect=lambda session, rows: ((e, 'hit') for e in rows)):
                    first = search_dialogue(session, 'hit', limit=10)
                    self.assertEqual(first['session_id'], session.id)
                    self.assertEqual(len(first['results']), total if total < 15 else 10)
                    self.assertEqual(first['has_more'], total >= 15)
                    if first['has_more']:
                        second = search_dialogue(session, 'hit', limit=10, before=first['next_before'])
                        self.assertEqual(second['session_id'], session.id)
                        ids = [r['source_seq'] for r in first['results'] + second['results']]
                        self.assertEqual(ids, list(reversed(range(total))))
                        self.assertFalse(second['has_more'])


    def test_commit_tail(self):
        """提交列表多读五项判定尾数，后续偏移保持准确。"""
        from harness.app.comparison import ComparisonService
        from unittest.mock import Mock

        for total in (54, 55):
            service = ComparisonService.__new__(ComparisonService)
            service.root = Mock(return_value=(Path('.'), Path('.')))
            service._git = Mock(return_value=SimpleNamespace(returncode=0, stdout=b'HEAD'))
            service.git = Mock(side_effect=lambda root, *args: (
                '\n'.join(f'{i:040x}\0subject' for i in range(total)).encode() if args[0] == 'log' else b''))
            result = service.options('.', offset=50)
            self.assertEqual(len(result['commits']), total if total == 54 else 50)
            self.assertEqual(result['next_offset'], None if total == 54 else 100)
            self.assertIn('--max-count=55', service.git.call_args.args)

    def test_archives_tail_and_owner(self):
        """两类归档吸收尾数，仍按归属和游标隔离。"""
        from harness.tools.job_archive import JobArchive
        from harness.subagent.archive import SubagentArchive
        from harness.core.runs import AgentRun, COMPLETED
        import json

        with tempfile.TemporaryDirectory() as folder:
            jobs = JobArchive(Path(folder) / 'jobs.sqlite3')
            agents = SubagentArchive(Path(folder) / 'agents.sqlite3')
            for i in range(25):
                owner = 'other' if i == 24 else 'owner'
                job = dict(id=str(i), kind='test', label='test', status='completed', startedAt=i)
                with jobs.db() as db:
                    db.execute('INSERT INTO jobs VALUES(?,?,?,?,?)', (str(i), owner, i, json.dumps(job), '{}'))
                agents.save(AgentRun(id=str(i), parent_session_id=owner, status=COMPLETED, created_at=i))
            for archive, field, identity in ((jobs, 'jobs', 'id'), (agents, 'agents', 'agent_id')):
                first = archive.page('owner', limit=10)
                second = archive.page('owner', limit=10, before=first['next_before'])
                self.assertEqual((len(first[field]), first['has_more']), (10, True))
                self.assertEqual((len(second[field]), second['has_more']), (14, False))
                self.assertEqual(len({row[identity] for row in first[field] + second[field]}), 24)


class ConversationPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_tail_and_cursor(self):
        """对话正文分页吸收四项尾数，第二页仍无重复。"""
        from harness.session.events import AppendOp
        from harness.session.store import SessionStore
        from harness.web.conversation import project_page

        with tempfile.TemporaryDirectory() as folder:
            session = SessionStore(folder).create()
            for i in range(24):
                await session.append('user/message', {'text': str(i)}, surface_op=AppendOp())
            first, _, history = project_page(session, session.events, limit=10)
            self.assertEqual((len(first), history['start'], history['has_more']), (10, 14, True))
            second, _, older = project_page(session, session.events, limit=10, cursor=history['next_cursor'])
            self.assertEqual((len(second), older['start'], older['has_more']), (14, 0, False))
            ids = [row['source_seq'] for row in second + first]
            self.assertEqual(ids, [event.seq for event in session.events])

    async def test_original_message_owner(self):
        """相同正文与序号仍携带各自对话身份，不用内容推断归属。"""
        from harness.session.events import AppendOp
        from harness.session.store import SessionStore
        from harness.web.conversation_search import read_dialogue

        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(folder)
            first, second = store.create(), store.create()
            for session in (first, second):
                event = await session.append('user/message', {'text': '原文😀'}, surface_op=AppendOp())
                result = read_dialogue(session, event.seq)
                self.assertEqual(result['session_id'], session.id)
                self.assertEqual(result['text'], '原文😀')
            self.assertNotEqual(first.id, second.id)

    async def test_skill_tail(self):
        """技能历史恰好五条时继续分页，四条时并入当前批。"""
        from harness.session.store import SessionStore
        from harness.web.skill_audit import skill_audit

        with tempfile.TemporaryDirectory() as folder:
            session = SessionStore(folder).create()
            for i in range(15):
                await session.append('tool/call', {'name': 'skill', 'call_id': str(i), 'status': 'running', 'input': {'name': 'test'}})
            page = skill_audit(session.events, limit=10)
            self.assertEqual((len(page['skills']), page['has_more']), (10, True))
            page = skill_audit(session.events[:-1], limit=10)
            self.assertEqual((len(page['skills']), page['has_more']), (14, False))

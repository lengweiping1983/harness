"""任务通知只来自真实启动运行的终态，并按对话权限隔离。"""
import asyncio
import json
import tempfile
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from harness.app.runtime import RunController
from harness.core.agent import TurnResult
from harness.core.events import EventBus
from harness.core.runs import AgentRun
from harness.web.api.sessions import build_router
from harness.web.context import WebContext
from harness.web.errors import BadRequest
from harness.web.hub import ConnectionHub
from harness.web.query import read_query, SESSION_ACTIVITY_QUERY
from harness.web.router import Request


class TaskNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_persisted_execution_completion_reaches_the_activity_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            host = SimpleNamespace(config=SimpleNamespace(home=Path(directory), cwd=directory),
                jobs=SimpleNamespace(assert_ready=Mock()), bus=EventBus(), subagents=SimpleNamespace(shutdown=AsyncMock()))
            controller = RunController(host)
            controller.queue_control = Mock(return_value={})
            ctx = WebContext(host, asyncio.get_running_loop())
            ctx.start_event_bridge()
            session = SimpleNamespace(id='session', open_turn=None, persistence_error=None,
                                      header=SimpleNamespace(cwd=directory))
            try:
                operation = AsyncMock(return_value=TurnResult(session.id, 1, 'completed', text='已核对'))
                await controller.execute(session, operation, record=AgentRun(id='actual-run', session_id=session.id))
                result = ctx.hub.activity_completions(ctx.hub.server_id, 0)
                self.assertEqual(result['completions'], [{'seq': 1, 'session_id': session.id,
                                                         'run_id': 'actual-run', 'status': 'completed'}])
                operation.assert_awaited_once()
            finally:
                ctx.stop_event_bridge()

    async def test_bridge_and_activity_route_retain_short_runs_and_filter_access(self):
        bus = EventBus()
        done = asyncio.get_running_loop().create_future()
        done.set_result(None)
        active = asyncio.get_running_loop().create_future()
        app = SimpleNamespace(bus=bus, runtime=SimpleNamespace(active={'allowed': active, 'private': active, 'finished': done}),
                              sessions=SimpleNamespace(catalog_revision=7))
        ctx = WebContext(app, asyncio.get_running_loop(), shared_access=SimpleNamespace(catalog_revision=lambda _: 3))
        ctx.start_event_bridge()
        router = build_router(ctx)
        handler = router.resolve('GET', '/api/v1/sessions/activity')[0]
        async def read(query=None, visible=None):
            response = await asyncio.to_thread(handler, Request('GET', '/api/v1/sessions/activity', query or {}, {}, b'', visible_session_ids=visible))
            return json.loads(response.body)
        try:
            baseline = await read()
            shared_baseline = await read(visible=frozenset({'allowed'}))
            self.assertEqual(baseline['completions'], [])
            for sid, status in [('allowed', 'completed'), ('allowed', 'failed'), ('private', 'interrupted')]:
                row = {'id': sid + status, 'session_id': sid, 'status': status, 'started_at': 1}
                await bus.emit('run/state', row)
                await bus.emit('run/state', row)
            for row in ({'id': 'queued', 'session_id': 'allowed', 'status': 'interrupted', 'started_at': None},
                        {'id': 'compact', 'session_id': 'allowed', 'status': 'completed', 'started_at': 1, 'kind': 'context_compact'},
                        {'id': 'running', 'session_id': 'allowed', 'status': 'running', 'started_at': 1}):
                await bus.emit('run/state', row)
            query = {'server_id': [baseline['server_id']], 'after': ['0']}
            result = await read(query)
            self.assertEqual([row['status'] for row in result['completions']], ['completed', 'failed', 'interrupted'])
            self.assertEqual(set(result['running']), {'allowed', 'private'})
            result['completions'][0]['session_id'] = 'modified'
            shared = await read({'server_id': [shared_baseline['server_id']], 'after': ['0']}, frozenset({'allowed'}))
            self.assertEqual(shared['completion_cursor'], 2)
            self.assertEqual([row['seq'] for row in shared['completions']], [1, 2])
            empty = await read(visible=frozenset())
            self.assertEqual(empty['completion_cursor'], 0)
            self.assertEqual(empty['completions'], [])
            self.assertEqual(shared['catalog_revision'], 3)
            self.assertEqual(shared['running'], ['allowed'])
            self.assertEqual([row['session_id'] for row in shared['completions']], ['allowed', 'allowed'])
            self.assertEqual((await read({'server_id': ['old-server'], 'after': ['0']}))['completions'], [])
            self.assertEqual((await read({'server_id': [baseline['server_id']], 'after': ['3']}))['completions'], [])
            ctx.hub.forget('allowed')
            self.assertEqual([row['session_id'] for row in (await read(query))['completions']], ['private'])
        finally:
            active.cancel()
            ctx.stop_event_bridge()

    async def test_replay_gap_is_explicit_and_terminal_snapshot_is_independent(self):
        hub = ConnectionHub(replay_size=1)
        hub._completions = deque(maxlen=2)
        for index in range(3):
            hub.stamp('fixture', {'type': 'run/state', 'id': str(index), 'status': 'completed', 'started_at': 1})
            hub.stamp('fixture', {'type': 'note'})
        result = hub.activity_completions(hub.server_id, 0)
        self.assertTrue(result['completions_lost'])
        self.assertEqual([row['seq'] for row in result['completions']], [2, 3])
        self.assertFalse(hub.activity_completions(hub.server_id, 1)['completions_lost'])
        self.assertEqual(hub.activity_completions()['completions'], [])

    async def test_invalid_cursor_is_rejected_before_reading(self):
        for query in ({'after': ['-1']}, {'after': ['1.2']}, {'after': ['1', '2']}, {'server_id': ['']}, {'unknown': ['1']}):
            with self.subTest(query=query), self.assertRaises(BadRequest):
                read_query(Request('GET', '/api/v1/sessions/activity', query, {}, b''), SESSION_ACTIVITY_QUERY)

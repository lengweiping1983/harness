"""待办广播失败后保留已提交快照，旧版本重试不得重复提交。"""
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from harness.session.store import SessionStore
from harness.tools.builtin.todo_tools import TodoStore, build_todo_tools


class TodoBroadcastCommitTests(unittest.IsolatedAsyncioTestCase):
    async def test_committed_snapshot_survives_broadcast_failure_and_stale_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            sessions=SessionStore(directory);session=sessions.create()
            bus=SimpleNamespace(emit=AsyncMock())
            store=TodoStore(bus=bus,session_of=sessions.get)
            tools={tool.schema.name:tool for tool in build_todo_tools(store)}
            ctx=SimpleNamespace(session_id=session.id,services={'session':session})
            first=await tools['todo_write'].execute({'todos':[{'content':'Original 原文','status':'pending'}]},ctx)
            failure=OSError('Original broadcast failure');bus.emit.side_effect=failure
            args={'snapshot_token':first.display['snapshot_token'],'todos':[{'content':'Updated 原文','status':'pending'}]}
            with self.assertRaises(OSError) as error:await tools['todo_write'].execute(args,ctx)
            self.assertIs(error.exception,failure)
            count=session.event_count
            self.assertEqual(store.get(session.id)[0].content,'Updated 原文')
            restored=SessionStore(directory).get(session.id)
            self.assertEqual(TodoStore(session_of=lambda _:restored).get(session.id)[0].content,'Updated 原文')
            bus.emit.reset_mock()
            stale=await tools['todo_write'].execute(args,ctx)
            self.assertTrue(stale.is_error)
            self.assertEqual(stale.data['code'],'VERSION_CONFLICT')
            self.assertEqual(session.event_count,count)
            bus.emit.assert_not_awaited()

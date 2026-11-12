"""成员继承提示按语言解析，执行和交付失败不能互相覆盖。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from harness.core.agent import AgentLoop
from harness.core.language import current_language
from harness.core.turn import TurnResult
from harness.i18n import tr
from harness.session.events import AppendOp
from harness.session.session import Session
from harness.subagent.manager import SubagentManager
from harness.core.runs import AgentRun, FAILED
from harness.tools.registry import ToolRegistry


class SubagentFailureCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_execution_close_and_delivery_failures_remain_in_original_order(self):
        manager = SubagentManager(adapter=None,tools=None,prompts=None)
        primary={'code':'original/execution','message':'Original execution 原文'}
        state=AgentRun(id='member',status=FAILED,error=primary,session=SimpleNamespace(close=AsyncMock(side_effect=OSError('Original close 原文'))))
        async def deliver(record):
            raise ValueError('Original delivery 原文')
        state._on_finished=deliver
        with self.assertLogs('harness.subagent.manager',level='ERROR'):
            await manager._finalization(state)
        self.assertEqual(state.status,FAILED)
        message=state.error_message
        self.assertLess(message.index('Original execution 原文'),message.index('Original close 原文'))
        self.assertLess(message.index('Original close 原文'),message.index('Original delivery 原文'))
        self.assertIn('original/execution',message)
        self.assertTrue(state.settled)
        state.session.close.assert_awaited_once()

    async def test_inherited_notice_uses_member_language_and_keeps_original_payload(self):
        parent=Session()
        await parent.append('user/message',{'text':'Original user 原文'},surface_op=AppendOp())
        for language in ('en','zh-CN','en'):
            manager=SubagentManager(adapter=SimpleNamespace(model='fixture'),tools=ToolRegistry(),prompts=None)
            manager.session_resolver=lambda _id:parent
            captured=[]
            async def prompt(agent,session,text,**kwargs):
                notice=session.latest('developer/message').data['text']
                self.assertTrue(notice.startswith(tr('agent.inheritance.context_notice')))
                payload=json.loads(notice[len(tr('agent.inheritance.context_notice')):])
                self.assertEqual(payload['source_session_id'],parent.id)
                self.assertEqual(payload['messages'][0]['text'],'Original user 原文')
                captured.append(session)
                return TurnResult(session.id,1,'completed',text='Original result 原文')
            with current_language.set(language), patch.object(AgentLoop,'prompt',prompt):
                try:
                    state=manager.spawn(prompt='Original task',description='fixture',parent_session_id=parent.id,fork_turns='all')
                    await manager.wait(state.id,timeout=1)
                    self.assertEqual(state.status,'completed')
                    self.assertTrue(captured[0]._closed)
                finally:
                    await manager.shutdown()

"""Ralph 使用真实成员管理器运行独立轮次并收拢取消后的上下文。"""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness.core.agent import AgentLoop
from harness.core.language import current_language
from harness.core.turn import TurnResult
from harness.session.events import AppendOp, TurnEndReason
from harness.subagent.manager import SubagentManager
from harness.tools.builtin.ralph_tools import build_ralph_tools
from harness.tools.registry import ToolRegistry


class RalphManagerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_manager_creates_fresh_sessions_and_keeps_only_report_between_rounds(self):
        reports = [dict(status='continue',summary='Original progress 原文',evidence=[],nextSteps=['Original step'],blocker=''),
                   dict(status='complete',summary='Original result 原文',evidence=['Original proof'],nextSteps=[],blocker='')]
        for language in ('en','zh-CN','en'):
            sessions, prompts = [], []
            async def prompt(agent, session, text, **kwargs):
                index=len(sessions)
                sessions.append(session);prompts.append(text)
                self.assertEqual(session.event_count,0)
                turn=await session.start_turn()
                await session.append('user/message',{'text':text},surface_op=AppendOp())
                output=json.dumps(reports[index],ensure_ascii=False)
                await session.append('assistant/message',{'text':output},surface_op=AppendOp())
                await session.end_turn(TurnEndReason(kind='completed'))
                return TurnResult(session.id,turn.data['turn'],'completed',text=output)
            manager=SubagentManager(adapter=SimpleNamespace(model='fixture'),tools=ToolRegistry(),prompts=None)
            with current_language.set(language), patch.object(AgentLoop,'prompt',prompt):
                try:
                    result=await build_ralph_tools(manager)[0].execute({'objective':'Original goal 原文','maxRounds':2},SimpleNamespace(session_id='owner'))
                    self.assertEqual(result.data['status'],'complete')
                    self.assertEqual(result.data['roundsStarted'],2)
                    self.assertEqual(result.data['report'],reports[-1])
                    self.assertEqual(len({session.id for session in sessions}),2)
                    self.assertTrue(all(session._closed for session in sessions))
                    self.assertTrue(all(state.task.done() for state in manager.list_states()))
                    self.assertIn(json.dumps(reports[0],ensure_ascii=False,indent=2),prompts[1])
                finally:
                    await manager.shutdown()

    async def test_cancel_real_manager_waits_for_owned_session_finalization(self):
        entered, release=asyncio.Event(),asyncio.Event()
        sessions=[]
        async def prompt(agent,session,text,**kwargs):
            sessions.append(session);entered.set()
            try:
                await asyncio.Future()
            finally:
                await release.wait()
        manager=SubagentManager(adapter=SimpleNamespace(model='fixture'),tools=ToolRegistry(),prompts=None)
        with patch.object(AgentLoop,'prompt',prompt):
            task=asyncio.create_task(build_ralph_tools(manager)[0].execute({'objective':'Original'},SimpleNamespace(session_id='owner')))
            try:
                await entered.wait();task.cancel();await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(sessions[0]._closed)
                self.assertTrue(all(state.task.done() for state in manager.list_states()))
                await manager.shutdown()

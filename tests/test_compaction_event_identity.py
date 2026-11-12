"""无需摘要的整理分支也须用同一身份关联落盘与实时结束事件。"""
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness import Harness, HarnessConfig
from tests.agent_configuration_fixture import RecordingTransport


class CompactionEventIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_skipped_summary_and_prune_end_match_persisted_start(self):
        for pruned in (False,True):
            with self.subTest(pruned=pruned), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                flags = {item.name:False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
                transport = RecordingTransport()
                app = Harness(HarnessConfig(api_key='fixture',home=root/'state',cwd=root,**flags),transport=transport)
                starts, ends = [], []
                app.bus.on('compaction/start',lambda data:starts.append(dict(data)))
                app.bus.on('compaction/end',lambda data:ends.append(dict(data)))
                try:
                    session = app.create_session()
                    agent = app._agent_for(session)
                    agent._project_tool_history = AsyncMock(return_value=False)
                    agent.tool_result_pruner = SimpleNamespace(prune_session=AsyncMock(return_value=SimpleNamespace(changed=True,pruned=[1],chars_removed=10))) if pruned else None
                    await agent._maybe_compact(session,agent.config,0,force=True,manual=True)
                    committed_starts = [event for event in session.events if event.type=='compaction/start']
                    committed_ends = [event for event in session.events if event.type=='compaction/end']
                    self.assertEqual(len(committed_starts),1)
                    self.assertEqual(len(committed_ends),1)
                    self.assertEqual(committed_ends[0].data['compaction_id'],committed_starts[0].seq)
                    self.assertEqual(len(starts),1)
                    self.assertEqual(len(ends),1)
                    self.assertEqual(starts[0]['compaction_id'],committed_starts[0].seq)
                    for key,value in committed_ends[0].data.items():
                        self.assertEqual(ends[0][key],value)
                    self.assertEqual(transport.requests,[])
                    self.assertEqual([event for event in session.surface if event.type=='user/message'],[])
                finally:
                    await app.close()

"""旧运行终态及桥接done不清空当前运行的临时生成状态。"""
import unittest
from harness.web.hub import ConnectionHub


class HubRunIdentityTests(unittest.TestCase):
    def test_old_end_keeps_current_generation_and_draft_but_preserves_replay(self):
        for status in ('completed','failed','interrupted'):
            hub=ConnectionHub()
            hub.stamp('fixture',{'type':'turn/start','turn':2,'run_id':'current'})
            hub.stamp('fixture',{'type':'request/start','request_id':'request','generation':{'phase':'waiting'}})
            hub.stamp('fixture',{'type':'assistant/delta','request_id':'request','delta':'Original 原文'})
            for event in ({'type':'run/state','id':'previous','status':status,'started_at':1},
                          {'type':'done','run_id':'previous'}):
                stamped=hub.stamp('fixture',event)
                self.assertEqual(hub.generation('fixture')['request_id'],'request')
                self.assertEqual(hub.draft('fixture')['text'],'Original 原文')
                self.assertEqual(hub.replay('fixture',stamped['seq']-1),[stamped])
            hub.stamp('fixture',{'type':'run/state','id':'current','status':status,'started_at':1})
            self.assertIsNone(hub.generation('fixture'))

    def test_forget_drops_prior_run_identity(self):
        hub=ConnectionHub();hub.stamp('fixture',{'type':'turn/start','run_id':'old'})
        hub.forget('fixture')
        hub.stamp('fixture',{'type':'request/start','request_id':'new','generation':{'phase':'waiting'}})
        hub.stamp('fixture',{'type':'done','run_id':'new-run'})
        self.assertIsNone(hub.generation('fixture'))

class HubRunBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_bridge_carries_original_run_id_without_modifying_input(self):
        import asyncio
        from types import SimpleNamespace
        from harness.core.events import EventBus
        from harness.web.context import WebContext
        bus=EventBus();context=WebContext(SimpleNamespace(bus=bus),asyncio.get_running_loop())
        context.start_event_bridge()
        row={'id':'original-run','session_id':'fixture','status':'completed','started_at':1}
        try:
            await bus.emit('run/state',row)
            replay=context.hub.replay('fixture',0)
            self.assertEqual([event['type'] for event in replay],['run/state','done'])
            self.assertEqual(replay[1]['run_id'],'original-run')
            self.assertNotIn('seq',row);self.assertNotIn('type',row)
        finally:context.stop_event_bridge()

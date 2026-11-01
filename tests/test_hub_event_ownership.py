"""实时事件编号、重放与各发送队列不共享调用方的可变正文。"""
import asyncio
from types import MappingProxyType
import unittest

from harness.web.hub import ConnectionHub


class Connection:
    closed = False
    def __init__(self, mutate=False):
        self.mutate = mutate
        self.messages = []
        self.sent = asyncio.Event()
    async def send_json(self, event):
        if self.mutate:event['nested']['values'].append('Connection mutation')
        self.messages.append(event);self.sent.set()


class HubEventOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def test_stamping_one_input_for_multiple_conversations_keeps_original_and_replay_identity(self):
        hub = ConnectionHub()
        original = {'type':'note','nested':{'values':['Original 原文']}}
        first = hub.stamp('one',original)
        second = hub.stamp('two',original)
        self.assertNotIn('seq',original)
        first['nested']['values'].append('Caller mutation');second['seq']=99
        self.assertEqual(hub.replay('one',0)[0]['nested']['values'],['Original 原文'])
        self.assertEqual(hub.replay('two',0)[0]['seq'],1)
        replay = hub.replay('one',0);replay[0]['nested']['values'].clear()
        self.assertEqual(hub.replay('one',0)[0]['nested']['values'],['Original 原文'])

    async def test_each_subscriber_and_replay_own_nested_event_snapshots(self):
        hub = ConnectionHub()
        first,second = Connection(mutate=True),Connection()
        subscribers=[hub.subscribe('fixture',connection) for connection in (first,second)]
        value=hub.stamp('fixture',{'type':'note','nested':{'values':['Original 原文']}})
        try:
            hub.publish('fixture',value)
            value['nested']['values'].append('Producer mutation')
            await asyncio.wait_for(asyncio.gather(first.sent.wait(),second.sent.wait()),1)
            self.assertEqual(first.messages[0]['nested']['values'],['Original 原文','Connection mutation'])
            self.assertEqual(second.messages[0]['nested']['values'],['Original 原文'])
            self.assertEqual(hub.replay('fixture',0)[0]['nested']['values'],['Original 原文'])
        finally:
            hub.close_all()
            await asyncio.gather(*(item.writer for item in subscribers),return_exceptions=True)

    def test_frozen_input_and_nested_draft_snapshot_remain_isolated(self):
        hub=ConnectionHub()
        value=MappingProxyType({'type':'turn/start',
                                'speaker':MappingProxyType({'name':'Agent','extra':('Original',)})})
        hub.stamp('fixture',value)
        draft=hub.draft('fixture')
        draft['speaker']['extra'].clear()
        self.assertEqual(hub.draft('fixture')['speaker']['extra'],['Original'])
        self.assertNotIn('seq',value)

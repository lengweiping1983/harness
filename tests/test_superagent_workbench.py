"""工作台沿统一服务执行、持久管理及生命周期的定向验证。"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.app.superagent_workbench import SuperagentWorkbench
from harness.app.superagent_operator import CoordinatorOperator,OperatorRequests
from harness.app.superagent_config import read_config
from harness.app.superagent_state import open_state
from harness.app.superagent_local import submit_local
from harness.app.superagent_service import SuperagentService
from harness.core.errors import HarnessError
from tests.superagent_product_fixture import product_config,ProductTransport


class WorkbenchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path,self.raw = product_config(self.root)
        self.spec = read_config(self.path)
        self.roles,self.work = ProductTransport(),ProductTransport()
        self.created = []
        async def factory(path):
            service = await SuperagentService.open(path,coordinator_transport=self.roles,worker_transport=self.work)
            self.created.append(service)
            return service
        self.manager = SuperagentWorkbench(service_factory=factory)
        self.addAsyncCleanup(self.manager.close)
        self.environment = patch.dict(os.environ,{'HARNESS_API_KEY':'fixture'}); self.environment.start(); self.addCleanup(self.environment.stop)
        self.key = self.manager.connect(str(self.path))['connection_id']

    async def test_workbench_submits_starts_and_stops_the_real_unified_service_once(self):
        await self.manager.dispatch(self.key,'submit',{'text':'Fix the typo.'},'submit')
        first,second = await asyncio.gather(*(self.manager.dispatch(self.key,'start',{},'start') for _ in range(2)))
        self.assertEqual(first,second); self.assertEqual(len(self.created),1)
        for _ in range(100):
            page = self.manager.read(self.key,'missions')
            if page['items'] and page['items'][0]['state']=='completed':
                break
            await asyncio.sleep(.02)
        self.assertEqual(page['items'][0]['state'],'completed')
        await self.manager.dispatch(self.key,'stop',{},'stop')
        self.assertTrue(self.created[0].closed)
        self.assertEqual((len(self.roles.requests),len(self.work.requests)),(2,1))
        self.assertEqual(self.manager.connection(self.key)['runtime'],'stopped')
        with self.assertRaises(HarnessError):
            await self.manager.dispatch(self.key,'submit',{'text':'Different request'},'submit')

    async def test_source_and_policy_changes_commit_original_receipts_atomically_and_survive_restart(self):
        submit_local(self.path,'Fix the typo.',request_id='seed')
        store = open_state(self.spec)
        operator = CoordinatorOperator(store,self.spec)
        result = operator.edit('source',{'source_id':'local','expected_revision':1,'enabled':False},'disable')
        repeated = operator.edit('source',{'source_id':'local','expected_revision':1,'enabled':False},'disable')
        self.assertEqual(result,repeated)
        self.assertFalse(result['receipt']['enabled'])
        self.assertEqual(result['receipt']['config_revision'],2)
        with self.assertRaises(Exception):
            operator.edit('source',{'source_id':'local','expected_revision':1,'enabled':True},'stale')
        with self.assertRaises(HarnessError):
            OperatorRequests.receipt(store,'stale')
        policy = operator.auth.current_policy()
        edited = {**policy['policy'],'mode':'observe'}
        changed = operator.edit('policy',{'policy':edited,'expected_revision':policy['revision']},'policy')
        self.assertEqual(operator.edit('policy',{'policy':edited,'expected_revision':policy['revision']},'policy'),changed)
        service = await SuperagentService.open(self.path,coordinator_transport=self.roles,worker_transport=self.work)
        self.addAsyncCleanup(service.aclose)
        self.assertFalse(service.coordinator.sources.get_instance('local').enabled)
        self.assertEqual(service.coordinator.authorization.current_policy()['mode'],'observe')
        self.assertEqual(len(self.roles.requests),0)

    async def test_start_stop_race_waits_for_the_original_factory_and_close_owns_it(self):
        entered,release = asyncio.Event(),asyncio.Event()
        factory = self.manager._factory
        async def delayed(path):
            entered.set(); await release.wait()
            return await factory(path)
        self.manager._factory = delayed
        starting = asyncio.create_task(self.manager.dispatch(self.key,'start',{},'start'))
        await entered.wait()
        stopping = asyncio.create_task(self.manager.dispatch(self.key,'stop',{},'stop'))
        await asyncio.sleep(0)
        self.assertFalse(stopping.done())
        release.set(); await starting; await stopping
        self.assertEqual(len(self.created),1)
        self.assertTrue(self.created[0].closed)

    async def test_changed_config_and_foreign_connection_are_rejected_without_rebinding(self):
        with self.assertRaises(HarnessError): self.manager.read('foreign','missions')
        self.raw['model']='changed'; self.path.write_text(json.dumps(self.raw))
        with self.assertRaises(HarnessError): self.manager.read(self.key,'missions')
        self.assertEqual(self.manager.connections()[0]['runtime'],'unavailable')
        self.assertFalse((self.root/'state').exists())

    async def test_explicit_reply_grant_is_revision_bound_atomic_and_preserves_original_address(self):
        submit_local(self.path,'Fix the typo.',request_id='seed')
        store = open_state(self.spec)
        operator = CoordinatorOperator(store,self.spec)
        channel = operator.sources.list_channels()[0]
        payload = {'channel_id':channel['channel_id'],'expected_source_revision':1,'enabled':True}
        first = await self.manager.dispatch(self.key,'reply',payload,'grant')
        self.assertTrue(first['receipt']['receipt']['capabilities']['reply'])
        self.assertEqual(await self.manager.dispatch(self.key,'reply',payload,'grant'),first)
        operator.edit('source',{'source_id':'local','expected_revision':1,'enabled':False},'disable')
        operator.edit('source',{'source_id':'local','expected_revision':2,'enabled':True},'enable')
        page = self.manager.read(self.key,'channels')
        self.assertEqual(page['items'][0]['reply_enabled'],0)
        with self.assertRaises(PermissionError): operator.edit('reply',payload,'stale-grant')
        self.assertEqual(operator.edit('reply',{**payload,'expected_source_revision':3},'regrant')['state'],'completed')
        self.assertEqual(self.manager.read(self.key,'channels')['items'][0]['reply_enabled'],1)
        self.assertEqual(operator.sources.get_channel(channel['channel_id'])['address'],channel['address'])
        self.assertEqual(self.manager.read(self.key,'receipt',identifier='regrant')['state'],'completed')
        self.assertTrue(any(row['event_type']=='channel.reply_configured' for row in self.manager.read(self.key,'audit')['items']))

    async def test_local_attention_answer_uses_original_thread_and_starts_fresh_activity(self):
        self.work.report = {'status':'attention','summary':'Need a decision.','reply':'',
            'question':'Which behavior should remain?','options':['A','B']}
        await self.manager.dispatch(self.key,'submit',{'text':'Fix the typo.'},'submit')
        await self.manager.dispatch(self.key,'start',{},'start')
        for _ in range(150):
            rows = self.manager.read(self.key,'attentions')['items']
            if rows: break
            await asyncio.sleep(.02)
        self.assertEqual(len(rows),1)
        payload = {'attention_id':rows[0]['attention_id'],'text':'Preserve A.','source_id':'local',
            'actor_id':'human:1','thread_id':'work'}
        with self.assertRaises(HarnessError):
            await self.manager.dispatch(self.key,'answer',{**payload,'thread_id':'foreign'},'foreign')
        self.work.report = {'status':'completed','summary':'A preserved.','reply':'Done.'}
        result = await self.manager.dispatch(self.key,'answer',payload,'answer')
        for _ in range(150):
            if self.manager.read(self.key,'attentions')['items'][0]['state']=='answered': break
            await asyncio.sleep(.02)
        self.assertEqual(self.manager.read(self.key,'attentions')['items'][0]['state'],'answered')
        self.assertEqual(await self.manager.dispatch(self.key,'answer',payload,'answer'),result)
        with self.assertRaises(HarnessError):
            await self.manager.dispatch(self.key,'answer',payload,'stale-answer')
        await self.manager.dispatch(self.key,'stop',{},'stop')
        store = open_state(self.spec)
        with store.transaction() as db:
            runs = [row[0] for row in db.execute('SELECT run_id FROM assignments')]
            answers = db.execute("SELECT count(*) FROM triggers WHERE json_extract(metadata_json,'$.attention_id')=?",(rows[0]['attention_id'],)).fetchone()[0]
        self.assertEqual(len(runs),2); self.assertEqual(len(set(runs)),2)
        self.assertEqual(answers,1)

    async def test_stored_enabled_revision_is_authoritative_over_disabled_local_seed(self):
        self.raw['sources']['local']['enabled']=False
        self.path.write_text(json.dumps(self.raw))
        self.spec=read_config(self.path)
        self.key=self.manager.connect(str(self.path))['connection_id']
        with self.assertRaises(HarnessError): submit_local(self.path,'Not enabled.')
        self.assertFalse((self.root/'state').exists())
        store=open_state(self.spec,initialize=True)
        operator=CoordinatorOperator(store,self.spec)
        operator.edit('source',{'source_id':'local','expected_revision':1,'enabled':True},'enable-seed')
        received=submit_local(self.path,'Explicitly enabled.',request_id='enabled-submit')
        self.assertTrue(received['queued'])
        self.assertEqual(self.manager.read(self.key,'local_options')['sources'][0]['source_id'],'local')

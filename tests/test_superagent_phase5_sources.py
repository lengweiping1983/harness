"""Review／CI 来源上下文、原消息媒体和人工回查的实际消费验证。"""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path

from PIL import Image

from harness.app.superagent_media import SourceMedia
from harness.app.superagent_operator import CoordinatorOperator
from harness.core.errors import HarnessError
from tests.superagent_delivery_fixture import DeliveryFixture


class Phase5SourceTests(DeliveryFixture):
    def github(self,data,kind,delivery='activity'):
        headers,raw = self.packet('github',data)
        headers.update({'x-github-event':kind,'x-github-delivery':delivery})
        return self.service.source_ingress('github-source').receive(headers,raw)

    async def test_review_and_check_events_are_scoped_context_and_never_start_or_resume_work(self):
        await self.remote('github')
        original = copy.deepcopy(self.remote_data)
        pull = {'id':70,'number':7,'head':{'sha':'a'*40},'base':{'repo':{'id':10}}}
        review = {'action':'submitted','repository':original['repository'],'sender':original['sender'],'pull_request':pull,
            'review':{'id':700,'body':'@bot Review needs a correction.','state':'changes_requested','commit_id':'a'*40,
                'submitted_at':'2026-11-08T00:01:00Z','user':original['sender']}}
        stored = self.github(review,'pull_request_review')
        repeated = self.github(review,'pull_request_review','redelivered')
        self.assertEqual(stored['trigger_id'],repeated['trigger_id'])
        self.assertEqual(self.rows('SELECT context_only FROM ticket_inputs WHERE trigger_id=?',(stored['trigger_id'],))[0]['context_only'],1)
        policy = self.f.host.authorization.current_policy()
        changed = copy.deepcopy(policy['policy']); changed['sources']['github-source']['bots']=[{'id':'github:bot:17'}]
        self.f.host.authorization.publish_policy(changed,expected_revision=policy['revision'],actor={'kind':'human'})
        check = {'action':'completed','repository':original['repository'],'sender':original['sender'],
            'check_run':{'id':900,'head_sha':'a'*40,'status':'completed','conclusion':'success','pull_requests':[pull]}}
        result = self.github(check,'check_run')
        body = json.loads(self.rows('SELECT body_json FROM triggers WHERE trigger_id=?',(result['trigger_id'],))[0]['body_json'])
        self.assertEqual(body['head_sha'],'a'*40)
        self.assertEqual(self.rows('SELECT context_only FROM ticket_inputs WHERE trigger_id=?',(result['trigger_id'],))[0]['context_only'],1)
        before = list(self.adapter.shared['submit_calls'])
        await self.service.tick()
        self.assertEqual(self.adapter.shared['submit_calls'],before)
        self.assertEqual(len(self.rows('SELECT * FROM missions')),1)
        self.assertEqual(len(self.rows('SELECT * FROM reviews')),0)
        self.assertEqual(len(self.rows('SELECT * FROM evaluations')),1)

    async def test_check_without_unique_original_pull_or_with_foreign_head_never_claims_authority(self):
        await self.remote('github')
        original = self.remote_data
        event = {'action':'completed','repository':original['repository'],'sender':original['sender'],
            'check_run':{'id':800,'head_sha':'a'*40,'status':'completed','conclusion':'success','pull_requests':[]}}
        before = len(self.rows('SELECT * FROM triggers'))
        self.assertEqual(self.github(event,'check_run')['state'],'ignored')
        event['check_run']['pull_requests']=[{'id':70,'number':7,'head':{'sha':'b'*40},'base':{'repo':{'id':10}}}]
        with self.assertRaises(HarnessError): self.github(event,'check_run')
        event['check_run']['pull_requests'][0]['head']['sha']='a'*40
        event['check_run']['pull_requests'][0]['base']['repo']['id']=999
        with self.assertRaises(HarnessError): self.github(event,'check_run')
        self.assertEqual(len(self.rows('SELECT * FROM triggers')),before)

    async def image(self):
        await self.remote('feishu')
        event = copy.deepcopy(self.remote_data)
        event['header']['event_id']='media-event'
        event['event']['message'].update(message_id='om_media',root_id='om_origin',message_type='image',content=json.dumps({'image_key':'img_fixture'}))
        headers,raw = self.packet('feishu',event)
        stored = self.service.source_ingress('feishu-source').receive(headers,raw)
        data = io.BytesIO(); Image.new('RGB',(2,2),'red').save(data,format='PNG')
        outer = self
        class Client:
            def __init__(self): self.reads=0; self.wrong=False; self.revoke=False
            async def request(self,method,path):
                outer.assertEqual(method,'GET')
                if path.endswith('/bot/v3/info/'):
                    return {'bot':{'open_id':'ou_foreign' if self.wrong else 'ou_bot'}}
                return {'data':{'items':[{'message_id':'om_media','root_id':'om_origin','chat_id':'oc_chat','msg_type':'image',
                    'body':{'content':json.dumps({'image_key':'img_fixture'})}}]}}
            async def request_bytes(self,path,*,before_request):
                self.reads+=1
                if self.revoke:
                    source = outer.f.host.sources.get_instance('feishu-source')
                    outer.f.host.sources.update_instance('feishu-source',expected_revision=source.config_revision,enabled=False)
                before_request()
                outer.assertEqual(path,'/open-apis/im/v1/messages/om_media/resources/img_fixture?type=image')
                return {'data':data.getvalue(),'content_type':'image/png'}
        client = Client()
        media = SourceMedia(self.f.host._store,{'state_dir':str(self.service.root)},clients={'feishu-source':client})
        return stored['trigger_id'],client,media

    async def test_feishu_image_is_verified_saved_once_and_corruption_never_overwrites_original(self):
        trigger,client,media = await self.image()
        self.assertEqual(client.reads,0)
        first = await media.fetch(trigger,'img_fixture')
        self.assertEqual(first['content_type'],'image/png')
        self.assertEqual(await media.fetch(trigger,'img_fixture'),first)
        self.assertEqual(client.reads,1)
        row,raw,_ = media.cached(first['media_id'])
        self.assertTrue(raw.startswith(b'\x89PNG'))
        path = Path(self.service.root)/'media'/first['media_id']; path.write_bytes(b'preserve-corruption')
        with self.assertRaises(HarnessError): await media.fetch(trigger,'img_fixture')
        self.assertEqual(path.read_bytes(),b'preserve-corruption')
        self.assertEqual(client.reads,1)

    async def test_media_foreign_owner_resource_and_mid_request_revocation_prevent_local_publication(self):
        trigger,client,media = await self.image()
        with self.assertRaises(HarnessError): await media.fetch(trigger,'img_foreign')
        client.wrong=True
        with self.assertRaises(HarnessError): await media.fetch(trigger,'img_fixture')
        self.assertEqual(client.reads,0)
        client.wrong=False; client.revoke=True
        with self.assertRaises(HarnessError): await media.fetch(trigger,'img_fixture')
        self.assertEqual(self.rows('SELECT * FROM source_media'),[])
        self.assertFalse((self.service.root/'media').exists())

    async def test_rich_post_preserves_text_resources_and_never_downloads_at_webhook_ack(self):
        await self.remote('feishu')
        event = copy.deepcopy(self.remote_data)
        event['header']['event_id']='post-event'
        event['event']['message'].update(message_id='om_post',root_id='om_origin',message_type='post',content=json.dumps({'title':'Design input',
            'content':[[{'tag':'text','text':'Keep the public name.'},{'tag':'img','image_key':'img_fixture'}]]}))
        headers,raw = self.packet('feishu',event)
        result = self.service.source_ingress('feishu-source').receive(headers,raw)
        body = json.loads(self.rows('SELECT body_json FROM triggers WHERE trigger_id=?',(result['trigger_id'],))[0]['body_json'])
        self.assertEqual(body['text'],'Design input\nKeep the public name.')
        self.assertEqual(body['attachments'],[{'resource_key':'img_fixture','type':'image','name':''}])
        self.assertEqual(self.rows('SELECT * FROM source_media'),[])

    async def test_human_unknown_check_uses_original_provider_proof_and_never_posts_again(self):
        provider,assignment,_ = await self.remote('github')
        provider.lose_ack=True; provider.mode='absent'
        await self.complete(assignment)
        effect = self.rows("SELECT * FROM effects WHERE kind='outbox.send'")[0]
        sources = {source.source_instance_id:{'kind':source.kind,'config':dict(source.config),'enabled':source.enabled}
            for source in self.f.host.sources.list_instances()}
        operator = CoordinatorOperator(self.f.host._store,{'sources':sources,'secrets':{}})
        pending = await operator.verify({'effect_id':effect['effect_id'],'generation':effect['generation'],'note':'Original result needs verification.'},'observe')
        self.assertEqual(pending['receipt']['outcome'],'indeterminate')
        self.assertEqual(self.rows('SELECT state FROM outbox_messages')[0]['state'],'unknown')
        provider.mode='exact'
        payload = {'effect_id':effect['effect_id'],'generation':effect['generation'],'note':'Inspect the original provider object.'}
        checked = await operator.verify(payload,'verified',runtime=self.service)
        self.assertEqual(checked['receipt']['outcome'],'completed')
        self.assertEqual(self.rows('SELECT state FROM outbox_messages')[0]['state'],'sent')
        self.assertEqual(await operator.verify(payload,'verified',runtime=self.service),checked)
        self.assertEqual(provider.posts,1)
        with self.assertRaises(HarnessError):
            await operator.verify({**payload,'receipt':{'applied':True}},'forged',runtime=self.service)

    async def test_github_lifecycle_variants_preserve_original_thread_and_authors(self):
        await self.remote('github')
        original = self.remote_data
        pull = {'id':70,'number':7,'head':{'sha':'a'*40},'base':{'repo':{'id':10}},'body':'PR context'}
        base = {'repository':original['repository'],'sender':original['sender'],'pull_request':pull}
        for kind,entity,actions in (
            ('pull_request_review',{'id':701,'user':original['sender'],'body':'Old review','state':'approved','commit_id':'b'*40},('submitted','edited','dismissed')),
            ('pull_request_review_comment',{'id':702,'user':original['sender'],'body':'Line comment','path':'main.py'},('created','edited','deleted')),
            ('pull_request',None,('synchronize','ready_for_review','converted_to_draft','edited','labeled','unlabeled'))):
            for action in actions:
                with self.subTest(kind=kind,action=action):
                    event={**base,'action':action}
                    if action in {'labeled','unlabeled'}: event['label']={'name':'needs-review'}
                    if entity: event['review' if kind=='pull_request_review' else 'comment']=entity
                    received=self.github(event,kind,kind+'-'+action)
                    trigger=self.f.host.sources.get_trigger(received['trigger_id'])
                    origin=self.rows('SELECT goal_ref FROM missions')[0]['goal_ref']
                    self.assertEqual(trigger['primary_thread_id'],self.f.host.sources.get_trigger(origin)['primary_thread_id'])
                    self.assertEqual(trigger['metadata']['thread_number'],7)
                    self.assertEqual(len(self.f.host.sources.reply_channels(received['trigger_id'])),1)
                    self.assertTrue(trigger['metadata']['context_event'])
                    if kind=='pull_request_review': self.assertTrue(trigger['body']['review']['outdated'])
        bad={**base,'action':'submitted','review':{'id':703,'user':{'id':999},'state':'approved','commit_id':'a'*40}}
        with self.assertRaises(HarnessError): self.github(bad,'pull_request_review')
        bad['review']['user']=original['sender']; bad['review']['commit_id']='invalid'
        with self.assertRaises(HarnessError): self.github(bad,'pull_request_review')
        bot=copy.deepcopy(base); bot.update(action='submitted',sender={'id':42,'login':'bot','type':'User'})
        bot['review']={'id':704,'user':bot['sender'],'state':'approved','commit_id':'a'*40}
        received=self.github(bot,'pull_request_review')
        self.assertEqual(self.f.host.sources.get_trigger(received['trigger_id'])['actor']['actor_id'],'github:bot:42')
        self.assertEqual(self.rows('SELECT * FROM ticket_inputs WHERE trigger_id=?',(received['trigger_id'],)),[])
        self.assertEqual(len(self.rows('SELECT * FROM missions')),1)
        self.assertEqual(len(self.rows('SELECT * FROM evaluations')),1)

    async def test_file_audio_video_media_are_opaque_and_cached_reads_respect_source_revocation(self):
        await self.remote('feishu')
        for kind in ('file','audio','media'):
            with self.subTest(kind=kind):
                event=copy.deepcopy(self.remote_data); event['header']['event_id']=kind+'-event'
                content={'file_key':'file_'+kind,'file_name':'original.'+kind}
                if kind=='media': content['image_key']='img_preview'
                event['event']['message'].update(message_id='om_'+kind,root_id='om_origin',message_type=kind,content=json.dumps(content))
                headers,raw=self.packet('feishu',event)
                received=self.service.source_ingress('feishu-source').receive(headers,raw)
                calls=[]
                class Client:
                    async def request(self,method,path):
                        if path.endswith('/info/'): return {'bot':{'open_id':'ou_bot'}}
                        return {'data':{'items':[{'message_id':'om_'+kind,'root_id':'om_origin','chat_id':'oc_chat','msg_type':kind,'body':{'content':json.dumps(content)}}]}}
                    async def request_bytes(self,path,*,before_request):
                        before_request(); calls.append(path)
                        return {'data':b'<script>untrusted file</script>','content_type':'text/html'}
                media=SourceMedia(self.f.host._store,{'state_dir':str(self.service.root)},clients={'feishu-source':Client()})
                saved=await media.fetch(received['trigger_id'],'file_'+kind)
                self.assertEqual(saved['content_type'],'application/octet-stream')
                self.assertEqual(media.cached(saved['media_id'])[1],b'<script>untrusted file</script>')
                self.assertEqual(calls,['/open-apis/im/v1/messages/om_'+kind+'/resources/file_'+kind+'?type=file'])
                self.assertEqual(await media.fetch(received['trigger_id'],'file_'+kind),saved)
        source=self.f.host.sources.get_instance('feishu-source')
        self.f.host.sources.update_instance('feishu-source',expected_revision=source.config_revision,enabled=False)
        with self.assertRaises(HarnessError): media.cached(saved['media_id'])
        self.assertTrue((self.service.root/'media'/saved['media_id']).exists())

    async def test_source_refresh_serializes_listener_creation_and_preserves_edited_config(self):
        import asyncio
        import os
        from unittest.mock import patch
        from harness.app.superagent_bootstrap import provider_configuration
        from harness.app.superagent_state import configure
        await self.remote('feishu')
        sources={source.source_instance_id:{'kind':source.kind,'config':dict(source.config),'enabled':source.enabled,
            'transport':{'webhook_port':0,'api_url':'http://127.0.0.1:1'}} for source in self.f.host.sources.list_instances()}
        spec={'instance_id':self.f.instance_id,'sources':sources,'secrets':{'token':'FIXTURE_TOKEN','encrypt':'FIXTURE_ENCRYPT'},'language':'en'}
        source=self.f.host.sources.get_instance('feishu-source')
        config={**source.config,'chats':['oc_chat','oc_added']}
        operator=CoordinatorOperator(self.f.host._store,spec)
        operator.edit('source',{'source_id':'feishu-source','expected_revision':1,'config':config},'edit-chat')
        self.assertEqual(configure(self.f.host._store,spec)[1].get_instance('feishu-source').config['chats'],sorted(config['chats']))
        with patch.dict(os.environ,{'FIXTURE_TOKEN':'fixture-token','FIXTURE_ENCRYPT':'fixture-key'}):
            clients,_=provider_configuration(spec,self.f.host._store)
        self.assertEqual(clients,{})
        entered,release=asyncio.Event(),asyncio.Event(); created=[]
        class Server:
            errors=[]
            def __init__(self,*args,**kwargs): created.append(self); self.closes=0
            async def start(self): entered.set(); await release.wait()
            def stop(self): pass
            async def aclose(self): self.closes+=1
        self.service._production_config=spec
        with patch('harness.app.superagent_bootstrap.provider_configuration',return_value=({},None)),patch('harness.app.superagent_http.WebhookServer',Server):
            first=asyncio.create_task(self.service.refresh_sources()); await entered.wait()
            second=asyncio.create_task(self.service.refresh_sources()); await asyncio.sleep(0)
            self.assertEqual(len(created),1); self.assertFalse(second.done())
            release.set(); await first; await second
            self.assertEqual(len(self.service.listeners),1)
            operator.edit('source',{'source_id':'feishu-source','expected_revision':2,'enabled':False},'disable-chat')
            await self.service.refresh_sources()
        self.assertEqual(created[0].closes,1); self.assertEqual(self.service.listeners,[])
        self.assertEqual(len(self.rows('SELECT * FROM missions')),1)

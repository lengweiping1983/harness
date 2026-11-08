import assert from 'node:assert/strict'
import test from 'node:test'

let saved, state, page, claims, sends, mode, claimedPhase, alarm, alarmCreates
const listeners={}
const url='https://chatgpt.com/c/00000000-0000-0000-0000-000000000001'
const clone=value=>structuredClone(value)
const reset=()=>{
 saved={settings:{endpoint:'http://127.0.0.1:8877/',token:'fixture'},binding:{id:'binding',tabId:1,url,enabled:true,expiresAt:Date.now()+100000,idleKey:'a',idleSince:Date.now()-10000}}
 state={state:'continue',milestone:'m1',url,pending:null,sent_phases:[]}
 page={ready:true,assistantKey:'a',hasAssistant:true,hasImage:false,userMessages:[]}
 claims=0;sends=0;mode='confirmed';claimedPhase=undefined
 alarm=undefined;alarmCreates=[]
}
reset()
globalThis.chrome={
 storage:{local:{setAccessLevel:async()=>{},get:async keys=>Object.fromEntries((typeof keys==='string'?[keys]:keys).map(key=>[key,clone(saved[key])])),set:async value=>Object.assign(saved,clone(value)),remove:async key=>{delete saved[key]}}},
 alarms:{get:async()=>clone(alarm),create:async(name,options)=>{alarm={name,...options};alarmCreates.push(clone(alarm))},onAlarm:{addListener(callback){listeners.alarm=callback}}},
 runtime:{id:'fixture',getURL:path=>'chrome-extension://fixture/'+path,onStartup:{addListener(callback){listeners.startup=callback}},onInstalled:{addListener(callback){listeners.installed=callback}},onMessage:{addListener(callback){listeners.message=callback}}},
 action:{setBadgeText:async()=>{}},tabs:{get:async()=>({url}),sendMessage:async(_id,message)=>{
  if(message.type==='probe')return clone(page)
  sends++
  if(mode==='confirmed')page.userMessages.push(message.prompt)
  return {outcome:mode,reason:mode==='unknown'?'send_unconfirmed':''}
 }},
}
globalThis.fetch=async(raw,options)=>{
 const path=new URL(raw).pathname
 const input=options.body?JSON.parse(options.body):null
 let result
 if(path==='/state')result=clone(state)
 if(path==='/claim'){
  claimedPhase=input.phase
  claims++;state.pending={id:'intent',prompt:'继续'};result=clone(state.pending)
 }
 if(path==='/receipt'){
  if(input.outcome==='confirmed'){state.pending=null;state.sent_phases.push(claimedPhase)}
  if(input.outcome==='not_sent')state.pending=null
  result={outcome:input.outcome}
 }
 return {ok:true,json:async()=>result}
}
const {tick}=await import('../../extensions/chatgpt-companion/background.mjs')

test('successive automatic checks draw fresh one-shot delays from 120 through 180 seconds',async t=>{
 reset();state.state='working'
 const samples=[0,0.5,1-Number.EPSILON]
 t.mock.method(Math,'random',()=>{assert.ok(samples.length);return samples.shift()})
 await listeners.startup()
 assert.deepEqual(alarm,{name:'harness-companion',delayInMinutes:2})
 await listeners.alarm({name:'unrelated'})
 assert.equal(alarmCreates.length,1);assert.equal(saved.binding.checkedAt,undefined)
 for(const expected of [2.5,3]){
  alarm=undefined
  await listeners.alarm({name:'harness-companion'})
  assert.deepEqual(alarm,{name:'harness-companion',delayInMinutes:expected})
  assert.equal(saved.binding.reason,'waiting_executor')
 }
 assert.equal(alarmCreates.length,3);assert.equal(samples.length,0)
 assert.equal(claims,0);assert.equal(sends,0)
})

test('worker startup preserves a scheduled one-shot alarm and replaces a periodic alarm',async t=>{
 reset();t.mock.method(Math,'random',()=>0.5)
 alarm={name:'harness-companion',scheduledTime:Date.now()+150000}
 const scheduled=clone(alarm)
 await listeners.startup();await listeners.installed()
 assert.deepEqual(alarm,scheduled);assert.equal(alarmCreates.length,0)
 alarm={name:'harness-companion',periodInMinutes:0.5}
 await listeners.installed()
 assert.deepEqual(alarm,{name:'harness-companion',delayInMinutes:2.5})
 alarm=undefined
 await listeners.startup()
 assert.equal(alarmCreates.length,2)
})

test('an in-flight check schedules only after completion and overlapping ticks do not add checks',async t=>{
 reset();state.state='working';t.mock.method(Math,'random',()=>0)
 let release,accepted
 const held=new Promise(resolve=>{release=resolve})
 const started=new Promise(resolve=>{accepted=resolve})
 const original=globalThis.fetch
 t.mock.method(globalThis,'fetch',async(...args)=>{accepted();await held;return original(...args)})
 const checking=tick()
 try{
  await started
  await tick()
  assert.equal(alarmCreates.length,0)
 }finally{release();await checking}
 assert.deepEqual(alarmCreates,[{name:'harness-companion',delayInMinutes:2}])
})

test('bridge failures still schedule a randomized read check and alarm failures release the tick lock',async t=>{
 reset();state.state='working';t.mock.method(Math,'random',()=>1-Number.EPSILON)
 const fetchMock=t.mock.method(globalThis,'fetch',async()=>{throw TypeError('offline')})
 await tick()
 assert.equal(saved.binding.reason,'bridge_unavailable');assert.equal(saved.binding.enabled,true)
 assert.deepEqual(alarm,{name:'harness-companion',delayInMinutes:3})
 fetchMock.mock.restore()
 const failure=Error('alarm unavailable')
 const createMock=t.mock.method(chrome.alarms,'create',async()=>{throw failure})
 await assert.rejects(tick(),error=>error===failure)
 createMock.mock.restore()
 await tick()
 assert.equal(saved.binding.reason,'waiting_executor');assert.equal(alarmCreates.length,2)
 assert.equal(claims,0);assert.equal(sends,0)
})

test('enabling continuation still checks immediately and schedules the next randomized check',{timeout:1000},async t=>{
 reset();state.state='working';t.mock.method(Math,'random',()=>0.5)
 let scheduled
 const finished=new Promise(resolve=>{scheduled=resolve})
 const original=chrome.alarms.create
 t.mock.method(chrome.alarms,'create',async(...args)=>{await original(...args);scheduled()})
 const binding=clone(saved.binding)
 saved.binding.enabled=false
 const response=await new Promise(resolve=>{
  assert.equal(listeners.message({type:'start',binding},{id:chrome.runtime.id,url:chrome.runtime.getURL('popup.html')},resolve),true)
 })
 assert.deepEqual(response,{ok:true})
 await finished
 assert.equal(saved.binding.reason,'waiting_executor')
 assert.deepEqual(alarm,{name:'harness-companion',delayInMinutes:2.5})
})

test('the popup and expanded tab can control checks while other sender pages remain rejected',async()=>{
 reset();saved.binding.enabled=false
 for(const suffix of ['', '?view=tab&sourceTab=42&draft=fixture']){
  const response=await new Promise(resolve=>{
   assert.equal(listeners.message({type:'tick'},{id:chrome.runtime.id,url:chrome.runtime.getURL('popup.html')+suffix},resolve),true)
  })
  assert.deepEqual(response,{ok:true})
 }
 for(const sender of [
  {id:'other',url:chrome.runtime.getURL('popup.html')},
  {id:chrome.runtime.id,url:'https://example.com/popup.html?view=tab'},
  {id:chrome.runtime.id,url:chrome.runtime.getURL('popup.html.attacker?view=tab')},
  {id:chrome.runtime.id,url:chrome.runtime.getURL('page.mjs?view=tab')},
 ])assert.equal(listeners.message({type:'tick'},sender,()=>assert.fail('untrusted sender')),undefined)
 assert.equal(claims,0);assert.equal(sends,0)
})

test('a confirmed continuation is not sent again without new durable progress',async()=>{
 reset();await tick();await tick();assert.equal(sends,1);assert.equal(claims,1)
})
test('uncertain sends stay paused even when an identical historical continuation exists',async()=>{
 reset();mode='unknown';await tick();assert.equal(saved.binding.enabled,false);await tick();assert.equal(sends,1)
 saved.binding.enabled=true;page.userMessages.push(state.pending.prompt);await tick()
 assert.ok(state.pending);assert.equal(sends,1);assert.equal(saved.binding.enabled,false)
 assert.equal(saved.binding.reason,'send_unconfirmed')
})
test('a locally confirmed send can finish its original receipt without sending again',async()=>{
 reset();state.pending={id:'intent',prompt:'继续'};saved.pending={...state.pending,outcome:'confirmed'}
 await tick();assert.equal(state.pending,null);assert.equal(saved.pending,undefined);assert.equal(sends,0)
})
test('drafts, active generation and changed controls prevent a send claim',async()=>{
 for(const reason of ['draft','composing','busy','unsupported_page']){
  reset();page.ready=false;page.reason=reason;await tick();assert.equal(claims,0)
  if(reason==='unsupported_page')assert.equal(saved.binding.enabled,false)
 }
 reset();state.state='working';await tick();assert.equal(sends,0)
})
test('a stopped binding, an expired binding and completed work do not create messages',async()=>{
 reset();saved.binding.enabled=false;await tick();assert.equal(claims,0)
 reset();saved.binding.expiresAt=Date.now()-1;await tick();assert.equal(claims,0);assert.equal(saved.binding.enabled,false)
 reset();state.state='complete';await tick();assert.equal(claims,0);assert.equal(saved.binding.enabled,false)
})
test('bridge reconnection uses reads and does not replay an unknown dispatch',async()=>{
 reset();state.pending={id:'old',prompt:'继续'};page.userMessages=['继续'];await tick()
 assert.equal(claims,0);assert.equal(sends,0);assert.equal(saved.binding.enabled,false)
})

test('pairing refusal pauses the binding without claiming a message',async()=>{
 reset();const original=globalThis.fetch
 try{
  globalThis.fetch=async()=>({ok:false,status:403})
  await tick()
  assert.equal(saved.binding.enabled,false)
  assert.equal(saved.binding.reason,'pairing_denied')
  assert.equal(claims,0);assert.equal(sends,0)
 }finally{globalThis.fetch=original}
})

test('missing page script pauses with refresh guidance instead of a bridge error',async()=>{
 reset();const original=chrome.tabs.sendMessage
 try{
  chrome.tabs.sendMessage=async()=>{throw Error('Receiving end does not exist')}
  await tick()
  assert.equal(saved.binding.enabled,false)
  assert.equal(saved.binding.reason,'page_unavailable')
  assert.equal(claims,0);assert.equal(sends,0)
 }finally{chrome.tabs.sendMessage=original}
})

test('an image milestone requests generation once and delivery once after a distinct loaded image',async()=>{
 reset();state.state='image';await tick()
 assert.deepEqual(state.sent_phases,['generate'])
 await tick();assert.equal(claims,1)
 page.hasImage=true;page.assistantKey='new-loaded-image'
 saved.binding.idleKey=page.assistantKey;saved.binding.idleSince=Date.now()-10000
 await tick();assert.deepEqual(state.sent_phases,['generate','deliver'])
 await tick();assert.equal(claims,2);assert.equal(sends,2)
})

test('missing replies and executor jobs report distinct reasons and a check timestamp',async()=>{
 reset();page.hasAssistant=false;await tick()
 assert.equal(saved.binding.reason,'reply_unrecognized');assert.equal(claims,0)
 assert.ok(saved.binding.checkedAt>0)
 reset();state.state='working';await tick()
 assert.equal(saved.binding.reason,'waiting_executor');assert.equal(claims,0)
})

test('busy observations reset the continuous idle window before a later send',async()=>{
 reset();page.ready=false;page.reason='busy';await tick()
 assert.equal(saved.binding.idleSince,0);assert.equal(claims,0)
 page.ready=true;page.reason='';await tick()
 assert.equal(saved.binding.reason,'stabilizing');assert.equal(claims,0)
 saved.binding.idleSince=Date.now()-4000;await tick()
 assert.equal(claims,1);assert.equal(sends,1)
})

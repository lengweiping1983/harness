import assert from 'node:assert/strict'
import test from 'node:test'

let saved, state, page, claims, sends, mode, claimedPhase
const url='https://chatgpt.com/c/00000000-0000-0000-0000-000000000001'
const clone=value=>structuredClone(value)
const reset=()=>{
 saved={settings:{endpoint:'http://127.0.0.1:8877/',token:'fixture'},binding:{id:'binding',tabId:1,url,enabled:true,expiresAt:Date.now()+100000,idleKey:'a',idleSince:Date.now()-10000}}
 state={state:'continue',milestone:'m1',url,pending:null,sent_phases:[]}
 page={ready:true,assistantKey:'a',hasAssistant:true,hasImage:false,userMessages:[]}
 claims=0;sends=0;mode='confirmed';claimedPhase=undefined
}
reset()
globalThis.chrome={
 storage:{local:{setAccessLevel:async()=>{},get:async keys=>Object.fromEntries((typeof keys==='string'?[keys]:keys).map(key=>[key,clone(saved[key])])),set:async value=>Object.assign(saved,clone(value)),remove:async key=>{delete saved[key]}}},
 alarms:{get:async()=>({}),create:async()=>{},onAlarm:{addListener(){}}},
 runtime:{id:'fixture',onStartup:{addListener(){}},onInstalled:{addListener(){}},onMessage:{addListener(){}}},
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

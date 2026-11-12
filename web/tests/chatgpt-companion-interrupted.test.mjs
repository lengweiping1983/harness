import assert from 'node:assert/strict'
import test from 'node:test'
import {inspectPage,sendOnce} from '../../extensions/chatgpt-companion/page.mjs'

const url='https://chatgpt.com/c/00000000-0000-0000-0000-000000000001'
function fixture(){
 const state={status:'cancelled',turnId:'cancelled-turn',currentAssistant:true,stop:false,loading:false,dialog:false,attachment:false,changeOnInput:false,clicks:0}
 const users=[]
 const message=text=>({textContent:text,contains:()=>false,compareDocumentPosition:()=>4,
  getAttribute:()=>null,closest:()=>null,querySelectorAll:()=>[],cloneNode:()=>({textContent:text,querySelectorAll:()=>[]})})
 const assistant=message('Partial reply')
 assistant.getAttribute=name=>name==='data-message-id'?'assistant-1':null
 assistant.querySelectorAll=selector=>selector==='img'&&!state.currentAssistant?[{complete:true,naturalWidth:1,getAttribute:()=>null,currentSrc:'previous-image'}]:[]
 const root={getAttribute:()=>state.turnId,contains:node=>state.currentAssistant&&node===assistant,closest:()=>null}
 const turn={getAttribute:()=>state.status,closest:()=>root}
 const editor={value:'',focus(){},closest:()=>form,dispatchEvent(){if(state.changeOnInput)state.status='in_progress'}}
 const button={disabled:false,click(){state.clicks++;users.push(message(editor.value));editor.value=''}}
 const form={querySelector:()=>state.attachment?{}:null,querySelectorAll:selector=>{
  if(selector.startsWith('[data-composer-stop-icon]'))return state.loading?[{}]:[]
  return selector.startsWith('button[')?[button]:[]
 }}
 const doc={addEventListener(){},querySelectorAll:selector=>{
  if(selector.startsWith('#prompt-textarea'))return [editor]
  if(selector==='[data-message-author-role="user"]')return users
  if(selector==='[data-message-author-role="assistant"]')return [assistant]
  if(selector==='[data-turn-key] [data-talvt-turn-state]')return [turn]
  if(selector==='[data-testid="stop-button"]')return state.stop?[{}]:[]
  if(selector==='[role="dialog"]')return state.dialog?[{}]:[]
  return []
 }}
 const probe=(enabled=true,composing=false,active=false)=>inspectPage(doc,url,url,composing,active,enabled)
 return {state,doc,editor,probe}
}

test('a cancelled ChatGPT reply is idle only in the selected continuation mode',()=>{
 const page=fixture()
 assert.equal(page.probe(false).reason,'busy')
 assert.equal(page.probe().interrupted,true)
 assert.equal(page.probe().ready,true)
 for(const status of ['in_progress','running','pending','error','interrupted','stopped','future']){
  page.state.status=status
  assert.equal(page.probe().ready,false,status)
 }
})

test('cancelled replies retain stop, loading, dialog, draft, attachment and IME protections',()=>{
 const page=fixture()
 for(const key of ['stop','loading','dialog','attachment']){
  page.state[key]=true;assert.equal(page.probe().ready,false,key);page.state[key]=false
 }
 page.editor.value='User draft';assert.equal(page.probe().reason,'draft');page.editor.value=''
 assert.equal(page.probe(true,true).reason,'composing')
 assert.equal(page.probe(true,false,true).reason,'user_active')
})

test('a turn cancelled before assistant text has its own identity and cannot borrow an earlier image',()=>{
 const page=fixture();page.state.currentAssistant=false
 const first=page.probe()
 assert.equal(first.ready,true);assert.equal(first.interrupted,true)
 assert.equal(first.hasAssistant,false);assert.equal(first.hasImage,false)
 assert.match(first.assistantKey,/^[0-9a-f]{16}$/)
 assert.equal(page.probe().assistantKey,first.assistantKey)
 page.state.turnId='another-cancelled-turn'
 assert.notEqual(page.probe().assistantKey,first.assistantKey)
})

test('sending to a cancelled reply uses the same mode and rechecks resumed generation before clicking',async t=>{
 const page=fixture()
 const previous=Object.getOwnPropertyDescriptor(globalThis,'InputEvent')
 Object.defineProperty(globalThis,'InputEvent',{configurable:true,writable:true,value:class{}})
 t.after(()=>{if(previous)Object.defineProperty(globalThis,'InputEvent',previous);else delete globalThis.InputEvent})
 assert.deepEqual(await sendOnce(page.doc,url,()=>url,'继续',()=>true),{outcome:'not_sent',reason:'busy'})
 assert.deepEqual(await sendOnce(page.doc,url,()=>url,'继续',()=>true,fill=>fill(),true),{outcome:'confirmed'})
 assert.equal(page.state.clicks,1)
 page.state.changeOnInput=true
 assert.deepEqual(await sendOnce(page.doc,url,()=>url,'继续',()=>true,fill=>fill(),true),{outcome:'unknown',reason:'user_active'})
 assert.equal(page.state.clicks,1);assert.equal(page.editor.value,'继续')
})

test('the real content script propagates interruption mode through both probe and dispatch',async t=>{
 const page=fixture();let listener
 const chrome={runtime:{id:'fixture',getURL:()=>new URL('../../extensions/chatgpt-companion/page.mjs',import.meta.url).href,
  onMessage:{addListener:callback=>{listener=callback}}}}
 for(const [key,value] of Object.entries({document:page.doc,chrome,location:new URL(url),InputEvent:class{}})){
  const previous=Object.getOwnPropertyDescriptor(globalThis,key)
  Object.defineProperty(globalThis,key,{configurable:true,writable:true,value})
  t.after(()=>{if(previous)Object.defineProperty(globalThis,key,previous);else delete globalThis[key]})
 }
 t.mock.method(Date,'now',()=>10000)
 await import('../../extensions/chatgpt-companion/content.js')
 const request=message=>new Promise(resolve=>listener({url,...message},{id:'fixture'},resolve))
 assert.equal((await request({type:'probe'})).ready,false)
 const probe=await request({type:'probe',allowInterrupted:true})
 assert.equal(probe.ready,true)
 assert.deepEqual(await request({type:'dispatch',allowInterrupted:true,assistantKey:probe.assistantKey,prompt:'继续'}),{outcome:'confirmed'})
 assert.equal(page.state.clicks,1)
 assert.equal((await request({type:'dispatch',allowInterrupted:'true',prompt:'继续'})).outcome,'not_sent')
 assert.equal(page.state.clicks,1)
})

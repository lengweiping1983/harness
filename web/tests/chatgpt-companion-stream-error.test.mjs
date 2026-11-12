import assert from 'node:assert/strict'
import test from 'node:test'
import {inspectPage,sendOnce} from '../../extensions/chatgpt-companion/page.mjs'

const url='https://chatgpt.com/c/00000000-0000-0000-0000-000000000001'
function fixture(t){
 let now=10000
 t.mock.method(Date,'now',()=>now)
 t.mock.method(globalThis,'setTimeout',(callback,ms)=>{now+=ms;queueMicrotask(callback);return 0})
 const previous=Object.getOwnPropertyDescriptor(globalThis,'InputEvent')
 Object.defineProperty(globalThis,'InputEvent',{configurable:true,writable:true,value:class{}})
 t.after(()=>{if(previous)Object.defineProperty(globalThis,'InputEvent',previous);else delete globalThis.InputEvent})
 const state={created:false,append:true,text:'继续',native:true,clicks:0,retryLabel:'重试',retryDisabled:false,ariaDisabled:false,
  danger:true,hidden:false,oldAlert:false,insideComposer:false,loading:false,buttons:1,hiddenFirst:false}
 const classes=values=>({contains:value=>values.includes(value),[Symbol.iterator]:()=>values[Symbol.iterator]()})
 const legacy=text=>({textContent:text,contains:()=>false,compareDocumentPosition:()=>4,getAttribute:()=>null,
  closest:()=>null,querySelectorAll:()=>[],cloneNode:()=>({textContent:text,querySelectorAll:()=>[]})})
 const history=legacy('继续'),assistant=legacy('Partial response')
 const retry={get textContent(){return state.retryLabel},get disabled(){return state.retryDisabled},getAttribute:()=>state.ariaDisabled?'true':null}
 const alert={get hidden(){return state.hidden},get classList(){return classes(state.danger?['bg-background-danger-surface/20']:[])},
  querySelectorAll:()=>[...(state.hiddenFirst?[{hidden:true,disabled:false,getAttribute:()=>null}]:[]),...Array(state.buttons).fill(retry)],compareDocumentPosition:()=>4}
 const main={contains:()=>true,querySelectorAll:()=>[alert]}
 const root={getAttribute:()=> 'current-turn',closest:()=>main,contains:node=>node===heading||node===content||node===assistant||node===alert&&!state.oldAlert,
  compareDocumentPosition:()=>state.oldAlert?2:4,parentElement:main,querySelectorAll:()=>[heading],
  cloneNode:()=>({textContent:state.text+'消息流中的错误重试',querySelectorAll:()=>[]})}
 const content={get textContent(){return state.text+'17:55'},classList:classes(['group/user-message']),matches:()=>state.native,
  contains:()=>false,compareDocumentPosition:()=>4,parentElement:root,
  cloneNode:()=>{const body={textContent:state.text+'17:55',querySelectorAll:()=>[{remove(){body.textContent=state.text}}]};return body}}
 const heading={getAttribute:()=> 'user',closest:()=>root,nextElementSibling:content,parentElement:root}
 const turn={getAttribute:()=> 'error',closest:()=>root}
 const editor={value:'',focus(){},closest:()=>form,dispatchEvent(){}}
 const submit={disabled:false,click(){state.clicks++;state.created=state.append;editor.value=''}}
 const form={contains:()=>state.insideComposer,querySelector:()=>null,querySelectorAll:selector=>{
  if(selector.startsWith('[data-composer-stop-icon]'))return state.loading?[{}]:[]
  return selector.startsWith('button[')?[submit]:[]
 }}
 const doc={querySelectorAll:selector=>{
  if(selector.startsWith('#prompt-textarea'))return [editor]
  if(selector==='[data-message-author-role="user"]')return [history]
  if(selector==='[data-message-author-role="assistant"]')return [assistant]
  if(selector==='h4[data-conversation-role="user"]')return state.created?[heading]:[]
  if(selector==='[data-turn-key] [data-talvt-turn-state]')return [turn]
  return []
 }}
 return {state,editor,probe:enabled=>inspectPage(doc,url,url,false,false,enabled),send:()=>sendOnce(doc,url,()=>url,'继续',()=>true,undefined,true)}
}

test('a newly sent native user message is confirmed independently of the stream error and timestamp',async t=>{
 const page=fixture(t)
 assert.deepEqual(await page.send(),{outcome:'confirmed'})
 assert.equal(page.state.clicks,1)
})

test('a stream error and identical historical continuation cannot confirm an unobserved send',async t=>{
 const page=fixture(t);page.state.append=false
 assert.deepEqual(await page.send(),{outcome:'unknown',reason:'send_unconfirmed'})
 assert.equal(page.state.clicks,1)
})

test('native receipt matching still requires the exact newly sent text',async t=>{
 const page=fixture(t);page.state.text='请继续'
 assert.deepEqual(await page.send(),{outcome:'unknown',reason:'send_unconfirmed'})
 assert.equal(page.state.clicks,1)
})

test('a current native response error with Retry is recoverable only in continuous mode',t=>{
 const page=fixture(t)
 assert.equal(page.probe(false).ready,false)
 assert.equal(page.probe(true).ready,true)
 assert.equal(page.probe(true).interrupted,true)
 for(const label of ['Retry','重试']){
  page.state.retryLabel=label;assert.equal(page.probe(true).ready,true)
 }
})

test('retry-in-progress and unrelated or non-retry alerts do not authorize another continuation',t=>{
 const page=fixture(t)
 for(const key of ['retryDisabled','ariaDisabled','oldAlert','insideComposer','loading','hidden']){
  page.state[key]=true;assert.equal(page.probe(true).ready,false,key);page.state[key]=false
 }
 page.state.danger=false;assert.equal(page.probe(true).ready,false);page.state.danger=true
 page.state.buttons=2;assert.equal(page.probe(true).ready,false);page.state.buttons=1
 page.state.hiddenFirst=true;page.state.retryDisabled=true;assert.equal(page.probe(true).ready,false)
 page.state.retryDisabled=false;assert.equal(page.probe(true).ready,true);page.state.hiddenFirst=false
 for(const label of ['Start new chat','Use default model','Upgrade']){
  page.state.retryLabel=label;assert.equal(page.probe(true).ready,false,label)
 }
 page.state.retryLabel='Retry';page.editor.value='User draft';assert.equal(page.probe(true).reason,'draft')
})

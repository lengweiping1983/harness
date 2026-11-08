import assert from 'node:assert/strict'
import test from 'node:test'
import {readFile} from 'node:fs/promises'

let instance=0
async function mountPopup(t,{search='',session={}}={}){
 const elements=new Map()
 const element=id=>{
  if(!elements.has(id))elements.set(id,{
   dataset:{},value:'',textContent:'',type:'password',handlers:{},
   addEventListener(type,handler){this.handlers[type]=handler},
   setAttribute(){},querySelectorAll(){return []},closest(){return this},
   replaceChildren(...options){this.options=options;this.value=options[0]?.value||''},
  })
  return elements.get(id)
 }
 const timing=element('timing');timing.dataset.key='timing'
 let text='',rendered
 Object.defineProperty(timing,'textContent',{
  get:()=>text,set:value=>{text=value;rendered?.()},
 })
 const opener=element('open-tab');opener.dataset.key='open_tab';opener.dataset.ariaKey='open_tab'
 const document={documentElement:{lang:'',dataset:{}},getElementById:element,createElement:()=>({}),
  querySelectorAll:selector=>selector==='[data-key]'?[timing,opener]:selector==='[data-aria-key]'?[opener]:[]}
 const calls={controls:[],writes:0,created:[],probes:[],tabReads:[]}
 const saved={settings:{language:'en'}}
 const chat={id:42,url:'https://chatgpt.com/c/00000000-0000-0000-0000-000000000001'}
 const chrome={storage:{local:{get:async()=>structuredClone(saved),set:async value=>{calls.writes++;Object.assign(saved,value)}},
  session:{get:async key=>({[key]:structuredClone(session[key])}),set:async value=>Object.assign(session,structuredClone(value)),remove:async key=>{delete session[key]}},onChanged:{addListener(){}}},
  tabs:{query:async()=>[chat],get:async id=>{calls.tabReads.push(id);assert.equal(id,chat.id);return chat},create:async input=>{calls.created.push(input)},
   sendMessage:async(id,message)=>{calls.probes.push({id,message});return {ready:true}}},
  runtime:{getManifest:()=>({version:'1.0.0'}),getURL:path=>'chrome-extension://fixture/'+path,sendMessage:async message=>{calls.controls.push(message);return {ok:true}}}}
 const location=new URL('chrome-extension://fixture/popup.html'+search)
 for(const [key,value] of Object.entries({document,chrome,location})){
  const previous=Object.getOwnPropertyDescriptor(globalThis,key)
  Object.defineProperty(globalThis,key,{configurable:true,writable:true,value})
  t.after(()=>{if(previous)Object.defineProperty(globalThis,key,previous);else delete globalThis[key]})
 }
 t.mock.method(globalThis,'fetch',async path=>{
  if(!String(path).startsWith('http'))return {json:async()=>JSON.parse(await readFile(new URL('../../extensions/chatgpt-companion/'+path,import.meta.url),'utf8'))}
  const pathname=new URL(path).pathname
  const values={
   '/health':{protocol:'harness-chat-companion',protocol_version:1,executor:'ready'},
   '/sessions':{sessions:[{session_id:'he_'+'1'.repeat(32),title:'A long development target',project_id:'fixture-project'}]},
   '/bind':{binding_id:'bind_'+'2'.repeat(32),url:chat.url},
  }
  assert.ok(values[pathname],pathname)
  return {ok:true,json:async()=>structuredClone(values[pathname])}
 })
 await import('../../extensions/chatgpt-companion/popup.mjs?test='+ ++instance)
 return {element,timing,opener,document,calls,chrome,chat,session,onRender:callback=>{rendered=callback}}
}

test('the same popup renders randomized timing help in English, Chinese and English without starting checks',{timeout:2000},async t=>{
 const {element,timing,opener,document,calls,onRender}=await mountPopup(t)
 for(const language of ['en','zh_CN','en']){
  if(element('language').value!==language){
   const translated=new Promise(resolve=>{onRender(resolve)})
   element('language').value=language
   element('language').handlers.change({target:element('language')})
   await translated
  }
  const messages=JSON.parse(await readFile(new URL(
   `../../extensions/chatgpt-companion/_locales/${language}/messages.json`,import.meta.url),'utf8'))
  assert.equal(timing.textContent,messages.timing.message)
  assert.equal(opener.textContent,messages.open_tab.message)
  assert.match(timing.textContent,/120[–～]180/)
  assert.match(timing.textContent,language==='en'?/randomized/:/重新随机/)
  assert.equal(document.documentElement.lang,language==='en'?'en':'zh-CN')
 }
 assert.equal(calls.controls.length,0)
 assert.equal(calls.writes,2)
})

test('opening a wider tab transfers private drafts once, retains the source chat and rejects duplicate clicks',async t=>{
 const popup=await mountPopup(t)
 popup.element('endpoint').value='http://127.0.0.1:9999/'
 popup.element('token').value='private-unsaved-token'
 let release
 const held=new Promise(resolve=>{release=resolve})
 const create=popup.chrome.tabs.create
 t.mock.method(popup.chrome.tabs,'create',async input=>{await create(input);await held})
 const opening=popup.opener.handlers.click()
 try{await popup.opener.handlers.click()}finally{release();await opening}
 assert.equal(popup.calls.created.length,1)
 const target=new URL(popup.calls.created[0].url)
 assert.equal(target.searchParams.get('view'),'tab')
 assert.equal(target.searchParams.get('sourceTab'),'42')
 assert.equal(target.searchParams.get('sourceChat'),popup.chat.url)
 assert.ok(!target.href.includes('private-unsaved-token'))
 assert.ok(!target.href.includes('9999'))
 assert.equal(popup.calls.writes,0);assert.equal(popup.calls.controls.length,0)
 const tab=await mountPopup(t,{search:target.search,session:popup.session})
 assert.equal(tab.document.documentElement.dataset.view,'tab')
 assert.equal(tab.opener.hidden,true)
 assert.equal(tab.element('endpoint').value,'http://127.0.0.1:9999/')
 assert.equal(tab.element('token').value,'private-unsaved-token')
 assert.deepEqual(tab.session,{})
 assert.equal(tab.calls.controls.length,0);assert.equal(tab.calls.writes,0)
})

test('the expanded tab starts only the original ChatGPT tab and reports when it is gone',async t=>{
 const url='https://chatgpt.com/c/00000000-0000-0000-0000-000000000001'
 const popup=await mountPopup(t,{search:'?view=tab&sourceTab=42&sourceChat='+encodeURIComponent(url)})
 popup.element('token').value='fixture-token'
 await popup.element('connect').handlers.click()
 assert.equal(popup.element('session').value,'fixture-project')
 popup.calls.controls=[]
 await popup.element('start').handlers.click()
 assert.deepEqual(popup.calls.tabReads,[42])
 assert.equal(popup.calls.probes[0].id,42)
 const start=popup.calls.controls.find(message=>message.type==='start')
 assert.equal(start.binding.tabId,42);assert.equal(start.binding.url,popup.chat.url)
 popup.chat.url='https://chatgpt.com/c/00000000-0000-0000-0000-000000000002'
 popup.calls.controls=[]
 await popup.element('start').handlers.click()
 assert.deepEqual(popup.calls.controls,[])
 assert.match(popup.element('status').textContent,/original ChatGPT tab/)
 t.mock.method(popup.chrome.tabs,'get',async()=>{throw Error('No tab')})
 popup.calls.controls=[]
 await popup.element('start').handlers.click()
 assert.deepEqual(popup.calls.controls,[])
 assert.match(popup.element('status').textContent,/original ChatGPT tab/)
 for(const language of ['zh_CN','en']){
  const translated=new Promise(resolve=>popup.onRender(resolve))
  popup.element('language').value=language
  popup.element('language').handlers.change({target:popup.element('language')})
  await translated
  assert.match(popup.element('status').textContent,language==='en'?/original ChatGPT tab/:/原 ChatGPT 标签页/)
  assert.deepEqual(popup.calls.controls,[])
 }
})

test('an expanded tab without a source chat cannot send a continuation',async t=>{
 const popup=await mountPopup(t,{search:'?view=tab&sourceTab=invalid'})
 popup.element('token').value='fixture-token'
 await popup.element('connect').handlers.click()
 popup.calls.controls=[]
 await popup.element('start').handlers.click()
 assert.deepEqual(popup.calls.controls,[])
 assert.match(popup.element('status').textContent,/original ChatGPT tab/)
})

test('a failed tab opening keeps the input draft, removes only its transfer and permits an explicit retry',async t=>{
 const popup=await mountPopup(t)
 popup.element('token').value='unsaved-token'
 popup.session.unrelated={value:'keep'}
 t.mock.method(popup.chrome.tabs,'create',async()=>{throw Error('tab creation refused')})
 await popup.opener.handlers.click()
 assert.equal(popup.element('token').value,'unsaved-token')
 assert.deepEqual(popup.session,{unrelated:{value:'keep'}})
 assert.equal(popup.opener.disabled,false)
 assert.match(popup.element('status').textContent,/Request failed/)
 assert.equal(popup.calls.writes,0);assert.deepEqual(popup.calls.controls,[])
})

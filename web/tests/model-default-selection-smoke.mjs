import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ModelMenu,ModelSeat} from '/src/components/chat/ModelPicker.tsx'
import {GenerationDock} from '/src/components/chat/GenerationDock.tsx'
import {useAppStore} from '/src/store/app.ts'
import {sessionModelOf} from '/src/lib/workspaces.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t;window.writes=[];window.closes=[];window.calls=[]
window.fetch=async(path,options={})=>{
 window.calls.push({path,method:options.method||'GET'})
 if(path==='/api/v1/connection')return Response.json({version:2,revision:'fixture',providers:[],models:[],current:null,cycle:1})
 if(options.method==='PATCH')return new Promise(resolve=>window.writes.push({path,body:JSON.parse(options.body),resolve}))
 if(String(path).startsWith('/api/v1/sessions/'))return Response.json({header:{id:'one',metadata:{model:'A'}},events:[],messages:[],usage:{},stats:{},todos:[],timeline:[]})
 throw Error('Unexpected fixture request '+path)
}
const models=['A','B','C'].map(id=>({id,label:'Model '+id,provider:'Fixture 原文',visible:true})),root=createRoot(document.getElementById('root'))
let epoch=0
function Fixture(){
 const owner=useAppStore(s=>s.activeSessionId),sessions=useAppStore(s=>s.sessions),fallback=useAppStore(s=>s.defaultModel),open=useAppStore(s=>s.modelMenuOpen)
 const session=sessions.find(row=>row.id===owner)
 return <main className="mx-auto max-w-xl space-y-3 p-2"><ModelSeat/><input aria-label="Original draft" defaultValue="Keep 用户草稿" className="w-full"/>
 <GenerationDock generation={{request_id:'original',phase:'waiting',model:'Original request model',receivedAt:performance.now(),text_chars:0,thinking_chars:0,retry_in_seconds:0,elapsed_ms:0,idle_ms:0}} connection="open" stopping={false}/>
 {open&&<ModelMenu models={models} current={sessionModelOf(session,fallback)} onClose={()=>{window.closes.push(owner);useAppStore.getState().setModelMenuOpen(false)}}/>}</main>
}
window.reset=(model=null,owner='one')=>{useAppStore.setState({activeSessionId:owner,sessions:[{id:'one',model},{id:'two',model:'B'}],models,defaultModel:'A',modelSaves:{},defaultModelSave:null,modelMenuOpen:true});root.render(<Fixture key={++epoch}/>)}
window.select=owner=>useAppStore.setState({activeSessionId:owner})
window.open=()=>useAppStore.getState().setModelMenuOpen(true)
window.state=()=>useAppStore.getState()
window.defaultModel=model=>useAppStore.setState({defaultModel:model})
window.answer=(index,mode='accepted')=>{
 const write=window.writes[index],owner=decodeURIComponent(write.path.split('/').at(-1))
 write.resolve(mode==='refused'?Response.json({message:'Original refusal 原文'},{status:403}):mode==='malformed'?new Response('not JSON',{status:200}):Response.json({session:{id:owner,metadata:{model:write.body.model??''}}}))
}
window.reset()
`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
 await page.goto(server.resolvedUrls.local[0]+'__model-default-selection')
 const copy=(key,params)=>page.evaluate(({key,params})=>window.copy(key,params),{key,params})
 const option=id=>page.getByRole('option').filter({hasText:'Model '+id})
 const count=()=>page.evaluate(()=>window.writes.length)
 const reset=async(model=null,owner='one')=>{await page.evaluate(({model,owner})=>window.reset(model,owner),{model,owner});await page.getByRole('listbox').waitFor()}
 const settled=()=>page.waitForFunction(()=>Object.keys(window.state().modelSaves).length===0)
 const actualRequest=async()=>{
  const label=await copy('ui.generation.requestModel',{model:'Original request model'})
  await page.waitForFunction(label=>document.querySelector('summary')?.title===label,label)
  assert.equal(await page.locator('dd').first().textContent(),'Original request model')
 }
 const seat=async(name='Model A',saving=false)=>page.getByRole('button',{name:await copy(saving?'ui.models.selectionSaving':'ui.components.chat.ModelPicker.8b625429e269',{p0:name}),exact:true}).waitFor()

 // 默认模型直接选中；组合输入、Tab 和 Escape 均不提交。
 await seat();assert.equal(await option('A').getAttribute('aria-selected'),'true')
 await option('B').focus()
 for(const flags of [{isComposing:true},{keyCode:229},{isComposing:true,shiftKey:true},{keyCode:229,ctrlKey:true}]){
  assert.equal(await option('B').evaluate((node,flags)=>node.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true,...flags})),flags),false)
 }
 await page.keyboard.press('Escape');assert.equal(await count(),0);await page.getByRole('listbox').waitFor({state:'detached'})
 await page.evaluate(()=>window.open());await option('B').focus();await page.keyboard.press('Tab');await page.getByRole('listbox').waitFor({state:'detached'});assert.equal(await count(),0)
 await page.evaluate(()=>window.open());await option('B').focus();await page.keyboard.press('Enter');await page.waitForFunction(()=>window.writes.length===1)
 await option('C').evaluate(node=>{node.click();node.click()});assert.equal(await count(),1)
 assert.deepEqual(await page.evaluate(()=>window.writes[0].body),{model:'B'});await seat('Model B',true);await actualRequest()
 await page.evaluate(()=>window.answer(0));await settled();await page.getByRole('listbox').waitFor({state:'detached'})
 assert.equal(await page.evaluate(()=>window.state().defaultModel),'A')

 await reset()
 for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440])for(const font of [10,22]){
  await page.setViewportSize({width,height:1100});await page.evaluate(({locale,dark,font})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark);document.documentElement.style.fontSize=font+'px'},{locale,dark,font})
  await seat();await page.getByText(await copy('ui.models.conversationScope'),{exact:true}).waitFor()
  assert.equal(await page.getByRole('option',{selected:true}).count(),1)
  assert.equal(await option('A').getAttribute('aria-selected'),'true')
  assert.equal(/继承配置|恢复模型继承|Inherited configuration|Restore model inheritance/i.test(await page.locator('body').innerText()),false)
  assert.equal(await page.getByText('Fixture 原文',{exact:true}).evaluate(node=>getComputedStyle(node).textTransform),'none')
  const hints=await page.locator('footer > span').evaluateAll(nodes=>nodes.map(node=>node.getBoundingClientRect().toJSON()))
  for(let index=1;index<hints.length;index++)assert.ok(hints[index].top>=hints[index-1].bottom-1||hints[index].left>=hints[index-1].right-1,'Keyboard hints must wrap without overlap')
  await actualRequest();assert.equal(await count(),1);assert.equal(await page.getByRole('textbox',{name:'Original draft'}).inputValue(),'Keep 用户草稿')
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true)
 }
 if(process.env.HARNESS_TEST_SCREENSHOT)await page.screenshot({path:process.env.HARNESS_TEST_SCREENSHOT})
 // 默认设置改变时更新显示，不产生对话写入；手选才保存具体模型。
 await page.evaluate(()=>window.defaultModel('C'));await seat('Model C');assert.equal(await option('C').getAttribute('aria-selected'),'true');assert.equal(await count(),1)
 await page.evaluate(()=>window.defaultModel('A'));await seat()
 await option('A').click();await page.getByRole('listbox').waitFor({state:'detached'});assert.equal(await count(),1)
 await page.evaluate(()=>window.open());await option('B').click();await page.waitForFunction(()=>window.writes.length===2)
 assert.deepEqual(await page.evaluate(()=>window.writes[1].body),{model:'B'});await page.evaluate(()=>window.answer(1));await settled();await seat('Model B')
 // 拒绝保留原模型和诊断；未知写入只回查，不自动重放。
 await reset('chatgpt/Original override');await option('A').click();await page.waitForFunction(()=>window.writes.length===3)
 await page.evaluate(()=>window.answer(2,'refused'));await settled()
 for(const locale of ['en','zh-CN','en']){
  await page.evaluate(locale=>window.locale(locale),locale)
  await page.getByRole('button',{name:await copy('ui.models.retrySwitch',{model:'Model A'}),exact:true}).waitFor()
  await seat('chatgpt/Original override');await page.getByText('Original refusal 原文',{exact:true}).waitFor();assert.equal(await count(),3)
 }
 await page.getByRole('button',{name:await copy('ui.models.retrySwitch',{model:'Model A'}),exact:true}).click();await page.waitForFunction(()=>window.writes.length===4)
 await page.evaluate(()=>window.answer(3,'malformed'));await settled();await page.getByRole('listbox').waitFor({state:'detached'})
 assert.equal(await page.evaluate(()=>window.calls.filter(row=>row.method==='GET'&&row.path.startsWith('/api/v1/sessions/')).length),1)
 assert.equal(await count(),4,'Unknown write must be confirmed by a read, not replayed')
 // A→B→A 后旧保存不得关闭新菜单；原始请求和草稿保持不变。
 for(const mode of ['accepted','refused']){
  await reset('A');const before=await count(),closed=await page.evaluate(()=>window.closes.length)
  await option('C').click();await page.waitForFunction(before=>window.writes.length===before+1,before)
  await page.evaluate(()=>window.select('two'));await page.getByRole('listbox').waitFor()
  if(mode==='accepted'){
   await option('A').click();await page.waitForFunction(before=>window.writes.length===before+2,before)
   await page.evaluate(index=>window.answer(index),before+1);await page.getByRole('listbox').waitFor({state:'detached'});await page.evaluate(()=>window.open())
  }
  await page.evaluate(()=>window.select('one'));await page.getByRole('listbox').waitFor()
  await page.evaluate(({index,mode})=>window.answer(index,mode),{index:before,mode});await settled();await page.getByRole('listbox').waitFor()
  assert.equal(await page.getByRole('alert').count(),0);assert.equal(await page.evaluate(()=>window.closes.length),closed+(mode==='accepted'?1:0))
  assert.equal(await page.evaluate(()=>window.state().sessions.find(row=>row.id==='one').model),mode==='accepted'?'C':'A');await actualRequest()
 }
 await reset('A');const beforeStaleClick=await count()
 await option('C').evaluate(node=>{window.select('two');node.click()});await seat('Model B');assert.equal(await count(),beforeStaleClick)
 await reset('A',null);await page.getByText(await copy('ui.models.defaultScope'),{exact:true}).waitFor()
 assert.deepEqual(errors,[]);await page.close()
 console.log('Default model selection: actual names, default checkmark, no inheritance controls, manual selection, keyboard/IME, request evidence, one-write recovery, refusal/retry, A→B→A ownership and en→zh→en theme/width/font matrix passed')
},{entry:'/__model-default-selection-entry.jsx',vite:{plugins:[{
 name:'model-default-selection-fixture',resolveId(id){if(id==='/__model-default-selection-entry.jsx')return id},load(id){if(id==='/__model-default-selection-entry.jsx')return entry},
 configureServer(server){server.middlewares.use('/__model-default-selection',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__model-default-selection','<html><body><div id="root"></div><script type="module" src="/__model-default-selection-entry.jsx"></script></body></html>'))}catch(error){next(error)}})},
}]}})

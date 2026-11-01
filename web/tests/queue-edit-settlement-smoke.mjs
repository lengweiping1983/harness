import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TaskQueue} from '/src/components/inspector/TaskQueue.tsx'
import {Toaster} from '/src/components/overlays/CommandPalette.tsx'
import {useQueueEditStore} from '/src/store/queueEdits.ts'
import {useChatStore,emptySession} from '/src/store/chat.ts'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.copy=t
const queued={id:'entry',kind:'runs',text:'Original input 原文',edit_version:'a'.repeat(64)}
const verify=new URLSearchParams(location.search).get('mode').startsWith('verify')
useChatStore.setState({sessions:{fixture:{...emptySession(),connection:'open',queued:1}}})
useAppStore.setState({toasts:[]})
useQueueEditStore.getState().setEdit('fixture',{entry:queued,draft:'Submitted edit 原文',...(verify?{unconfirmed:{entry:queued,text:'Submitted edit 原文'}}:{})})
window.replace=()=>useQueueEditStore.getState().setEdit('fixture',{entry:queued,draft:'Newer draft 原文'})
window.snapshot=()=>({edit:useQueueEditStore.getState().bySession.fixture,toasts:useAppStore.getState().toasts})
createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,React.createElement(TaskQueue,{sessionId:'fixture'}),React.createElement(Toaster)))
`
await browserScenario(async({server,browser})=>{
 for(const mode of ['save-new','save-current','verify-new','verify-current']){
  const page=await browser.newPage(),requests=[],errors=[],unexpected=[]
  page.setDefaultTimeout(15000);page.on('pageerror',error=>errors.push(error.message))
  await page.clock.install({time:new Date(0)});await page.clock.pauseAt(new Date(86400000))
  let release,enter,finish,confirmed=false
  const gate=new Promise(resolve=>{release=resolve}),held=new Promise(resolve=>{enter=resolve}),done=new Promise(resolve=>{finish=resolve})
  await page.route('**/api/v1/**',async route=>{
   const request=route.request(),path=new URL(request.url()).pathname,method=request.method();requests.push({path,method})
   if(path.endsWith('/queue'))return route.fulfill({json:{runs:[{id:'entry',text:confirmed?'Submitted edit 原文':'Original input 原文',edit_version:(confirmed?'b':'a').repeat(64)}]}})
   if(path.endsWith('/runs'))return route.fulfill({json:{runs:[],has_more:false,next_before:null}})
   if(path.endsWith('/runs/entry')){
    enter();await gate;confirmed=true
    await route.fulfill({json:method==='PATCH'?{id:'entry',text:'Submitted edit 原文',edit_version:'b'.repeat(64)}:{id:'entry',session_id:'fixture',text:'Submitted edit 原文',status:'queued',created_at:1}});finish();return
   }
   unexpected.push(path);return route.fulfill({status:500,json:{error:'Unexpected request'}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__queue-settlement?mode='+mode)
  const actionKey=mode.startsWith('save')?'ui.components.inspector.TaskQueue.a3030bf8f16d':'ui.components.inspector.TaskQueue.9b75ad5a4769'
  const action=page.getByRole('button',{name:await page.evaluate(key=>window.copy(key),actionKey),exact:true})
  await action.click();await held
  if(mode.endsWith('new'))await page.evaluate(()=>window.replace())
  release();await done;await page.waitForFunction(()=>window.snapshot().toasts.length===1)
  if(mode.endsWith('new')){
   assert.equal(await page.evaluate(()=>window.snapshot().edit?.draft),'Newer draft 原文','Accepted old edit must preserve the replacement draft')
   assert.equal(await page.getByRole('textbox').inputValue(),'Newer draft 原文')
  }else{
   assert.equal(await page.evaluate(()=>window.snapshot().edit),undefined)
   assert.equal(await page.getByRole('textbox').count(),0)
  }
  await page.clock.runFor(32)
  const count=requests.length,titleKey=mode.startsWith('save')?'ui.components.inspector.TaskQueue.220107f131b5':'ui.components.inspector.TaskQueue.d31dd3908461'
  for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme;document.documentElement.dataset.theme=theme},{locale,theme})
   await page.getByText(await page.evaluate(key=>window.copy(key),titleKey),{exact:true}).waitFor()
   await page.getByText(await page.evaluate(()=>window.copy('ui.queue.pageIndicator',{page:1})),{exact:true}).waitFor()
   assert.equal(requests.length,count)
   if(mode.endsWith('new'))assert.equal(await page.getByRole('textbox').inputValue(),'Newer draft 原文')
  }
  assert.equal(requests.filter(row=>row.method==='PATCH').length,mode.startsWith('save')?1:0)
  assert.equal(requests.filter(row=>row.path.endsWith('/runs/entry')&&row.method==='GET').length,mode.startsWith('verify')?1:0)
  assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[])
  await page.close();console.log(mode+': original settlement only, current draft and live notification language preserved')
 }
},{entry:'/__queue-settlement-entry.js',vite:{plugins:[{
 name:'queue-settlement-fixture',resolveId(id){if(id==='/__queue-settlement-entry.js')return id},load(id){if(id==='/__queue-settlement-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__queue-settlement',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__queue-settlement','<html><body><div id="root"></div><script type="module" src="/__queue-settlement-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
